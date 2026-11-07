# -*- coding: utf-8 -*-
"""配置与白名单匹配单元测试。"""
import json
import tempfile
import unittest
from pathlib import Path

from ghacc.config import Config, match_rule


class TestConfig(unittest.TestCase):

    def setUp(self):
        self.cfg = Config.load()

    def test_default_config_is_valid(self):
        self.assertEqual([], self.cfg.validate())

    def test_shipped_json_config_is_valid(self):
        root = Path(__file__).resolve().parents[1]
        loaded = Config.load(root)
        self.assertEqual([], loaded.validate())
        self.assertTrue(loaded.loaded_dir is not None)
        self.assertGreaterEqual(len(loaded.enabled_rules()), 9)

    def test_exact_and_wildcard_match(self):
        self.assertTrue(match_rule("github.com", "github.com"))
        self.assertTrue(match_rule("github.com", "GITHUB.COM"))
        self.assertTrue(match_rule("*.githubusercontent.com", "raw.githubusercontent.com"))
        self.assertTrue(match_rule("*.githubusercontent.com", "a.b.githubusercontent.com"))
        self.assertFalse(match_rule("github.com", "evil-github.com"))
        self.assertFalse(match_rule("*.github.io", "notgithub.io"))

    def test_exact_rule_wins_over_wildcard(self):
        rule = self.cfg.rule_for("raw.githubusercontent.com")
        self.assertEqual("raw.githubusercontent.com", rule.get("host"))

    def test_pool_routing(self):
        self.assertEqual("github_cdn", self.cfg.pool_for("raw.githubusercontent.com")[0])
        self.assertEqual("github_api", self.cfg.pool_for("api.github.com")[0])
        self.assertEqual("github_web", self.cfg.pool_for("github.com")[0])

    def test_non_target_is_not_intercepted(self):
        self.assertFalse(self.cfg.is_target("example.com"))
        self.assertFalse(self.cfg.is_target("github.com.evil.com"))

    def test_validate_rejects_non_loopback(self):
        self.cfg.set_setting("listen_host", "0.0.0.0")
        problems = self.cfg.validate()
        self.assertTrue(any("回环" in p for p in problems))

    def test_validate_rejects_bad_port_and_ip(self):
        self.cfg.set_setting("listen_port", 99999)
        self.cfg.rules["ip_pools"]["github_web"].append("999.999.999.999")
        problems = self.cfg.validate()
        self.assertTrue(any("listen_port" in p for p in problems))
        self.assertTrue(any("非法 IPv4" in p for p in problems))

    def test_save_roundtrip_to_writable_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config.load()
            cfg.writable_dir = Path(tmp)
            cfg.set_setting("listen_port", 19999)
            ok, where = cfg.save()
            self.assertTrue(ok)
            data = json.loads((Path(tmp) / "settings.json").read_text(encoding="utf-8"))
            self.assertEqual(19999, data["listen_port"])
            self.assertTrue((Path(tmp) / "rules.json").is_file())

    def test_save_failure_returns_reason_not_exception(self):
        cfg = Config.load()
        cfg.writable_dir = None
        ok, reason = cfg.save()
        self.assertFalse(ok)
        self.assertIn("纯内存", reason)


if __name__ == "__main__":
    unittest.main()
