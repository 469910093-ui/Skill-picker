"""本机已装索引：catalog → 索引 → 注入 discover.html。

这份索引会被写进一个 HTML 文件，并在看板里被浏览器读走，所以三件事必须锁住：
索引里不能带本机绝对路径（含用户名）、漂移判定要和看板「理技能」tab 一致、
注入必须幂等且不会被 skill 名里的 "<" 提前闭合标签。
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import discover  # noqa: E402


def _skill(dir_name, host, sha, name=None, path=None):
    return {
        "name": name if name is not None else dir_name,
        "dir_name": dir_name,
        "description": "d",
        "keywords": "",
        "category": "c",
        "categories": ["c"],
        "path": path or f"/home/someone/.{host}/skills/{dir_name}/SKILL.md",
        "host": host,
        "root": f"/home/someone/.{host}/skills",
        "sha256": sha,
    }


class LocalSkillIndexTest(unittest.TestCase):
    def test_single_copy(self):
        cat = {"skills": [_skill("work-report", "cursor", "aaa")], "duplicates": {}}
        idx = discover.local_skill_index(cat)
        self.assertEqual(
            idx["names"]["work-report"],
            {"copies": 1, "hosts": ["cursor"], "drifted": False},
        )

    def test_same_dir_and_name_counted_once(self):
        """dir_name 与 frontmatter name 相同是常态，不能把一份 skill 记成两份。"""
        cat = {"skills": [_skill("canvas", "cursor", "aaa", name="canvas")], "duplicates": {}}
        idx = discover.local_skill_index(cat)
        self.assertEqual(idx["names"]["canvas"]["copies"], 1)

    def test_differing_name_registers_both_keys(self):
        """frontmatter name 和目录名不一致时，两个名字都要能被 feed 侧对上。"""
        cat = {
            "skills": [_skill("ai-short-drama", "claude-code", "aaa", name="短剧")],
            "duplicates": {},
        }
        idx = discover.local_skill_index(cat)
        self.assertIn("ai-short-drama", idx["names"])
        self.assertIn("短剧", idx["names"])
        self.assertEqual(idx["names"]["ai-short-drama"]["copies"], 1)
        self.assertEqual(idx["names"]["短剧"]["copies"], 1)

    def test_multi_host_copies_and_hosts_sorted(self):
        cat = {
            "skills": [
                _skill("hyperframes", "openclaw", "aaa"),
                _skill("hyperframes", "claude-code", "aaa"),
                _skill("hyperframes", "cursor", "aaa"),
            ],
            "duplicates": {},
        }
        idx = discover.local_skill_index(cat)
        hit = idx["names"]["hyperframes"]
        self.assertEqual(hit["copies"], 3)
        self.assertEqual(hit["hosts"], ["claude-code", "cursor", "openclaw"])
        self.assertFalse(hit["drifted"])

    def test_case_is_folded(self):
        cat = {"skills": [_skill("Work-Report", "cursor", "aaa", name="Work-Report")], "duplicates": {}}
        idx = discover.local_skill_index(cat)
        self.assertIn("work-report", idx["names"])
        self.assertNotIn("Work-Report", idx["names"])

    def test_drift_comes_from_duplicates_not_recomputed(self):
        """漂移只认 catalog["duplicates"]，不自己比 sha256。

        find_duplicates 会豁免 external_plugins 下不同产品的同名 skill。若这里重算，
        徽标会对看板已经判定「不算重复」的组报漂移，同一个 skill 出现两种说法。
        """
        cat = {
            "skills": [
                _skill("access", "claude-code", "aaa", path="/h/external_plugins/discord/access/SKILL.md"),
                _skill("access", "claude-code", "bbb", path="/h/external_plugins/telegram/access/SKILL.md"),
            ],
            # find_duplicates 豁免了这一组，所以 same_name 里没有它
            "duplicates": {"same_name": []},
        }
        idx = discover.local_skill_index(cat)
        self.assertEqual(idx["names"]["access"]["copies"], 2)
        self.assertFalse(
            idx["names"]["access"]["drifted"],
            "sha256 不同但 catalog 判定不算重复，索引不该自己翻案",
        )

    def test_drift_flagged_when_duplicates_says_so(self):
        cat = {
            "skills": [
                _skill("work-report", "claude-code", "aaa"),
                _skill("work-report", "cursor", "bbb"),
            ],
            "duplicates": {
                "same_name": [{"name": "work-report", "status": "drifted", "copies": []}]
            },
        }
        idx = discover.local_skill_index(cat)
        self.assertTrue(idx["names"]["work-report"]["drifted"])

    def test_identical_status_is_not_drift(self):
        cat = {
            "skills": [
                _skill("canvas", "claude-code", "aaa"),
                _skill("canvas", "cursor", "aaa"),
            ],
            "duplicates": {
                "same_name": [{"name": "canvas", "status": "identical", "copies": []}]
            },
        }
        idx = discover.local_skill_index(cat)
        self.assertEqual(idx["names"]["canvas"]["copies"], 2)
        self.assertFalse(idx["names"]["canvas"]["drifted"])

    def test_drift_propagates_to_the_frontmatter_name_key(self):
        """same_name 按 dir_name 分组，但 feed 可能是按 frontmatter name 对上的。"""
        cat = {
            "skills": [
                _skill("ai-short-drama", "claude-code", "aaa", name="短剧"),
                _skill("ai-short-drama", "cursor", "bbb", name="短剧"),
            ],
            "duplicates": {
                "same_name": [{"name": "ai-short-drama", "status": "drifted", "copies": []}]
            },
        }
        idx = discover.local_skill_index(cat)
        self.assertTrue(idx["names"]["ai-short-drama"]["drifted"])
        self.assertTrue(idx["names"]["短剧"]["drifted"])

    def test_no_local_paths_leak(self):
        """索引进 HTML 文件，本机绝对路径里常带用户名，一个字符都不许出现。"""
        cat = {
            "skills": [_skill("work-report", "cursor", "aaa", path="/home/alice/.cursor/skills/work-report/SKILL.md")],
            "duplicates": {},
        }
        blob = json.dumps(discover.local_skill_index(cat), ensure_ascii=False)
        self.assertNotIn("alice", blob)
        self.assertNotIn("/", blob)
        self.assertNotIn("sha256", blob)
        self.assertNotIn("aaa", blob)

    def test_empty_catalog_yields_empty_index(self):
        self.assertEqual(discover.local_skill_index({}), {"names": {}})

    def test_blank_names_are_dropped(self):
        cat = {"skills": [{"dir_name": "", "name": "", "host": "cursor"}], "duplicates": {}}
        self.assertEqual(discover.local_skill_index(cat), {"names": {}})


class InjectLocalIndexTest(unittest.TestCase):
    HTML = "<html><head><title>t</title></head><body>b</body></html>"

    def _blocks(self, html):
        return re.findall(
            r'<script type="application/json" id="skillpicker-local">(.*?)</script>',
            html,
            re.DOTALL,
        )

    def test_block_lands_before_head_close(self):
        out = discover.inject_local_index(self.HTML, {"names": {"a": {"copies": 1}}})
        self.assertIn("</head>", out)
        self.assertLess(
            out.index('id="skillpicker-local"'),
            out.index("</head>"),
            "数据块必须在 </head> 之前，否则会被塞进 body 影响布局",
        )

    def test_payload_round_trips(self):
        idx = {"names": {"work-report": {"copies": 2, "hosts": ["cursor", "codex"], "drifted": True}}}
        out = discover.inject_local_index(self.HTML, idx)
        blocks = self._blocks(out)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(json.loads(blocks[0]), idx)

    def test_idempotent(self):
        """scan / build_dashboard 会各自同步一次，重复注入不能越堆越多。"""
        out = discover.inject_local_index(self.HTML, {"names": {"a": {"copies": 1}}})
        out2 = discover.inject_local_index(out, {"names": {"b": {"copies": 2}}})
        blocks = self._blocks(out2)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(json.loads(blocks[0]), {"names": {"b": {"copies": 2}}})

    def test_decoy_mention_in_page_script_is_not_eaten(self):
        """页面自己的 JS 里会提到这个 id（读取代码 + 注释）。

        删旧块的正则必须认完整标签，不能只认 id —— 认松了会把整个内联 script 吞掉，
        页面直接白屏，而且是在用户本机才复现。
        """
        page = (
            "<html><head><title>t</title>"
            "<script>const x = document.getElementById('skillpicker-local');"
            "/* id=skillpicker-local type=application/json */ f();</script>"
            "</head><body>b</body></html>"
        )
        out = discover.inject_local_index(page, {"names": {"a": {"copies": 1}}})
        self.assertEqual(len(self._blocks(out)), 1)
        self.assertIn("const x = document.getElementById('skillpicker-local');", out)
        self.assertIn("f();", out)
        # 再来一次仍然只有一个块，且页面 JS 还在
        out2 = discover.inject_local_index(out, {"names": {"b": {"copies": 1}}})
        self.assertEqual(len(self._blocks(out2)), 1)
        self.assertIn("f();", out2)

    def test_angle_bracket_cannot_close_the_tag_early(self):
        """skill 名来自任意 frontmatter，不能假设里面没有 "</script"。

        不变量是 payload 里一个裸 "<" 都不许剩——只要没有 "<"，HTML 解析器就不可能
        在数据块中间认出标签。名字里留着 onerror 这种字样无所谓：它是 JSON 字符串的
        内容，渲染时还要过一遍 escapeHtml。
        """
        idx = {"names": {"</script><img src=x onerror=alert(1)>": {"copies": 1, "hosts": [], "drifted": False}}}
        out = discover.inject_local_index(self.HTML, idx)
        blocks = self._blocks(out)
        self.assertEqual(len(blocks), 1, "标签被提前闭合了")
        self.assertNotIn("<", blocks[0], "payload 里还有裸 <")
        self.assertNotIn("<img", out)
        self.assertNotIn("</script><img", out)
        # 转义后仍要能被 JSON.parse 还原成原始名字
        self.assertEqual(json.loads(blocks[0]), idx)

    def test_no_head_falls_back_to_prepend(self):
        out = discover.inject_local_index("<div>x</div>", {"names": {}})
        self.assertTrue(out.startswith('<script type="application/json"'))
        self.assertIn("<div>x</div>", out)

    def test_deterministic_output(self):
        """同一份 catalog 反复生成必须逐字节一致，否则 discover.html 每次 scan 都在变。"""
        cat = {
            "skills": [
                _skill("b-skill", "cursor", "aaa"),
                _skill("a-skill", "codex", "bbb"),
            ],
            "duplicates": {},
        }
        a = discover.inject_local_index(self.HTML, discover.local_skill_index(cat))
        b = discover.inject_local_index(self.HTML, discover.local_skill_index(cat))
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
