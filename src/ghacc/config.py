# -*- coding: utf-8 -*-
"""配置管理：加载、校验、保存、多级目录降级。

交付假设：
- 程序目录可能只读（U 盘 / 企业管控 / 只读共享），因此读不到外部配置时
  必须能回落到内置默认（纯内存）继续运行。
- 用户改动优先写用户目录，绝不覆盖交付包内文件。
- 外部输入一律校验；坏配置不允许让程序「半启动」。
"""
from __future__ import annotations

import copy
import ipaddress
import json
import os
import re
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 默认配置（内置副本，保证零外部文件也能启动）
# ---------------------------------------------------------------------------

DEFAULT_SETTINGS: Dict[str, Any] = {
    "listen_host": "127.0.0.1",
    "listen_port": 18792,
    "mode": "proxy",              # proxy | hosts | both
    "set_system_proxy": True,     # 是否自动设置系统代理（HKCU，免管理员）
    "probe_timeout_ms": 1200,     # 单 IP 探测超时
    "probe_workers": 12,          # 并发探测线程数
    "probe_tls_verify": True,     # 探测时做 TLS 证书校验，排除被劫持 IP
    "connect_timeout_s": 8.0,     # 代理连接上游超时
    "relay_timeout_s": 0.0,       # 双向转发空闲超时，0=不限
    "cache_ttl_s": 600,           # 优选结果缓存 TTL
    "refresh_interval_s": 0,      # 后台重测间隔，0=关闭
    "fallback": "direct",         # 白名单「优选失败」时：direct=回退系统DNS直连 | reject=返回502
    "log_level": "INFO",
    "log_to_file": True,
    "log_max_lines": 2000,
    "system_proxy_bypass": "localhost;127.*;10.*;192.168.*;<local>",
}

DEFAULT_RULES: Dict[str, Any] = {
    "version": 1,
    "note": "内置 IP 为 GitHub 官方公开网段的常用地址示例；失效项会被 TLS 校验自动淘汰，"
            "可通过 --refresh-ips 从 api.github.com/meta 更新。",
    "domains": [
        {"host": "github.com", "enabled": True, "pool": "github_web"},
        {"host": "www.github.com", "enabled": True, "pool": "github_web"},
        {"host": "api.github.com", "enabled": True, "pool": "github_api"},
        {"host": "gist.github.com", "enabled": True, "pool": "github_web"},
        {"host": "codeload.github.com", "enabled": True, "pool": "github_web"},
        {"host": "raw.githubusercontent.com", "enabled": True, "pool": "github_cdn"},
        {"host": "avatars.githubusercontent.com", "enabled": True, "pool": "github_cdn"},
        {"host": "*.githubusercontent.com", "enabled": True, "pool": "github_cdn"},
        {"host": "github.io", "enabled": True, "pool": "github_cdn"},
        {"host": "*.github.io", "enabled": True, "pool": "github_cdn"},
        {"host": "objects.githubusercontent.com", "enabled": False,
         "pool": "github_cdn", "note": "对象存储 IP 变化频繁，默认关闭"},
        {"host": "github-releases.githubusercontent.com", "enabled": False,
         "pool": "github_cdn", "note": "同上"},
    ],
    "ip_pools": {
        "github_web": [
            "140.82.112.3", "140.82.113.3", "140.82.114.3", "140.82.116.3",
            "140.82.121.3", "20.205.243.166", "20.27.177.113", "20.200.245.247",
        ],
        "github_api": [
            "140.82.112.5", "140.82.113.5", "140.82.114.5", "20.205.243.168",
        ],
        "github_cdn": [
            "185.199.108.133", "185.199.109.133", "185.199.110.133",
            "185.199.111.133", "185.199.108.153", "185.199.109.153",
        ],
    },
    "fallback": "direct",
}

_HOST_RE = re.compile(r"^(?:\*\.)?[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$")


class ConfigError(Exception):
    """配置不可用（校验失败或无法写入）。"""


def project_base_dir() -> Path:
    """项目根目录 / 冻结后的 exe 所在目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def is_writable_dir(directory: Path) -> bool:
    """真实写入探针：不靠 os.access（Windows 上不可靠），失败一律返回 False。"""
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / (".wprobe_%d" % os.getpid())
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except Exception:
        return False


def match_rule(rule_host: str, host: str) -> bool:
    """精确匹配或单级通配 `*.suffix`。大小写不敏感，忽略末尾点。"""
    h = (host or "").lower().rstrip(".")
    r = (rule_host or "").lower().rstrip(".")
    if not h or not r:
        return False
    if r.startswith("*."):
        suffix = r[2:]
        return h == suffix or h.endswith("." + suffix)
    return h == r


class Config(object):
    """一份生效配置。settings / rules 均为可直接序列化的 dict。"""

    def __init__(self, settings: Dict[str, Any], rules: Dict[str, Any],
                 base_dir: Optional[Path] = None, loaded_dir: Optional[Path] = None,
                 writable_dir: Optional[Path] = None, load_issues: Optional[List[str]] = None):
        self.settings = settings
        self.rules = rules
        self.base_dir = Path(base_dir) if base_dir else project_base_dir()
        self.loaded_dir = loaded_dir
        self.writable_dir = writable_dir
        self.load_issues = load_issues or []
        self._lock = threading.RLock()

    # ---------------- 加载 ----------------
    @classmethod
    def load(cls, base_dir: Optional[Path] = None,
             config_dir: Optional[Path] = None) -> "Config":
        base = Path(base_dir) if base_dir else project_base_dir()
        issues: List[str] = []
        candidates: List[Path] = []
        if config_dir:
            candidates.append(Path(config_dir))
        env_dir = os.environ.get("GHACC_CONFIG_DIR")
        if env_dir:
            candidates.append(Path(env_dir))
        user_dir = Path.home() / ".ghacc" / "config"
        meipass = getattr(sys, "_MEIPASS", None)
        if getattr(sys, "frozen", False):
            # 冻结包：用户改过的配置 > exe 同级现场配置 > 随包兜底配置。
            # 若把 _MEIPASS/config 放最前，GUI 保存到 ~/.ghacc 的设置会在下次启动被覆盖。
            candidates.append(user_dir)
            candidates.append(base / "config")
            if meipass:
                candidates.append(Path(meipass) / "config")
        else:
            candidates.append(base / "config")
            candidates.append(user_dir)

        settings = copy.deepcopy(DEFAULT_SETTINGS)
        rules = copy.deepcopy(DEFAULT_RULES)
        loaded: Optional[Path] = None
        for directory in candidates:
            s_path, r_path = directory / "settings.json", directory / "rules.json"
            if not (s_path.is_file() or r_path.is_file()):
                continue
            try:
                if s_path.is_file():
                    with open(str(s_path), "r", encoding="utf-8") as fh:
                        settings.update(json.load(fh))
                if r_path.is_file():
                    with open(str(r_path), "r", encoding="utf-8") as fh:
                        rules.update(json.load(fh))
                loaded = directory
                break
            except Exception as exc:
                issues.append("配置读取失败 %s: %s" % (directory, exc))

        writable: Optional[Path] = None
        for directory in [user_dir, base / "config",
                          Path(tempfile.gettempdir()) / "ghacc" / "config"]:
            if is_writable_dir(directory):
                writable = directory
                break
        return cls(settings, rules, base, loaded, writable, issues)

    # ---------------- 校验 ----------------
    def validate(self) -> List[str]:
        """返回问题列表；空列表代表可安全启动。"""
        problems: List[str] = []
        s = self.settings
        try:
            port = int(s.get("listen_port", 0))
            if not (1 <= port <= 65535):
                problems.append("listen_port 必须在 1~65535")
        except (TypeError, ValueError):
            problems.append("listen_port 不是整数")
        if s.get("mode") not in ("proxy", "hosts", "both"):
            problems.append("mode 必须是 proxy / hosts / both")
        if s.get("fallback") not in ("direct", "reject"):
            problems.append("fallback 必须是 direct / reject")
        if str(s.get("listen_host", "")).strip() not in ("127.0.0.1", "localhost", "::1"):
            problems.append("listen_host 仅允许回环地址（安全边界：不对局域网暴露）")
        for key, low, high in (("probe_timeout_ms", 100, 15000),
                               ("probe_workers", 1, 64),
                               ("cache_ttl_s", 10, 86400),
                               ("refresh_interval_s", 0, 86400)):
            try:
                val = float(s.get(key, 0))
                if not (low <= val <= high):
                    problems.append("%s 超出范围 %s~%s" % (key, low, high))
            except (TypeError, ValueError):
                problems.append("%s 不是数值" % key)
        try:
            if float(s.get("connect_timeout_s", 0)) <= 0:
                problems.append("connect_timeout_s 必须 > 0")
        except (TypeError, ValueError):
            problems.append("connect_timeout_s 不是数值")
        if s.get("log_level") not in ("DEBUG", "INFO", "WARNING", "ERROR"):
            problems.append("log_level 非法")

        rules = self.rules
        domains = rules.get("domains")
        pools = rules.get("ip_pools")
        if not isinstance(domains, list) or not domains:
            problems.append("rules.domains 必须是非空数组")
        if not isinstance(pools, dict) or not pools:
            problems.append("rules.ip_pools 必须是非空对象")
        if isinstance(domains, list):
            for idx, item in enumerate(domains):
                if not isinstance(item, dict) or not _HOST_RE.match(str(item.get("host", ""))):
                    problems.append("domains[%d].host 非法: %r" % (idx, item))
                    continue
                pool = item.get("pool")
                if pool and isinstance(pools, dict) and pool not in pools:
                    problems.append("domains[%d].pool 未定义: %s" % (idx, pool))
        if isinstance(pools, dict):
            for name, ips in pools.items():
                if not isinstance(ips, list) or not ips:
                    problems.append("ip_pools.%s 必须是非空数组" % name)
                    continue
                for ip_str in ips:
                    try:
                        ipaddress.IPv4Address(str(ip_str))
                    except Exception:
                        problems.append("ip_pools.%s 含非法 IPv4: %r" % (name, ip_str))
        return problems

    # ---------------- 查询 ----------------
    def enabled_rules(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [d for d in self.rules.get("domains", [])
                    if isinstance(d, dict) and d.get("enabled", True)]

    def rule_for(self, host: str) -> Optional[Dict[str, Any]]:
        """按「先精确、后通配」的顺序匹配，避免通配规则抢占精确规则。"""
        rules = self.enabled_rules()
        for rule in rules:
            if not str(rule.get("host", "")).startswith("*.") and \
                    match_rule(str(rule.get("host", "")), host):
                return rule
        for rule in rules:
            if match_rule(str(rule.get("host", "")), host):
                return rule
        return None

    def is_target(self, host: str) -> bool:
        return self.rule_for(host) is not None

    def pool_for(self, host: str) -> Tuple[str, List[str]]:
        rule = self.rule_for(host) or {}
        pool_name = rule.get("pool") or self._guess_pool(host)
        with self._lock:
            ips = list(self.rules.get("ip_pools", {}).get(pool_name, []))
        return str(pool_name), ips

    @staticmethod
    def _guess_pool(host: str) -> str:
        h = (host or "").lower()
        if h == "api.github.com":
            return "github_api"
        if h.endswith("githubusercontent.com") or h.endswith("github.io"):
            return "github_cdn"
        return "github_web"

    @property
    def fallback(self) -> str:
        return str(self.rules.get("fallback") or self.settings.get("fallback") or "direct")

    # ---------------- 修改与保存 ----------------
    def set_setting(self, key: str, value: Any) -> None:
        with self._lock:
            self.settings[key] = value

    def set_rules(self, rules: Dict[str, Any]) -> None:
        with self._lock:
            self.rules = rules

    def merge_pools(self, new_pools: Dict[str, List[str]]) -> int:
        """合并官方 meta 返回的候选，返回新增 IP 数。"""
        added = 0
        with self._lock:
            pools = self.rules.setdefault("ip_pools", {})
            for name, ips in new_pools.items():
                target = pools.setdefault(name, [])
                known = set(target)
                for ip_str in ips:
                    if ip_str not in known:
                        target.append(ip_str)
                        known.add(ip_str)
                        added += 1
        return added

    def save(self) -> Tuple[bool, str]:
        """原子写回可写目录；任何失败都不抛异常，只返回原因。"""
        if self.writable_dir is None:
            return False, "无可用可写目录，当前为纯内存配置（重启后丢失）"
        directory = self.writable_dir
        try:
            if not is_writable_dir(directory):
                return False, "配置目录不可写：%s" % directory
            with self._lock:
                s_dump = json.dumps(self.settings, ensure_ascii=False, indent=2)
                r_dump = json.dumps(self.rules, ensure_ascii=False, indent=2)
            for name, payload in (("settings.json", s_dump), ("rules.json", r_dump)):
                tmp = directory / (name + ".tmp")
                tmp.write_text(payload, encoding="utf-8")
                os.replace(str(tmp), str(directory / name))
            self.loaded_dir = directory
            return True, str(directory)
        except Exception as exc:
            return False, "%s: %s" % (type(exc).__name__, exc)

    def as_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {"settings": copy.deepcopy(self.settings),
                    "rules": copy.deepcopy(self.rules)}
