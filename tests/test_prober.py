# -*- coding: utf-8 -*-
"""T6 应用层健康断言单元测试（纯函数，不依赖公网）。"""
import unittest

from ghacc.ip_pool import is_official_ip
from ghacc.prober import ProbeResult, check_http_response


class TestHttpAssertion(unittest.TestCase):

    def test_normal_responses_pass(self):
        for status, body in ((200, b"<html>github</html>"),
                            (301, b""), (302, b"redirect"),
                            (404, b"Not Found"),
                            (415, b'{"message":"Requires accept header"}'),
                            (400, b"Bad Request")):
            ok, _ = check_http_response(status, body)
            self.assertTrue(ok, "status %d should pass" % status)

    def test_risk_pages_rejected(self):
        for body in (b"Whoa there! You have sent an invalid request.",
                     b"Please do not send this request again.",
                     b"rate limit exceeded",
                     b"abuse detection",
                     b"access denied"):
            ok, reason = check_http_response(200, body)
            self.assertFalse(ok)
            self.assertIn("risk-page", reason)

    def test_bad_status_rejected(self):
        for status in (403, 429, 500, 502, 503):
            ok, _ = check_http_response(status, b"")
            self.assertFalse(ok, "status %d should be rejected" % status)

    def test_timeout_has_no_status_line(self):
        ok, reason = check_http_response(None, b"")
        self.assertFalse(ok)
        self.assertIn("no-status-line", reason)

    def test_probe_result_exposes_http_ok(self):
        r = ProbeResult("1.2.3.4", True, 12.5, True, True, "tls+http")
        d = r.as_dict()
        self.assertTrue(d["ok"])
        self.assertTrue(d["tls_ok"])
        self.assertTrue(d["http_ok"])

    def test_official_cidr_validation(self):
        self.assertTrue(is_official_ip("140.82.113.10", ["140.82.112.0/20"]))
        self.assertFalse(is_official_ip("1.2.3.4", ["140.82.112.0/20"]))
        self.assertTrue(is_official_ip("185.199.108.133", ["185.199.108.0/22"]))
        # IPv6 段被忽略，不影响 IPv4 判定
        self.assertFalse(is_official_ip("140.82.113.10", ["::1/128"]))


if __name__ == "__main__":
    unittest.main()
