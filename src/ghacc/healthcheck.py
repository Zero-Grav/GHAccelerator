# -*- coding: utf-8 -*-
"""--selftest 协议实现。

退出码：0 全通过 / 1 关键项失败 / 2 部分能力降级但可运行
输出：stdout 人类可读摘要 + selftest_<timestamp>.json 证据文件

全部断言在回环（loopback）内完成，不依赖 GitHub 是否可达，
因此在客户机断网 / DNS 被污染时依然能给出可信结论。
"""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

from .config import Config, is_writable_dir, project_base_dir
from .logger import get_logger, setup_logging
from .proxy_server import ProxyServer
from .resolver import Resolver
from . import sysproxy
from .version import APP_NAME, APP_VER, CACHE_VER

_LOG = get_logger("selftest")


def _free_port() -> int:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


class _EchoServer(object):
    """回环 echo 服务：验证代理是否真的把流量送到了「优选 IP」。"""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = int(self.sock.getsockname()[1])
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        while not self._stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except Exception:
                return
            try:
                conn.settimeout(5.0)
                while True:
                    data = conn.recv(4096)
                    if not data:
                        break
                    conn.sendall(data)
            except Exception:
                pass
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

    def close(self):
        self._stop.set()
        try:
            self.sock.close()
        except Exception:
            pass
        self.thread.join(timeout=2)


class _MockBackend(object):
    """系统代理后端替身：记录 apply/restore 参数，可注入失败。"""
    name = "mock-injected"

    def __init__(self, fail_apply: bool = False):
        self.fail_apply = fail_apply
        self.enabled = False
        self.server = ""
        self.port = ""
        self.calls: List[Tuple[Any, ...]] = []

    def read(self):
        return sysproxy.ProxySnapshot("Mock", self.enabled, self.server, self.port, "",
                                      {"marker": "before"})

    def apply(self, host, port, bypass):
        self.calls.append(("apply", host, port))
        if self.fail_apply:
            return False, "simulated: registry read-only / policy blocked"
        self.enabled, self.server, self.port = True, host, str(port)
        return True, "ok"

    def restore(self, snap):
        self.calls.append(("restore", snap.as_dict()))
        self.enabled, self.server, self.port = snap.enabled, snap.server, snap.port
        return True, "ok"

    def hint(self):
        return "mock"


def _probe_via_proxy(proxy_port: int, target_host: str, target_port: int,
                     payload: bytes = b"PING") -> Dict[str, Any]:
    """走本地代理发 CONNECT，返回响应与回显结果。"""
    sock = None
    try:
        sock = socket.create_connection(("127.0.0.1", proxy_port), timeout=4)
        sock.settimeout(6.0)
        request = ("CONNECT %s:%d HTTP/1.1\r\nHost: %s:%d\r\n\r\n"
                   % (target_host, target_port, target_host, target_port))
        sock.sendall(request.encode("iso-8859-1"))
        head = sock.recv(4096)
        text = head.decode("iso-8859-1", "replace")
        if "200 Connection Established" not in text:
            return {"ok": False, "status": text.split("\r\n")[0][:80]}
        sock.sendall(payload)
        echoed = sock.recv(4096)
        return {"ok": echoed == payload, "status": "200",
                "echo": echoed.decode("iso-8859-1", "replace")[:40]}
    except Exception as exc:
        return {"ok": False, "status": "%s: %s" % (type(exc).__name__, exc)}
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass


def run_selftest(base_dir: Optional[Path] = None, quick: bool = False) -> Tuple[int, Dict[str, Any]]:
    started = time.time()
    report: Dict[str, Any] = {
        "app": APP_NAME, "app_version": APP_VER, "cache_ver": CACHE_VER,
        "frozen": bool(getattr(sys, "frozen", False)),
        "os": __import__("platform").platform(),
        "python": sys.version.split()[0],
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "checks": [], "errors": [], "reverse_assertions": {},
    }
    try:
        import tkinter  # noqa: F401
        report["tkinter"] = True
    except Exception:
        report["tkinter"] = False

    checks: List[Dict[str, Any]] = []

    def check(name: str, fn: Callable[[], Dict[str, Any]], critical: bool = True) -> Dict[str, Any]:
        t0 = time.time()
        entry: Dict[str, Any] = {"name": name, "critical": critical}
        try:
            detail = fn() or {}
            entry.update(detail)
            entry["pass"] = bool(detail.get("pass", True))
        except Exception as exc:
            entry.update({"pass": False, "error": "%s: %s" % (type(exc).__name__, exc),
                          "trace": traceback.format_exc()[-800:]})
            report["errors"].append("%s: %s" % (name, exc))
        entry["ms"] = int((time.time() - t0) * 1000)
        checks.append(entry)
        return entry

    tmp_root = Path(tempfile.mkdtemp(prefix="ghacc_selftest_"))
    os.environ.setdefault("GHACC_CONFIG_DIR", str(tmp_root / "config"))
    os.environ.setdefault("GHACC_STATE_DIR", str(tmp_root / "state"))

    # ---------------------------------------------------------------
    # 1. 配置校验
    # ---------------------------------------------------------------
    def _cfg_check():
        cfg = Config.load(base_dir)
        problems = cfg.validate()
        return {"pass": not problems, "problems": problems,
                "domains": len(cfg.enabled_rules()),
                "pools": cfg.pool_summary() if hasattr(cfg, "pool_summary") else
                {k: len(v) for k, v in cfg.rules.get("ip_pools", {}).items()},
                "loaded_dir": str(cfg.loaded_dir or "(builtin)"),
                "writable_dir": str(cfg.writable_dir or "(memory)")}

    check("config_validate", _cfg_check)

    # ---------------------------------------------------------------
    # 2. 回环代理：正向断言（加速路径确实连到优选 IP）
    #    test.local 无法 DNS 解析，只有走 127.0.0.1 的优选 IP 才能成功
    # ---------------------------------------------------------------
    echo = _EchoServer()
    proxy_port = _free_port()
    temp_cfg = Config(
        settings=dict(Config.load(base_dir).settings, **{
            "listen_host": "127.0.0.1", "listen_port": proxy_port,
            "probe_tls_verify": False, "probe_timeout_ms": 400,
            "probe_workers": 2, "connect_timeout_s": 2.0,
            "set_system_proxy": False, "mode": "proxy", "log_to_file": False,
        }),
        rules={"version": 1,
               "domains": [{"host": "test.local", "enabled": True, "pool": "loopback"}],
               "ip_pools": {"loopback": ["127.0.0.1"]}, "fallback": "direct"},
        base_dir=base_dir or project_base_dir(),
    )
    temp_resolver = Resolver(temp_cfg)
    server = ProxyServer(temp_cfg, temp_resolver)
    server_started = False
    try:
        ok, msg = server.start()
        server_started = ok
        report["proxy_start"] = {"ok": ok, "message": msg}

        def _accel_path():
            result = _probe_via_proxy(proxy_port, "test.local", echo.port)
            return {"pass": result.get("ok") is True,
                    "detail": result,
                    "note": "test.local 无 DNS 记录，成功即证明走的是优选 IP 127.0.0.1"}

        check("accel_path_uses_preferred_ip", _accel_path)

        def _direct_path():
            result = _probe_via_proxy(proxy_port, "127.0.0.1", echo.port, b"DIRECT")
            return {"pass": result.get("ok") is True,
                    "detail": result, "note": "非白名单域名原样直连，不劫持"}

        check("non_whitelist_direct_passthrough", _direct_path)

        # R1：优选全部失败 -> 回退系统 DNS，代理不崩溃、给出 502
        def _r1():
            temp_resolver.set_force_fail(True)
            t0 = time.time()
            result = _probe_via_proxy(proxy_port, "test.local", echo.port)
            elapsed_ms = int((time.time() - t0) * 1000)
            alive = server.running
            temp_resolver.set_force_fail(False)
            # 防回归断言：回退直连必须带 DNS 硬超时，不能让 getaddrinfo 拖到客户端先超时
            passed = (not result.get("ok")) and alive \
                     and "502" in str(result.get("status", "")) and elapsed_ms < 4000
            report["reverse_assertions"]["R1_dns_bounded"] = passed
            return {"pass": passed, "detail": result, "proxy_alive": alive,
                    "elapsed_ms": elapsed_ms, "bound_ms": 4000}

        check("R1_all_candidates_fail_fallback", _r1)

        # R4：上游不可达 -> 502，主程序存活
        def _r4():
            result = _probe_via_proxy(proxy_port, "127.0.0.1", 1)
            alive = server.running
            failed = server.counters.as_dict()["failed"]
            return {"pass": alive and (not result.get("ok")) and failed >= 2,
                    "detail": result, "proxy_alive": alive, "failed_count": failed}

        check("R4_upstream_failure_does_not_crash", _r4)

        report["proxy_counters"] = server.counters.as_dict()
    finally:
        if server_started:
            server.stop()
        echo.close()

    # ---------------------------------------------------------------
    # 3. R2：系统代理不可写 -> DEGRADED 而非崩溃，本地代理仍服务
    #    R5：开启->关闭后系统代理状态与快照一致（可回退）
    # ---------------------------------------------------------------
    from .app import Accelerator

    def _r2_r5(fail_apply: bool, label: str):
        mocked = _MockBackend(fail_apply=fail_apply)
        cfg2 = Config(
            settings=dict(Config.load(base_dir).settings, **{
                "listen_port": _free_port(), "mode": "proxy",
                "set_system_proxy": True, "log_to_file": False,
                "probe_timeout_ms": 200, "refresh_interval_s": 0,
            }),
            rules={"version": 1,
                   "domains": [{"host": "github.com", "enabled": True, "pool": "p"}],
                   "ip_pools": {"p": ["127.0.0.1"]}, "fallback": "direct"},
            base_dir=base_dir or project_base_dir(),
        )
        resolver2 = Resolver(cfg2)
        resolver2.set_force_fail(True)       # 避免 selftest 触碰外网
        before = mocked.read().as_dict()
        acc = Accelerator(cfg=cfg2, backend=mocked, resolver=resolver2)
        start = acc.start()
        proxy_alive = acc.proxy.running
        stop = acc.stop()
        after = mocked.read().as_dict()
        return {"start": start, "stop": stop, "before": before, "after": after,
                "proxy_alive": proxy_alive, "calls": mocked.calls, "label": label}

    def _r2():
        data = _r2_r5(True, "readonly-sysproxy")
        expected_state = "DEGRADED"
        passed = (data["start"]["state"] == expected_state and data["proxy_alive"]
                  and data["stop"]["state"] == "STOPPED")
        report["reverse_assertions"]["R2"] = passed
        return {"pass": passed, "state_on_failure": data["start"]["state"],
                "proxy_alive": data["proxy_alive"],
                "reasons": data["start"].get("degraded_reasons", []),
                "stopped_state": data["stop"]["state"]}

    check("R2_sysproxy_readonly_degrades", _r2)

    def _r5():
        data = _r2_r5(False, "normal")
        restored = data["before"] == data["after"]
        has_restore = any(c[0] == "restore" for c in data["calls"])
        state_cleared = data["stop"]["state"] == "STOPPED"
        passed = restored and has_restore and state_cleared
        report["reverse_assertions"]["R5"] = passed
        return {"pass": passed, "snapshot_restored": restored,
                "restore_called": has_restore, "before": data["before"],
                "after": data["after"]}

    check("R5_start_stop_restores_snapshot", _r5)

    # ---------------------------------------------------------------
    # 4. R3：日志目录不可写 -> 纯内存模式，不抛异常
    # ---------------------------------------------------------------
    def _r3():
        import ghacc.logger as logger_mod
        original = logger_mod.is_writable_dir
        logger_mod.is_writable_dir = lambda _p: False
        try:
            cfg3 = Config.load(base_dir)
            cfg3.settings["log_to_file"] = True
            _, bus, info = setup_logging(cfg3, base_dir)
            bus.emit(__import__("logging").makeLogRecord(
                {"name": "selftest", "levelno": 20, "levelname": "INFO",
                 "msg": "memory-only probe", "created": time.time()}))
            passed = info["mode"] == "memory" and bus.seq > 0
            detail = {"mode": info["mode"], "bus_seq": bus.seq}
        finally:
            logger_mod.is_writable_dir = original
            setup_logging(Config.load(base_dir), base_dir)
        report["reverse_assertions"]["R3"] = passed
        return {"pass": passed, "detail": detail}

    check("R3_log_dir_readonly_memory_fallback", _r3)

    # ---------------------------------------------------------------
    # 5. hosts 管理模式：写入 -> 精确回滚，且不丢用户原有内容
    # ---------------------------------------------------------------
    def _hosts():
        from .hosts_mode import BEGIN, HostsManager
        cfg4 = Config.load(base_dir)
        hm = HostsManager(cfg4)
        fake = tmp_root / "hosts"
        fake.write_text("# user config\n127.0.0.1 localhost\n", encoding="utf-8")
        hm.path = fake
        ok1, msg1 = hm.apply({"github.com": "1.2.3.4"})
        written = fake.read_text(encoding="utf-8")
        ok2, msg2 = hm.restore()
        restored = fake.read_text(encoding="utf-8")
        passed = (ok1 and ok2 and BEGIN in written and BEGIN not in restored
                  and "127.0.0.1 localhost" in restored)
        return {"pass": passed, "apply": msg1, "restore": msg2,
                "user_content_preserved": "127.0.0.1 localhost" in restored}

    check("hosts_managed_block_rollback", _hosts)

    # ---------------------------------------------------------------
    # 6. 系统代理后端可用性（真实读取，不写入）
    # ---------------------------------------------------------------
    def _backend():
        be = sysproxy.get_backend()
        snap = be.read()
        return {"pass": True, "backend": be.name,
                "current_enabled": snap.enabled,
                "hint": be.hint()}

    check("sysproxy_backend_read", _backend, critical=False)

    # ---------------------------------------------------------------
    report["checks"] = checks
    report["elapsed_ms"] = int((time.time() - started) * 1000)
    critical_failed = [c for c in checks if not c.get("pass") and c.get("critical")]
    optional_failed = [c for c in checks if not c.get("pass") and not c.get("critical")]
    report["summary"] = {
        "total": len(checks),
        "passed": sum(1 for c in checks if c.get("pass")),
        "failed": len(critical_failed) + len(optional_failed),
        "critical_failed": [c["name"] for c in critical_failed],
        "reverse_assertions": report["reverse_assertions"],
    }
    if critical_failed:
        code = 1
    elif optional_failed:
        code = 2
    else:
        code = 0
    report["exit_code"] = code

    # 证据落盘：优先用户目录，失败则临时目录，再失败只打 stdout
    for directory in (Path.home() / ".ghacc" / "logs",
                      (base_dir or project_base_dir()) / "logs", tmp_root):
        try:
            if is_writable_dir(directory):
                target = directory / ("selftest_%s.json" % time.strftime("%Y%m%d_%H%M%S"))
                target.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
                report["evidence_file"] = str(target)
                break
        except Exception:
            continue
    return code, report


def print_summary(report: Dict[str, Any]) -> None:
    print("=" * 68)
    print(" %s v%s  --selftest   %s" % (APP_NAME, APP_VER, report["timestamp"]))
    print("=" * 68)
    print(" OS      : %s" % report["os"])
    print(" Python  : %s   frozen=%s   tkinter=%s" %
          (report["python"], report["frozen"], report["tkinter"]))
    print(" %-46s %s" % ("CHECK", "RESULT"))
    print(" " + "-" * 66)
    for item in report["checks"]:
        flag = "PASS" if item.get("pass") else ("WARN" if not item.get("critical") else "FAIL")
        detail = ""
        if not item.get("pass"):
            detail = str(item.get("error") or item.get("problems") or
                         item.get("detail") or "")[:60]
        print(" %-46s %-5s %5dms %s" % (item["name"], flag, item["ms"], detail))
    summary = report["summary"]
    print(" " + "-" * 66)
    print(" total=%d passed=%d failed=%d elapsed=%dms" %
          (summary["total"], summary["passed"], summary["failed"], report["elapsed_ms"]))
    print(" reverse_assertions: %s" % summary["reverse_assertions"])
    if report.get("evidence_file"):
        print(" evidence: %s" % report["evidence_file"])
    print(" exit_code=%d (%s)" % (report["exit_code"],
                                  "全部通过" if report["exit_code"] == 0 else
                                  "存在降级项" if report["exit_code"] == 2 else "关键项失败"))
    print("=" * 68)
