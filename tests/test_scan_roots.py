"""扫描根测试：~/.config 下的宿主按约定自动发现，且不能重复进表。

存在的理由：`SCAN_ROOTS` 只写死了 `~/.config/opencode/skills`，而本机后来又冒出
crush / devin / goose 三个宿主，同样是 `<host>/skills` 布局，共 33 个 SKILL.md
完全在扫描根之外 —— 对匹配不可见，G1 报 FAIL，还得让用户手工补 extra_roots。
写死名单挡不住下一个新宿主，所以改成按约定发现。
"""

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import skillpick  # noqa: E402


class DiscoverConfigRootsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def host(self, name: str, with_skills: bool = True) -> Path:
        d = self.base / name
        (d / "skills" if with_skills else d).mkdir(parents=True)
        return d

    def test_finds_hosts_and_labels_them_by_directory_name(self):
        self.host("crush")
        self.host("goose")
        found = skillpick.discover_config_roots(self.base)
        self.assertEqual([(self.base / "crush" / "skills", "crush"),
                          (self.base / "goose" / "skills", "goose")], found)

    def test_host_without_a_skills_dir_is_ignored(self):
        self.host("configstore", with_skills=False)
        self.assertEqual(skillpick.discover_config_roots(self.base), [])

    def test_dotted_directories_are_ignored(self):
        self.host(".cache")
        self.assertEqual(skillpick.discover_config_roots(self.base), [])

    def test_pruned_names_are_ignored(self):
        self.host("node_modules")
        self.assertEqual(skillpick.discover_config_roots(self.base), [])

    def test_a_file_named_skills_is_not_a_root(self):
        d = self.base / "weird"
        d.mkdir()
        (d / "skills").write_text("not a dir", encoding="utf-8")
        self.assertEqual(skillpick.discover_config_roots(self.base), [])

    def test_missing_base_returns_empty(self):
        self.assertEqual(skillpick.discover_config_roots(self.base / "nope"), [])

    def test_order_is_deterministic(self):
        for name in ("zeta", "alpha", "mid"):
            self.host(name)
        names = [h for _, h in skillpick.discover_config_roots(self.base)]
        self.assertEqual(names, sorted(names))

    def test_nested_layout_is_found_and_still_labelled_by_host(self):
        # kimchi 用的是 <host>/harness/skills
        (self.base / "kimchi" / "harness" / "skills").mkdir(parents=True)
        self.assertEqual(skillpick.discover_config_roots(self.base),
                         [(self.base / "kimchi" / "harness" / "skills", "kimchi")])

    def test_both_layouts_can_coexist_for_one_host(self):
        d = self.base / "hybrid"
        (d / "skills").mkdir(parents=True)
        (d / "harness" / "skills").mkdir(parents=True)
        found = skillpick.discover_config_roots(self.base)
        self.assertEqual([h for _, h in found], ["hybrid", "hybrid"])
        self.assertEqual(len(found), 2)

    def test_does_not_descend_three_levels(self):
        # 再深就会把项目暂存区里解包出来的东西当成已装 skill
        (self.base / "host" / "a" / "b" / "skills").mkdir(parents=True)
        self.assertEqual(skillpick.discover_config_roots(self.base), [])

    def test_pruned_subdirectory_is_not_descended(self):
        (self.base / "host" / "node_modules" / "skills").mkdir(parents=True)
        self.assertEqual(skillpick.discover_config_roots(self.base), [])


class DiscoverAllSkillFilesTest(unittest.TestCase):
    """覆盖率的地面真值不该把 agent 的项目暂存区算进来。"""

    def walk(self, base: Path) -> list[str]:
        original = skillpick.DISCOVER_BASES
        skillpick.DISCOVER_BASES = [base]
        try:
            return [p.parent.name for p in skillpick.discover_all_skill_files()]
        finally:
            skillpick.DISCOVER_BASES = original

    def test_project_scratch_is_pruned(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            for rel in ("skills/real", "projects/empty-window/_extract_build/guided"):
                d = base / rel
                d.mkdir(parents=True)
                (d / "SKILL.md").write_text("x", encoding="utf-8")
            self.assertEqual(self.walk(base), ["real"])


class LoadScanRootsTest(unittest.TestCase):
    def test_no_duplicate_paths(self):
        # 同一个目录进两次会让每个 skill 多出一份副本，把同名多份与漂移统计一起做脏
        keys = [os.path.normcase(str(p)) for p, _ in skillpick.load_scan_roots()]
        dupes = {k for k in keys if keys.count(k) > 1}
        self.assertEqual(dupes, set(), f"扫描根重复：{dupes}")

    def test_config_hosts_are_no_longer_hardcoded(self):
        # 写死一个 opencode 就是当初漏掉 crush/devin/goose 的原因
        hardcoded = [p for p, _ in skillpick.SCAN_ROOTS
                     if skillpick.CONFIG_HOST_BASE in p.parents]
        self.assertEqual(hardcoded, [], f"~/.config 下的根应交给约定发现：{hardcoded}")

    def test_discovered_hosts_reach_the_scan_roots(self):
        discovered = skillpick.discover_config_roots()
        if not discovered:
            self.skipTest("本机 ~/.config 下没有 <host>/skills 布局的宿主")
        loaded = {os.path.normcase(str(p)) for p, _ in skillpick.load_scan_roots()}
        for path, host in discovered:
            self.assertIn(os.path.normcase(str(path)), loaded, f"{host} 没进扫描根")


if __name__ == "__main__":
    unittest.main()
