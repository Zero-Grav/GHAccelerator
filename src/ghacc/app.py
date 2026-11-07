# -*- coding: utf-8 -*-
"""应用编排：一键开启/关闭状态机、降级决策、事件广播。

生命周期顺序（不可颠倒）：
  start: 配置校验 -> 残留自愈 -> 优选预热 -> 本地代理 -> 系统代理(快照) -> hosts
  stop : 恢复系统代理 -> 恢复 hosts -> 停止本地代理 -> 清理状态文件
每一步独立 try：任何一步失败都记录并继续后续清理，最后统一回到 STOPPED。
这是针对参考产品「异常退出后系统代理未还原导致整机断网」的结构性修复。
"""
from __future__ import annotations

import atexit
import concurrent.futures
import platform
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from .config import Config
from .hosts_mode import HostsManager
from .ip_pool import IPPool
from .logger import get_bus, get_logger, log_exception, log_info, setup_logging
from .proxy_server import ProxyServer
from .resolver import Resolver
from . import sysproxy
from .version import APP_NAME, APP_VER, CACHE_VER

_LOG = get_logger("app")

STOPPED = "STOPPED"
STARTING = "STARTING"
RUNNING = "RUNNING"
DEGRADED = "DEGRADED"
STOPPING = "STOPPING"
ERROR = "ERROR"

_CORE_PREHEAT = ("github.com", "raw.githubusercontent.com")


class Accelerator(object):
    """单实例应用对象。GUI / CLI / selftest 共用同一套启停逻辑。"""

    def __init__(self, cfg: Optional[Config] = None, base_dir=None,
                 backend=None, resolver: Optional[Resolver] = None,
                 pool: Optional[IPPool] = None):
        self.cfg = cfg or Config.load(base_dir)
        self.base_dir = self.cfg.base_dir
        self.bus = get_bus()
        setup_logging(self.cfg, self.base_dir)
        self.pool = pool or IPPool(self.cfg)
        self.resolver = resolver or Resolver(self.cfg, self.pool)
        self.proxy = ProxyServer(self.cfg, self.resolver, on_event=self._emit)
        self.hosts = HostsManager(self.cfg)
        self.backend = backend or sysproxy.get_backend()
        self._lock = threading.RLock()
        self._state = STOPPED
        self._subscribers: List[Callable[[str, Dict[str, Any]], None]] = []
        self._degraded: List[str] = []
        self._snapshot: Optional[sysproxy.ProxySnapshot] = None
        self._applied_server = ""
        self._last_recovery: Dict[str, Any] = {}
        self._last_start: Dict[str, Any] = {}
        self._stop_event = threading.Event()
        self._refresh_thread: Optional[threading.Thread] = None
        self._hooks_installed = False

    # ------------------------------------------------------------------
    # 事件 / 状态
    # ------------------------------------------------------------------
    def subscribe(self, callback: Callable[[str, Dict[str, Any]], None]) -> None:
        with self._lock:
            self._subscribers.append(callback)

    def _emit(self, kind: str, payload: Optional[Dict[str, Any]] = None) -> None:
        data = payload or {}
        with self._lock:
            listeners = list(self._subscribers)
        for cb in listeners:
            try:
                cb(kind, data)
            except Exception:
                pass

    def _set_state(self, state: str, reasons: Optional[List[str]] = None) -> None:
        with self._lock:
            self._state = state
            self._degraded = list(reasons or [])
        _LOG.info("state -> %s%s", state,
                  (" reasons=" + "; ".join(self._degraded)) if self._degraded else "")
        self._emit("state", {"state": state, "reasons": list(self._degraded)})

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    # ------------------------------------------------------------------
    def install_shutdown_hooks(self) -> None:
        """atexit + 信号：最大程度避免异常退出留下系统代理。"""
        if self._hooks_installed:
            return
        self._hooks_installed = True
        atexit.register(self._atexit_stop)
        try:
            import signal

            def _on_signal(signum, frame):
                _LOG.warning("收到信号 %s，执行清理后退出", signum)
                try:
                    self.stop()
                finally:
                    sys.exit(0)

            for sig_name in ("SIGINT", "SIGTERM"):
                sig = getattr(signal, sig_name, None)
                if sig is not None:
                    try:
                        signal.signal(sig, _on_signal)
                    except (ValueError, OSError):
                        pass  # 非主线程注册会失败，忽略
        except Exception as exc:
            _LOG.debug("信号钩子跳过: %s", exc)

    def _atexit_stop(self) -> None:
        try:
            if self.state in (RUNNING, DEGRADED, STARTING, STOPPING):
                _LOG.warning("进程退出前自动执行 stop() 清理系统代理")
                self.stop()
        except Exception as exc:
            _LOG.error("退出清理失败: %s", exc)

    # ------------------------------------------------------------------
    def _preheat(self) -> Dict[str, Any]:
        hosts = [h for h in _CORE_PREHEAT if self.cfg.is_target(h)]
        started = time.time()
        if not hosts:
            return {"ok": 0, "failed": 0, "elapsed_ms": 0}
        ok = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(hosts)) as executor:
            futures = {executor.submit(self.resolver.select, h, 443): h for h in hosts}
            try:
                for future in concurrent.futures.as_completed(futures, timeout=15):
                    try:
                        if future.result() is not None:
                            ok += 1
                    except Exception as exc:
                        _LOG.warning("预热异常 %s", exc)
            except concurrent.futures.TimeoutError:
                _LOG.warning("优选预热整体超时，其余域名改为按需探测")
        return {"ok": ok, "failed": len(hosts) - ok,
                "elapsed_ms": int((time.time() - started) * 1000)}

    # ------------------------------------------------------------------
    def start(self) -> Dict[str, Any]:
        """一键开启。返回结构化结果，绝不把异常抛给 UI 线程。"""
        with self._lock:
            if self._state in (RUNNING, DEGRADED, STARTING):
                return {"ok": True, "state": self._state,
                        "message": "加速已在运行", "already": True}
            self._set_state(STARTING)

        details: Dict[str, Any] = {}
        degraded: List[str] = []
        try:
            problems = self.cfg.validate()
            if problems:
                raise RuntimeError("配置校验失败：" + "；".join(problems[:4]))
            details["config"] = {"loaded_dir": str(self.cfg.loaded_dir or "(builtin)"),
                                 "writable_dir": str(self.cfg.writable_dir or "(memory)"),
                                 "issues": self.cfg.load_issues}

            # 1) 残留自愈：先清掉上次异常退出遗留的系统代理 / hosts 块
            recovery = sysproxy.recover_if_needed(self.cfg, self.backend)
            mode_now = str(self.cfg.settings.get("mode", "proxy"))
            hosts_recovery = (self.hosts.recover_stale()
                              if mode_now in ("hosts", "both")
                              else {"recovered": False,
                                    "reason": "mode=proxy, hosts 文件未被触碰"})
            self._last_recovery = {"sysproxy": recovery, "hosts": hosts_recovery}
            details["recovery"] = self._last_recovery
            if recovery.get("recovered") or hosts_recovery.get("recovered"):
                _LOG.warning("已清理上次异常退出残留")

            # 2) 优选预热（核心域名），其余按需懒加载
            details["preheat"] = self._preheat()

            mode = str(self.cfg.settings.get("mode", "proxy"))
            host, port = self.proxy.address()
            applied = "%s:%d" % (host, port)

            # 3) 本地代理
            if mode in ("proxy", "both"):
                ok, msg = self.proxy.start()
                details["proxy"] = {"ok": ok, "message": msg}
                if not ok:
                    raise RuntimeError(msg)

            # 4) 系统代理（先快照，再写入，最后落盘状态文件）
            if mode in ("proxy", "both") and bool(self.cfg.settings.get("set_system_proxy", True)):
                snapshot = self.backend.read()
                ok, msg = self.backend.apply(host, port,
                                             str(self.cfg.settings.get("system_proxy_bypass", "")))
                if ok:
                    self._snapshot = snapshot
                    self._applied_server = applied
                    saved = sysproxy.save_state(self.cfg, snapshot, applied, self.backend.name)
                    details["sysproxy"] = {"ok": True, "message": msg,
                                           "state_saved": saved,
                                           "previous": snapshot.as_dict()}
                    if not saved:
                        degraded.append("状态文件不可写：异常退出后无法自动自愈，请用 --restore")
                else:
                    details["sysproxy"] = {"ok": False, "message": msg}
                    degraded.append("系统代理设置失败（%s）；本地代理仍可用，请手动把浏览器/代理设为 %s"
                                    % (msg, applied))
                    _LOG.warning("系统代理设置失败 -> 降级运行: %s", msg)

            # 5) 可选 hosts 模式
            if mode in ("hosts", "both"):
                mapping = {d["host"]: d["ip"] for d in self.resolver.decisions()}
                ok, msg = self.hosts.apply(mapping)
                details["hosts"] = {"ok": ok, "message": msg, "entries": len(mapping)}
                if not ok:
                    degraded.append("hosts 模式失败：%s" % msg)

            # 6) 后台定时重测（可选）
            interval = float(self.cfg.settings.get("refresh_interval_s", 0))
            if interval > 0:
                self._start_refresh_loop(interval)

            final_state = DEGRADED if degraded else RUNNING
            self._set_state(final_state, degraded)
            result = {"ok": True, "state": final_state,
                      "message": ("加速已开启（降级运行）" if degraded else "加速已开启"),
                      "degraded_reasons": list(degraded), "details": details}
            self._last_start = result
            self._emit("started", result)
            return result
        except Exception as exc:
            log_exception(_LOG, "start", exc)
            # 启动中途失败：把所有已生效的副作用全部撤回
            cleanup = self._cleanup_all()
            self._set_state(ERROR, [str(exc)])
            return {"ok": False, "state": ERROR, "message": "开启失败：%s" % exc,
                    "details": details, "cleanup": cleanup}

    # ------------------------------------------------------------------
    def _cleanup_all(self) -> Dict[str, Any]:
        """集中撤回所有系统级副作用；每一步失败都不阻断后续步骤。"""
        out: Dict[str, Any] = {}
        mode = str(self.cfg.settings.get("mode", "proxy"))
        if self._snapshot is not None:
            try:
                ok, msg = self.backend.restore(self._snapshot)
                out["sysproxy"] = {"ok": ok, "message": msg}
                if not ok:
                    _LOG.error("系统代理恢复失败: %s（可执行 --restore 重试）", msg)
            except Exception as exc:
                out["sysproxy"] = {"ok": False, "message": "%s: %s" % (type(exc).__name__, exc)}
            sysproxy.clear_state(self.cfg)
            self._snapshot = None
            self._applied_server = ""
        if mode in ("hosts", "both"):
            try:
                ok, msg = self.hosts.restore()
                out["hosts"] = {"ok": ok, "message": msg}
            except Exception as exc:
                out["hosts"] = {"ok": False, "message": str(exc)}
        try:
            self.proxy.stop()
            out["proxy"] = {"ok": True}
        except Exception as exc:
            out["proxy"] = {"ok": False, "message": str(exc)}
        return out

    def stop(self) -> Dict[str, Any]:
        """一键关闭。严格逆序恢复，finally 保证状态归位。"""
        with self._lock:
            if self._state == STOPPED:
                return {"ok": True, "state": STOPPED, "message": "加速已停止", "already": True}
            self._set_state(STOPPING)
        self._stop_event.set()
        cleanup: Dict[str, Any] = {}
        try:
            cleanup = self._cleanup_all()
        except Exception as exc:
            log_exception(_LOG, "stop", exc)
            cleanup["fatal"] = "%s: %s" % (type(exc).__name__, exc)
        finally:
            if self._refresh_thread is not None:
                self._refresh_thread.join(timeout=2.0)
                self._refresh_thread = None
            self._stop_event.clear()
            self._set_state(STOPPED)
        result = {"ok": True, "state": STOPPED, "message": "加速已关闭，系统网络设置已恢复",
                  "cleanup": cleanup}
        self._emit("stopped", result)
        return result

    # ------------------------------------------------------------------
    def _start_refresh_loop(self, interval: float) -> None:
        self._stop_event.clear()

        def _loop():
            while not self._stop_event.wait(interval):
                try:
                    result = self.resolver.refresh()
                    _LOG.info("定时重测完成 ok=%d failed=%d",
                              result["ok"], result["failed"])
                    self._emit("refreshed", result)
                except Exception as exc:
                    _LOG.warning("定时重测异常 %s", exc)

        self._refresh_thread = threading.Thread(target=_loop, name="ghacc-refresh",
                                                daemon=True)
        self._refresh_thread.start()

    def refresh_ips(self) -> Dict[str, Any]:
        """手动刷新官方 IP 池 + 使决策缓存失效。"""
        result = self.pool.refresh_from_meta()
        if result.get("ok"):
            saved, where = self.cfg.save()
            result["saved"] = saved
            result["saved_to"] = where
            self.resolver.invalidate()
        self._emit("ips-refreshed", result)
        return result

    def refresh_probe(self) -> Dict[str, Any]:
        result = self.resolver.refresh()
        self._emit("refreshed", result)
        return result

    # ------------------------------------------------------------------
    def status(self) -> Dict[str, Any]:
        with self._lock:
            state, degraded = self._state, list(self._degraded)
        return {
            "app": {"name": APP_NAME, "version": APP_VER, "cache_ver": CACHE_VER,
                    "python": sys.version.split()[0], "os": platform.platform(),
                    "frozen": bool(getattr(sys, "frozen", False)), "pid": __import__("os").getpid()},
            "state": state, "degraded_reasons": degraded,
            "mode": self.cfg.settings.get("mode"),
            "engine": "local-CONNECT-tunnel / no-MITM / no-root-CA",
            "listen": "%s:%d" % self.proxy.address(),
            "system_proxy": {"backend": self.backend.name,
                             "applied": self._applied_server,
                             "snapshot_saved": self._snapshot is not None,
                             "hint": sysproxy.backend_hint()},
            "proxy": self.proxy.status(),
            "resolver": self.resolver.stats(),
            "decisions": self.resolver.decisions(),
            "pools": self.pool.pool_summary(),
            "config": {"loaded_dir": str(self.cfg.loaded_dir or "(builtin)"),
                       "writable_dir": str(self.cfg.writable_dir or "(memory)"),
                       "issues": self.cfg.load_issues,
                       "domains": len(self.cfg.enabled_rules()),
                       "fallback": self.cfg.fallback},
            "log": log_info(),
            "recovery": self._last_recovery,
        }
