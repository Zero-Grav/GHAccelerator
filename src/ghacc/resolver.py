# -*- coding: utf-8 -*-
"""域名 -> 优选 IP 的决策层，带 TTL 缓存与三层失败回退。

回退链（逐级降级，任何一级成功都继续提供服务）：
  1. TLS 校验探测（只信持有 GitHub 合法证书的官方 IP）
  2. 全部失败且开启了校验 -> 降级为 TCP-only 探测（记 WARNING，界面标 degraded）
  3. 仍全部失败 -> 返回 None，由代理回退系统 DNS 直连（程序不中断服务）
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

from .config import Config
from .ip_pool import IPPool
from .logger import get_logger
from .prober import probe_pool

_LOG = get_logger("resolver")


class Decision(object):
    __slots__ = ("host", "ip", "port", "rtt_ms", "tls_ok", "source", "ts")

    def __init__(self, host: str, ip: str, port: int, rtt_ms: float,
                 tls_ok: bool, source: str):
        self.host = host
        self.ip = ip
        self.port = port
        self.rtt_ms = rtt_ms
        self.tls_ok = tls_ok
        self.source = source
        self.ts = time.time()

    def as_dict(self) -> Dict[str, Any]:
        return {"host": self.host, "ip": self.ip, "port": self.port,
                "rtt_ms": round(self.rtt_ms, 1), "tls_ok": self.tls_ok,
                "source": self.source, "age_s": int(time.time() - self.ts)}


class Resolver(object):
    """线程安全的优选结果缓存。"""

    def __init__(self, cfg: Config, pool: Optional[IPPool] = None):
        self.cfg = cfg
        self.pool = pool or IPPool(cfg)
        self._cache: Dict[str, Decision] = {}
        self._locks: Dict[str, threading.Lock] = {}
        self._guard = threading.RLock()
        self._force_fail = False          # 仅供 selftest 注入故障
        self._hits = 0
        self._misses = 0
        self._failures = 0
        self.last_probe: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    def set_force_fail(self, value: bool) -> None:
        """反向断言用：模拟全部候选 IP 不可达。"""
        with self._guard:
            self._force_fail = bool(value)
            self._cache.clear()

    def _lock_for(self, key: str) -> threading.Lock:
        with self._guard:
            if key not in self._locks:
                self._locks[key] = threading.Lock()
            return self._locks[key]

    @property
    def ttl(self) -> float:
        return float(self.cfg.settings.get("cache_ttl_s", 600))

    def select(self, host: str, port: int = 443) -> Optional[Decision]:
        """返回优选决策；None 表示「本域名不加速，交给系统 DNS 直连」。"""
        host = (host or "").lower().rstrip(".")
        if not host or not self.cfg.is_target(host):
            return None
        key = "%s:%d" % (host, port)
        with self._guard:
            cached = self._cache.get(key)
            if cached and (time.time() - cached.ts) < self.ttl:
                self._hits += 1
                return cached
            self._misses += 1
        with self._lock_for(key):
            with self._guard:
                cached = self._cache.get(key)
                if cached and (time.time() - cached.ts) < self.ttl:
                    return cached
            decision = self._probe(host, port)
            with self._guard:
                if decision:
                    self._cache[key] = decision
                else:
                    self._cache.pop(key, None)
                    self._failures += 1
            return decision

    def _probe(self, host: str, port: int) -> Optional[Decision]:
        if self._force_fail:
            _LOG.warning("优选被强制置为失败（故障注入）host=%s", host)
            self.last_probe = {"host": host, "ok": 0, "source": "force-fail"}
            return None

        pool_name, ips = self.pool.candidates(host)
        timeout_ms = int(self.cfg.settings.get("probe_timeout_ms", 1200))
        workers = int(self.cfg.settings.get("probe_workers", 12))
        verify = bool(self.cfg.settings.get("probe_tls_verify", True))

        if not ips:
            _LOG.warning("候选池为空 host=%s pool=%s -> 回退直连", host, pool_name)
            return None

        result = probe_pool(ips, host, port, timeout_ms, workers, verify)
        ok = result["ok"]
        source = "tls" if verify else "tcp"
        if not ok and verify:
            _LOG.warning("TLS 校验探测全失败 host=%s -> 降级 TCP-only 重测", host)
            result = probe_pool(ips, host, port, timeout_ms, workers, False)
            ok = result["ok"]
            source = "tcp-fallback"

        self.last_probe = {"host": host, "pool": pool_name,
                           "candidates": len(ips), "ok": len(ok),
                           "failed": result["failed"],
                           "elapsed_ms": result["elapsed_ms"], "source": source}
        if not ok:
            _LOG.error("全部候选不可用 host=%s (%d 个) -> 回退系统 DNS 直连",
                       host, len(ips))
            return None
        best = ok[0]
        _LOG.info("优选 host=%s ip=%s rtt=%.1fms source=%s pool=%s",
                  host, best.ip, best.rtt_ms, source, pool_name)
        return Decision(host, best.ip, port, best.rtt_ms, best.tls_ok, source)

    # ------------------------------------------------------------------
    def refresh(self, hosts: Optional[List[str]] = None, port: int = 443) -> Dict[str, Any]:
        """强制重测（忽略 TTL）。预热的域名由调用方给出。"""
        if hosts is None:
            hosts = [str(r.get("host", "")) for r in self.cfg.enabled_rules()
                     if not str(r.get("host", "")).startswith("*.")]
        started = time.time()
        succeeded, failed = [], []
        for host in hosts:
            with self._guard:
                self._cache.pop("%s:%d" % (host, port), None)
            if self.select(host, port):
                succeeded.append(host)
            else:
                failed.append(host)
        return {"ok": len(succeeded), "failed": len(failed),
                "succeeded": succeeded, "failed_hosts": failed,
                "elapsed_ms": int((time.time() - started) * 1000)}

    def invalidate(self, host: Optional[str] = None) -> None:
        with self._guard:
            if host is None:
                self._cache.clear()
            else:
                for key in [k for k in self._cache if k.startswith(host.lower() + ":")]:
                    self._cache.pop(key, None)

    def decisions(self) -> List[Dict[str, Any]]:
        with self._guard:
            return [d.as_dict() for d in self._cache.values()]

    def stats(self) -> Dict[str, Any]:
        with self._guard:
            return {"cached": len(self._cache), "hits": self._hits,
                    "misses": self._misses, "failures": self._failures,
                    "ttl_s": self.ttl, "force_fail": self._force_fail,
                    "last_probe": dict(self.last_probe)}
