# -*- coding: utf-8 -*-
"""并发候选 IP 探测：TCP -> TLS 证书校验 -> 真实 HTTP 请求断言。

T6 现场教训：只做到 TLS 握手就判「可用」是不够的。GitHub 官方网段里存在
「能完成 TCP + TLS 握手、却挂起后续 HTTP 请求」的半死节点（实测
140.82.113.10 握手成功，GET / 却 9.5s 超时），以及返回 abuse 风控页的
节点。它们能通过旧断言，却在真实浏览时暴露为打不开 / Whoa there。

因此 ok 的最终判据包含三层，缺一不可：
  1. TCP connect 成功
  2. TLS 握手成功且证书校验通过（SNI 用真实域名）
  3. 应用层断言：能读到状态行、状态码 < 500、响应体无风控关键字、
     且在 http_timeout 内返回（check_http_response 为纯函数，便于反向断言）
"""
from __future__ import annotations

import concurrent.futures
import socket
import ssl
import time
from typing import Any, Dict, List, Optional, Tuple

from .logger import get_logger

_LOG = get_logger("prober")

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# GitHub 侧 abuse / 请求异常拦截页特征；命中即淘汰该 IP
RISK_MARKERS = (
    b"whoa there",
    b"you have sent an invalid request",
    b"please do not send this request again",
    b"rate limit exceeded",
    b"abuse detection",
    b"access denied",
)


class ProbeResult(object):
    __slots__ = ("ip", "ok", "rtt_ms", "tls_ok", "http_ok", "reason")

    def __init__(self, ip: str, ok: bool, rtt_ms: float = 0.0,
                 tls_ok: bool = False, http_ok: bool = False, reason: str = ""):
        self.ip = ip
        self.ok = ok
        self.rtt_ms = rtt_ms
        self.tls_ok = tls_ok
        self.http_ok = http_ok
        self.reason = reason

    def as_dict(self) -> Dict[str, Any]:
        return {"ip": self.ip, "ok": self.ok, "rtt_ms": round(self.rtt_ms, 1),
                "tls_ok": self.tls_ok, "http_ok": self.http_ok,
                "reason": self.reason}


def check_http_response(status: Optional[int], body: bytes) -> Tuple[bool, str]:
    """应用层健康断言（纯函数，selftest 表驱动反向断言直接调用）。

    通过：2xx/3xx/4xx 中的业务性响应（301 重定向、404 缺路径、415 API
    根路径都属正常）。拒绝：无状态行（超时/连接重置）、5xx、风控页特征。
    """
    if status is None:
        return False, "no-status-line(timeout-or-reset)"
    if status >= 500:
        return False, "http-%d" % status
    if status in (403, 429):
        return False, "http-%d(blocked-or-throttled)" % status
    low = (body or b"").lower()
    for marker in RISK_MARKERS:
        if marker in low:
            return False, "risk-page:" + marker.decode("ascii", "replace")
    return True, "ok"


def _http_get(tls_sock: ssl.SSLSocket, host: str,
              timeout_s: float) -> Tuple[Optional[int], bytes]:
    """在已建立的 TLS 连接上发一个真实 GET /，返回 (状态码, 响应体片段)。

    用真实域名做 Host / SNI，不改写任何浏览器行为；只用于探测，不转发用户流量。
    任何网络异常都转成 (None, 片段)，交由 check_http_response 淘汰。
    """
    request = (
        "GET / HTTP/1.1\r\n"
        "Host: %s\r\n"
        "User-Agent: %s\r\n"
        "Accept: text/html,application/xhtml+xml\r\n"
        "Accept-Language: en-US,en;q=0.9\r\n"
        "Connection: close\r\n\r\n" % (host, _UA)
    )
    try:
        tls_sock.settimeout(timeout_s)
        tls_sock.sendall(request.encode("ascii"))
        buf = b""
        while b"\r\n\r\n" not in buf and len(buf) < 16384:
            chunk = tls_sock.recv(4096)
            if not chunk:
                break
            buf += chunk
        head, _, rest = buf.partition(b"\r\n\r\n")
        status: Optional[int] = None
        if head:
            first = head.split(b"\r\n", 1)[0].split()
            if len(first) >= 2 and first[1].isdigit():
                status = int(first[1])
        body = rest
        while len(body) < 4096:
            chunk = tls_sock.recv(4096)
            if not chunk:
                break
            body += chunk
        return status, body
    except Exception:
        return None, b""


def probe_one(ip: str, host: str, port: int, timeout_s: float,
              tls_verify: bool = True, http_verify: bool = True,
              http_timeout_s: float = 4.0) -> ProbeResult:
    """探测单个 IP。绝不抛异常，任何失败都转成 ProbeResult(ok=False)。"""
    started = time.time()
    sock: Optional[socket.socket] = None
    try:
        sock = socket.create_connection((ip, port), timeout=timeout_s)
        rtt = (time.time() - started) * 1000.0

        # 两个校验都关闭 = 纯 TCP 连通性判定（回环冒烟 / 无 CA 且无 HTTP 能力）。
        # 只要 http_verify 开启，即使跳过证书校验也仍做应用层断言，
        # 防止 CA 降级路径重新引入半死节点。
        if not tls_verify and not http_verify:
            return ProbeResult(ip, True, rtt, False, False, "tcp-only")

        ctx = ssl.create_default_context()
        if not tls_verify:
            # CA 信任库缺失时的降级：跳过证书校验，但应用层断言仍然生效，
            # 避免降级路径重新引入半死节点。
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        tls = ctx.wrap_socket(sock, server_hostname=host)
        sock = tls
        cert = tls.getpeercert()
        if tls_verify and not cert:
            return ProbeResult(ip, False, rtt, False, False, "empty-cert")

        elapsed = (time.time() - started) * 1000.0
        if not http_verify:
            return ProbeResult(ip, True, elapsed, bool(tls_verify), False,
                               "tls-verified" if tls_verify else "tls-unverified")

        status, body = _http_get(tls, host, http_timeout_s)
        ok, reason = check_http_response(status, body)
        elapsed = (time.time() - started) * 1000.0
        if not ok:
            _LOG.info("淘汰候选 ip=%s host=%s reason=%s", ip, host, reason)
            return ProbeResult(ip, False, elapsed, bool(tls_verify), False,
                               "http:" + reason)
        return ProbeResult(ip, True, elapsed, bool(tls_verify), True,
                           "tls+http" if tls_verify else "tls-unverified+http")
    except ssl.SSLError as exc:
        return ProbeResult(ip, False, (time.time() - started) * 1000.0,
                           False, False, "tls:" + str(exc)[:80])
    except Exception as exc:
        return ProbeResult(ip, False, (time.time() - started) * 1000.0,
                           False, False, type(exc).__name__ + ":" + str(exc)[:80])
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass


def probe_pool(ips: List[str], host: str, port: int = 443,
               timeout_ms: int = 1200, workers: int = 12,
               tls_verify: bool = True, http_verify: bool = True,
               http_timeout_ms: int = 4000) -> Dict[str, Any]:
    """并发探测整池，返回排序后的可用结果与统计。"""
    if not ips:
        return {"host": host, "ok": [], "failed": 0, "elapsed_ms": 0,
                "tls_verify": tls_verify, "http_verify": http_verify, "results": []}
    started = time.time()
    timeout_s = max(0.1, timeout_ms / 1000.0)
    http_timeout_s = max(0.5, http_timeout_ms / 1000.0)
    worker_count = min(workers, len(ips))
    results: List[ProbeResult] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as pool:
        futures = {pool.submit(probe_one, ip, host, port, timeout_s,
                               tls_verify, http_verify, http_timeout_s): ip
                   for ip in ips}
        # 整体上限按轮次估算，避免 32 个候选 × 4s 被旧的 3×timeout 误判超时
        rounds = len(ips) // worker_count + 2
        overall = max(timeout_s, http_timeout_s) * rounds + 5
        try:
            for future in concurrent.futures.as_completed(futures, timeout=overall):
                try:
                    results.append(future.result())
                except Exception as exc:
                    results.append(ProbeResult(futures[future], False, 0, False,
                                               False, "future:" + type(exc).__name__))
        except concurrent.futures.TimeoutError:
            for future, ip_str in futures.items():
                if not future.done():
                    results.append(ProbeResult(ip_str, False, 0, False, False,
                                               "probe-timeout"))
            _LOG.warning("probe %s 整体超时，%d 个候选未返回", host,
                         sum(1 for f in futures.values() if not f.done()))
    ok = sorted([r for r in results if r.ok],
                key=lambda r: (not r.tls_ok, not r.http_ok, r.rtt_ms))
    elapsed = int((time.time() - started) * 1000)
    _LOG.debug("probe %s total=%d ok=%d tls_ok=%d http_ok=%d ms=%d",
               host, len(ips), len(ok), sum(1 for r in ok if r.tls_ok),
               sum(1 for r in ok if r.http_ok), elapsed)
    return {"host": host, "ok": ok, "failed": len(results) - len(ok),
            "elapsed_ms": elapsed, "tls_verify": tls_verify,
            "http_verify": http_verify,
            "results": [r.as_dict() for r in results]}
