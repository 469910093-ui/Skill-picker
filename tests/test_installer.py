"""一键安装的单元测试。整条链路除下载外全部离线：zip 包在内存里现造。

重点不在「顺利装上」，而在「装不上的时候不许乱写」：同名不覆盖、跳目录、符号
链接、大小写撞名、体积炸弹、以及回滚只碰自己写过的文件。
"""

import io
import json
import shutil
import sys
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import installer  # noqa: E402
import skillpick  # noqa: E402

SKILL_MD_TEXT = """---
name: demo-skill
description: 一个用来测试的 skill
---

# demo

正文。
"""


def make_zip(files: dict, prefix: str = "repo-main/", comment: bytes = b"",
             symlinks=()) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        if comment:
            archive.comment = comment
        for name, payload in files.items():
            data = payload.encode("utf-8") if isinstance(payload, str) else payload
            info = zipfile.ZipInfo("placeholder")
            # 构造函数会在 Windows 上把 \ 归一成 /，直接赋值才能造出恶意包的原样路径
            info.filename = prefix + name
            info.external_attr = 0o644 << 16
            if name in symlinks:
                info.create_system = 3
                info.external_attr = 0o120777 << 16
            archive.writestr(info, data)
    return buffer.getvalue()


def simple_zip(**extra) -> bytes:
    files = {"SKILL.md": SKILL_MD_TEXT, "README.md": "读我"}
    files.update(extra)
    return make_zip(files)


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="skillpick-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = self.tmp / "skills"
        self.root.mkdir()
        self.ledger = self.tmp / "installed.json"

    def build_plan(self, archive=None, subdir="", host="cursor", **kwargs):
        source = installer.parse_source("https://github.com/owner/repo")
        package = installer.read_package(archive or simple_zip(), subdir)
        return installer.plan_install(source, "HEAD", package, host, self.root, **kwargs)


# ---------------------------------------------------------------- 来源解析

class ParseSourceTest(unittest.TestCase):
    def test_plain_repo_url(self):
        source = installer.parse_source("https://github.com/owner/repo")
        self.assertEqual((source.owner, source.repo), ("owner", "repo"))
        self.assertIsNone(source.ref)
        self.assertEqual(source.subdir, "")

    def test_tree_url_carries_ref_and_subdir(self):
        source = installer.parse_source(
            "https://github.com/owner/repo/tree/main/skills/foo")
        self.assertEqual(source.ref, "main")
        self.assertEqual(source.subdir, "skills/foo")

    def test_blob_url_pointing_at_the_file_falls_back_to_its_directory(self):
        source = installer.parse_source(
            "https://github.com/o/r/blob/dev/packages/a/SKILL.md")
        self.assertEqual(source.ref, "dev")
        self.assertEqual(source.subdir, "packages/a")

    def test_raw_url(self):
        source = installer.parse_source(
            "https://raw.githubusercontent.com/o/r/main/skills/x/SKILL.md")
        self.assertEqual((source.owner, source.repo, source.ref), ("o", "r", "main"))
        self.assertEqual(source.subdir, "skills/x")

    def test_shorthand(self):
        source = installer.parse_source("owner/repo")
        self.assertEqual((source.owner, source.repo), ("owner", "repo"))

    def test_bare_host_without_scheme(self):
        source = installer.parse_source("github.com/owner/repo/tree/main/a")
        self.assertEqual((source.owner, source.ref, source.subdir), ("owner", "main", "a"))

    def test_ssh_remote(self):
        source = installer.parse_source("git@github.com:owner/repo.git")
        self.assertEqual((source.owner, source.repo), ("owner", "repo"))

    def test_dot_git_suffix_is_dropped(self):
        self.assertEqual(installer.parse_source("https://github.com/o/r.git").repo, "r")

    def test_query_and_fragment_are_dropped(self):
        source = installer.parse_source("https://github.com/o/r/tree/main/a?tab=readme#x")
        self.assertEqual(source.subdir, "a")

    def test_trailing_slash(self):
        self.assertEqual(installer.parse_source("https://github.com/o/r/").repo, "r")

    def test_repo_url_is_rebuilt_canonically(self):
        source = installer.parse_source("git@github.com:o/r.git")
        self.assertEqual(source.repo_url, "https://github.com/o/r")

    def test_other_forges_are_refused(self):
        with self.assertRaises(installer.InstallError) as caught:
            installer.parse_source("https://gitlab.com/o/r")
        self.assertIn("只认 github.com", str(caught.exception))

    def test_empty_input_says_what_to_paste(self):
        with self.assertRaises(installer.InstallError) as caught:
            installer.parse_source("  ")
        self.assertIn("github.com", str(caught.exception))

    def test_repo_only_is_not_enough(self):
        with self.assertRaises(installer.InstallError):
            installer.parse_source("https://github.com/owner")

    def test_unknown_path_shape_is_refused(self):
        with self.assertRaises(installer.InstallError) as caught:
            installer.parse_source("https://github.com/o/r/issues/12")
        self.assertIn("--path", str(caught.exception))

    def test_tree_without_a_branch_is_refused(self):
        with self.assertRaises(installer.InstallError):
            installer.parse_source("https://github.com/o/r/tree")

    def test_hostile_owner_name_is_refused(self):
        with self.assertRaises(installer.InstallError):
            installer.parse_source("https://github.com/../etc/passwd")

    def test_archive_url_shape(self):
        source = installer.parse_source("owner/repo")
        self.assertEqual(source.archive_url("HEAD"),
                         "https://codeload.github.com/owner/repo/zip/HEAD")


# ---------------------------------------------------------------- 下载

class DownloadTest(unittest.TestCase):
    def setUp(self):
        self.source = installer.parse_source("owner/repo")

    def test_explicit_ref_is_used_verbatim(self):
        seen = []

        def opener(url):
            seen.append(url)
            return b"zip"

        data, ref = installer.download_archive(self.source, "v2", opener=opener)
        self.assertEqual((data, ref), (b"zip", "v2"))
        self.assertEqual(seen, ["https://codeload.github.com/owner/repo/zip/v2"])

    def test_url_ref_is_used_when_no_flag(self):
        source = installer.parse_source("https://github.com/o/r/tree/dev/a")
        _, ref = installer.download_archive(source, opener=lambda url: b"z")
        self.assertEqual(ref, "dev")

    def test_falls_through_the_ref_candidates(self):
        tried = []

        def opener(url):
            tried.append(url.rsplit("/", 1)[-1])
            if len(tried) < 3:
                raise installer.InstallError("404")
            return b"zip"

        _, ref = installer.download_archive(self.source, opener=opener)
        self.assertEqual(tried, list(installer.REF_CANDIDATES))
        self.assertEqual(ref, "master")

    def test_all_candidates_failing_reports_every_attempt(self):
        def opener(url):
            raise installer.InstallError("HTTP 404")

        with self.assertRaises(installer.InstallError) as caught:
            installer.download_archive(self.source, opener=opener)
        message = str(caught.exception)
        for candidate in installer.REF_CANDIDATES:
            self.assertIn(candidate, message)

    def test_oversized_content_length_is_refused_before_reading(self):
        class Response:
            headers = {"Content-Length": str(installer.MAX_ARCHIVE_BYTES + 1)}

            def read(self, *a):
                raise AssertionError("不该读，声明的体积就已经超限了")

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with mock.patch("urllib.request.urlopen", return_value=Response()):
            with self.assertRaises(installer.InstallError) as caught:
                installer._urlopen("https://codeload.github.com/o/r/zip/HEAD")
        self.assertIn("上限", str(caught.exception))

    def test_body_larger_than_the_cap_is_refused(self):
        class Response:
            headers = {}

            def read(self, size):
                return b"x" * size

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with mock.patch("urllib.request.urlopen", return_value=Response()):
            with self.assertRaises(installer.InstallError):
                installer._urlopen("https://codeload.github.com/o/r/zip/HEAD")


# ---------------------------------------------------------------- 解包

class ReadPackageTest(unittest.TestCase):
    def test_root_level_skill(self):
        package = installer.read_package(simple_zip())
        self.assertEqual(package.name, "demo-skill")
        self.assertEqual(package.description, "一个用来测试的 skill")
        self.assertEqual(sorted(package.files), ["README.md", "SKILL.md"])

    def test_uses_the_repo_frontmatter_parser_by_default(self):
        # 别在这里另写一个 frontmatter 解析器：两套解析必然漂移
        package = installer.read_package(simple_zip())
        self.assertEqual(package.name, skillpick.parse_frontmatter(SKILL_MD_TEXT)["name"])

    def test_falls_back_to_the_first_heading_without_frontmatter(self):
        package = installer.read_package(make_zip({"SKILL.md": "# 标题\n\n第一段。\n"}))
        self.assertEqual(package.name, "标题")

    def test_single_nested_skill_is_found_without_a_path_flag(self):
        package = installer.read_package(make_zip({"skills/foo/SKILL.md": SKILL_MD_TEXT}))
        self.assertEqual(package.dir_name, "foo")
        self.assertEqual(package.subdir, "skills/foo")

    def test_monorepo_demands_an_explicit_choice(self):
        archive = make_zip({"skills/a/SKILL.md": SKILL_MD_TEXT,
                            "skills/b/SKILL.md": SKILL_MD_TEXT})
        with self.assertRaises(installer.Ambiguous) as caught:
            installer.read_package(archive)
        self.assertEqual(caught.exception.candidates, ["skills/a", "skills/b"])

    def test_explicit_path_picks_one_out_of_a_monorepo(self):
        archive = make_zip({"skills/a/SKILL.md": SKILL_MD_TEXT,
                            "skills/a/ref.md": "参考",
                            "skills/b/SKILL.md": SKILL_MD_TEXT})
        package = installer.read_package(archive, "skills/a")
        self.assertEqual(sorted(package.files), ["SKILL.md", "ref.md"])
        self.assertEqual(package.dir_name, "a")

    def test_path_to_a_parent_directory_narrows_when_unambiguous(self):
        archive = make_zip({"skills/only/SKILL.md": SKILL_MD_TEXT})
        self.assertEqual(installer.read_package(archive, "skills").subdir, "skills/only")

    def test_path_to_a_parent_directory_with_several_children_is_ambiguous(self):
        archive = make_zip({"skills/a/SKILL.md": SKILL_MD_TEXT,
                            "skills/b/SKILL.md": SKILL_MD_TEXT})
        with self.assertRaises(installer.Ambiguous):
            installer.read_package(archive, "skills")

    def test_path_is_matched_case_insensitively(self):
        archive = make_zip({"Skills/Foo/SKILL.md": SKILL_MD_TEXT})
        self.assertEqual(installer.read_package(archive, "skills/foo").dir_name, "Foo")

    def test_missing_path_names_the_count(self):
        archive = make_zip({"skills/a/SKILL.md": SKILL_MD_TEXT})
        with self.assertRaises(installer.InstallError) as caught:
            installer.read_package(archive, "nope")
        self.assertIn("--path", str(caught.exception))

    def test_repo_without_a_skill_md_is_refused(self):
        with self.assertRaises(installer.InstallError) as caught:
            installer.read_package(make_zip({"README.md": "空仓"}))
        self.assertIn("SKILL.md", str(caught.exception))

    def test_lowercase_skill_md_is_normalised(self):
        package = installer.read_package(make_zip({"skill.md": SKILL_MD_TEXT}))
        self.assertIn("SKILL.md", package.files)

    def test_commit_comes_from_the_zip_comment(self):
        # GitHub 的 zipball 把提交号放在 zip 注释里，白拿一个精确版本号，不用多打一次 API
        sha = "a" * 40
        package = installer.read_package(
            make_zip({"SKILL.md": SKILL_MD_TEXT}, comment=sha.encode()))
        self.assertEqual(package.commit, sha)

    def test_missing_comment_leaves_the_commit_empty(self):
        self.assertEqual(installer.read_package(simple_zip()).commit, "")

    def test_non_sha_comment_is_ignored(self):
        package = installer.read_package(
            make_zip({"SKILL.md": SKILL_MD_TEXT}, comment=b"just a comment"))
        self.assertEqual(package.commit, "")

    def test_nested_skills_are_reported_not_silently_dropped(self):
        archive = make_zip({"SKILL.md": SKILL_MD_TEXT,
                            "extras/inner/SKILL.md": SKILL_MD_TEXT})
        package = installer.read_package(archive)
        self.assertEqual(package.nested_skills, ["extras/inner"])

    def test_not_a_zip_at_all(self):
        with self.assertRaises(installer.InstallError) as caught:
            installer.read_package(b"<html>404</html>")
        self.assertIn("zip", str(caught.exception))

    def test_archive_without_a_single_root_still_works(self):
        package = installer.read_package(
            make_zip({"SKILL.md": SKILL_MD_TEXT}, prefix=""))
        self.assertIn("SKILL.md", package.files)


class ExtractionSafetyTest(unittest.TestCase):
    def refuse(self, files, needle, **kwargs):
        with self.assertRaises(installer.InstallError) as caught:
            installer.read_package(make_zip(files, **kwargs))
        self.assertIn(needle, str(caught.exception))

    def test_parent_traversal_is_refused(self):
        self.refuse({"SKILL.md": SKILL_MD_TEXT, "../evil.md": "x"}, "跳出目标目录")

    def test_absolute_posix_path_is_refused(self):
        self.refuse({"SKILL.md": SKILL_MD_TEXT, "/etc/passwd": "x"},
                    "绝对路径", prefix="")

    def test_windows_drive_path_is_refused(self):
        self.refuse({"SKILL.md": SKILL_MD_TEXT, "C:/windows/system32/x": "x"},
                    "绝对路径", prefix="")

    def test_a_backslash_entry_never_gets_written(self):
        # Windows 上 zipfile 读central directory 时就把 \ 归一成 /，于是落到跳目录那条
        # 守卫；POSIX 上反斜杠原样保留，落到反斜杠那条。两边都必须拒。
        with self.assertRaises(installer.InstallError):
            installer.read_package(make_zip({"SKILL.md": SKILL_MD_TEXT, "a\\..\\b.md": "x"}))

    def test_backslash_is_refused_by_the_path_check(self):
        with self.assertRaises(installer.InstallError) as caught:
            installer._check_rel("a\\..\\b.md")
        self.assertIn("反斜杠", str(caught.exception))

    def test_nul_byte_is_refused_by_the_path_check(self):
        with self.assertRaises(installer.InstallError) as caught:
            installer._check_rel("a\x00.md")
        self.assertIn("控制字符", str(caught.exception))

    def test_leading_slash_is_refused_by_the_path_check(self):
        with self.assertRaises(installer.InstallError):
            installer._check_rel("/etc/passwd")

    def test_symlink_entry_is_refused(self):
        with self.assertRaises(installer.InstallError) as caught:
            installer.read_package(make_zip(
                {"SKILL.md": SKILL_MD_TEXT, "link": "/etc/passwd"}, symlinks=("link",)))
        self.assertIn("符号链接", str(caught.exception))

    def test_case_only_collision_is_refused(self):
        self.refuse({"SKILL.md": SKILL_MD_TEXT, "Ref.md": "a", "ref.md": "b"},
                    "大小写")

    def test_windows_reserved_name_is_refused(self):
        self.refuse({"SKILL.md": SKILL_MD_TEXT, "nul.md": "x"}, "保留名")

    def test_trailing_dot_is_refused(self):
        self.refuse({"SKILL.md": SKILL_MD_TEXT, "weird./a.md": "x"}, "尾随空格或点")

    def test_control_character_is_refused(self):
        self.refuse({"SKILL.md": SKILL_MD_TEXT, "a\nb.md": "x"}, "控制字符")

    def test_too_deep_is_refused(self):
        deep = "/".join(f"d{i}" for i in range(installer.MAX_PATH_DEPTH + 1)) + "/a.md"
        self.refuse({"SKILL.md": SKILL_MD_TEXT, deep: "x"}, "层")

    def test_too_many_files_is_refused(self):
        files = {"SKILL.md": SKILL_MD_TEXT}
        files.update({f"f{i}.md": "x" for i in range(installer.MAX_FILES + 1)})
        self.refuse(files, "上限")

    def test_total_size_cap_is_refused(self):
        with mock.patch.object(installer, "MAX_TOTAL_BYTES", 1024):
            self.refuse({"SKILL.md": SKILL_MD_TEXT, "big.bin": b"x" * 4096}, "体积")

    def test_declared_size_mismatch_is_refused(self):
        """包内声明的大小是体积门禁的依据，实际解出来必须对得上，否则门禁形同虚设。"""
        archive = make_zip({"SKILL.md": SKILL_MD_TEXT, "payload.bin": b"x" * 64})
        real_open = zipfile.ZipFile.open

        def truncated(self, name, *args, **kwargs):
            handle = real_open(self, name, *args, **kwargs)
            if getattr(name, "filename", name).endswith("payload.bin"):
                return io.BytesIO(handle.read()[:8])
            return handle

        with mock.patch.object(zipfile.ZipFile, "open", truncated):
            with self.assertRaises(installer.InstallError) as caught:
                installer.read_package(archive)
        self.assertIn("不符", str(caught.exception))


# ---------------------------------------------------------------- 装前计划

class PlanTest(TempDirCase):
    def test_target_is_root_plus_dir_name(self):
        plan = self.build_plan()
        self.assertEqual(plan.target, self.root / "demo-skill")
        self.assertTrue(plan.ok)

    def test_dir_name_comes_from_the_package_directory_not_the_name(self):
        archive = make_zip({"skills/actual-dir/SKILL.md": SKILL_MD_TEXT})
        plan = self.build_plan(archive)
        self.assertEqual(plan.dir_name, "actual-dir")

    def test_as_name_overrides(self):
        plan = self.build_plan(as_name="renamed")
        self.assertEqual(plan.target, self.root / "renamed")

    def test_existing_directory_is_a_hard_blocker(self):
        (self.root / "demo-skill").mkdir()
        plan = self.build_plan()
        self.assertFalse(plan.ok)
        self.assertIn("同名绝不覆盖", plan.blockers[0])

    def test_the_blocker_tells_you_how_to_get_out(self):
        (self.root / "demo-skill").mkdir()
        blocker = self.build_plan().blockers[0]
        self.assertIn("--as", blocker)
        self.assertIn("remove", blocker)

    def test_an_existing_file_with_the_same_name_also_blocks(self):
        (self.root / "demo-skill").write_text("我是个文件", encoding="utf-8")
        self.assertFalse(self.build_plan().ok)

    def test_same_name_on_another_host_is_only_a_note(self):
        plan = self.build_plan(existing=[{"name": "demo-skill", "host": "claude-code",
                                          "dir_name": "demo-skill"}])
        self.assertTrue(plan.ok)
        self.assertTrue(any("已有同名" in n for n in plan.notes))

    def test_the_note_says_where_the_existing_copy_lives(self):
        # 同名那份常常就在即将写入的同一个宿主里，只是嵌在别的目录下。不报位置，
        # 这条提示读起来就和上一行的目标路径自相矛盾（说要写、又说已有、却没拦）。
        plan = self.build_plan(existing=[{
            "name": "demo-skill", "host": "claude-code", "dir_name": "demo-skill",
            "path": str(Path("/home/u/.claude/skills/superpowers/skills/demo-skill/SKILL.md")),
            "root": str(Path("/home/u/.claude/skills")),
        }])
        self.assertTrue(any("superpowers/skills/demo-skill" in n for n in plan.notes),
                        plan.notes)

    def test_the_note_survives_a_path_outside_the_root(self):
        plan = self.build_plan(existing=[{
            "name": "demo-skill", "host": "claude-code", "dir_name": "demo-skill",
            "path": str(Path("/elsewhere/demo-skill/SKILL.md")),
            "root": str(Path("/home/u/.claude/skills")),
        }])
        self.assertTrue(any("demo-skill" in n for n in plan.notes), plan.notes)

    def test_the_note_falls_back_to_the_dir_name_without_a_path(self):
        # dir_name 故意和 name 取不同值：否则「位置」哪怕退化成占位符，断言也会
        # 被提示里的 name 蒙过去。
        plan = self.build_plan(existing=[{"name": "demo-skill", "host": "claude-code",
                                          "dir_name": "vendored-demo"}])
        self.assertTrue(any("vendored-demo" in n for n in plan.notes), plan.notes)

    def test_unrelated_existing_skills_produce_no_note(self):
        plan = self.build_plan(existing=[{"name": "other", "host": "cursor",
                                          "dir_name": "other"}])
        self.assertEqual(plan.notes, [])

    def test_nested_skills_show_up_in_the_plan(self):
        archive = make_zip({"SKILL.md": SKILL_MD_TEXT,
                            "inner/x/SKILL.md": SKILL_MD_TEXT})
        plan = self.build_plan(archive)
        self.assertTrue(any("嵌套" in n for n in plan.notes))

    def test_stale_ledger_entry_is_noted(self):
        ledger = {"installs": [{"id": "cursor:demo-skill"}]}
        plan = self.build_plan(ledger=ledger)
        self.assertTrue(any("存证里有这条" in n for n in plan.notes))

    def test_path_separator_in_as_name_is_refused(self):
        with self.assertRaises(installer.InstallError):
            self.build_plan(as_name="a/b")

    def test_dot_dot_as_name_is_refused(self):
        with self.assertRaises(installer.InstallError):
            self.build_plan(as_name="..")

    def test_render_shows_source_target_and_files(self):
        text = installer.render_plan(self.build_plan())
        for needle in ("https://github.com/owner/repo", str(self.root / "demo-skill"),
                       "SKILL.md", "ref=HEAD"):
            self.assertIn(needle, text)

    def test_render_shows_blockers(self):
        (self.root / "demo-skill").mkdir()
        self.assertIn("阻塞", installer.render_plan(self.build_plan()))


# ---------------------------------------------------------------- 写入与存证

class ApplyTest(TempDirCase):
    def apply(self, **kwargs):
        plan = self.build_plan(**kwargs)
        return installer.apply_install(plan, self.ledger,
                                       now=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc))

    def test_files_land_on_disk(self):
        receipt = self.apply()
        target = Path(receipt["target"])
        self.assertEqual((target / "SKILL.md").read_text(encoding="utf-8"), SKILL_MD_TEXT)
        self.assertTrue((target / "README.md").exists())

    def test_nested_files_land_too(self):
        archive = make_zip({"SKILL.md": SKILL_MD_TEXT, "refs/deep/a.md": "深"})
        receipt = self.apply(archive=archive)
        self.assertEqual((Path(receipt["target"]) / "refs" / "deep" / "a.md")
                         .read_text(encoding="utf-8"), "深")

    def test_receipt_records_the_provenance(self):
        receipt = self.apply()
        self.assertEqual(receipt["id"], "cursor:demo-skill")
        self.assertEqual(receipt["source"]["url"], "https://github.com/owner/repo")
        self.assertEqual(receipt["source"]["ref"], "HEAD")
        self.assertEqual(receipt["installed_at"], "2026-01-02T03:04:05+00:00")
        self.assertEqual(receipt["tool_version"], installer.__version__)

    def test_receipt_records_a_hash_per_file(self):
        receipt = self.apply()
        self.assertEqual(set(receipt["files"]), {"SKILL.md", "README.md"})
        self.assertEqual(receipt["files"]["SKILL.md"]["bytes"],
                         len(SKILL_MD_TEXT.encode("utf-8")))
        self.assertRegex(receipt["files"]["SKILL.md"]["sha256"], r"^[0-9a-f]{64}$")

    def test_ledger_is_written_and_reloadable(self):
        self.apply()
        ledger = installer.load_ledger(self.ledger)
        self.assertEqual(ledger["version"], installer.LEDGER_VERSION)
        self.assertEqual(len(ledger["installs"]), 1)

    def test_reinstalling_the_same_id_replaces_the_row(self):
        receipt = self.apply()
        shutil.rmtree(receipt["target"])
        self.apply()
        self.assertEqual(len(installer.load_ledger(self.ledger)["installs"]), 1)

    def test_two_hosts_keep_two_rows(self):
        self.apply()
        other = self.tmp / "other-skills"
        other.mkdir()
        plan = installer.plan_install(
            installer.parse_source("owner/repo"), "HEAD",
            installer.read_package(simple_zip()), "claude-code", other)
        installer.apply_install(plan, self.ledger)
        ids = {r["id"] for r in installer.load_ledger(self.ledger)["installs"]}
        self.assertEqual(ids, {"cursor:demo-skill", "claude-code:demo-skill"})

    def test_blocked_plan_refuses_to_write(self):
        (self.root / "demo-skill").mkdir()
        with self.assertRaises(installer.InstallError):
            self.apply()

    def test_directory_appearing_between_plan_and_apply_is_caught(self):
        plan = self.build_plan()
        (self.root / "demo-skill").mkdir()  # 抢跑
        with self.assertRaises(FileExistsError):
            installer.apply_install(plan, self.ledger)

    def test_a_failed_write_leaves_nothing_behind(self):
        plan = self.build_plan()
        real = Path.write_bytes

        def explode(self, data):
            if self.name == "README.md":
                raise OSError("磁盘满了")
            return real(self, data)

        with mock.patch.object(Path, "write_bytes", explode):
            with self.assertRaises(installer.InstallError):
                installer.apply_install(plan, self.ledger)
        self.assertFalse((self.root / "demo-skill").exists())
        self.assertEqual(installer.load_ledger(self.ledger)["installs"], [])

    def test_corrupt_ledger_is_replaced_not_crashed_on(self):
        self.ledger.write_text("{ 这不是 json", encoding="utf-8")
        self.apply()
        self.assertEqual(len(installer.load_ledger(self.ledger)["installs"]), 1)

    def test_ledger_of_the_wrong_shape_is_replaced(self):
        self.ledger.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        self.assertEqual(installer.load_ledger(self.ledger)["installs"], [])


# ---------------------------------------------------------------- 体检与回滚

class VerifyAndRemoveTest(TempDirCase):
    def setUp(self):
        super().setUp()
        plan = self.build_plan()
        self.receipt = installer.apply_install(plan, self.ledger)
        self.target = Path(self.receipt["target"])

    def test_fresh_install_verifies_clean(self):
        self.assertEqual(set(installer.verify_receipt(self.receipt).values()), {"ok"})

    def test_edited_file_shows_as_modified(self):
        (self.target / "README.md").write_text("我改了", encoding="utf-8")
        self.assertEqual(installer.verify_receipt(self.receipt)["README.md"], "modified")

    def test_deleted_file_shows_as_missing(self):
        (self.target / "README.md").unlink()
        self.assertEqual(installer.verify_receipt(self.receipt)["README.md"], "missing")

    def test_remove_deletes_the_directory(self):
        report = installer.remove_install(self.receipt, self.ledger)
        self.assertTrue(report["dir_gone"])
        self.assertFalse(self.target.exists())
        self.assertEqual(installer.load_ledger(self.ledger)["installs"], [])

    def test_remove_refuses_when_a_file_was_edited(self):
        (self.target / "SKILL.md").write_text("我改过了", encoding="utf-8")
        with self.assertRaises(installer.InstallError) as caught:
            installer.remove_install(self.receipt, self.ledger)
        self.assertIn("--force", str(caught.exception))
        self.assertTrue(self.target.exists())  # 一个字节都没删

    def test_force_removes_edited_files(self):
        (self.target / "SKILL.md").write_text("我改过了", encoding="utf-8")
        report = installer.remove_install(self.receipt, self.ledger, force=True)
        self.assertEqual(report["modified"], ["SKILL.md"])
        self.assertFalse(self.target.exists())

    def test_files_we_never_wrote_are_kept(self):
        (self.target / "user-note.md").write_text("我自己加的", encoding="utf-8")
        report = installer.remove_install(self.receipt, self.ledger)
        self.assertEqual(report["leftover"], ["user-note.md"])
        self.assertFalse(report["dir_gone"])
        self.assertTrue((self.target / "user-note.md").exists())
        self.assertFalse((self.target / "SKILL.md").exists())

    def test_empty_nested_directories_are_pruned(self):
        shutil.rmtree(self.target)
        plan = self.build_plan(make_zip({"SKILL.md": SKILL_MD_TEXT, "a/b/c.md": "深"}))
        receipt = installer.apply_install(plan, self.ledger)
        installer.remove_install(receipt, self.ledger)
        self.assertFalse(Path(receipt["target"]).exists())

    def test_already_missing_files_do_not_stop_the_removal(self):
        (self.target / "README.md").unlink()
        report = installer.remove_install(self.receipt, self.ledger)
        self.assertEqual(report["removed"], ["SKILL.md"])
        self.assertTrue(report["dir_gone"])

    def test_ledger_row_is_dropped_even_if_the_directory_vanished(self):
        shutil.rmtree(self.target)
        installer.remove_install(self.receipt, self.ledger)
        self.assertEqual(installer.load_ledger(self.ledger)["installs"], [])

    def test_a_receipt_without_files_is_refused(self):
        with self.assertRaises(installer.InstallError):
            installer.remove_install({"id": "x", "target": str(self.target), "files": {}},
                                     self.ledger)

    def test_a_target_near_the_filesystem_root_is_refused(self):
        receipt = dict(self.receipt, target=str(Path(self.target.anchor or "/")))
        with self.assertRaises(installer.InstallError) as caught:
            installer.remove_install(receipt, self.ledger)
        self.assertIn("太靠根", str(caught.exception))


class FindReceiptTest(unittest.TestCase):
    def setUp(self):
        self.ledger = {"installs": [
            {"id": "cursor:alpha", "dir_name": "alpha", "name": "Alpha"},
            {"id": "claude-code:alpha", "dir_name": "alpha", "name": "Alpha"},
            {"id": "cursor:beta", "dir_name": "beta", "name": "Beta"},
        ]}

    def test_exact_id(self):
        self.assertEqual(installer.find_receipt(self.ledger, "cursor:alpha")["id"],
                         "cursor:alpha")

    def test_id_is_case_insensitive(self):
        self.assertIsNotNone(installer.find_receipt(self.ledger, "CURSOR:beta"))

    def test_unique_loose_name(self):
        self.assertEqual(installer.find_receipt(self.ledger, "beta")["id"], "cursor:beta")

    def test_ambiguous_loose_name_lists_the_ids(self):
        with self.assertRaises(installer.InstallError) as caught:
            installer.find_receipt(self.ledger, "alpha")
        self.assertIn("cursor:alpha", str(caught.exception))

    def test_unknown_returns_none(self):
        self.assertIsNone(installer.find_receipt(self.ledger, "nope"))

    def test_empty_key_returns_none(self):
        self.assertIsNone(installer.find_receipt(self.ledger, ""))


# ---------------------------------------------------------------- 宿主选择

class InstallRootsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="skillpick-roots-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def make(self, rel: str, skills: int = 0) -> Path:
        path = self.tmp / rel
        path.mkdir(parents=True, exist_ok=True)
        for i in range(skills):
            (path / f"s{i}").mkdir()
            (path / f"s{i}" / "SKILL.md").write_text("x", encoding="utf-8")
        return path

    def roots_from(self, pairs):
        with mock.patch.object(skillpick, "load_scan_roots", return_value=pairs), \
             mock.patch.object(skillpick, "INSTALL_TARGETS", {}):
            return skillpick.install_roots()

    def test_plain_skills_root_is_a_target(self):
        root = self.make(".cursor/skills")
        self.assertEqual(self.roots_from([(root, "cursor")]), {"cursor": root})

    def test_plugin_cache_is_not_a_target(self):
        root = self.make(".cursor/plugins/cache/x/skills")
        self.assertEqual(self.roots_from([(root, "cursor-plugin")]), {})

    def test_builtin_directory_is_not_a_target(self):
        root = self.make(".cursor/skills-cursor")
        self.assertEqual(self.roots_from([(root, "cursor-builtin")]), {})

    def test_duplicate_host_keeps_the_busier_root(self):
        thin = self.make(".codex/skills", skills=1)
        fat = self.make(".agents/skills", skills=5)
        self.assertEqual(self.roots_from([(thin, "codex"), (fat, "codex")]),
                         {"codex": fat})

    def test_install_targets_are_always_offered(self):
        with mock.patch.object(skillpick, "load_scan_roots", return_value=[]):
            roots = skillpick.install_roots()
        self.assertEqual(set(roots), set(skillpick.INSTALL_TARGETS))
        self.assertTrue(all(p.name == "skills" for p in roots.values()))

    def test_default_host_is_the_busiest(self):
        roots = {"cursor": self.make("a/skills", skills=2),
                 "claude-code": self.make("b/skills", skills=7)}
        self.assertEqual(skillpick.default_install_host(roots), "claude-code")

    def test_default_host_breaks_ties_by_name(self):
        roots = {"zeta": self.make("z/skills", skills=1),
                 "alpha": self.make("a2/skills", skills=1)}
        self.assertEqual(skillpick.default_install_host(roots), "alpha")

    def test_default_host_without_any_root(self):
        self.assertEqual(skillpick.default_install_host({}), "cursor")


class ToolFilesTest(unittest.TestCase):
    def test_installer_travels_with_the_tool_copy(self):
        # 忘了这条，家目录那份一跑 add 就 ImportError
        self.assertIn("installer.py", skillpick.TOOL_FILES)

    def test_ledger_lives_next_to_the_catalog(self):
        self.assertEqual(skillpick.INSTALLED_LEDGER.parent, skillpick.DATA_DIR)


if __name__ == "__main__":
    unittest.main()
