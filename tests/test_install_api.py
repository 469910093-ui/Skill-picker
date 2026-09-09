"""「装到本机」按钮的服务端测试。

这一层的价值几乎全在安全门上：本机开一个能写盘的 HTTP 端点，等于在你浏览器里
放了个靶子。所以下面每一道门都单独测「关着」，再测三道门一起时同源页面能过。

功能部分测的是「计划和落盘必须是同一份」——第二次调用不能换个 URL 溜进来。
"""

import io
import json
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import install_api  # noqa: E402
import installer  # noqa: E402

SKILL_MD_TEXT = """---
name: demo-skill
description: 一个用来测试的 skill
---

# demo

正文。
"""

PORT = 8471
ORIGIN = f"http://127.0.0.1:{PORT}"


def simple_zip(**extra) -> bytes:
    files = {"SKILL.md": SKILL_MD_TEXT, "README.md": "读我"}
    files.update(extra)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in files.items():
            archive.writestr("repo-main/" + name, payload)
    return buffer.getvalue()


class ApiCase(unittest.TestCase):
    """整组依赖都替换掉：不出网、不落盘、不重扫。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="skillpick-api-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = self.tmp / "skills"
        self.root.mkdir()
        self.applied = []
        self.archive = simple_zip()

        self.deps = install_api.Deps(
            roots=lambda: {"cursor": self.root, "claude-code": self.tmp / "claude"},
            default_host=lambda roots: "cursor",
            known_skills=lambda: [],
            ledger_path=self.tmp / "installed.json",
            local_index=lambda: {"names": {"demo-skill": {"copies": 1, "hosts": ["cursor"],
                                                          "drifted": False}}},
            download=lambda source, ref=None: (self.archive, ref or "HEAD"),
            apply_install=self._apply,
            post_install=lambda receipt: {"code": 0, "names": ["demo-skill"],
                                          "twins": [], "gates_failed": []},
        )
        self.api = install_api.InstallApi(PORT, self.deps, token="T0KEN")

    def _apply(self, plan, ledger_path):
        self.applied.append(plan)
        return {"id": f"{plan.host}:{plan.dir_name}", "name": plan.package.name,
                "host": plan.host, "target": str(plan.target),
                "files": {k: "sha" for k in plan.package.files}}

    def headers(self, **over) -> dict:
        base = {"Host": f"127.0.0.1:{PORT}", "Origin": ORIGIN,
                "Content-Type": "application/json",
                install_api.TOKEN_HEADER: "T0KEN"}
        base.update(over)
        return {k: v for k, v in base.items() if v is not None}


# ------------------------------------------------------------------ 安全门

class TestHostHeaderStopsDnsRebinding(ApiCase):
    """攻击者把自己的域名解析到 127.0.0.1，请求能落到我们进程，但 Host 是他的域名。"""

    def test_attacker_domain_in_host_is_refused(self):
        with self.assertRaises(install_api.ApiError) as cm:
            self.api.guard(self.headers(Host="evil.example.com"))
        self.assertEqual(cm.exception.status, 403)

    def test_right_ip_wrong_port_is_refused(self):
        with self.assertRaises(install_api.ApiError):
            self.api.guard(self.headers(Host="127.0.0.1:9999"))

    def test_missing_host_is_refused(self):
        with self.assertRaises(install_api.ApiError):
            self.api.guard(self.headers(Host=None))

    def test_localhost_spelling_is_accepted(self):
        self.api.guard(self.headers(Host=f"localhost:{PORT}",
                                    Origin=f"http://localhost:{PORT}"))


class TestOriginHeaderStopsCsrf(ApiCase):
    def test_another_site_is_refused(self):
        with self.assertRaises(install_api.ApiError) as cm:
            self.api.guard(self.headers(Origin="https://evil.example.com"))
        self.assertEqual(cm.exception.status, 403)

    def test_missing_origin_is_refused(self):
        with self.assertRaises(install_api.ApiError):
            self.api.guard(self.headers(Origin=None))

    def test_a_prefix_of_our_origin_is_not_enough(self):
        """前缀匹配是这类校验的经典写法，也是经典漏洞：evil 可以注册这种域名。"""
        with self.assertRaises(install_api.ApiError):
            self.api.guard(self.headers(Origin=ORIGIN + ".evil.example.com"))

    def test_https_flavour_of_our_own_origin_is_refused(self):
        with self.assertRaises(install_api.ApiError):
            self.api.guard(self.headers(Origin=f"https://127.0.0.1:{PORT}"))


class TestTokenIsRequired(ApiCase):
    def test_no_token_is_refused(self):
        with self.assertRaises(install_api.ApiError):
            self.api.guard(self.headers(**{install_api.TOKEN_HEADER: None}))

    def test_wrong_token_is_refused(self):
        with self.assertRaises(install_api.ApiError):
            self.api.guard(self.headers(**{install_api.TOKEN_HEADER: "nope"}))

    def test_token_is_not_guessable_by_default(self):
        fresh = install_api.InstallApi(PORT, self.deps)
        self.assertGreaterEqual(len(fresh.token), 24)
        self.assertNotEqual(fresh.token, install_api.InstallApi(PORT, self.deps).token)


class TestContentTypeMustBeJson(ApiCase):
    """表单 POST 和 text/plain 是「简单请求」，不触发预检、真的会被发出去。"""

    def test_form_urlencoded_is_refused(self):
        with self.assertRaises(install_api.ApiError):
            self.api.guard(self.headers(**{"Content-Type":
                                           "application/x-www-form-urlencoded"}))

    def test_text_plain_is_refused(self):
        with self.assertRaises(install_api.ApiError):
            self.api.guard(self.headers(**{"Content-Type": "text/plain"}))

    def test_multipart_is_refused(self):
        with self.assertRaises(install_api.ApiError):
            self.api.guard(self.headers(**{"Content-Type": "multipart/form-data"}))

    def test_charset_suffix_is_fine(self):
        self.api.guard(self.headers(**{"Content-Type": "application/json; charset=utf-8"}))


class TestGuardDoesNotLeakWhichDoorFailed(ApiCase):
    def test_all_three_doors_give_the_same_kind_of_answer(self):
        cases = [self.headers(Host="evil.example.com"),
                 self.headers(Origin="https://evil.example.com"),
                 self.headers(**{"Content-Type": "text/plain"})]
        for h in cases:
            with self.assertRaises(install_api.ApiError) as cm:
                self.api.guard(h)
            self.assertEqual(cm.exception.status, 403)
            self.assertIn("本机看板", cm.exception.message)


class TestSameOriginPageGetsThrough(ApiCase):
    def test_the_real_headers_pass(self):
        self.api.guard(self.headers())  # 不抛就是过了


class TestBodyLimits(ApiCase):
    def test_oversized_body_is_refused(self):
        with self.assertRaises(install_api.ApiError) as cm:
            self.api.parse_body(b"x" * (install_api.MAX_BODY + 1))
        self.assertEqual(cm.exception.status, 413)

    def test_garbage_is_a_400_not_a_crash(self):
        with self.assertRaises(install_api.ApiError) as cm:
            self.api.parse_body(b"{not json")
        self.assertEqual(cm.exception.status, 400)

    def test_a_bare_list_is_refused(self):
        with self.assertRaises(install_api.ApiError):
            self.api.parse_body(b"[1,2,3]")

    def test_empty_body_is_an_empty_dict(self):
        self.assertEqual(self.api.parse_body(b""), {})


# ------------------------------------------------------------------ 会话

class TestSession(ApiCase):
    def test_hands_out_the_token_and_hosts(self):
        body = self.api.session()
        self.assertEqual(body["token"], "T0KEN")
        self.assertEqual(body["hosts"], ["claude-code", "cursor"])
        self.assertEqual(body["default_host"], "cursor")


# ------------------------------------------------------------------ 出计划

class TestPlan(ApiCase):
    def test_describes_what_would_be_written(self):
        body = self.api.plan({"url": "https://github.com/owner/repo"})
        self.assertTrue(body["ok"])
        self.assertEqual(body["name"], "demo-skill")
        # SKILL.md 在包根时没有子目录名可用，目录名退回 frontmatter 的 name
        self.assertEqual(body["dir_name"], "demo-skill")
        self.assertEqual(body["file_count"], 2)
        self.assertIn("SKILL.md", [f["path"] for f in body["files"]])

    def test_nothing_is_written_by_planning(self):
        self.api.plan({"url": "https://github.com/owner/repo"})
        self.assertEqual(self.applied, [])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_missing_url_is_a_400(self):
        with self.assertRaises(install_api.ApiError) as cm:
            self.api.plan({})
        self.assertEqual(cm.exception.status, 400)

    def test_unknown_host_is_refused_and_lists_the_real_ones(self):
        with self.assertRaises(install_api.ApiError) as cm:
            self.api.plan({"url": "https://github.com/owner/repo", "host": "emacs"})
        self.assertEqual(cm.exception.status, 400)
        self.assertIn("cursor", cm.exception.message)

    def test_a_bad_url_is_a_400_not_a_500(self):
        with self.assertRaises(install_api.ApiError) as cm:
            self.api.plan({"url": "not a url at all"})
        self.assertEqual(cm.exception.status, 400)

    def test_ambiguous_package_returns_the_candidates(self):
        self.archive = simple_zip(**{"a/SKILL.md": SKILL_MD_TEXT,
                                     "b/SKILL.md": SKILL_MD_TEXT})
        self.archive = _zip_without_root()
        with self.assertRaises(install_api.ApiError) as cm:
            self.api.plan({"url": "https://github.com/owner/repo"})
        self.assertEqual(cm.exception.status, 409)
        self.assertTrue(cm.exception.extra["candidates"])

    def test_existing_target_shows_up_as_a_blocker_not_an_error(self):
        (self.root / "demo-skill").mkdir()
        body = self.api.plan({"url": "https://github.com/owner/repo"})
        self.assertFalse(body["ok"])
        self.assertTrue(body["blockers"])

    def test_target_path_is_shown_so_consent_is_informed(self):
        body = self.api.plan({"url": "https://github.com/owner/repo"})
        self.assertEqual(body["target"], str(self.root / "demo-skill"))

    def test_as_name_renames_the_target(self):
        body = self.api.plan({"url": "https://github.com/owner/repo", "as": "mine"})
        self.assertEqual(body["dir_name"], "mine")
        self.assertEqual(body["target"], str(self.root / "mine"))

    def test_file_list_is_capped_but_the_count_is_not(self):
        extra = {f"f{i}.md": "x" for i in range(30)}
        self.archive = simple_zip(**extra)
        body = self.api.plan({"url": "https://github.com/owner/repo"})
        self.assertEqual(len(body["files"]), install_api.LIST_CAP)
        self.assertEqual(body["file_count"], 32)
        self.assertEqual(body["more_files"], 32 - install_api.LIST_CAP)


def _zip_without_root() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("repo-main/a/SKILL.md", SKILL_MD_TEXT)
        archive.writestr("repo-main/b/SKILL.md", SKILL_MD_TEXT)
    return buffer.getvalue()


# ------------------------------------------------------------------ 落盘

class TestApplyInstallsExactlyWhatWasPlanned(ApiCase):
    def test_happy_path(self):
        plan = self.api.plan({"url": "https://github.com/owner/repo"})
        body = self.api.apply({"plan_id": plan["plan_id"]})
        self.assertTrue(body["ok"])
        self.assertEqual(body["name"], "demo-skill")
        self.assertEqual(len(self.applied), 1)

    def test_apply_cannot_smuggle_in_a_different_url(self):
        """第二次调用只认 plan_id。给它别的 url 也没用——装的是缓存里那份计划。"""
        plan = self.api.plan({"url": "https://github.com/owner/repo"})
        self.api.apply({"plan_id": plan["plan_id"],
                        "url": "https://github.com/attacker/evil"})
        self.assertEqual(self.applied[0].source.repo_url,
                         "https://github.com/owner/repo")

    def test_unknown_plan_id_is_refused(self):
        with self.assertRaises(install_api.ApiError) as cm:
            self.api.apply({"plan_id": "made-up"})
        self.assertEqual(cm.exception.status, 409)

    def test_a_plan_can_only_be_applied_once(self):
        plan = self.api.plan({"url": "https://github.com/owner/repo"})
        self.api.apply({"plan_id": plan["plan_id"]})
        with self.assertRaises(install_api.ApiError):
            self.api.apply({"plan_id": plan["plan_id"]})
        self.assertEqual(len(self.applied), 1)

    def test_a_blocked_plan_is_never_applied(self):
        (self.root / "demo-skill").mkdir()
        plan = self.api.plan({"url": "https://github.com/owner/repo"})
        with self.assertRaises(install_api.ApiError) as cm:
            self.api.apply({"plan_id": plan["plan_id"]})
        self.assertEqual(cm.exception.status, 409)
        self.assertEqual(self.applied, [])

    def test_the_cache_does_not_grow_without_bound(self):
        for _ in range(install_api.PLAN_CACHE_MAX + 5):
            self.api.plan({"url": "https://github.com/owner/repo"})
        self.assertLessEqual(len(self.api._plans), install_api.PLAN_CACHE_MAX)

    def test_the_oldest_plan_is_the_one_dropped(self):
        first = self.api.plan({"url": "https://github.com/owner/repo"})
        for _ in range(install_api.PLAN_CACHE_MAX):
            self.api.plan({"url": "https://github.com/owner/repo"})
        with self.assertRaises(install_api.ApiError):
            self.api.apply({"plan_id": first["plan_id"]})


class TestApplyReportsBackEnoughToRefreshTheBadge(ApiCase):
    def test_fresh_local_index_comes_back(self):
        plan = self.api.plan({"url": "https://github.com/owner/repo"})
        body = self.api.apply({"plan_id": plan["plan_id"]})
        self.assertIn("demo-skill", body["local"]["names"])

    def test_a_failing_health_check_is_not_reported_as_ok(self):
        self.deps.post_install = lambda receipt: {"code": 2, "gates_failed": ["G1"],
                                                  "names": [], "twins": []}
        plan = self.api.plan({"url": "https://github.com/owner/repo"})
        body = self.api.apply({"plan_id": plan["plan_id"]})
        self.assertFalse(body["ok"])
        self.assertEqual(body["report"]["gates_failed"], ["G1"])

    def test_a_rollback_is_reported(self):
        self.deps.post_install = lambda receipt: {"code": 2, "rolled_back": True,
                                                  "broken": {"SKILL.md": "modified"}}
        plan = self.api.plan({"url": "https://github.com/owner/repo"})
        body = self.api.apply({"plan_id": plan["plan_id"]})
        self.assertFalse(body["ok"])
        self.assertTrue(body["report"]["rolled_back"])

    def test_an_installer_failure_is_a_500_with_a_message(self):
        def boom(plan, ledger_path):
            raise installer.InstallError("磁盘满了")

        self.deps.apply_install = boom
        plan = self.api.plan({"url": "https://github.com/owner/repo"})
        with self.assertRaises(install_api.ApiError) as cm:
            self.api.apply({"plan_id": plan["plan_id"]})
        self.assertEqual(cm.exception.status, 500)
        self.assertIn("磁盘满了", cm.exception.message)


if __name__ == "__main__":
    unittest.main()
