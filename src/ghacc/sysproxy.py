# -*- coding: utf-8 -*-
"""系统代理设置 / 精确恢复 / 异常退出残留自愈。

参考产品（Watt Toolkit / DevSidecar）最高频的现场故障是：程序异常退出后
系统代理未被还原，导致整机断网。本模块的对策：
  1. 开启前快照原状态；
  2. 关闭时按快照精确恢复（不是简单「关掉」）；
  3. 启动时检测上次残留：只有当当前代理地址仍等于本程序写入的值才恢复，
     否则不触碰（避免误伤用户或其他代理软件）；
  4. 提供 --restore 应急入口。

Windows 使用 HKCU（当前用户），因此默认路径不需要管理员权限。
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import Config, is_writable_dir
from .logger import get_logger

_LOG = get_logger("sysproxy")
STATE_FILE = "sysproxy_state.json"


class ProxySnapshot(object):
    """跨平台的系统代理快照。"""

    def __init__(self, system: str = "", enabled: bool = False,
                 server: str = "", port: str = "", bypass: str = "",
                 extra: Optional[Dict[str, Any]] = None):
        self.system = system or platform.system()
        self.enabled = bool(enabled)
        self.server = server or ""
        self.port = str(port or "")
        self.bypass = bypass or ""
        self.extra = extra or {}

    def as_dict(self) -> Dict[str, Any]:
        return {"system": self.system, "enabled": self.enabled,
                "server": self.server, "port": self.port,
                "bypass": self.bypass, "extra": self.extra}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProxySnapshot":
        return cls(data.get("system", ""), data.get("enabled", False),
                   data.get("server", ""), data.get("port", ""),
                   data.get("bypass", ""), data.get("extra", {}))


class _Backend(object):
    name = "base"

    def read(self) -> ProxySnapshot:
        raise NotImplementedError

    def apply(self, host: str, port: int, bypass: str) -> Tuple[bool, str]:
        raise NotImplementedError

    def restore(self, snap: ProxySnapshot) -> Tuple[bool, str]:
        raise NotImplementedError

    def hint(self) -> str:
        return ""


class _WindowsBackend(_Backend):
    name = "windows-hkcu"
    KEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"

    def __init__(self):
        import winreg  # noqa: F401  (仅 Windows 可用)

    @staticmethod
    def _get(key, name, default):
        try:
            value, _ = __import__("winreg").QueryValueEx(key, name)
            return value
        except Exception:
            return default

    def read(self) -> ProxySnapshot:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.KEY, 0,
                                winreg.KEY_READ) as key:
                enabled = int(self._get(key, "ProxyEnable", 0)) != 0
                server = str(self._get(key, "ProxyServer", ""))
                override = str(self._get(key, "ProxyOverride", ""))
            host, _, port = server.rpartition(":")
            return ProxySnapshot("Windows", enabled,
                                 host if port.isdigit() else server,
                                 port if port.isdigit() else "", override,
                                 {"raw_server": server})
        except Exception as exc:
            _LOG.warning("读取系统代理失败: %s", exc)
            return ProxySnapshot("Windows")

    def _write(self, enabled: bool, server: str, override: str) -> Tuple[bool, str]:
        import winreg
        try:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, self.KEY) as key:
                winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD,
                                  1 if enabled else 0)
                winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, server)
                winreg.SetValueEx(key, "ProxyOverride", 0, winreg.REG_SZ, override)
            self._refresh()
            return True, "HKCU 已更新"
        except Exception as exc:
            return False, "%s: %s" % (type(exc).__name__, exc)

    @staticmethod
    def _refresh() -> None:
        """通知 WinINet 重载设置；失败不影响注册表写入（部分环境无 wininet）。"""
        try:
            import ctypes
            wininet = ctypes.windll.wininet
            wininet.InternetSetOptionW(0, 39, None, 0)  # SETTINGS_CHANGED
            wininet.InternetSetOptionW(0, 42, None, 0)  # REFRESH
        except Exception as exc:
            _LOG.debug("WinINet 刷新跳过: %s", exc)

    def apply(self, host, port, bypass):
        return self._write(True, "%s:%d" % (host, port), bypass)

    def restore(self, snap):
        raw = snap.extra.get("raw_server") or (
            "%s:%s" % (snap.server, snap.port) if snap.server and snap.port else snap.server)
        return self._write(snap.enabled, raw, snap.bypass)

    def hint(self):
        return "Windows：写入 HKCU\\...\\Internet Settings，免管理员权限"


class _MacBackend(_Backend):
    name = "macos-networksetup"

    @staticmethod
    def _run(args: List[str]) -> str:
        out = subprocess.check_output(["networksetup"] + args,
                                      stderr=subprocess.STDOUT, timeout=8)
        return out.decode("utf-8", "replace")

    @staticmethod
    def _services() -> List[str]:
        try:
            lines = _MacBackend._run(["-listallnetworkservices"]).splitlines()[1:]
            return [ln.strip() for ln in lines
                    if ln.strip() and not ln.strip().startswith("*")]
        except Exception as exc:
            _LOG.warning("枚举网络服务失败: %s", exc)
            return []

    @staticmethod
    def _parse(out: str) -> Dict[str, str]:
        result: Dict[str, str] = {}
        for line in out.splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                result[key.strip().lower()] = value.strip()
        return result

    def read(self) -> ProxySnapshot:
        services = self._services()
        for svc in services:
            try:
                info = self._parse(self._run(["-getwebproxy", svc]))
                enabled = info.get("enabled", "no").lower() in ("yes", "on", "1")
                return ProxySnapshot("Darwin", enabled,
                                     info.get("server", ""), info.get("port", ""),
                                     "", {"services": services, "service": svc})
            except Exception:
                continue
        return ProxySnapshot("Darwin", extra={"services": services})

    def apply(self, host, port, bypass):
        services = self._services()
        if not services:
            return False, "无法枚举网络服务（networksetup 不可用）"
        ok_count = 0
        for svc in services:
            for kind in ("web", "secureweb"):
                try:
                    self._run(["-set%sproxy" % kind, svc, host, str(port)])
                    self._run(["-set%sproxystate" % kind, svc, "on"])
                    ok_count += 1
                except Exception as exc:
                    _LOG.warning("设置 %s/%s 失败: %s", svc, kind, exc)
        return (ok_count > 0, "已设置 %d 项（共 %d 服务）" %
                (ok_count, len(services) * 2))

    def restore(self, snap):
        services = snap.extra.get("services") or self._services()
        if not snap.enabled:
            for svc in services:
                for kind in ("web", "secureweb"):
                    try:
                        self._run(["-set%sproxystate" % kind, svc, "off"])
                    except Exception:
                        pass
            return True, "已关闭代理"
        ok = False
        for svc in services:
            for kind in ("web", "secureweb"):
                try:
                    self._run(["-set%sproxy" % kind, svc, snap.server, snap.port])
                    self._run(["-set%sproxystate" % kind, svc, "on"])
                    ok = True
                except Exception:
                    pass
        return ok, "已恢复原代理" if ok else "恢复失败，请手动检查系统代理"

    def hint(self):
        return "macOS：networksetup 可能需要用户授权；未签名的包会触发 Gatekeeper"


class _LinuxBackend(_Backend):
    name = "linux-gsettings"
    _BASE = "org.gnome.system.proxy"

    @staticmethod
    def _run(args: List[str]) -> str:
        out = subprocess.check_output(["gsettings"] + args,
                                      stderr=subprocess.STDOUT, timeout=8)
        return out.decode("utf-8", "replace").strip()

    @staticmethod
    def _get(schema: str, key: str) -> str:
        return _LinuxBackend._run(["get", schema, key]).strip("'")

    @staticmethod
    def _set(schema: str, key: str, value: str) -> None:
        _LinuxBackend._run(["set", schema, key, value])

    def read(self) -> ProxySnapshot:
        try:
            mode = self._get(self._BASE, "mode")
            host = self._get(self._BASE + ".http", "host")
            port = self._get(self._BASE + ".http", "port")
            return ProxySnapshot("Linux", mode == "manual", host, port, "",
                                 {"mode": mode})
        except Exception as exc:
            return ProxySnapshot("Linux", extra={"error": str(exc)})

    def apply(self, host, port, bypass):
        try:
            for kind in ("http", "https"):
                self._set(self._BASE + "." + kind, "host", "'%s'" % host)
                self._set(self._BASE + "." + kind, "port", "'%d'" % port)
            self._set(self._BASE, "mode", "'manual'")
            return True, "GNOME 代理已设置"
        except Exception as exc:
            return False, "gsettings 不可用（非 GNOME 或缺少 dbus）: %s；请手动配置代理" % exc

    def restore(self, snap):
        try:
            mode = snap.extra.get("mode") or ("manual" if snap.enabled else "none")
            if snap.enabled and snap.server:
                for kind in ("http", "https"):
                    self._set(self._BASE + "." + kind, "host", "'%s'" % snap.server)
                    self._set(self._BASE + "." + kind, "port", "'%s'" % snap.port)
            self._set(self._BASE, "mode", "'%s'" % mode)
            return True, "已恢复 mode=%s" % mode
        except Exception as exc:
            return False, "恢复失败: %s" % exc

    def hint(self):
        return "Linux：仅自动支持 GNOME；KDE / 无桌面环境请用 --cli 并手动设置代理"


def get_backend() -> _Backend:
    system = platform.system()
    try:
        if system == "Windows":
            return _WindowsBackend()
        if system == "Darwin":
            return _MacBackend()
        return _LinuxBackend()
    except Exception as exc:
        _LOG.warning("后端 %s 初始化失败，降级为空后端: %s", system, exc)
        return _Backend()


def state_dir(cfg: Optional[Config] = None) -> Optional[Path]:
    cfg = cfg or Config.load()
    override = os.environ.get("GHACC_STATE_DIR")
    candidates = [Path(override)] if override else []
    candidates += [Path.home() / ".ghacc", cfg.base_dir / "logs"]
    for candidate in candidates:
        if is_writable_dir(candidate):
            return candidate
    return None


def state_path(cfg: Optional[Config] = None) -> Optional[Path]:
    directory = state_dir(cfg)
    return directory / STATE_FILE if directory else None


def read_snapshot(backend: Optional[_Backend] = None) -> ProxySnapshot:
    try:
        return (backend or get_backend()).read()
    except Exception as exc:
        _LOG.warning("读取系统代理快照失败: %s", exc)
        return ProxySnapshot()


def save_state(cfg: Config, previous: ProxySnapshot, applied_server: str,
               backend_name: str) -> bool:
    """把开启前的快照落盘，供异常退出后自愈。写入失败不抛异常。"""
    path = state_path(cfg)
    if path is None:
        _LOG.warning("无可用状态目录，异常退出后无法自动自愈（可用 --restore 手动恢复）")
        return False
    payload = {"pid": os.getpid(), "ts": time.time(), "app_ver": "1.0.0",
               "backend": backend_name, "applied_server": applied_server,
               "previous": previous.as_dict()}
    try:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(str(tmp), str(path))
        return True
    except Exception as exc:
        _LOG.warning("状态文件写入失败: %s", exc)
        return False


def clear_state(cfg: Config) -> None:
    path = state_path(cfg)
    if path and path.is_file():
        try:
            path.unlink()
        except Exception as exc:
            _LOG.warning("状态文件删除失败: %s", exc)


def recover_if_needed(cfg: Config, backend: Optional[_Backend] = None) -> Dict[str, Any]:
    """启动自愈：仅当当前代理地址仍等于本程序写入的值时，按快照恢复。"""
    path = state_path(cfg)
    result: Dict[str, Any] = {"checked": False, "recovered": False, "reason": "no-state"}
    if not path or not path.is_file():
        return result
    result["checked"] = True
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        previous = ProxySnapshot.from_dict(payload.get("previous", {}))
        applied = str(payload.get("applied_server", ""))
        be = backend or get_backend()
        current = be.read()
        current_server = ("%s:%s" % (current.server, current.port)
                          if current.server and current.port else current.server)
        if current.enabled and applied and current_server == applied:
            ok, msg = be.restore(previous)
            result.update({"recovered": ok, "reason": msg,
                           "previous_pid": payload.get("pid")})
            _LOG.warning("检测到上次异常退出残留的系统代理 %s -> 已恢复 (%s)",
                         applied, msg)
        else:
            result["reason"] = "当前系统代理非本程序写入（已被用户/其他软件修改），不触碰"
            _LOG.info("状态文件存在但系统代理已变化，跳过恢复：%s", result["reason"])
    except Exception as exc:
        result["reason"] = "%s: %s" % (type(exc).__name__, exc)
        _LOG.warning("自愈处理异常: %s", exc)
    finally:
        clear_state(cfg)
    return result


def restore_now(cfg: Config) -> Dict[str, Any]:
    """--restore 应急入口：按最近一次快照强制恢复系统代理。"""
    path = state_path(cfg)
    if not path or not path.is_file():
        return {"ok": False, "message": "无残留状态文件，系统代理无需恢复"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        be = get_backend()
        ok, msg = be.restore(ProxySnapshot.from_dict(payload.get("previous", {})))
        clear_state(cfg)
        return {"ok": ok, "message": msg,
                "previous": payload.get("previous", {})}
    except Exception as exc:
        return {"ok": False, "message": "%s: %s" % (type(exc).__name__, exc)}


def backend_hint() -> str:
    return get_backend().hint()
