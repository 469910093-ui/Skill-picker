"""frontmatter / 关键词提炼 / 回退解析的单元测试。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import skillpick  # noqa: E402


class TestParseFrontmatter(unittest.TestCase):
    def test_multiline_folded(self):
        text = "---\nname: foo\ndescription: >-\n  第一行\n  第二行 continues\n---\n# Body\n"
        fm = skillpick.parse_frontmatter(text)
        self.assertEqual(fm["name"], "foo")
        self.assertEqual(fm["description"], "第一行 第二行 continues")

    def test_no_frontmatter(self):
        self.assertEqual(skillpick.parse_frontmatter("# Just a title\ncontent"), {})

    def test_unclosed_frontmatter(self):
        self.assertEqual(skillpick.parse_frontmatter("---\nname: x\n(no closing)"), {})


class TestFallbackMeta(unittest.TestCase):
    def test_title_and_first_paragraph(self):
        meta = skillpick.fallback_meta("# My Skill\n\n这是描述第一行。\n这是第二行。\n\n## 后续")
        self.assertEqual(meta["name"], "My Skill")
        self.assertIn("这是描述第一行。", meta["description"])
        self.assertNotIn("后续", meta["description"])


class TestExtractKeywords(unittest.TestCase):
    def test_headings_bold_code(self):
        text = ("---\nname: x\ndescription: y\n---\n"
                "# 主标题\n\n## 二级章节\n\n**加粗要点** 普通文字 `run_tool.py` 更多\n")
        kw = skillpick.extract_keywords(text)
        self.assertIn("主标题", kw)
        self.assertIn("二级章节", kw)
        self.assertIn("加粗要点", kw)
        self.assertIn("run_tool.py", kw)

    def test_length_cap(self):
        text = "# t\n" + "\n".join(f"## 章节{i}标题内容较长一些" for i in range(100))
        self.assertLessEqual(len(skillpick.extract_keywords(text)), 400)


class TestPluginFamily(unittest.TestCase):
    def test_same_family(self):
        a = skillpick._plugin_family(r"C:\u\.cursor\plugins\cache\pub\aws\hash\skills\a\SKILL.md")
        b = skillpick._plugin_family(r"C:\u\.cursor\plugins\cache\pub\aws\hash\skills\b\SKILL.md")
        self.assertEqual(a, b)

    def test_non_plugin_path(self):
        self.assertIsNone(skillpick._plugin_family(r"C:\u\.claude\skills\foo\SKILL.md"))


if __name__ == "__main__":
    unittest.main()
