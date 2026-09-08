"""工具副本门禁（G0）测试：家目录那份是否与克隆一致、且没缺件。

存在的理由是两个真实事故：

1. `scan` 不刷工具副本，只有 `install` 会。产品的真实调用路径全是 ~/.skill-picker
   （meta-skill、MCP 注册项、catalog.md 里写的命令），所以 `git pull` 之后不重装，
   家目录跑的还是旧代码 —— 已经修好的匹配回归会继续 FAIL，门禁再按 AGENTS.md 把
   用户打发去仓库报 issue。
2. `version.py` 从来没进 TOOL_FILES，而 mcp_server.py 导它。家目录那份 MCP 服务器
   一启动就 ModuleNotFoundError，而 Cursor 规则的第一步正是调 MCP skill_dashboard。
   没有任何测试断言过「拷过去的那份能不能 import」，所以它一直没被发现。
"""

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dashboard  # noqa: E402
import skillpick  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
# G0 能给出的全部状态。看板与命令行都按 status 直接下标，缺键就是崩。
STATUSES = ("pass", "warn", "fail", "skip")


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class ToolFilesCoverImportsTest(unittest.TestCase):
    """仓库级不变量：拷过去的文件集合必须自洽，否则家目录那份 import 就断。"""

    def test_tool_files_cover_every_local_import(self):
        closure = skillpick.local_import_closure(skillpick.TOOL_FILES, REPO)
        missing = sorted(closure - set(skillpick.TOOL_FILES))
        self.assertEqual(
            missing, [],
            f"这些本地模块被工具文件 import 但不在 TOOL_FILES 里：{missing}；"
            "它们不会被拷到 ~/.skill-picker，那份副本会 ModuleNotFoundError",
        )

    def test_version_py_is_carried(self):
        # 单独钉住：这是真实翻过车的那一个
        self.assertIn("version.py", skillpick.TOOL_FILES)

    def test_listed_tool_files_all_exist_in_repo(self):
        missing = [f for f in skillpick.TOOL_FILES if not (REPO / f).exists()]
        self.assertEqual(missing, [], f"TOOL_FILES 列了仓库里不存在的文件：{missing}")


class LocalImportClosureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.src = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_follows_transitive_local_imports(self):
        write(self.src / "a.py", "import b\n")
        write(self.src / "b.py", "import c\n")
        write(self.src / "c.py", "x = 1\n")
        self.assertEqual(skillpick.local_import_closure(["a.py"], self.src),
                         {"a.py", "b.py", "c.py"})

    def test_catches_imports_nested_in_functions(self):
        # cmd_scan 里的 discover / dashboard 就是这么延迟导入的，只看顶层会漏
        write(self.src / "a.py", "def f():\n    from b import g\n    return g\n")
        write(self.src / "b.py", "def g():\n    pass\n")
        self.assertEqual(skillpick.local_import_closure(["a.py"], self.src), {"a.py", "b.py"})

    def test_from_import_is_followed(self):
        write(self.src / "a.py", "from b import thing\n")
        write(self.src / "b.py", "thing = 1\n")
        self.assertIn("b.py", skillpick.local_import_closure(["a.py"], self.src))

    def test_stdlib_and_third_party_are_ignored(self):
        write(self.src / "a.py", "import json, os\nimport numpy\nfrom pathlib import Path\n")
        self.assertEqual(skillpick.local_import_closure(["a.py"], self.src), {"a.py"})

    def test_dotted_import_resolves_on_the_top_package(self):
        write(self.src / "a.py", "import b.sub\n")
        write(self.src / "b.py", "x = 1\n")
        self.assertIn("b.py", skillpick.local_import_closure(["a.py"], self.src))

    def test_import_cycle_terminates(self):
        write(self.src / "a.py", "import b\n")
        write(self.src / "b.py", "import a\n")
        self.assertEqual(skillpick.local_import_closure(["a.py"], self.src), {"a.py", "b.py"})

    def test_non_python_entries_are_skipped(self):
        write(self.src / "rules.json", "{}\n")
        self.assertEqual(skillpick.local_import_closure(["rules.json"], self.src), set())

    def test_unparsable_file_does_not_raise(self):
        write(self.src / "a.py", "def broken(:\n")
        self.assertEqual(skillpick.local_import_closure(["a.py"], self.src), {"a.py"})

    def test_missing_entry_does_not_raise(self):
        self.assertEqual(skillpick.local_import_closure(["nope.py"], self.src), {"nope.py"})


class ToolCopyDriftTest(unittest.TestCase):
    """G0 的判定。全部在临时目录里跑，不碰真实的 ~/.skill-picker。"""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        root = Path(self.tmp.name)
        self.src, self.data = root / "clone", root / "home"
        self.addCleanup(self.tmp.cleanup)
        # 造一份最小克隆：skillpick.py 是定位克隆用的锚，rules.json 代表数据文件
        write(self.src / "skillpick.py", "import matching\n")
        write(self.src / "matching.py", "x = 1\n")
        write(self.src / "rules.json", "{}\n")
        self.files = ["skillpick.py", "matching.py", "rules.json"]
        self._patch_tool_files(self.files)

    def _patch_tool_files(self, files):
        original = skillpick.TOOL_FILES
        skillpick.TOOL_FILES = list(files)
        self.addCleanup(lambda: setattr(skillpick, "TOOL_FILES", original))

    def install(self):
        copied = skillpick.copy_tools(self.src, self.data)
        skillpick.write_install_manifest(self.src, copied, data_dir=self.data)
        return copied

    def drift(self, running=None):
        return skillpick.tool_copy_drift(data_dir=self.data, running_dir=running or self.src)

    # ---- 一致 / 过期 / 缺件

    def test_fresh_install_passes(self):
        self.install()
        self.assertEqual(self.drift()["status"], "pass")

    def test_stale_copy_fails_and_names_the_file(self):
        self.install()
        write(self.src / "matching.py", "x = 2  # 克隆往前走了一步\n")
        gate = self.drift()
        self.assertEqual(gate["status"], "fail")
        self.assertTrue(any("matching.py" in it for it in gate["items"]), gate["items"])
        self.assertNotIn("rules.json", " ".join(gate["items"]))

    def test_missing_copy_fails(self):
        self.install()
        (self.data / "matching.py").unlink()
        gate = self.drift()
        self.assertEqual(gate["status"], "fail")
        self.assertTrue(any("matching.py" in it for it in gate["items"]))

    def test_data_file_drift_is_caught_too(self):
        # rules.json 不是代码，但打分权重与黄金用例都在里面，过期一样会让 G4 说谎
        self.install()
        write(self.src / "rules.json", '{"weights": {}}\n')
        self.assertEqual(self.drift()["status"], "fail")

    def test_unlisted_local_dependency_is_caught(self):
        """version.py 那一类：被 import 但没进 TOOL_FILES，家目录因此缺件。"""
        write(self.src / "skillpick.py", "import matching\nimport version\n")
        write(self.src / "version.py", "__version__ = '1.0.0'\n")
        # 名单里故意不加 version.py，模拟当时的状态
        skillpick.copy_tools(self.src, self.data)   # copy_tools 会自己补上
        (self.data / "version.py").unlink()          # 手工制造出当时的缺件现场
        gate = self.drift()
        self.assertEqual(gate["status"], "fail")
        self.assertTrue(any("version.py" in it for it in gate["items"]), gate["items"])

    def test_copy_tools_carries_unlisted_local_dependency(self):
        write(self.src / "skillpick.py", "import matching\nimport version\n")
        write(self.src / "version.py", "__version__ = '1.0.0'\n")
        copied = skillpick.copy_tools(self.src, self.data)
        self.assertIn("version.py", copied)
        self.assertTrue((self.data / "version.py").exists())

    # ---- 就地改动

    def test_in_place_edit_matching_the_clone_only_warns(self):
        self.install()
        # 副本被手工改成与克隆一致之外的历史：哈希对不上安装记录，但内容与克隆相同
        manifest = self.data / "install.json"
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        payload["files"]["matching.py"] = "0" * 64
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        gate = self.drift()
        self.assertEqual(gate["status"], "warn")
        self.assertTrue(any("matching.py" in it for it in gate["items"]))

    def test_in_place_edit_diverging_from_clone_fails(self):
        self.install()
        write(self.data / "matching.py", "x = 99  # 直接改了家目录那份\n")
        gate = self.drift()
        self.assertEqual(gate["status"], "fail")
        joined = " ".join(gate["items"])
        self.assertIn("≠ 克隆", joined)
        self.assertIn("安装时记录", joined)

    # ---- 无法判定时必须 skip，不能诬告

    def test_no_copies_yet_skips(self):
        self.assertEqual(self.drift()["status"], "skip")

    def test_running_from_home_without_manifest_skips(self):
        skillpick.copy_tools(self.src, self.data)   # 老版本装的：没有清单
        gate = self.drift(running=self.data)
        self.assertEqual(gate["status"], "skip")
        self.assertIn("install", gate["detail"])

    def test_deleted_clone_skips(self):
        self.install()
        for f in list(self.src.iterdir()):
            f.unlink()
        gate = self.drift(running=self.data)
        self.assertEqual(gate["status"], "skip")

    def test_home_run_locates_the_clone_through_the_manifest(self):
        self.install()
        self.assertEqual(self.drift(running=self.data)["status"], "pass")
        write(self.src / "matching.py", "x = 2\n")
        self.assertEqual(self.drift(running=self.data)["status"], "fail")

    def test_clone_run_ignores_a_bogus_manifest_source(self):
        self.install()
        manifest = self.data / "install.json"
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        payload["source_dir"] = str(self.src / "nonexistent")
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        # 正从克隆跑，源就是自己，不该去信清单里的路径
        self.assertEqual(self.drift()["status"], "pass")

    # ---- 措辞与形状

    def test_failure_tells_the_user_to_reinstall_not_to_file_an_issue(self):
        self.install()
        write(self.src / "matching.py", "x = 2\n")
        gate = self.drift()
        self.assertIn("install", gate["action"])
        self.assertIn("issue", gate["action"])       # 明确写「别去报 issue」
        self.assertIn("别去报 issue", gate["action"])

    def test_gate_shape_matches_the_other_gates(self):
        self.install()
        gate = self.drift()
        self.assertEqual(gate["id"], "G0")
        for key in ("id", "name", "status", "detail", "items", "action"):
            self.assertIn(key, gate)
        self.assertIsInstance(gate["items"], list)

    def test_every_status_is_renderable(self):
        self.assertIn(self.drift()["status"], STATUSES)


class ManifestTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        root = Path(self.tmp.name)
        self.src, self.data = root / "clone", root / "home"
        self.addCleanup(self.tmp.cleanup)
        write(self.src / "skillpick.py", "x = 1\n")
        self.data.mkdir(parents=True)

    def test_records_source_and_hashes(self):
        skillpick.copy_tools(self.src, self.data)
        path = skillpick.write_install_manifest(self.src, ["skillpick.py"], data_dir=self.data)
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["source_dir"], str(self.src))
        self.assertEqual(payload["files"]["skillpick.py"],
                         skillpick._sha256(self.data / "skillpick.py"))
        self.assertTrue(payload["installed_at"])

    def test_installing_from_home_keeps_pointing_at_the_clone(self):
        skillpick.copy_tools(self.src, self.data)
        skillpick.write_install_manifest(self.src, ["skillpick.py"], data_dir=self.data)
        # 从 ~/.skill-picker 自己跑 install：source_dir 若被改成自己，G0 就永远比不出漂移
        skillpick.write_install_manifest(self.data, ["skillpick.py"], data_dir=self.data)
        payload = json.loads((self.data / "install.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["source_dir"], str(self.src))

    def test_unreadable_manifest_reads_as_empty(self):
        write(self.data / "install.json", "not json{")
        self.assertEqual(skillpick.read_install_manifest(self.data / "install.json"), {})

    def test_copy_tools_onto_itself_does_not_raise(self):
        # AGENTS.md 让用户一切都走 ~ 路径，所以 install 也会从家目录跑；
        # copy2(src, src) 在 Windows 上抛 PermissionError，POSIX 上抛 SameFileError
        skillpick.copy_tools(self.src, self.data)
        self.assertEqual(skillpick.copy_tools(self.data, self.data), ["skillpick.py"])


class GateStatusRenderingTest(unittest.TestCase):
    """G0 引入了第四种状态 skip，两个渲染处都是按 status 直接下标的。"""

    def test_dashboard_covers_every_status(self):
        for status in STATUSES:
            self.assertIn(status, dashboard.GATE_COLORS)
            self.assertIn(status, dashboard.GATE_MARK)

    def test_dashboard_has_an_english_label_for_g0(self):
        self.assertIn("G0", dashboard.GATE_EN)

    def test_print_gates_handles_every_status(self):
        gates = [{"id": "G0", "name": "工具副本", "status": s, "detail": "d", "items": [],
                  "action": ""} for s in STATUSES]
        with redirect_stdout(io.StringIO()) as out:
            skillpick.print_gates(gates)   # 缺键会 KeyError
        self.assertEqual(len(out.getvalue().strip().splitlines()), len(STATUSES))

    def test_run_gates_puts_tool_copies_first(self):
        # G0 决定其余门禁的结论可不可信，必须先报；顺序错了用户会先看到 G4 FAIL 就去报 issue
        source = (REPO / "skillpick.py").read_text(encoding="utf-8")
        self.assertIn("gates = [tool_copy_drift()", source)


if __name__ == "__main__":
    unittest.main()
