"""黄金用例回归：共享匹配引擎必须对典型意图给出正确 Top 候选。

历史 bug 全部固化为测试：
- 「设计」曾被拆成单字命中麦肯锡/百度地图（修复：查询侧禁单字 + 泛词降权 + 场景置顶）
- 「剪视频」曾打不中英文描述的 video-use（修复：中英近义词扩展）
- 描述含泛词整串命中曾 +0.25 刷分（修复：DF 占比门槛）
"""

import json
import random
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from fixtures import FIXTURE_SKILLS, RULES, build_test_index

import matching


class TestGoldenMatching(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = build_test_index(FIXTURE_SKILLS)

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
        cls.index = build_test_index(FIXTURE_SKILLS)

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


class TestGrabBagDescription(unittest.TestCase):
    """把一堆触发词罗列进 description 的 skill 不该压过名称直接命中的。

    真实机器上 lark-apps（妙搭应用开发）在「设计」上曾拿到 0.7644，压过
    figma 全家。两处原因：整串「设计」在描述里出现又白拿一次 desc_substr
    （描述分已经算过同一条证据）；以及 +0.35 的分类置顶把「描述里提了一句」
    和「名字就叫这个」当成同一档。
    """

    @classmethod
    def setUpClass(cls):
        cls.index = build_test_index(FIXTURE_SKILLS, RULES)

    def _find(self, query, name):
        for r in matching.match(self.index, query, top=len(FIXTURE_SKILLS)):
            if r["name"] == name:
                return r
        return None

    def test_grab_bag_is_still_retrievable(self):
        self.assertIsNotNone(self._find("设计", "lark-apps"),
                             "它确实沾设计，不该被一脚踢出榜，只是不该排第一")

    def test_grab_bag_loses_to_name_matched_skills(self):
        ranked = [r["name"] for r in matching.match(self.index, "设计",
                                                    top=len(FIXTURE_SKILLS))]
        self.assertLess(ranked.index("figma-generate-design"), ranked.index("lark-apps"),
                        f"名称直接命中的必须排在大杂烩前面: {ranked[:6]}")

    def test_description_only_match_gets_a_reduced_category_bonus(self):
        r = self._find("设计", "lark-apps")
        self.assertTrue(r["why"]["cat_pinned"], "它确实被分类点名，否则这条测试没在测东西")
        self.assertLessEqual(r["why"]["name"], RULES["weights"]["field_hit_floor"])
        self.assertLessEqual(r["why"]["kw"], RULES["weights"]["field_hit_floor"])
        full = RULES["weights"]["desc"] * r["why"]["desc"] + RULES["weights"]["cat_pin"]
        self.assertLess(r["score"], full,
                        f"描述里独中却拿到了全额分类置顶: score={r['score']} full={full:.4f}")

    def test_name_matched_skill_keeps_the_full_category_bonus(self):
        r = self._find("设计", "figma-generate-design")
        self.assertGreater(r["why"]["name"], RULES["weights"]["field_hit_floor"],
                           "它是名称命中，不该被当成描述里独中")

    def test_the_compact_query_is_itself_a_scored_token(self):
        """desc_substr 守卫的前提：短中文查询的整串本身就在打分 token 里。

        前提不成立，守卫就永远不触发，上面那些断言也就测不到东西。
        """
        qw = dict(matching.expand_intent(matching.tokenize("设计", query=True), RULES, "设计"))
        self.assertIn("设计", qw)

    def test_the_guard_does_not_kill_substr_for_multiword_queries(self):
        """守卫只挡重复。多词查询的紧凑整串不是打分 token，仍应能加分。"""
        qw = dict(matching.expand_intent(
            matching.tokenize("code connect", query=True), RULES, "codeconnect"))
        self.assertNotIn("codeconnect", qw)


class TestFigjamIsNotDesign(unittest.TestCase):
    """FigJam 是白板产品，不该被「设计」的近义词表拉进来。"""

    @classmethod
    def setUpClass(cls):
        cls.index = build_test_index(FIXTURE_SKILLS)

    def test_figjam_not_in_design_expansion(self):
        for q in ("设计", "平面设计"):
            qw = dict(matching.expand_intent(matching.tokenize(q, query=True), RULES, q))
            self.assertNotIn("figjam", qw, f"「{q}」的近义词里不该有 figjam")

    def test_figjam_does_not_outrank_design_generation(self):
        ranked = [r["name"] for r in matching.match(self.index, "设计",
                                                    top=len(FIXTURE_SKILLS))]
        self.assertIn("figma-use-figjam", ranked, "白板 skill 仍应可被检索到")
        self.assertLess(ranked.index("figma-generate-design"),
                        ranked.index("figma-use-figjam"),
                        f"出图类 skill 必须排在白板前面: {ranked[:6]}")

    def test_figjam_still_reachable_by_its_own_name(self):
        top = [r["name"] for r in matching.match(self.index, "figjam", top=3)]
        self.assertIn("figma-use-figjam", top, f"按自己的名字应能搜到: {top}")


class TestTieOrderIsDeterministic(unittest.TestCase):
    """并列条目的先后不得取决于 catalog 的输入顺序（即文件系统扫描顺序）。

    真实机器上「设计」一个查询就有 5 组并列、共 12 个条目；不定序意味着
    同一份 catalog 换台机器就能给出不同 topN，黄金用例也就成了抛硬币。
    """

    def test_shuffling_input_does_not_change_topn(self):
        seen = set()
        for seed in range(16):
            skills = list(FIXTURE_SKILLS)
            random.Random(seed).shuffle(skills)
            index = build_test_index(skills, RULES)
            seen.add(tuple(r["dir_name"] for r in matching.match(index, "设计", top=6)))
        self.assertEqual(len(seen), 1, f"输入顺序改变了 topN，出现 {len(seen)} 种结果: {seen}")

    def test_tied_entries_are_ordered_by_dir_name(self):
        index = build_test_index(FIXTURE_SKILLS, RULES)
        ranked = matching.match(index, "设计", top=len(FIXTURE_SKILLS))
        groups: dict[float, list] = {}
        for r in ranked:
            groups.setdefault(r["score"], []).append(r["dir_name"])
        tied = [v for v in groups.values() if len(v) > 1]
        self.assertTrue(tied, "fixture 里应存在并列，否则这条测试没在测东西")
        for names in tied:
            self.assertEqual(names, sorted(names), f"并列组未按 dir_name 排序: {names}")

    def test_golden_is_stable_under_shuffling(self):
        for seed in range(8):
            skills = list(FIXTURE_SKILLS)
            random.Random(seed).shuffle(skills)
            index = build_test_index(skills, RULES)
            failed = [g for g in matching.run_golden(index, RULES) if g["status"] == "fail"]
            self.assertEqual(failed, [], f"seed={seed} 下黄金用例失败: {failed}")


class TestEnginesStayInSync(unittest.TestCase):
    """源码级存在性检查：挡住「只改了 Python 那边」这种最常见的漂移。

    真正的数值一致由下面 TestEnginesAgreeNumerically 保证；这两条便宜、
    没有 node 也能跑，留着当第一道岗。
    """

    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parent.parent
        cls.js = (root / "dashboard.py").read_text(encoding="utf-8")
        cls.py = (root / "matching.py").read_text(encoding="utf-8")

    def test_both_engines_guard_desc_substr(self):
        self.assertIn("q_compact not in scored_toks", self.py)
        self.assertIn("!scoredToks.has(qCompact)", self.js)

    def test_both_engines_break_ties(self):
        self.assertIn('(-x["score"], x["dir_name"])', self.py)
        self.assertIn("dirMap", self.js)

    def test_js_payload_carries_the_tiebreak_key(self):
        self.assertIn('"dir": m["dir_name"]', self.js,
                      "JS 侧并列兜底要用 dir_name，payload 必须带上它")

    def test_neither_engine_hardcodes_the_field_hit_floor(self):
        """阈值写死时行为看不出差别（它此刻正好等于 0.08），改配置才会翻车。

        所以只能盯源码：0.08 一旦回到打分路径里，rules.json 就管不住它了。
        """
        for label, src, marker in (("matching.py", self.py, 'floor = w["field_hit_floor"]'),
                                   ("dashboard.py", self.js, "const floor = W.field_hit_floor;")):
            with self.subTest(f=label):
                self.assertIn(marker, src, f"{label} 的交叉门槛要从 rules 取")
                self.assertNotIn("0.08", src,
                                 f"{label} 里还有写死的 0.08，改 rules.json 管不到它")


class TestFieldHitFloorIsHonoured(unittest.TestCase):
    """交叉命中的门槛必须真的来自 rules，不是碰巧和写死值相等。"""

    def _score(self, floor, name, query="剪视频"):
        rules = json.loads(json.dumps(RULES))
        rules["weights"]["field_hit_floor"] = floor
        index = build_test_index(FIXTURE_SKILLS, rules)
        for r in matching.match(index, query, top=len(FIXTURE_SKILLS)):
            if r["name"] == name:
                return r["score"]
        return 0.0

    def test_raising_the_floor_withdraws_the_cross_bonus(self):
        low = self._score(0.0, "video-use")
        high = self._score(0.99, "video-use")
        self.assertGreater(low, 0, "基线就没分，这条测试测不到东西")
        self.assertLess(high, low,
                        "把门槛抬到 0.99 后分数没降——交叉加成没在读 field_hit_floor")


NODE = shutil.which("node")


@unittest.skipUnless(NODE, "需要 node")
class TestEnginesAgreeNumerically(unittest.TestCase):
    """同一份 skill、同一个查询，两套引擎必须算出同一个排名和同一个分数。

    正则只能证明「两边都写了这行」，证不了「两边算出同一个数」。这里把
    dashboard.py 模板里的打分片段原样切出来喂 node，跟 matching.py 对数。
    切片而非重抄是关键：抄一份就等于又造了第三个实现。
    """

    QUERIES = ["设计", "剪视频", "写周报", "figma", "code connect", "画个图表",
               "写飞书文档", "做一个ppt"]
    TRANSLATIONS = {
        "video-use": {"zh": "对话式剪辑任意视频：转写、切分、调色、生成叠加动画、烧字幕"},
        "work-report": {"en": "Scan local work directories and summarise recent commits "
                              "into a structured Chinese work report, weekly or daily."},
    }

    @classmethod
    def setUpClass(cls):
        import dashboard
        src = dashboard.PAGE  # 必须取运行时常量：源码里的 \\u4e00 是 Python 转义

        def cut(start, end, keep_end=False):
            i = src.index(start)
            j = src.index(end, i)
            return src[i:j + (len(end) if keep_end else 0)]

        # 跳过中间那段界面文案 + localStorage（node 里没有）
        prelude = (cut("const stripStop =", "// 界面文案双语")
                   + cut("const N = SKILLS.length;", "function matchedRuns"))
        scoring = cut("  const qToks = tokenize(q, {query: true});", ".slice(0, 4);",
                      keep_end=True)

        # 用假译文库，但必须两个方向都覆盖到（中文原文配英译、英文原文配中译），
        # 否则 bilingual() 里的分支只走一半，双语口径的分歧测不出来
        cls.index = build_test_index(translations=cls.TRANSLATIONS)
        js_skills = [{
            "name": m["name"], "dir": m["dir_name"],
            # 逐字复刻 build_dashboard 里 js_data 的构造：双语拼接走共享的
            # matching.bilingual（索引时已写入 desc_zh/desc_en），且一律送全文。
            # 这里改回截断或改回单语，下面的对数就会挂——那正是它要守的东西。
            "descZh": m["desc_zh"], "descEn": m["desc_en"],
            "cat": m["category"], "cats": m.get("categories", [m["category"]]),
            "kw": m["keywords"],
        } for m in cls.index.skills]
        js_rules = {k: RULES[k] for k in
                    ("syn", "weak_syn", "design_triggers", "design_fallback",
                     "stopwords", "weights")}

        harness = (f"const SKILLS = {json.dumps(js_skills, ensure_ascii=False)};\n"
                   f"const R = {json.dumps(js_rules, ensure_ascii=False)};\n"
                   "const W = R.weights;\n"
                   f"{prelude}\nconst OUT = {{}};\n"
                   f"for (const q0 of {json.dumps(cls.QUERIES, ensure_ascii=False)}) {{\n"
                   # 与 render() 里那行逐字一致：先去停用词，去光了再退回原串
                   "  const q = norm(stripStop(q0.trim())) || norm(q0.trim());\n"
                   f"{scoring}\n"
                   "  OUT[q0] = scored.map(x => [x.s.name, Math.round(x.score * 1e4) / 1e4]);\n"
                   "}\nconsole.log(JSON.stringify(OUT));\n")

        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "parity.mjs"
            f.write_text(harness, encoding="utf-8")
            proc = subprocess.run([NODE, str(f)], capture_output=True, text=True,
                                  encoding="utf-8")
        if proc.returncode:
            raise AssertionError(f"JS 引擎跑挂了，先修它：\n{proc.stderr[-1500:]}")
        cls.js_out = json.loads(proc.stdout)

    def _py_top(self, q):
        floor = RULES["weights"]["min_score"]
        return [(r["name"], round(r["score"], 4))
                for r in matching.match(self.index, q, top=len(FIXTURE_SKILLS))
                if r["score"] > floor][:4]

    def test_same_ranking(self):
        for q in self.QUERIES:
            with self.subTest(q=q):
                py = [n for n, _ in self._py_top(q)]
                js = [n for n, _ in self.js_out[q]]
                self.assertEqual(py, js, f"「{q}」两套引擎排名不同")

    def test_same_scores(self):
        for q in self.QUERIES:
            with self.subTest(q=q):
                py, js = self._py_top(q), self.js_out[q]
                self.assertEqual(len(py), len(js), f"「{q}」入榜条数不同")
                for (pn, ps), (jn, js_) in zip(py, js):
                    self.assertAlmostEqual(
                        ps, js_, places=3, msg=f"「{q}」{pn}/{jn} 分数不同")

    def test_the_harness_can_actually_fail(self):
        """守住 harness 本身：切片没切到东西时上面两条会空跑成绿。"""
        self.assertTrue(any(self.js_out[q] for q in self.QUERIES),
                        "JS 侧一条都没排出来，切片多半没切对")

    def test_the_fake_translations_actually_reach_the_index(self):
        """假译文若没喂进去，双语这一半就没在测，上面的对数会退化成单语比对。"""
        vu = next(s for s in self.index.skills if s["name"] == "video-use")
        self.assertIn("剪辑", vu["desc_zh"], "英文 skill 应拿到中译")
        self.assertIn("对话式剪辑", vu["_dt"], "中译必须进索引文本，否则中文意图打不中")
        wr = next(s for s in self.index.skills if s["name"] == "work-report")
        self.assertIn("weekly", wr["_dt"], "中文 skill 的英译必须进索引")


class TestIndexTextIsNotTruncated(unittest.TestCase):
    """索引字段必须送全文。

    截断曾切掉半数 skill 的内容（146/280 描述超 220、210/280 关键词超 300，
    共丢 4136 个 token），而 CLI 用的是全文，于是同一个查询在看板和会话内
    给出不同答案。展示需要短文本是另一件事，在 JS 的 sDesc() 里截。
    """

    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parent.parent
        cls.src = (root / "dashboard.py").read_text(encoding="utf-8")

    def test_payload_does_not_slice_the_indexed_fields(self):
        for field in ('m["desc_zh"]', 'm["desc_en"]', 'm["keywords"]', 'm["description"]'):
            with self.subTest(field=field):
                self.assertNotRegex(
                    self.src, re.escape(field) + r"\[:\d+\]",
                    f"{field} 又被截断了；索引要全文，展示长度请在 sDesc() 里限")

    def test_display_still_caps_the_length(self):
        """全文进 payload 后，推荐卡必须自己截，否则 1000 字描述会撑爆卡片。"""
        self.assertIn("DESC_SHOWN", self.src)
        self.assertRegex(self.src, r"t\.slice\(0, DESC_SHOWN\)")

    def test_dashboard_reuses_the_shared_bilingual_helpers(self):
        """看板不许自己再写一份中文判定或译文加载。

        `_is_zh` 的阈值决定哪一栏算原文、哪一栏算译文，也就决定索引文本；
        两处各写一份，同一个 skill 在看板和 CLI 上会被判成不同语言。
        """
        import dashboard
        self.assertIs(dashboard._is_zh, matching.is_zh)
        self.assertIs(dashboard.load_translations, matching.load_translations)

    def test_both_sides_concat_bilingual_the_same_way(self):
        """缺译文时不能把同一段文字拼两遍——token 集合不变，但 _dt 会翻倍。"""
        self.assertIn("en && en !== zh ? zh + ' ' + en : zh", self.src)
        py = (Path(__file__).resolve().parent.parent / "matching.py").read_text(encoding="utf-8")
        self.assertIn('desc = zh if en == zh else f"{zh} {en}"', py)


class TestBilingualIndexing(unittest.TestCase):
    """双语索引：中文意图要能打中只写英文描述的 skill，反之亦然。"""

    # 查询词刻意选「调色」：它不在 rules.json 的近义词表里，所以命中只能来自
    # 译文本身。用「剪辑」「字幕」这类表里已有的词，近义词扩展会自己桥到英文
    # 描述上，测试就变成绿的但什么也没证明。
    TR = {"video-use": {"zh": "对话式剪辑任意视频：转写、切分、调色、烧字幕"}}
    QUERY = "调色"

    def test_the_query_has_no_synonym_bridge(self):
        """前提检查：这个词一旦进了近义词表，下面那条测试就失去意义。"""
        blob = " ".join(list(RULES["syn"]) + list(RULES["syn"].values()))
        self.assertNotIn(self.QUERY, blob,
                         f"「{self.QUERY}」进了近义词表，请换一个无桥的词")

    def test_chinese_translation_makes_an_english_skill_reachable(self):
        mono = build_test_index()
        bi = build_test_index(translations=self.TR)
        self.assertNotIn("video-use",
                         [r["name"] for r in matching.match(mono, self.QUERY)],
                         "无译文时本来打不中，这条测试的前提就在这")
        self.assertIn("video-use",
                      [r["name"] for r in matching.match(bi, self.QUERY)],
                      "喂了中译却还打不中，双语索引没生效")

    def test_missing_translation_falls_back_to_the_original(self):
        zh, en = matching.bilingual({"description": "扫描本地工作目录并汇总近期改动",
                                     "dir_name": "nope"}, {})
        self.assertEqual(zh, en, "缺译文时两栏都应退回原文")

    def test_translations_are_keyed_by_dir_name_lowercased(self):
        s = {"description": "Edit any video by conversation.", "dir_name": "Video-Use"}
        zh, _ = matching.bilingual(s, {"video-use": {"zh": "对话式剪辑"}})
        self.assertEqual(zh, "对话式剪辑", "键要折小写，否则大小写不同就查不到译文")

    def test_the_default_reads_the_real_translation_file(self):
        """产品路径必须默认吃同一份译文库；默认值一变，CLI 与看板就又分家了。"""
        import inspect
        sig = inspect.signature(matching.build_index)
        self.assertIsNone(sig.parameters["translations"].default,
                          "默认值要保持 None（= 读盘），别改成 {}")
        src = inspect.getsource(matching.build_index)
        self.assertIn("load_translations()", src)


if __name__ == "__main__":
    unittest.main()
