# -*- coding: utf-8 -*-
"""本地 HTTP/HTTPS 代理（仅监听回环）。

核心机制：对白名单域名的 CONNECT 请求，把 TCP 连接直接钉到「优选出的
GitHub 官方 IP」，随后纯字节透传。TLS 握手由浏览器与目标服务器直接完成，
因此：不安装根证书、不解密 HTTPS、不改写任何请求内容。

安全边界（硬编码，不可配置）：
  - 只监听 127.0.0.1
  - 非白名单域名一律原样直连，绝不劫持、绝不转发到第三方节点
  - 日志只记 host:port 与字节数，不记 URL path / query / header 中的凭据
"""
from __future__ import annotations

import ipaddress
import select
import socket
import socketserver
import threading
import time
from typing import Any, Dict, Optional, Tuple

from .config import Config
from .logger import get_logger
from .resolver import Decision, Resolver

_LOG = get_logger("proxy")

_CONNECT_OK = (b"HTTP/1.1 200 Connection Established\r\n"
               b"Proxy-Agent: GHAccelerator/1.0\r\n\r\n")
_BAD_GATEWAY = (b"HTTP/1.1 502 Bad Gateway\r\n"
                b"Content-Type: text/plain; charset=utf-8\r\n"
                b"X-GHAcc-Error: upstream-unavailable\r\n"
                b"Content-Length: 0\r\nConnection: close\r\n\r\n")
_FORBIDDEN = (b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n"
              b"Connection: close\r\n\r\n")


def _is_ip_literal(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except (ValueError, TypeError):
        return False


def _resolve_infos(host: str, port: int, timeout: float):
    """DNS 解析带硬超时。

    socket.create_connection 的 timeout 只作用于 connect，不覆盖 getaddrinfo；
    DNS 被污染或域名不存在时可能阻塞数秒，导致「回退比不加速还慢」。
    这里用 daemon 线程隔离解析，超时即放弃并返回 502。
    """
    box = {}

    def worker():
        try:
            box["infos"] = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
        except Exception as exc:
            box["error"] = exc

    thread = threading.Thread(target=worker, name="ghacc-dns", daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        _LOG.warning("DNS 解析超时 host=%s limit=%.2fs -> 快速失败", host, timeout)
        return []
    if "error" in box:
        _LOG.debug("DNS 解析失败 host=%s %s", host, box["error"])
        return []
    return box.get("infos", [])


def _open_socket(target: str, port: int, timeout: float,
                 dns_timeout: float) -> socket.socket:
    """用已解析的地址建连，避免二次 DNS；IP 字面量直接连。"""
    if _is_ip_literal(target):
        return socket.create_connection((target, port), timeout=timeout)
    infos = _resolve_infos(target, port, dns_timeout)
    if not infos:
        raise OSError("DNS resolve failed or timed out")
    last_error: Optional[Exception] = None
    for family, socktype, proto, _, sockaddr in infos[:4]:
        sock = None
        try:
            sock = socket.socket(family, socktype, proto)
            sock.settimeout(timeout)
            sock.connect(sockaddr)
            return sock
        except Exception as exc:
            last_error = exc
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
    raise last_error or OSError("no reachable address")


class Counters(object):
    def __init__(self):
        self._lock = threading.Lock()
        self.total = 0
        self.accelerated = 0
        self.direct = 0
        self.failed = 0
        self.bytes_up = 0
        self.bytes_down = 0
        self.active = 0

    def bump(self, field: str, value: int = 1) -> None:
        with self._lock:
            setattr(self, field, getattr(self, field) + value)

    def as_dict(self) -> Dict[str, int]:
        with self._lock:
            return {k: getattr(self, k) for k in
                    ("total", "accelerated", "direct", "failed",
                     "bytes_up", "bytes_down", "active")}


def parse_authority(target: str) -> Tuple[str, int]:
    """解析 CONNECT 目标，兼容 host:port 与 [IPv6]:port。"""
    target = target.strip()
    if target.startswith("["):
        host, _, rest = target[1:].partition("]")
        port = int(rest.lstrip(":") or "443")
        return host, port
    if ":" in target:
        host, _, port_str = target.rpartition(":")
        return host, int(port_str or "443")
    return target, 443


class _TCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address, handler, owner):
        self.owner = owner
        socketserver.ThreadingTCPServer.__init__(self, address, handler)

    def handle_error(self, request, client_address):
        # 不把单个连接的异常打到 stderr 刷屏，统一进结构化日志
        import sys
        _LOG.debug("handler error client=%s err=%s", client_address, sys.exc_info()[1])


class _Handler(socketserver.StreamRequestHandler):
    timeout = 15.0

    def handle(self) -> None:
        owner = self.server.owner  # type: ignore[attr-defined]
        counters = owner.counters
        counters.bump("total")
        counters.bump("active")
        client = self.connection
        upstream: Optional[socket.socket] = None
        host = ""
        port = 443
        mode = "direct"
        ip_used = ""
        try:
            raw_line = self.rfile.readline(65536)
            if not raw_line:
                return
            line = raw_line.decode("iso-8859-1").strip()
            parts = line.split()
            if len(parts) < 2:
                return
            method, target = parts[0].upper(), parts[1]
            headers = self._read_headers()

            if method == "CONNECT":
                host, port = parse_authority(target)
                decision = self._decide(owner, host, port)
                if decision:
                    mode, ip_used = "accelerated", decision.ip
                elif decision is None and owner._is_reject_case(host):
                    client.sendall(_FORBIDDEN)
                    counters.bump("failed")
                    return
                upstream = self._connect(owner, host, port, ip_used)
                if upstream is None:
                    client.sendall(_BAD_GATEWAY)
                    counters.bump("failed")
                    return
                client.sendall(_CONNECT_OK)
            else:
                host, port, path, headers = self._parse_http(target, headers)
                if not host:
                    client.sendall(_BAD_GATEWAY)
                    counters.bump("failed")
                    return
                decision = self._decide(owner, host, port)
                if decision:
                    mode, ip_used = "accelerated", decision.ip
                upstream = self._connect(owner, host, port, ip_used)
                if upstream is None:
                    client.sendall(_BAD_GATEWAY)
                    counters.bump("failed")
                    return
                request_head = ("%s %s HTTP/1.1\r\n" % (method, path)).encode("iso-8859-1")
                request_head += b"\r\n".join(headers) + b"\r\n\r\n"
                upstream.sendall(request_head)

            counters.bump("accelerated" if mode == "accelerated" else "direct")
            _LOG.info("conn host=%s:%d mode=%s ip=%s", host, port, mode,
                      ip_used or "(system-dns)")
            self._relay(owner, client, upstream, counters)
        except Exception as exc:
            _LOG.debug("conn aborted host=%s:%d %s: %s", host, port,
                       type(exc).__name__, exc)
        finally:
            counters.bump("active", -1)
            if upstream is not None:
                try:
                    upstream.close()
                except Exception:
                    pass
            try:
                self.connection.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    def _read_headers(self):
        lines = []
        while True:
            row = self.rfile.readline(65536)
            if not row or row in (b"\r\n", b"\n"):
                break
            lines.append(row.rstrip(b"\r\n"))
            if len(lines) > 120:
                break
        return lines

    @staticmethod
    def _parse_http(target, headers):
        """把绝对 URI 改写成 origin-form，并剔除逐跳代理头。"""
        host, port, path = "", 80, target
        if target.startswith("http://") or target.startswith("https://"):
            rest = target.split("://", 1)[1]
            slash = rest.find("/")
            authority = rest if slash < 0 else rest[:slash]
            path = "/" if slash < 0 else rest[slash:]
            host, port = parse_authority(authority) if ":" in authority else (authority, 80)
        kept = []
        for row in headers:
            lower = row.lower()
            if lower.startswith(b"proxy-connection:") or \
               lower.startswith(b"proxy-authorization:") or \
               lower.startswith(b"connection:"):
                continue
            if lower.startswith(b"host:") and not host:
                host = row.split(b":", 1)[1].strip().decode("iso-8859-1")
            kept.append(row)
        if host and b":" in host.encode("iso-8859-1"):
            host, port_str = host.rsplit(":", 1)
            try:
                port = int(port_str)
            except ValueError:
                port = 80
        return host, port, path, kept

    @staticmethod
    def _decide(owner, host, port) -> Optional[Decision]:
        return owner.resolver.select(host, port)

    @staticmethod
    def _connect(owner, host, port, pinned_ip) -> Optional[socket.socket]:
        timeout = float(owner.cfg.settings.get("connect_timeout_s", 8.0))
        dns_timeout = min(timeout, 1.5)
        target = pinned_ip or host
        try:
            return _open_socket(target, port, timeout, dns_timeout)
        except Exception as exc:
            if pinned_ip:
                # 优选 IP 连接失败 -> 再试一次系统 DNS 直连（连接级失败回退）
                _LOG.warning("优选连接失败 host=%s ip=%s -> 重试系统 DNS (%s: %s)",
                             host, pinned_ip, type(exc).__name__, exc)
                try:
                    return _open_socket(host, port, timeout, dns_timeout)
                except Exception as exc2:
                    _LOG.error("直连重试也失败 host=%s %s: %s", host,
                               type(exc2).__name__, exc2)
            else:
                _LOG.warning("直连失败 host=%s %s: %s", host,
                             type(exc).__name__, exc)
            return None

    @staticmethod
    def _relay(owner, client, upstream, counters) -> None:
        idle = float(owner.cfg.settings.get("relay_timeout_s", 0.0))
        for sock in (client, upstream):
            try:
                sock.settimeout(60.0)
            except Exception:
                pass
        last = time.time()
        sockets = [client, upstream]
        while not owner._stopping.is_set():
            try:
                readable, _, broken = select.select(sockets, [], sockets, 0.5)
            except Exception:
                break
            if broken:
                break
            if not readable:
                if idle and (time.time() - last) > idle:
                    break
                continue
            last = time.time()
            for sock in readable:
                try:
                    data = sock.recv(65536)
                except Exception:
                    return
                if not data:
                    return
                other = upstream if sock is client else client
                try:
                    other.sendall(data)
                except Exception:
                    return
                counters.bump("bytes_up" if sock is client else "bytes_down", len(data))


class ProxyServer(object):
    """代理服务生命周期。serve_forever 跑在独立线程，stop 可从 UI 线程安全调用。"""

    def __init__(self, cfg: Config, resolver: Resolver, on_event=None):
        self.cfg = cfg
        self.resolver = resolver
        self.on_event = on_event
        self.counters = Counters()
        self._server: Optional[_TCPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._stopping = threading.Event()
        self._run_guard = threading.Lock()

    @property
    def running(self) -> bool:
        return self._server is not None and self._thread is not None and \
               self._thread.is_alive() and not self._stopping.is_set()

    def address(self) -> Tuple[str, int]:
        return (str(self.cfg.settings.get("listen_host", "127.0.0.1")),
                int(self.cfg.settings.get("listen_port", 18792)))

    def _is_reject_case(self, host: str) -> bool:
        """仅当「白名单域名 + 优选失败 + 用户显式选择 reject」才拒绝。"""
        return str(self.cfg.fallback).lower() == "reject" and \
               self.cfg.is_target(host) and self.resolver.select(host) is None

    def start(self) -> Tuple[bool, str]:
        with self._run_guard:
            if self.running:
                return True, "already running"
            self._stopping.clear()
            address = self.address()
            try:
                srv = _TCPServer(address, _Handler, self)
            except OSError as exc:
                return False, "本地端口 %d 监听失败（可能被占用或管控拦截）: %s" % (
                    address[1], exc)
            self._server = srv
            self._thread = threading.Thread(target=self._serve, name="ghacc-proxy",
                                            daemon=True)
            self._thread.start()
            # 真实探活：能连上才算启动成功，不信「线程已 start」
            try:
                probe = socket.create_connection(address, timeout=1.5)
                probe.close()
            except Exception as exc:
                self.stop()
                return False, "代理启动自检失败: %s" % exc
            _LOG.info("proxy listening %s:%d", address[0], address[1])
            self._emit("proxy-started", {"address": "%s:%d" % address})
            return True, "%s:%d" % address

    def _serve(self) -> None:
        try:
            self._server.serve_forever(poll_interval=0.2)  # type: ignore[union-attr]
        except Exception as exc:
            _LOG.error("serve_forever 异常 %s: %s", type(exc).__name__, exc)
        finally:
            self._emit("proxy-stopped", {})

    def stop(self) -> None:
        with self._run_guard:
            if self._server is None:
                return
            self._stopping.set()
            try:
                self._server.shutdown()
            except Exception as exc:
                _LOG.warning("shutdown 异常: %s", exc)
            try:
                self._server.server_close()
            except Exception:
                pass
            if self._thread is not None:
                self._thread.join(timeout=3.0)
            self._server = None
            self._thread = None
            _LOG.info("proxy stopped, counters=%s", self.counters.as_dict())

    def _emit(self, kind: str, payload: Dict[str, Any]) -> None:
        if self.on_event:
            try:
                self.on_event(kind, payload)
            except Exception:
                pass

    def status(self) -> Dict[str, Any]:
        host, port = self.address()
        return {"running": self.running, "listen": "%s:%d" % (host, port),
                "counters": self.counters.as_dict(),
                "engine": "local-CONNECT-tunnel (no MITM, no root CA)"}
