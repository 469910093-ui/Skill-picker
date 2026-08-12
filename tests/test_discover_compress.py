"""短关键词压缩与 discover URL。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import discover  # noqa: E402
import mcp_server  # noqa: E402


class CompressIntentTest(unittest.TestCase):
    def test_max_two_keys(self):
        q = discover.compress_intent_query(
            "帮我设计一个跟团选品工具的UI，需要调用本机 skills",
            max_keys=2,
        )
        parts = q.split()
        self.assertLessEqual(len(parts), 2, q)
        self.assertTrue(parts, q)
        # 应抓住选品 / UI 这类实体，而不是整句
        blob = q.lower()
        self.assertTrue(
            "选品" in q or "ui" in blob,
            f"expected 选品/UI in {q!r}",
        )

    def test_already_short(self):
        self.assertEqual(discover.compress_intent_query("剪视频"), "剪视频")
        self.assertEqual(discover.compress_intent_query("UI 选品"), "UI 选品")

    def test_dashboard_url_uses_short_q(self):
        url = mcp_server.dashboard_url_with_intent(
            "http://127.0.0.1:8471/dashboard.html",
            "帮我用本机 skills 做一个跟团选品工具的交互 UI 设计方案",
            bust=False,
        )
        q = parse_qs(urlparse(url).query).get("q", [""])[0]
        self.assertLessEqual(len(q.split()), 2, q)
        self.assertNotIn("帮我", q)

    def test_empty_match_can_open_discover_tab(self):
        url = mcp_server.dashboard_url_with_intent(
            "http://127.0.0.1:8471/dashboard.html",
            "选品 UI",
            bust=False,
            tab="discover",
        )
        qs = parse_qs(urlparse(url).query)
        self.assertEqual(qs.get("tab"), ["discover"])


if __name__ == "__main__":
    unittest.main()
