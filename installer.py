"""从 GitHub 装 skill 到本机宿主：解析来源 → 拉包 → 体检 → 写入 → 存证 → 回滚。

免费层能力，口径见 docs/monetization-handshake.md 的 D1 与 D5。四条铁律：

1. **同名绝不覆盖**。目标目录已存在就是硬阻塞，只能改名或先卸载。这个工具对
   已有 skill 一直是只读的，一键安装不能破这条。
2. **装前先出计划**。默认只打印「从哪拉、写到哪、有什么冲突」，写入必须显式确认。
3. **逐文件 sha256 入存证**。存证既是回滚依据，也是「这份文件是我写的、之后没被
   人改过」的唯一判据。
4. **回滚只删自己写过且未被改动的文件**。用户后来往目录里加的东西一律保留。

本模块不碰宿主路径与扫描根——那些归 skillpick.py。这里只做「给我一个来源和一个
目标目录，我负责安全地把它落地」，所以整条链路除下载外都能离线测。
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from version import __version__

SKILL_MD = "SKILL.md"
ARCHIVE_TMPL = "https://codeload.github.com/{owner}/{repo}/zip/{ref}"
# 没给 ref 时的尝试顺序。codeload 认 HEAD，省掉一次 API 调用（API 匿名限 60/小时，
# 而 codeload 不限），main/master 只是老仓库的兜底。
REF_CANDIDATES = ("HEAD", "main", "master")
LEDGER_VERSION = 1

# 解包上限。skill 是文本资产，这几个数量级已经很宽松；它们挡的是 zip 炸弹和
# 「把整个 monorepo 倒进宿主目录」这类事故。
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_FILES = 300
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_PATH_DEPTH = 12
LIST_CAP = 20

# Windows 保留设备名。创建这些名字的文件会失败或被静默改写，直接在门口拒掉。
_WIN_RESERVED = ({"con", "prn", "aux", "nul"}
                 | {f"com{i}" for i in range(1, 10)}
                 | {f"lpt{i}" for i in range(1, 10)})

_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")


class InstallError(Exception):
    """安装链路上可预期的失败。消息里必须带一句用户能照着做的下一步。"""


class Ambiguous(InstallError):
    """包里有多个 SKILL.md，必须由人挑一个（feed 里 93.6% 是 monorepo 子条目）。"""

    def __init__(self, message: str, candidates: list[str]):
        super().__init__(message)
        self.candidates = candidates


# ---------------------------------------------------------------- 来源解析

@dataclass(frozen=True)
class Source:
    owner: str
    repo: str
    ref: str | None = None
    subdir: str = ""

    @property
    def repo_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}"

    def archive_url(self, ref: str) -> str:
        return ARCHIVE_TMPL.format(owner=self.owner, repo=self.repo, ref=ref)


def _strip_skill_md(subdir: str) -> str:
    """路径指到 SKILL.md 本身时退回它所在目录（feed 的链接常常直接指文件）。"""
    parts = [p for p in subdir.split("/") if p]
    if parts and parts[-1].lower() == SKILL_MD.lower():
        parts.pop()
    return "/".join(parts)


def parse_source(text: str) -> Source:
    """认 GitHub 仓库页、tree/blob 子目录、raw 链接、git@ 与 owner/repo 简写。

    tree/blob 的 ref 只取一段：分支名带斜杠（`feature/x`）时无法在本地区分
    「ref 的第二段」和「子目录的第一段」，那种情况用 --ref 显式给。
    """
    raw = (text or "").strip()
    if not raw:
        raise InstallError("要装什么？给一个 GitHub 地址，例如 "
                           "https://github.com/owner/repo/tree/main/skills/foo")
    raw = raw.split("#", 1)[0].split("?", 1)[0].strip().rstrip("/")
    if raw.startswith("git@github.com:"):
        raw = "https://github.com/" + raw[len("git@github.com:"):]
    if "://" not in raw:
        lowered = raw.lower()
        for prefix in ("github.com/", "www.github.com/", "raw.githubusercontent.com/"):
            if lowered.startswith(prefix):
                raw = ("https://raw.githubusercontent.com/" if "raw." in prefix
                       else "https://github.com/") + raw[len(prefix):]
                break
        else:
            raw = "https://github.com/" + raw.lstrip("/")

    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        raise InstallError(f"看不出仓库是哪个：{text}。"
                           "要么给 https://github.com/owner/repo，要么给 owner/repo")
    owner, repo = parts[0], parts[1]
    if repo.lower().endswith(".git"):
        repo = repo[:-4]
    for value, label in ((owner, "owner"), (repo, "repo")):
        if not _NAME_RE.match(value):
            raise InstallError(f"{label} 名字不合法：{value!r}")

    ref: str | None = None
    subdir = ""
    rest = parts[2:]
    if host in ("raw.githubusercontent.com", "raw.github.com"):
        # raw 链接的形状是 /owner/repo/<ref>/<path...>，没有 tree/blob 这一段
        if rest:
            ref, subdir = rest[0], "/".join(rest[1:])
    elif host in ("github.com", "www.github.com"):
        if rest and rest[0] in ("tree", "blob"):
            if len(rest) < 2:
                raise InstallError(f"{rest[0]} 后面缺分支名：{text}")
            ref, subdir = rest[1], "/".join(rest[2:])
        elif rest:
            raise InstallError(
                f"看不懂的 GitHub 路径 …/{'/'.join(rest[:3])}。"
                "给仓库首页，或 .../tree/<分支>/<子目录>，子目录也可以用 --path 单独给")
    else:
        raise InstallError(f"第一版只认 github.com，收到的是 {host or text}")

    return Source(owner=owner, repo=repo, ref=ref, subdir=_strip_skill_md(subdir))


# ---------------------------------------------------------------- 下载

def _urlopen(url: str, timeout: int = 30) -> bytes:
    request = urllib.request.Request(
        url, headers={"User-Agent": f"skill-picker/{__version__}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            declared = response.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) > MAX_ARCHIVE_BYTES:
                raise InstallError(
                    f"压缩包 {int(declared) // 1048576} MB，超过 "
                    f"{MAX_ARCHIVE_BYTES // 1048576} MB 上限：{url}")
            data = response.read(MAX_ARCHIVE_BYTES + 1)
    except urllib.error.HTTPError as e:
        raise InstallError(f"下载失败 HTTP {e.code}：{url}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise InstallError(f"下载失败（网络不可达或被墙？）：{url} —— {e}") from e
    if len(data) > MAX_ARCHIVE_BYTES:
        raise InstallError(f"压缩包超过 {MAX_ARCHIVE_BYTES // 1048576} MB 上限：{url}")
    return data


def download_archive(source: Source, ref: str | None = None,
                     opener=None) -> tuple[bytes, str]:
    """拉 zipball，返回（字节, 实际用上的 ref）。opener 可注入，测试不出网。"""
    opener = opener or _urlopen
    explicit = ref or source.ref
    candidates = [explicit] if explicit else list(REF_CANDIDATES)
    failures = []
    for candidate in candidates:
        try:
            return opener(source.archive_url(candidate)), candidate
        except InstallError as e:
            failures.append(f"{candidate}: {e}")
    raise InstallError(f"{source.repo_url} 没能下载下来。试过 "
                       f"{'、'.join(candidates)}\n  " + "\n  ".join(failures))


# ---------------------------------------------------------------- 解包

@dataclass
class Package:
    dir_name: str
    name: str
    description: str
    files: dict[str, bytes]
    subdir: str = ""
    commit: str = ""
    nested_skills: list[str] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        return sum(len(b) for b in self.files.values())


def _archive_prefix(names: list[str]) -> str:
    """zipball 顶层统一是 `{repo}-{ref}/`，剥掉它再看仓库内部路径。"""
    tops = {n.split("/", 1)[0] for n in names if "/" in n}
    if len(tops) == 1 and all("/" in n for n in names):
        return tops.pop() + "/"
    return ""


def _check_rel(rel: str) -> None:
    if not rel:
        raise InstallError("包内有空路径条目")
    if "\\" in rel:
        raise InstallError(f"包内路径含反斜杠，拒绝：{rel}")
    if any(ord(c) < 32 for c in rel):
        raise InstallError(f"包内路径含控制字符，拒绝：{rel!r}")
    if rel.startswith("/") or re.match(r"^[A-Za-z]:", rel):
        raise InstallError(f"包内路径是绝对路径，拒绝：{rel}")
    segments = rel.split("/")
    if len(segments) > MAX_PATH_DEPTH:
        raise InstallError(f"包内路径超过 {MAX_PATH_DEPTH} 层，拒绝：{rel}")
    for segment in segments:
        if segment in ("", ".", ".."):
            raise InstallError(f"包内路径想跳出目标目录，拒绝：{rel}")
        if segment != segment.strip() or segment.endswith("."):
            # Windows 会把尾随空格/点静默吃掉，落地文件名和存证就对不上了
            raise InstallError(f"包内路径含尾随空格或点，拒绝：{rel}")
        if segment.split(".", 1)[0].lower() in _WIN_RESERVED:
            raise InstallError(f"包内路径命中 Windows 保留名，拒绝：{rel}")


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    return bool(info.create_system == 3 and (info.external_attr >> 16) & 0o170000 == 0o120000)


def _locate_skill_dir(rels: dict[str, zipfile.ZipInfo], subdir: str) -> str:
    holders = sorted({rel.rsplit("/", 1)[0] if "/" in rel else ""
                      for rel in rels if rel.rsplit("/", 1)[-1].lower() == SKILL_MD.lower()})
    if subdir:
        wanted = subdir.strip("/")
        lowered = {h.lower(): h for h in holders}
        if wanted.lower() in lowered:
            return lowered[wanted.lower()]
        inside = [h for h in holders if h.lower().startswith(wanted.lower() + "/")]
        if len(inside) == 1:
            return inside[0]
        if inside:
            raise Ambiguous(
                f"{wanted}/ 下面有 {len(inside)} 个 SKILL.md，挑一个用 --path 指定：",
                inside[:LIST_CAP])
        raise InstallError(f"{wanted}/{SKILL_MD} 在包里不存在。"
                           f"包里共有 {len(holders)} 个 {SKILL_MD}，用 --path 指一个")
    if not holders:
        raise InstallError(f"这个仓库里没有 {SKILL_MD}，装不了")
    if "" in holders:
        return ""
    if len(holders) == 1:
        return holders[0]
    raise Ambiguous(
        f"这个仓库里有 {len(holders)} 个 {SKILL_MD}（monorepo），"
        "用 --path 指定要装哪一个：", holders[:LIST_CAP])


def read_package(data: bytes, subdir: str = "", parse_meta=None) -> Package:
    """从 zipball 里取出一个 skill 目录。所有安全门都在这一层。"""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise InstallError("下载到的不是 zip 包（仓库名或分支名对吗？）") from e

    with archive:
        commit = (archive.comment or b"").decode("utf-8", "replace").strip()
        commit = commit if _SHA1_RE.match(commit) else ""
        infos = [i for i in archive.infolist() if not i.is_dir()]
        prefix = _archive_prefix([i.filename for i in infos])
        rels: dict[str, zipfile.ZipInfo] = {}
        for info in infos:
            if prefix and not info.filename.startswith(prefix):
                continue
            rels[info.filename[len(prefix):]] = info

        base = _locate_skill_dir(rels, subdir)
        scope = base + "/" if base else ""
        picked = {rel: info for rel, info in rels.items() if rel.startswith(scope)}
        if len(picked) > MAX_FILES:
            raise InstallError(f"这个 skill 目录有 {len(picked)} 个文件，"
                               f"超过 {MAX_FILES} 个上限，先去 GitHub 看看是不是选错了目录")

        files: dict[str, bytes] = {}
        total = 0
        lowered: dict[str, str] = {}
        for rel, info in sorted(picked.items()):
            inner = rel[len(scope):]
            _check_rel(inner)
            if _is_symlink(info):
                raise InstallError(f"包内含符号链接，拒绝：{inner}")
            total += info.file_size
            if total > MAX_TOTAL_BYTES:
                raise InstallError(f"解包体积超过 {MAX_TOTAL_BYTES // 1048576} MB 上限，"
                                   "拒绝写入")
            key = inner.lower()
            if key in lowered:
                # Windows 大小写不敏感，两个只差大小写的条目会互相覆盖
                raise InstallError(f"包内有仅大小写不同的两个路径，拒绝："
                                   f"{lowered[key]} 与 {inner}")
            lowered[key] = inner
            with archive.open(info) as handle:
                payload = handle.read(MAX_TOTAL_BYTES + 1)
            if len(payload) != info.file_size:
                raise InstallError(f"解压后大小与包内声明不符，拒绝：{inner}")
            files[inner] = payload

    if SKILL_MD not in files:
        actual = next((k for k in files if k.lower() == SKILL_MD.lower()), None)
        if actual is None:
            raise InstallError(f"选中的目录里没有 {SKILL_MD}")
        files[SKILL_MD] = files.pop(actual)

    if parse_meta is None:
        from skillpick import fallback_meta, parse_frontmatter  # 单一真相源，延迟导入避免循环

        def parse_meta(text: str) -> dict:
            return parse_frontmatter(text) or fallback_meta(text)

    text = files[SKILL_MD].decode("utf-8", errors="replace")
    meta = parse_meta(text) or {}
    dir_name = base.rsplit("/", 1)[-1] if base else ""
    nested = sorted(rel.rsplit("/", 1)[0] for rel in files
                    if rel.rsplit("/", 1)[-1].lower() == SKILL_MD.lower() and "/" in rel)
    return Package(
        dir_name=dir_name or (meta.get("name") or "").strip(),
        name=(meta.get("name") or "").strip() or dir_name,
        description=(meta.get("description") or "").strip(),
        files=files,
        subdir=base,
        commit=commit,
        nested_skills=nested[:LIST_CAP],
    )


# ---------------------------------------------------------------- 装前计划

@dataclass
class Plan:
    source: Source
    ref: str
    package: Package
    host: str
    target: Path
    dir_name: str
    blockers: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.blockers


def _check_dir_name(dir_name: str) -> None:
    if not dir_name:
        raise InstallError("推不出目录名（包里既没有子目录名也没有 frontmatter name），"
                           "用 --as <目录名> 显式给一个")
    if "/" in dir_name or "\\" in dir_name:
        raise InstallError(f"目录名不能带路径分隔符：{dir_name}")
    _check_rel(dir_name)


def _where(skill: dict) -> str:
    """已有同名 skill 的位置，尽量表达成相对宿主根的路径。

    必须带上位置：同名的那份常常就在同一个宿主里（只是嵌在别的目录下），光说
    「在宿主 claude-code」会和上一行的目标路径读起来自相矛盾——明明说要写进去，
    又说那儿已经有一个，却没拦。
    """
    path, root = skill.get("path") or "", skill.get("root") or ""
    if not path:
        return skill.get("dir_name") or "?"
    parent = Path(path).parent
    if root:
        try:
            return Path(parent).relative_to(root).as_posix()
        except ValueError:
            pass
    return parent.name


def plan_install(source: Source, ref: str, package: Package, host: str,
                 target_root: Path, existing: list[dict] | None = None,
                 as_name: str | None = None, ledger: dict | None = None) -> Plan:
    """把「要写什么、写到哪、撞了什么」算清楚，一个字节都还没落盘。"""
    dir_name = (as_name or package.dir_name or "").strip()
    _check_dir_name(dir_name)
    target = target_root / dir_name
    plan = Plan(source=source, ref=ref, package=package, host=host,
                target=target, dir_name=dir_name)

    if target.exists():
        plan.blockers.append(
            f"目标目录已存在，同名绝不覆盖：{target}\n"
            f"      换个目录名：--as <新名字>；确认不要它了：先 "
            f"skillpick.py remove {host}:{dir_name}")
    if not package.files:
        plan.blockers.append("包里一个文件都没有")

    for receipt in (ledger or {}).get("installs", []):
        if receipt.get("id") == f"{host}:{dir_name}" and not target.exists():
            plan.notes.append("存证里有这条但目录已不在，装完会更新存证")

    same_name = [s for s in (existing or [])
                 if (s.get("name") or "").strip().lower() == package.name.strip().lower()
                 or (s.get("dir_name") or "").strip().lower() == dir_name.lower()]
    for skill in same_name[:LIST_CAP]:
        plan.notes.append(
            f"本机已有同名：「{skill.get('name')}」在 {skill.get('host')} 的 "
            f"{_where(skill)}（不覆盖，装完会被 G3 统计成同名多份）")
    if package.nested_skills:
        plan.notes.append(
            f"包内另有 {len(package.nested_skills)} 个嵌套 {SKILL_MD}，"
            f"装进去会被扫成多个 skill：{', '.join(package.nested_skills[:5])}")
    return plan


def render_plan(plan: Plan) -> str:
    """装前展示：来源仓库、ref/commit、目标路径、文件清单、冲突（D5 第一条）。"""
    package = plan.package
    lines = [
        "安装计划（还没写任何文件）",
        f"  来源    {plan.source.repo_url}"
        + (f"  子目录 {package.subdir}" if package.subdir else ""),
        f"  版本    ref={plan.ref}"
        + (f"  commit={package.commit[:12]}" if package.commit else "  commit=未知"),
        f"  名字    {package.name or '(空)'}",
        f"  写入    {plan.target}  （宿主 {plan.host}）",
        f"  内容    {len(package.files)} 个文件，{package.total_bytes / 1024:.1f} KB",
    ]
    for rel in sorted(package.files)[:LIST_CAP]:
        lines.append(f"            {rel}  {len(package.files[rel])} B")
    if len(package.files) > LIST_CAP:
        lines.append(f"            …… 另有 {len(package.files) - LIST_CAP} 个")
    for note in plan.notes:
        lines.append(f"  提示    {note}")
    for blocker in plan.blockers:
        lines.append(f"  阻塞    {blocker}")
    return "\n".join(lines)


# ---------------------------------------------------------------- 存证

def load_ledger(path: Path) -> dict:
    if not path.exists():
        return {"version": LEDGER_VERSION, "installs": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": LEDGER_VERSION, "installs": []}
    if not isinstance(data, dict) or not isinstance(data.get("installs"), list):
        return {"version": LEDGER_VERSION, "installs": []}
    data.setdefault("version", LEDGER_VERSION)
    return data


def save_ledger(path: Path, ledger: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")


def find_receipt(ledger: dict, key: str) -> dict | None:
    """按 `host:dir` 精确找，找不到再按目录名/名字唯一匹配。"""
    key = (key or "").strip()
    if not key:
        return None
    installs = ledger.get("installs", [])
    for receipt in installs:
        if receipt.get("id", "").lower() == key.lower():
            return receipt
    loose = [r for r in installs
             if key.lower() in ((r.get("dir_name", "").lower()), (r.get("name", "").lower()))]
    if len(loose) == 1:
        return loose[0]
    if loose:
        raise InstallError(f"{key} 装了 {len(loose)} 份，用完整 id 指定："
                           + "、".join(r.get("id", "?") for r in loose))
    return None


def apply_install(plan: Plan, ledger_path: Path, now: datetime | None = None) -> dict:
    """按计划写盘并入存证。中途任何失败都把已写的目录整体删掉，不留半个 skill。"""
    if plan.blockers:
        raise InstallError("计划有阻塞项，拒绝写入：\n  " + "\n  ".join(plan.blockers))
    target = plan.target
    target.mkdir(parents=True, exist_ok=False)  # 已存在就抛，堵住 plan 与 apply 之间的抢跑
    files: dict[str, dict] = {}
    try:
        for rel in sorted(plan.package.files):
            payload = plan.package.files[rel]
            destination = target / Path(*rel.split("/"))
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(payload)
            files[rel] = {"sha256": hashlib.sha256(payload).hexdigest(),
                          "bytes": len(payload)}
    except OSError as e:
        shutil.rmtree(target, ignore_errors=True)
        raise InstallError(f"写入失败，已回滚 {target}：{e}") from e

    stamp = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    receipt = {
        "id": f"{plan.host}:{plan.dir_name}",
        "name": plan.package.name,
        "dir_name": plan.dir_name,
        "host": plan.host,
        "target": str(target),
        "source": {
            "url": plan.source.repo_url,
            "owner": plan.source.owner,
            "repo": plan.source.repo,
            "ref": plan.ref,
            "commit": plan.package.commit,
            "subdir": plan.package.subdir,
            "archive": plan.source.archive_url(plan.ref),
        },
        "installed_at": stamp,
        "tool_version": __version__,
        "files": files,
    }
    ledger = load_ledger(ledger_path)
    ledger["installs"] = [r for r in ledger.get("installs", [])
                          if r.get("id") != receipt["id"]] + [receipt]
    save_ledger(ledger_path, ledger)
    return receipt


# ---------------------------------------------------------------- 体检与回滚

def verify_receipt(receipt: dict) -> dict[str, str]:
    """逐文件比对存证里的 sha256：ok / modified / missing。"""
    target = Path(receipt.get("target", ""))
    states = {}
    for rel, meta in (receipt.get("files") or {}).items():
        path = target / Path(*rel.split("/"))
        if not path.is_file():
            states[rel] = "missing"
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        states[rel] = "ok" if digest == meta.get("sha256") else "modified"
    return states


def remove_install(receipt: dict, ledger_path: Path, force: bool = False) -> dict:
    """卸载：只删存证里记着、且内容仍是我们写的那份的文件。

    改动过的文件默认不删——那已经是用户的东西了。目录里安装后新增的文件一律保留。
    """
    target = Path(receipt.get("target", ""))
    if not receipt.get("files"):
        raise InstallError(f"{receipt.get('id')} 的存证里没有文件清单，不敢删")
    if len(target.parts) <= 2:
        raise InstallError(f"存证里的目标路径太靠根，拒绝删除：{target}")

    states = verify_receipt(receipt)
    modified = sorted(rel for rel, state in states.items() if state == "modified")
    if modified and not force:
        raise InstallError(
            f"{receipt.get('id')} 有 {len(modified)} 个文件装完之后被改过："
            + "、".join(modified[:LIST_CAP])
            + "\n  这些改动会一起丢掉。确认要删就加 --force")

    removed, failed = [], []
    for rel, state in sorted(states.items()):
        if state == "missing":
            continue
        try:
            (target / Path(*rel.split("/"))).unlink()
            removed.append(rel)
        except OSError as e:
            failed.append(f"{rel}: {e}")

    leftover = []
    if target.is_dir():
        for path in sorted(target.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if path.is_dir() and not any(path.iterdir()):
                path.rmdir()
            elif path.is_file():
                leftover.append(str(path.relative_to(target)))
        if not any(target.iterdir()):
            target.rmdir()

    ledger = load_ledger(ledger_path)
    ledger["installs"] = [r for r in ledger.get("installs", [])
                          if r.get("id") != receipt.get("id")]
    save_ledger(ledger_path, ledger)
    return {"id": receipt.get("id"), "target": str(target), "removed": removed,
            "modified": modified, "leftover": sorted(leftover), "failed": failed,
            "dir_gone": not target.exists()}
