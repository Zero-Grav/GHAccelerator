# -*- coding: utf-8 -*-
"""GitHub 官方 IP 候选池管理 + 从官方 meta 接口刷新。

T6 教训：早期版本把 meta 返回的 CIDR 按「每 /24 取 .1 和 .10」机械展开成
候选地址。这些地址绝大多数不是真实 Web 前端（实测 140.82.113.10 能完成
TLS 握手却挂起 HTTP 请求），且把 Fastly/Pages 段混进了 github.com 池，
直接导致现场选到半死节点。

现在的策略：
  - meta **只用于确定合法官方网段**（白名单校验 / 审计），不再生成地址；
  - 新候选来自对目标域名的**真实 DNS 解析结果**，且必须落在对应官方段内；
  - 刷新成功时**重建**池（内置干净池 + 合规 DNS 候选），而不是 merge，
    因此执行一次 --refresh-ips 即可自净历史遗留的污染池；
  - 任何失败都完整保留现有池（失败回退），不清空、不猜测。
"""
from __future__ import annotations

import http.client
import ipaddress
import json
import socket
import ssl
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from .config import DEFAULT_RULES, Config
from .logger import get_logger

_LOG = get_logger("ippool")
_META_HOST = "api.github.com"
_META_PATH = "/meta"

_BUILTIN_POOLS: Dict[str, List[str]] = {
    str(k): list(v) for k, v in DEFAULT_RULES["ip_pools"].items()
}

# 每个池允许补充的官方段来源，以及用于 DNS 解析的代表域名
_POOL_SOURCES: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {
    "github_web": (("web", "git", "hooks", "importer"),
                   ("github.com", "www.github.com", "codeload.github.com")),
    "github_api": (("api",), ("api.github.com",)),
    "github_cdn": (("pages",),
                   ("raw.githubusercontent.com", "github.io")),
}


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """把 TLS 连接钉到指定 IP，但 SNI / 证书校验仍用真实域名。"""

    def __init__(self, host: str, pinned_ip: str, timeout: float, **kwargs: Any):
        http.client.HTTPSConnection.__init__(self, host, timeout=timeout, **kwargs)
        self._pinned_ip = pinned_ip

    def connect(self) -> None:
        raw = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


def _fetch_meta(pinned_ip: Optional[str], timeout: float) -> Dict[str, Any]:
    ctx = ssl.create_default_context()
    if pinned_ip:
        conn = _PinnedHTTPSConnection(_META_HOST, pinned_ip, timeout, context=ctx)
    else:
        conn = http.client.HTTPSConnection(_META_HOST, timeout=timeout, context=ctx)
    try:
        conn.request("GET", _META_PATH, headers={
            "Host": _META_HOST,
            "User-Agent": "GHAccelerator/1.0",
            "Accept": "application/vnd.github+json",
        })
        resp = conn.getresponse()
        if resp.status != 200:
            raise RuntimeError("meta HTTP %d" % resp.status)
        return json.loads(resp.read().decode("utf-8"))
    finally:
        conn.close()


def is_official_ip(ip_str: str, cidrs: List[str]) -> bool:
    """纯函数：IPv4 是否落在给定的官方 CIDR 段内（IPv6 段忽略）。"""
    try:
        addr = ipaddress.IPv4Address(str(ip_str))
    except Exception:
        return False
    for cidr in cidrs:
        if ":" in str(cidr):          # 跳过 IPv6
            continue
        try:
            net = ipaddress.ip_network(str(cidr), strict=False)
        except Exception:
            continue
        if net.version == 4 and addr in net:
            return True
    return False


def _resolve_a(host: str, timeout_s: float = 2.0) -> List[str]:
    """真实 DNS 解析（IPv4 A 记录），daemon 线程 + 硬超时。"""
    box: Dict[str, Any] = {}

    def worker() -> None:
        try:
            rows = socket.getaddrinfo(host, 443, socket.AF_INET, socket.SOCK_STREAM)
            box["ips"] = [str(row[4][0]) for row in rows]
        except Exception as exc:
            box["error"] = exc

    thread = threading.Thread(target=worker, name="ghacc-pool-dns", daemon=True)
    thread.start()
    thread.join(timeout_s)
    if thread.is_alive():
        return []
    return list(dict.fromkeys(box.get("ips", [])))


class IPPool(object):
    """候选 IP 的读取、重建、官方网段校验。"""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.last_refresh: Dict[str, Any] = {"ok": False, "at": 0, "message": "never"}

    def candidates(self, host: str) -> Tuple[str, List[str]]:
        name, ips = self.cfg.pool_for(host)
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
        """用官方 meta 校验网段 + DNS 实解析，重建候选池（失败则保留原池）。"""
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

        elapsed_fetch = int((time.time() - started) * 1000)
        if not data:
            self.last_refresh = {"ok": False, "at": time.time(),
                                 "message": "刷新失败，完整保留现有池；" + last_error}
            return {"ok": False, "added": 0, "replaced": False,
                    "pools": self.pool_summary(), "source": source,
                    "message": self.last_refresh["message"],
                    "elapsed_ms": int((time.time() - started) * 1000)}

        # 1) 只保存官方网段（用于校验与审计），不再展开成地址
        cidrs_by_section: Dict[str, List[str]] = {}
        for section, value in data.items():
            if isinstance(value, list):
                cidrs_by_section[str(section)] = [str(v) for v in value]

        # 2) 重建池：内置干净池 + 落在对应官方段内的真实 DNS 解析结果
        new_pools: Dict[str, List[str]] = {
            name: list(ips) for name, ips in _BUILTIN_POOLS.items()
        }
        dns_candidates = 0
        for pool_name, (sections, hosts) in _POOL_SOURCES.items():
            cidrs: List[str] = []
            for section in sections:
                cidrs.extend(cidrs_by_section.get(section, []))
            for host in hosts:
                for ip_str in _resolve_a(host, 2.0):
                    if not is_official_ip(ip_str, cidrs):
                        continue
                    if ip_str not in new_pools.setdefault(pool_name, []):
                        new_pools[pool_name].append(ip_str)
                        dns_candidates += 1

        rules = dict(self.cfg.rules)
        rules["ip_pools"] = new_pools
        rules["official_cidrs"] = cidrs_by_section
        rules["pool_source"] = "meta-cidr-validation + real-dns (%s)" % source
        self.cfg.set_rules(rules)
        problems = self.cfg.validate()
        if problems:
            # 校验不过就回滚，绝不让程序带着坏配置运行
            _LOG.error("重建后的配置校验失败，回滚: %s", problems[:3])
            return {"ok": False, "added": 0, "replaced": False,
                    "pools": self.pool_summary(), "source": source,
                    "message": "重建配置校验失败，已回滚",
                    "elapsed_ms": int((time.time() - started) * 1000)}

        message = ("已从 %s 重建池：内置 %s + DNS 合规候选 %d 个；"
                   "旧池中机械展开地址已清除" %
                   (source,
                    "/".join("%s=%d" % (k, len(v)) for k, v in _BUILTIN_POOLS.items()),
                    dns_candidates))
        _LOG.info("meta 刷新成功 %s", message)
        self.last_refresh = {"ok": True, "at": time.time(), "message": message}
        return {"ok": True, "added": dns_candidates, "replaced": True,
                "pools": self.pool_summary(), "source": source,
                "message": message, "fetch_ms": elapsed_fetch,
                "elapsed_ms": int((time.time() - started) * 1000)}
