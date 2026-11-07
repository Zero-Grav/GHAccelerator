# -*- coding: utf-8 -*-
"""GitHub 官方 IP 候选池管理 + 从官方 meta 接口刷新。

只使用 GitHub 官方公布的 IPv4 网段，不引入任何第三方中继/镜像节点。
刷新失败一律保留内置池（失败回退），不清空、不猜测。
"""
from __future__ import annotations

import http.client
import ipaddress
import json
import socket
import ssl
import time
from typing import Any, Dict, List, Optional, Tuple

from .config import Config
from .logger import get_logger

_LOG = get_logger("ippool")
_META_HOST = "api.github.com"
_META_PATH = "/meta"
_META_SECTIONS = ("web", "api", "git", "pages", "hooks", "importer")


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """把 TLS 连接钉到指定 IP，但 SNI / 证书校验仍用真实域名。

    这样在 DNS 被污染时仍能访问官方接口，且不会降低证书校验强度。
    """

    def __init__(self, host: str, pinned_ip: str, timeout: float, **kwargs: Any):
        http.client.HTTPSConnection.__init__(self, host, timeout=timeout, **kwargs)
        self._pinned_ip = pinned_ip

    def connect(self) -> None:
        raw = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


def _fetch_meta(pinned_ip: Optional[str], timeout: float) -> Dict[str, Any]:
    if pinned_ip:
        conn = _PinnedHTTPSConnection(_META_HOST, pinned_ip, timeout, context=ssl.create_default_context())
    else:
        conn = http.client.HTTPSConnection(_META_HOST, timeout=timeout,
                                           context=ssl.create_default_context())
    try:
        conn.request("GET", _META_PATH, headers={
            "Host": _META_HOST,
            "User-Agent": "GHAccelerator/1.0",
            "Accept": "application/vnd.github+json",
        })
        resp = conn.getresponse()
        if resp.status != 200:
            raise RuntimeError("meta HTTP %d" % resp.status)
        body = resp.read()
        return json.loads(body.decode("utf-8"))
    finally:
        conn.close()


def _representatives(cidrs: List[str], limit: int = 24) -> List[str]:
    """把官方 CIDR 段展开成可测速的代表 IPv4（每 /24 取 1~2 个），避免爆炸。"""
    out: List[str] = []
    seen = set()
    for cidr in cidrs:
        try:
            net = ipaddress.ip_network(str(cidr), strict=False)
        except Exception:
            continue
        if net.version != 4 or net.num_addresses > 65536:
            continue
        if net.prefixlen <= 24:
            subnets = list(net.subnets(new_prefix=24)) if net.num_addresses > 256 else [net]
        else:
            subnets = [net]
        for sub in subnets[:16]:
            hosts = list(sub.hosts())
            if not hosts:
                continue
            for index in (0, min(9, len(hosts) - 1)):
                ip_str = str(hosts[index])
                if ip_str not in seen:
                    seen.add(ip_str)
                    out.append(ip_str)
                if len(out) >= limit:
                    return out
    return out


class IPPool(object):
    """候选 IP 的读取、合并、官方刷新。"""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.last_refresh: Dict[str, Any] = {"ok": False, "at": 0, "message": "never"}

    def candidates(self, host: str) -> Tuple[str, List[str]]:
        name, ips = self.cfg.pool_for(host)
        # 去重且保持顺序
        seen, uniq = set(), []
        for ip_str in ips:
            if ip_str not in seen:
                seen.add(ip_str)
                uniq.append(ip_str)
        return name, uniq

    def pool_summary(self) -> Dict[str, int]:
        pools = self.cfg.rules.get("ip_pools", {}) or {}
        return {str(k): len(v) for k, v in pools.items()}

    def refresh_from_meta(self, timeout: float = 6.0) -> Dict[str, Any]:
        """尽力从官方 meta 刷新候选池；任何失败都保留内置池。

        返回 {ok, added, pools, source, message, elapsed_ms}
        """
        started = time.time()
        _, api_ips = self.candidates("api.github.com")
        attempts: List[Tuple[str, Optional[str]]] = [("dns", None)]
        if api_ips:
            attempts.append(("pinned:" + api_ips[0], api_ips[0]))

        data: Optional[Dict[str, Any]] = None
        source = ""
        last_error = ""
        for label, pinned in attempts:
            try:
                data = _fetch_meta(pinned, timeout)
                source = label
                break
            except Exception as exc:
                last_error = "%s: %s: %s" % (label, type(exc).__name__, exc)
                _LOG.warning("meta 刷新失败 %s", last_error)

        elapsed = int((time.time() - started) * 1000)
        if not data:
            self.last_refresh = {"ok": False, "at": time.time(),
                                 "message": "刷新失败，保留内置池；" + last_error}
            return {"ok": False, "added": 0, "pools": self.pool_summary(),
                    "source": source, "message": self.last_refresh["message"],
                    "elapsed_ms": elapsed}

        web_cidrs: List[str] = []
        api_cidrs: List[str] = []
        cdn_cidrs: List[str] = []
        for section in _META_SECTIONS:
            value = data.get(section)
            if not isinstance(value, list):
                continue
            if section in ("web", "git", "hooks", "importer"):
                web_cidrs.extend(str(v) for v in value)
            elif section == "api":
                api_cidrs.extend(str(v) for v in value)
            elif section == "pages":
                cdn_cidrs.extend(str(v) for v in value)

        new_pools = {
            "github_web": _representatives(web_cidrs, 24),
            "github_api": _representatives(api_cidrs, 16),
            # pages 段常为空，保留内置 CDN 池，不覆盖
        }
        new_pools = {k: v for k, v in new_pools.items() if v}
        if cdn_cidrs:
            new_pools["github_cdn"] = _representatives(cdn_cidrs, 12)
        added = self.cfg.merge_pools(new_pools) if new_pools else 0
        message = "已从 %s 合并 %d 个新候选（保留内置池）" % (source, added)
        _LOG.info("meta 刷新成功 %s", message)
        self.last_refresh = {"ok": True, "at": time.time(), "message": message}
        return {"ok": True, "added": added, "pools": self.pool_summary(),
                "source": source, "message": message, "elapsed_ms": elapsed}
