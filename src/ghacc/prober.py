# -*- coding: utf-8 -*-
"""并发候选 IP 探测：TCP 连通性 + RTT + 可选 TLS 证书校验。

TLS 校验是安全边界的关键：连到候选 IP 后用真实域名做 SNI 并验证证书，
只有确实持有 GitHub 合法证书的 IP 才会入选，从机制上排除被劫持地址。
"""
from __future__ import annotations

import concurrent.futures
import socket
import ssl
import time
from typing import Any, Dict, List, Optional

from .logger import get_logger

_LOG = get_logger("prober")


class ProbeResult(object):
    __slots__ = ("ip", "ok", "rtt_ms", "tls_ok", "reason")

    def __init__(self, ip: str, ok: bool, rtt_ms: float = 0.0,
                 tls_ok: bool = False, reason: str = ""):
        self.ip = ip
        self.ok = ok
        self.rtt_ms = rtt_ms
        self.tls_ok = tls_ok
        self.reason = reason

    def as_dict(self) -> Dict[str, Any]:
        return {"ip": self.ip, "ok": self.ok, "rtt_ms": round(self.rtt_ms, 1),
                "tls_ok": self.tls_ok, "reason": self.reason}


def probe_one(ip: str, host: str, port: int, timeout_s: float,
              tls_verify: bool = True) -> ProbeResult:
    """探测单个 IP。绝不抛异常，任何失败都转成 ProbeResult(ok=False)。"""
    started = time.time()
    sock: Optional[socket.socket] = None
    try:
        sock = socket.create_connection((ip, port), timeout=timeout_s)
        rtt = (time.time() - started) * 1000.0
        if not tls_verify:
            return ProbeResult(ip, True, rtt, False, "tcp-only")
        ctx = ssl.create_default_context()
        tls = ctx.wrap_socket(sock, server_hostname=host)
        try:
            cert = tls.getpeercert()
            if not cert:
                return ProbeResult(ip, False, rtt, False, "empty-cert")
            # check_hostname=True 时 wrap_socket 已校验 host 与证书 SAN 匹配
            tls.close()
            sock = None
            return ProbeResult(ip, True, (time.time() - started) * 1000.0,
                               True, "tls-verified")
        except Exception:
            try:
                tls.close()
            except Exception:
                pass
            sock = None
            raise
    except ssl.SSLError as exc:
        return ProbeResult(ip, False, (time.time() - started) * 1000.0,
                           False, "tls:" + str(exc)[:80])
    except Exception as exc:
        return ProbeResult(ip, False, (time.time() - started) * 1000.0,
                           False, type(exc).__name__ + ":" + str(exc)[:80])
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass


def probe_pool(ips: List[str], host: str, port: int = 443,
               timeout_ms: int = 1200, workers: int = 12,
               tls_verify: bool = True) -> Dict[str, Any]:
    """并发探测整池，返回排序后的可用结果与统计。"""
    if not ips:
        return {"host": host, "ok": [], "failed": 0, "elapsed_ms": 0,
                "tls_verify": tls_verify, "results": []}
    started = time.time()
    timeout_s = max(0.1, timeout_ms / 1000.0)
    results: List[ProbeResult] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(workers, len(ips))) as pool:
        futures = {pool.submit(probe_one, ip, host, port, timeout_s, tls_verify): ip
                   for ip in ips}
        try:
            for future in concurrent.futures.as_completed(futures, timeout=timeout_s * 3 + 5):
                try:
                    results.append(future.result())
                except Exception as exc:
                    results.append(ProbeResult(futures[future], False, 0, False,
                                               "future:" + type(exc).__name__))
        except concurrent.futures.TimeoutError:
            for future, ip_str in futures.items():
                if not future.done():
                    results.append(ProbeResult(ip_str, False, 0, False, "probe-timeout"))
            _LOG.warning("probe %s 整体超时，%d 个候选未返回", host,
                         sum(1 for f in futures.values() if not f.done()))
    ok = sorted([r for r in results if r.ok],
                key=lambda r: (not r.tls_ok, r.rtt_ms))
    elapsed = int((time.time() - started) * 1000)
    _LOG.debug("probe %s total=%d ok=%d tls_ok=%d ms=%d",
               host, len(ips), len(ok), sum(1 for r in ok if r.tls_ok), elapsed)
    return {"host": host, "ok": ok, "failed": len(results) - len(ok),
            "elapsed_ms": elapsed, "tls_verify": tls_verify,
            "results": [r.as_dict() for r in results]}
