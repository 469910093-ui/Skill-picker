"""「装到本机」按钮的服务端：把 installer 的两段式安装接到本地 HTTP 上。

为什么要有这一层（而不是让页面直接调 installer）：feed 页跑在浏览器里，浏览器
只会说 HTTP。而一旦本机开出一个**能写盘**的 HTTP 端点，它就成了你浏览器里任何
一个标签页都能打的靶子——你随便访问一个恶意站点，它的 JS 就能往
`127.0.0.1:8471` 发请求，往你本机装任意仓库的东西。所以这层的主要内容不是转发，
是三道互相独立、每一道单独就够用的门：

1. **Host 头白名单** —— 挡 DNS rebinding。攻击者把自己的域名解析到 127.0.0.1，
   请求就能落到我们进程上，但 `Host:` 里带的是他的域名。
2. **Origin 头精确匹配** —— 挡 CSRF。跨站页面发过来的请求，Origin 是它自己的源。
3. **自定义令牌头** —— 挡前两道万一写漏。带自定义头的跨源请求会先触发 CORS
   预检，我们不实现 OPTIONS，预检就过不去，请求根本发不出来。令牌本身由
   `GET /api/session` 发放，而那个响应没有 CORS 头，跨源页面读不到。

外加一条：只认 `Content-Type: application/json`。表单 POST 和 text/plain 属于
「简单请求」，不触发预检、能被真的发出去；要求 JSON 就把这条路也堵死了。

计划与写盘分两次调用，中间用 plan_id 挂钩：页面上看到的清单和真正落盘的内容
必须是同一份，不能让第二次调用换个 URL 进来。
"""

from __future__ import annotations

import hmac
import json
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import installer

# 计划里带着整个 zip 解出来的文件内容，攒多了吃内存；用户也不会同时挂十几个计划
PLAN_CACHE_MAX = 8
# 请求体没有理由超过这个大小（最长的字段是 GitHub URL）
MAX_BODY = 16 * 1024
# 计划里回给页面的文件清单上限，和 CLI 的 render_plan 对齐
LIST_CAP = 12

TOKEN_HEADER = "X-Skillpick-Token"


class ApiError(Exception):
    """带 HTTP 状态码的可预期失败。"""

    def __init__(self, status: int, message: str, **extra):
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra

    def body(self) -> dict:
        return {"ok": False, "error": self.message, **self.extra}


@dataclass
class Deps:
    """外部世界的接口，测试整组替换掉，一个字节都不出网、不落盘。"""

    roots: Callable[[], dict]
    default_host: Callable[[dict], str]
    known_skills: Callable[[], list]
    ledger_path: Path
    local_index: Callable[[], dict]
    download: Callable[..., tuple] = installer.download_archive
    read_package: Callable[..., installer.Package] = installer.read_package
    apply_install: Callable[..., dict] = installer.apply_install
    post_install: Optional[Callable[[dict], dict]] = None


@dataclass
class _Cached:
    plan: installer.Plan
    payload: dict


class InstallApi:
    """一个 serve 进程一份。令牌随进程生，不落盘——磁盘上的旧文件就泄不出去。"""

    def __init__(self, port: int, deps: Deps, token: Optional[str] = None):
        self.port = int(port)
        self.deps = deps
        self.token = token or secrets.token_urlsafe(24)
        self.origins = {f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}"}
        self.hosts = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}
        self._plans: dict[str, _Cached] = {}

    # ------------------------------------------------------------ 安全门

    def guard(self, headers: dict) -> None:
        """三道门 + Content-Type。任何一道不过都抛 403，不解释是哪道不过。

        不告诉调用方错在哪：能问出「是 Origin 不对还是令牌不对」的，只有正在
        试探的人；真正的同源页面这三样天然都对。
        """
        get = lambda k: str(headers.get(k) or headers.get(k.lower()) or "")  # noqa: E731

        if get("Host") not in self.hosts:
            raise ApiError(403, "拒绝：请求不是发给本机看板的")
        if get("Origin") not in self.origins:
            raise ApiError(403, "拒绝：请求不是从本机看板发出的")
        ctype = get("Content-Type").split(";")[0].strip().lower()
        if ctype != "application/json":
            raise ApiError(403, "拒绝：请求不是本机看板发出的")
        supplied = get(TOKEN_HEADER)
        if not supplied or not hmac.compare_digest(supplied, self.token):
            raise ApiError(403, "拒绝：令牌不对，刷新看板重试")

    def parse_body(self, raw: bytes) -> dict:
        if len(raw) > MAX_BODY:
            raise ApiError(413, "请求体过大")
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ApiError(400, "请求体不是合法 JSON") from None
        if not isinstance(data, dict):
            raise ApiError(400, "请求体应当是一个对象")
        return data

    # ------------------------------------------------------------ 端点

    def session(self) -> dict:
        """页面开局拿令牌和宿主列表。GET，没有 CORS 头，跨源页面读不到响应。"""
        roots = self.deps.roots()
        return {
            "ok": True,
            "token": self.token,
            "hosts": sorted(roots),
            "default_host": self.deps.default_host(roots),
        }

    def plan(self, payload: dict) -> dict:
        """出计划：拉包、解包、算冲突，一个字节都不落盘。"""
        url = str(payload.get("url") or "").strip()
        if not url:
            raise ApiError(400, "没给仓库地址")

        roots = self.deps.roots()
        host = str(payload.get("host") or "").strip() or self.deps.default_host(roots)
        if host not in roots:
            raise ApiError(400, f"没有叫 {host} 的宿主。可选：{', '.join(sorted(roots))}")

        try:
            source = installer.parse_source(url)
            archive, ref = self.deps.download(source, str(payload.get("ref") or "") or None)
            subdir = str(payload.get("path") or "") or source.subdir
            package = self.deps.read_package(archive, str(subdir or ""))
            plan = installer.plan_install(
                source, ref, package, host, roots[host],
                existing=self.deps.known_skills(),
                as_name=str(payload.get("as") or "") or None,
                ledger=installer.load_ledger(self.deps.ledger_path))
        except installer.Ambiguous as e:
            raise ApiError(409, str(e), candidates=list(e.candidates)) from None
        except installer.InstallError as e:
            raise ApiError(400, str(e)) from None

        plan_id = secrets.token_urlsafe(12)
        body = self._describe(plan, plan_id)
        self._remember(plan_id, plan, body)
        return body

    def apply(self, payload: dict) -> dict:
        """按 plan_id 落盘。换个 URL 再进来是拿不到别的计划的。"""
        plan_id = str(payload.get("plan_id") or "").strip()
        cached = self._plans.pop(plan_id, None)
        if cached is None:
            raise ApiError(409, "这份安装计划已经过期，重新点一次「装到本机」")
        plan = cached.plan
        if not plan.ok:
            raise ApiError(409, "这份计划有阻塞项，没法装", blockers=list(plan.blockers))

        try:
            receipt = self.deps.apply_install(plan, self.deps.ledger_path)
        except installer.InstallError as e:
            raise ApiError(500, str(e)) from None

        report = self.deps.post_install(receipt) if self.deps.post_install else {"code": 0}
        return {
            "ok": report.get("code", 0) == 0,
            "id": receipt.get("id"),
            "name": receipt.get("name"),
            "host": receipt.get("host"),
            "files": len(receipt.get("files") or {}),
            "report": report,
            "local": self.deps.local_index(),
        }

    # ------------------------------------------------------------ 内部

    def _remember(self, plan_id: str, plan: installer.Plan, body: dict) -> None:
        self._plans[plan_id] = _Cached(plan=plan, payload=body)
        while len(self._plans) > PLAN_CACHE_MAX:
            self._plans.pop(next(iter(self._plans)))

    @staticmethod
    def _describe(plan: installer.Plan, plan_id: str) -> dict:
        package = plan.package
        names = sorted(package.files)
        return {
            "ok": plan.ok,
            "plan_id": plan_id,
            "source": plan.source.repo_url,
            "subdir": package.subdir,
            "ref": plan.ref,
            "commit": (package.commit or "")[:12],
            "name": package.name,
            "dir_name": plan.dir_name,
            "host": plan.host,
            # 目标绝对路径里带用户名。这条和写进 HTML 文件的本机索引不同，它只活在
            # 用户自己浏览器的一次响应里，而「要往哪儿写」是知情同意的核心信息。
            "target": str(plan.target),
            "file_count": len(package.files),
            "total_bytes": package.total_bytes,
            "files": [{"path": n, "bytes": len(package.files[n])} for n in names[:LIST_CAP]],
            "more_files": max(0, len(names) - LIST_CAP),
            "notes": list(plan.notes),
            "blockers": list(plan.blockers),
        }
