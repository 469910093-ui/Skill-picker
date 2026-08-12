"""MCP server 协议处理与注册幂等性的单元测试（零第三方依赖）。"""

import json
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fixtures import FIXTURE_SKILLS  # noqa: E402

import matching  # noqa: E402
import mcp_server  # noqa: E402
import skillpick  # noqa: E402


class _FixtureServer(mcp_server.McpServer):
    """注入 fixture catalog，避免依赖本机真实环境。"""

    def __init__(self):
        super().__init__()
        self._catalog = {"skills": FIXTURE_SKILLS, "gates": [
            {"id": "G1", "status": "pass", "detail": "fixture"},
        ]}
        self._index = matching.build_index(FIXTURE_SKILLS)


class TestMcpProtocol(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = _FixtureServer()

    def _call(self, method, params=None, msg_id=1):
        return self.server.handle(
            {"jsonrpc": "2.0", "id": msg_id, "method": method,
             "params": params or {}})

    def test_initialize(self):
        resp = self._call("initialize", {"protocolVersion": "2024-11-05"})
        self.assertEqual(resp["id"], 1)
        self.assertIn("serverInfo", resp["result"])
        self.assertEqual(resp["result"]["serverInfo"]["name"], "skill-picker")
        self.assertIn("tools", resp["result"]["capabilities"])

    def test_notification_returns_none(self):
        resp = self.server.handle({"jsonrpc": "2.0",
                                   "method": "notifications/initialized"})
        self.assertIsNone(resp, "通知不应有响应")

    def test_tools_list(self):
        resp = self._call("tools/list")
        names = [t["name"] for t in resp["result"]["tools"]]
        self.assertEqual(sorted(names), ["skill_dashboard", "skill_match"])
        for t in resp["result"]["tools"]:
            self.assertIn("inputSchema", t)
            self.assertTrue(t["description"])

    def test_skill_match_tool(self):
        # 避免单测拉起真实 serve：桩掉 dashboard 探测
        self.server.tool_skill_dashboard = lambda args: {
            "url": "http://127.0.0.1:8471/dashboard.html?q=x",
            "fallback_file": "D:/fake/dashboard.html",
            "dashboard_required": True,
            "agent_must": "HARD GATE",
            "note": "test",
        }
        resp = self._call("tools/call",
                          {"name": "skill_match",
                           "arguments": {"query": "剪视频", "top": 3}})
        self.assertNotIn("error", resp)
        payload = json.loads(resp["result"]["content"][0]["text"])
        names = [r["name"] for r in payload["results"]]
        self.assertIn("video-use", names, f"「剪视频」应命中 video-use: {names}")
        self.assertTrue(payload["gates"], "输出必须携带门禁状态")
        self.assertTrue(payload.get("dashboard_required"),
                        "match 必须标记 dashboard_required")
        self.assertTrue(payload.get("dashboard_url"),
                        "match 必须附带 dashboard_url")
        self.assertIn("HARD GATE", payload.get("agent_must", ""))

    def test_skill_match_empty_query(self):
        resp = self._call("tools/call",
                          {"name": "skill_match", "arguments": {"query": " "}})
        payload = json.loads(resp["result"]["content"][0]["text"])
        self.assertIn("error", payload)

    def test_unknown_tool(self):
        resp = self._call("tools/call", {"name": "nope", "arguments": {}})
        self.assertIn("error", resp)
        self.assertEqual(resp["error"]["code"], -32602)

    def test_unknown_method(self):
        resp = self._call("does/not/exist")
        self.assertEqual(resp["error"]["code"], -32601)

    def test_ping(self):
        self.assertEqual(self._call("ping")["result"], {})


class TestDashboardUrlWithIntent(unittest.TestCase):
    def test_query_param_not_hash(self):
        url = mcp_server.dashboard_url_with_intent(
            "http://127.0.0.1:8471/dashboard.html", "剪视频", bust=False)
        self.assertIn("?q=", url)
        self.assertNotIn("#q=", url)
        self.assertIn(urllib.parse.quote("剪视频"), url)

    def test_bust_forces_reload_param(self):
        url = mcp_server.dashboard_url_with_intent(
            "http://127.0.0.1:8471/dashboard.html", "做周报", bust=True)
        self.assertIn("&_=", url)
        self.assertIn("q=", url)

    def test_replaces_old_hash_and_q(self):
        url = mcp_server.dashboard_url_with_intent(
            "http://127.0.0.1:8471/dashboard.html?q=old#_=1", "新意图", bust=False)
        self.assertIn(urllib.parse.quote("新意图"), url)
        self.assertNotIn("q=old", url)
        self.assertFalse(url.rstrip("/").endswith("#q=old") or "#q=" in url)

    def test_empty_intent_keeps_base(self):
        base = "http://127.0.0.1:8471/dashboard.html"
        self.assertEqual(
            mcp_server.dashboard_url_with_intent(base, "", bust=True), base)

    def test_pending_intent_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            prev = mcp_server.PENDING_INTENT_JSON
            prev_dir = mcp_server.DATA_DIR
            try:
                mcp_server.DATA_DIR = Path(td)
                mcp_server.PENDING_INTENT_JSON = Path(td) / "pending_intent.json"
                mcp_server.save_pending_intent("数据分析", source="test")
                self.assertEqual(mcp_server.load_pending_intent(), "数据分析")
            finally:
                mcp_server.PENDING_INTENT_JSON = prev
                mcp_server.DATA_DIR = prev_dir


class TestMcpRegistration(unittest.TestCase):
    def test_json_register_idempotent_and_preserving(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "mcp.json"
            cfg.write_text(json.dumps({
                "mcpServers": {"existing-server": {"command": "npx",
                                                   "args": ["-y", "foo"]}}
            }), encoding="utf-8")
            s1 = skillpick.register_mcp_json(cfg)
            self.assertIn("created", s1)
            s2 = skillpick.register_mcp_json(cfg)
            self.assertEqual(s2, "unchanged", "重复注册必须幂等")
            data = json.loads(cfg.read_text(encoding="utf-8"))
            self.assertIn("existing-server", data["mcpServers"],
                          "不得覆盖用户已有配置")
            self.assertIn("skill-picker", data["mcpServers"])
            self.assertTrue(data["mcpServers"]["skill-picker"]["args"][0]
                            .endswith("mcp_server.py"))

    def test_json_register_new_file(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "mcp.json"
            self.assertIn("created", skillpick.register_mcp_json(cfg))
            self.assertIn("skill-picker",
                          json.loads(cfg.read_text(encoding="utf-8"))["mcpServers"])

    def test_json_register_broken_file_untouched(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "mcp.json"
            cfg.write_text("{ not valid json", encoding="utf-8")
            self.assertIn("skipped", skillpick.register_mcp_json(cfg))
            self.assertEqual(cfg.read_text(encoding="utf-8"), "{ not valid json",
                             "解析失败时不得改动用户文件")

    def test_toml_register_idempotent_and_appending(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "config.toml"
            original = '[mcp_servers.node_repl]\ncommand = "node"\n'
            cfg.write_text(original, encoding="utf-8")
            s1 = skillpick.register_mcp_toml(cfg)
            self.assertIn("created", s1)
            s2 = skillpick.register_mcp_toml(cfg)
            self.assertEqual(s2, "unchanged", "重复注册必须幂等")
            text = cfg.read_text(encoding="utf-8")
            self.assertTrue(text.startswith(original), "追加不得破坏用户已有配置")
            self.assertIn('[mcp_servers."skill-picker"]', text)
            self.assertIn("mcp_server.py", text)


class TestCursorRuleInstall(unittest.TestCase):
    def test_cursor_rule_template_has_wake_phrases(self):
        text = skillpick.CURSOR_RULE_TEMPLATE
        self.assertIn("alwaysApply: true", text)
        for phrase in (
            "帮我选个 skill", "用哪个 skill", "有没有 skill", "帮我找找",
            "本机有没有", "你会", "最适配", "整理一下我当前安装的所有 skills",
            "理技能", "skill_match", "open_resource",
        ):
            self.assertIn(phrase, text, f"规则须含唤起/步骤关键词: {phrase}")

    def test_meta_skill_description_has_expanded_wake_phrases(self):
        text = skillpick.META_SKILL_TEMPLATE
        for phrase in (
            "帮我看看本机有没有", "帮我找找", "最适配",
            "整理一下我当前安装的所有 skills", "你会",
        ):
            self.assertIn(phrase, text, f"meta-skill 须含唤起语: {phrase}")


if __name__ == "__main__":
    unittest.main()
