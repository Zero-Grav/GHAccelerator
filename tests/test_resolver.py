# -*- coding: utf-8 -*-
"""优选决策与失败回退单元测试（不触碰外网）。"""
import time
import unittest
from pathlib import Path

from ghacc.config import Config
from ghacc.resolver import Decision, Resolver


def _loopback_config():
    return Config(
        settings=dict(Config.load().settings, **{
            "probe_tls_verify": False, "probe_timeout_ms": 300,
            "connect_timeout_s": 1.0, "cache_ttl_s": 60, "log_to_file": False,
        }),
        rules={"version": 1,
               "domains": [{"host": "test.local", "enabled": True, "pool": "loopback"}],
               "ip_pools": {"loopback": ["127.0.0.1"]}, "fallback": "direct"},
        base_dir=Path(__file__).resolve().parents[1],
    )


class TestResolver(unittest.TestCase):

    def test_non_whitelist_returns_none(self):
        resolver = Resolver(_loopback_config())
        self.assertIsNone(resolver.select("example.com"))

    def test_force_fail_returns_none_and_counts_failure(self):
        resolver = Resolver(_loopback_config())
        resolver.set_force_fail(True)
        self.assertIsNone(resolver.select("test.local"))
        self.assertEqual(1, resolver.stats()["failures"])

    def test_force_fail_is_reversible(self):
        resolver = Resolver(_loopback_config())
        resolver.set_force_fail(True)
        self.assertIsNone(resolver.select("test.local"))
        resolver.set_force_fail(False)
        self.assertFalse(resolver.stats()["force_fail"])

    def test_cache_hit_does_not_reprobe(self):
        resolver = Resolver(_loopback_config())
        resolver._cache["test.local:443"] = Decision(
            "test.local", "127.0.0.1", 443, 1.0, False, "unit-test")
        decision = resolver.select("test.local")
        self.assertIsNotNone(decision)
        self.assertEqual("127.0.0.1", decision.ip)
        self.assertEqual(1, resolver.stats()["hits"])

    def test_expired_cache_is_evicted(self):
        resolver = Resolver(_loopback_config())
        old = Decision("test.local", "127.0.0.1", 443, 1.0, False, "unit-test")
        old.ts = time.time() - 3600
        resolver._cache["test.local:443"] = old
        resolver.set_force_fail(True)
        self.assertIsNone(resolver.select("test.local"))

    def test_invalidate_clears_cache(self):
        resolver = Resolver(_loopback_config())
        resolver._cache["test.local:443"] = Decision(
            "test.local", "127.0.0.1", 443, 1.0, False, "unit-test")
        resolver.invalidate()
        self.assertEqual(0, resolver.stats()["cached"])

    def test_decisions_serializable(self):
        resolver = Resolver(_loopback_config())
        resolver._cache["test.local:443"] = Decision(
            "test.local", "127.0.0.1", 443, 1.0, True, "unit-test")
        rows = resolver.decisions()
        self.assertEqual(1, len(rows))
        self.assertEqual("127.0.0.1", rows[0]["ip"])
        self.assertTrue(rows[0]["tls_ok"])


if __name__ == "__main__":
    unittest.main()
