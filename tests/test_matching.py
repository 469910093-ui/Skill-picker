"""黄金用例回归：共享匹配引擎必须对典型意图给出正确 Top 候选。

历史 bug 全部固化为测试：
- 「设计」曾被拆成单字命中麦肯锡/百度地图（修复：查询侧禁单字 + 泛词降权 + 场景置顶）
- 「剪视频」曾打不中英文描述的 video-use（修复：中英近义词扩展）
- 描述含泛词整串命中曾 +0.25 刷分（修复：DF 占比门槛）
"""

import unittest

from fixtures import FIXTURE_SKILLS, RULES

import matching


class TestGoldenMatching(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = matching.build_index(FIXTURE_SKILLS, RULES)

    def _top(self, query, n=4):
        return [r["name"] for r in matching.match(self.index, query, top=n)]

    def test_design_hits_figma_first(self):
        top = self._top("设计", 3)
        self.assertTrue(any(name.startswith("figma-") or name == "figma-use" for name in top[:1]),
                        f"「设计」第一名应为 figma 系，实际 {top}")
        self.assertNotIn("mckinsey-consultant", top, f"麦肯锡不应进「设计」前三: {top}")
        self.assertNotIn("baidu-ai-map", top, f"百度地图不应进「设计」前三: {top}")

    def test_graphic_design(self):
        top = self._top("平面设计", 4)
        self.assertTrue(any(n.startswith("figma") for n in top),
                        f"「平面设计」前四应含 figma 系: {top}")

    def test_cut_video_cross_language(self):
        top = self._top("剪视频", 3)
        self.assertIn("video-use", top, f"「剪视频」前三应含 video-use（英文描述）: {top}")

    def test_weekly_report(self):
        top = self._top("我要做一份周报", 4)
        self.assertTrue({"work-report", "ibu-html-weekly-overview",
                         "gochina-weekly-review"} & set(top),
                        f"「周报」前四应含周报类 skill: {top}")

    def test_feishu_doc(self):
        top = self._top("写飞书文档", 3)
        self.assertIn("lark-doc", top, f"「写飞书文档」前三应含 lark-doc: {top}")
        self.assertNotEqual(top[0], "lark-calendar", "日历不应压过文档")

    def test_ppt(self):
        top = self._top("做一个ppt", 4)
        self.assertTrue({"html-ppt", "guizang-ppt-skill"} & set(top),
                        f"「做一个ppt」前四应含 PPT skill: {top}")

    def test_chart(self):
        top = self._top("画个图表", 3)
        self.assertIn("chart-visualization", top, f"「画个图表」前三应含图表 skill: {top}")

    def test_no_match_returns_empty(self):
        self.assertEqual(matching.match(self.index, "量子退火炼丹", top=4), [],
                         "无关意图应返回空，而不是硬凑")

    def test_stopwords_stripped(self):
        with_stop = self._top("帮我做一份周报", 4)
        without_stop = self._top("周报", 4)
        self.assertTrue(set(with_stop) & set(without_stop),
                        "停用词不应改变匹配主体")

    def test_run_golden_uses_fixture(self):
        report = matching.run_golden(self.index, RULES)
        executed = [g for g in report if g["status"] != "skip"]
        self.assertTrue(executed, "fixture 至少应执行部分黄金用例")
        failed = [g for g in executed if g["status"] == "fail"]
        self.assertEqual(failed, [], f"黄金用例失败: {failed}")


class TestMatchMechanics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = matching.build_index(FIXTURE_SKILLS, RULES)

    def test_query_tokenize_no_single_cjk(self):
        toks = matching.tokenize("设计", query=True)
        self.assertNotIn("设", toks)
        self.assertNotIn("计", toks)
        self.assertIn("设计", toks)

    def test_doc_tokenize_keeps_single_cjk(self):
        toks = matching.tokenize("设计", query=False)
        self.assertIn("设", toks)
        self.assertIn("设计", toks)

    def test_expand_intent_weak_syn_discounted(self):
        qw = dict(matching.expand_intent({"设计"}, RULES))
        self.assertIn("figma", qw)
        self.assertGreater(qw["figma"], qw.get("design", 0),
                           "强近义词 figma 权重必须高于泛词 design")

    def test_merge_copies(self):
        skills = FIXTURE_SKILLS + [dict(FIXTURE_SKILLS[0],
                                        host="cursor",
                                        path="C:/fake/cursor/figma-generate-design/SKILL.md")]
        merged = matching.merge_copies(skills)
        target = [m for m in merged if m["name"] == "figma-generate-design"]
        self.assertEqual(len(target), 1, "同名副本必须合并为一条")
        self.assertEqual(sorted(target[0]["hosts"]), ["claude-code", "cursor"])

    def test_result_shape(self):
        r = matching.match(self.index, "剪视频", top=1)[0]
        for key in ("name", "score", "category", "categories", "hosts", "copies",
                    "description", "why"):
            self.assertIn(key, r)


if __name__ == "__main__":
    unittest.main()
