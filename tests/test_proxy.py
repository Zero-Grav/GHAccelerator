# -*- coding: utf-8 -*-
"""本地代理回环冒烟：正向断言 + 反向断言（全部不依赖公网）。"""
import socket
import threading
import unittest
from pathlib import Path

from ghacc.config import Config
from ghacc.proxy_server import ProxyServer
from ghacc.resolver import Resolver


def free_port():
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


class EchoServer(object):
    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        while not self.stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except Exception:
                return
            try:
                conn.settimeout(5)
                while True:
                    data = conn.recv(4096)
                    if not data:
                        break
                    conn.sendall(data)
            except Exception:
                pass
            finally:
                conn.close()

    def close(self):
        self.stop.set()
        try:
            self.sock.close()
        except Exception:
            pass
        self.thread.join(timeout=2)


def connect_through(proxy_port, host, port, payload=b"PING"):
    s = None
    try:
        s = socket.create_connection(("127.0.0.1", proxy_port), timeout=4)
        s.settimeout(5)
        s.sendall(("CONNECT %s:%d HTTP/1.1\r\nHost: %s:%d\r\n\r\n"
                   % (host, port, host, port)).encode())
        head = s.recv(4096).decode("iso-8859-1", "replace")
        if "200 Connection Established" not in head:
            return False, head.split("\r\n")[0]
        s.sendall(payload)
        echoed = s.recv(4096)
        return echoed == payload, "200"
    except Exception as exc:
        return False, "%s: %s" % (type(exc).__name__, exc)
    finally:
        if s is not None:
            try:
                s.close()
            except Exception:
                pass


class TestProxy(unittest.TestCase):

    def setUp(self):
        self.echo = EchoServer()
        self.port = free_port()
        self.cfg = Config(
            settings=dict(Config.load().settings, **{
                "listen_host": "127.0.0.1", "listen_port": self.port,
                "probe_tls_verify": False, "probe_timeout_ms": 300,
                "connect_timeout_s": 1.0, "log_to_file": False,
                "set_system_proxy": False, "mode": "proxy",
            }),
            rules={"version": 1,
                   "domains": [{"host": "test.local", "enabled": True, "pool": "loopback"}],
                   "ip_pools": {"loopback": ["127.0.0.1"]}, "fallback": "direct"},
            base_dir=Path(__file__).resolve().parents[1],
        )
        self.resolver = Resolver(self.cfg)
        self.server = ProxyServer(self.cfg, self.resolver)
        ok, msg = self.server.start()
        self.assertTrue(ok, msg)

    def tearDown(self):
        self.server.stop()
        self.echo.close()

    def test_whitelist_uses_preferred_ip(self):
        # test.local 无 DNS 记录，能通则证明走的是 127.0.0.1 优选 IP
        ok, status = connect_through(self.port, "test.local", self.echo.port)
        self.assertTrue(ok, status)
        counters = self.server.counters.as_dict()
        self.assertGreaterEqual(counters["accelerated"], 1)
        self.assertGreater(counters["bytes_up"], 0)

    def test_non_whitelist_direct_passthrough(self):
        ok, status = connect_through(self.port, "127.0.0.1", self.echo.port, b"DIRECT")
        self.assertTrue(ok, status)
        self.assertGreaterEqual(self.server.counters.as_dict()["direct"], 1)

    def test_reverse_all_candidates_fail_still_alive(self):
        self.resolver.set_force_fail(True)
        ok, status = connect_through(self.port, "test.local", self.echo.port)
        self.assertFalse(ok)
        self.assertIn("502", status)
        self.assertTrue(self.server.running)

    def test_reverse_upstream_unreachable_returns_502(self):
        ok, status = connect_through(self.port, "127.0.0.1", 1)
        self.assertFalse(ok)
        self.assertIn("502", status)
        self.assertTrue(self.server.running)

    def test_stop_is_idempotent(self):
        self.server.stop()
        self.server.stop()
        self.assertFalse(self.server.running)


if __name__ == "__main__":
    unittest.main()
