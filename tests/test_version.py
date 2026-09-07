"""版本号一致性测试（零第三方依赖）。

存在的理由：MCP 的 SERVER_INFO 曾长期硬编码 "1.0.0"，而 CHANGELOG 与 GitHub
release 都停在 0.2.1。注册了 MCP 的 agent 宿主拿到的是那个假版本号，对不上任何
一个真实发布，排查问题时会指向错误的代码。这类漂移不会自己暴露，只能靠断言守。
"""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mcp_server  # noqa: E402
from version import __version__  # noqa: E402

CHANGELOG = Path(__file__).resolve().parent.parent / "CHANGELOG.md"
RELEASED_RE = re.compile(r"^##\s*\[(\d+\.\d+\.\d+)\]", re.M)


class TestVersionConsistency(unittest.TestCase):
    def test_semver_shape(self):
        self.assertRegex(__version__, r"^\d+\.\d+\.\d+$")

    def test_mcp_server_info_uses_single_source(self):
        self.assertEqual(mcp_server.SERVER_INFO["version"], __version__)

    def test_matches_latest_released_changelog_entry(self):
        text = CHANGELOG.read_text(encoding="utf-8")
        released = RELEASED_RE.findall(text)
        self.assertTrue(released, "CHANGELOG.md 里找不到任何已发布版本条目")
        self.assertEqual(
            released[0],
            __version__,
            "version.py 与 CHANGELOG 顶部的已发布条目不一致；"
            "未发布的改动应记在 [Unreleased] 下，而不是直接改版本号",
        )


if __name__ == "__main__":
    unittest.main()
