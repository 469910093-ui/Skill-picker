#!/usr/bin/env python3
"""skill-picker: 本机 agent skills 的扫描、查重、场景聚类与路由工具。

零依赖（仅 Python 标准库），零服务器，数据全在本地。
灵感来自 tab-out（https://github.com/zarazhangrui/tab-out）：
不管理数据，只读取已存在的事实（SKILL.md），自动聚类、暴露冗余、让用户决策。

用法:
  python skillpick.py scan            扫描所有 skill 目录，生成 catalog + 门禁
  python skillpick.py check           同 scan，退出码 0=可信 / 2=门禁 FAIL
  python skillpick.py match "意图"    共享打分引擎检索候选（--top N / --json）
  python skillpick.py install         scan + 把 meta-skill 装进四宿主 + 工具自拷贝
  python skillpick.py report          打印上次扫描的摘要
"""

import ast
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import matching

HOME = Path.home()
DATA_DIR = HOME / ".skill-picker"
CATALOG_JSON = DATA_DIR / "catalog.json"
CATALOG_MD = DATA_DIR / "catalog.md"
CONFIG_JSON = DATA_DIR / "config.json"
INSTALL_MANIFEST = DATA_DIR / "install.json"
SELF_NAME = "skill-picker"
TOOL_FILES = ["skillpick.py", "matching.py", "dashboard.py", "discover.py", "rules.json",
              "translations.json", "mcp_server.py", "version.py"]
MCP_SERVER_NAME = "skill-picker"

# 扫描根目录 -> 宿主标签。存在才扫，不存在跳过。
SCAN_ROOTS = [
    (HOME / ".claude" / "skills", "claude-code"),
    (HOME / ".claude" / "plugins", "claude-plugin"),
    (HOME / ".cursor" / "skills", "cursor"),
    (HOME / ".cursor" / "skills-cursor", "cursor-builtin"),
    (HOME / ".cursor" / "plugins" / "cache", "cursor-plugin"),
    (HOME / ".agents" / "skills", "codex"),
    (HOME / ".agents" / "plugins", "codex-plugin"),
    (HOME / ".codex" / "skills", "codex"),
    (HOME / ".codex" / "plugins" / "cache", "codex-plugin"),
    (HOME / ".openclaw" / "skills", "openclaw"),
    (HOME / ".openclaw" / "workspace" / "skills", "openclaw"),
    (HOME / ".gemini" / "skills", "gemini"),
    (HOME / ".config" / "opencode" / "skills", "opencode"),
]

# 覆盖率门禁的全盘发现范围：这些基目录下任何 SKILL.md 都必须被扫描根覆盖
DISCOVER_BASES = [
    HOME / ".claude", HOME / ".cursor", HOME / ".agents",
    HOME / ".codex", HOME / ".openclaw", HOME / ".gemini", HOME / ".config",
]
PRUNE_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv", "dist", "build",
              "terminals", "agent-transcripts", ".tmp", "tmp"}

# meta-skill 安装目标：四宿主
INSTALL_TARGETS = {
    "cursor": HOME / ".cursor" / "skills" / SELF_NAME / "SKILL.md",
    "claude-code": HOME / ".claude" / "skills" / SELF_NAME / "SKILL.md",
    "codex": HOME / ".agents" / "skills" / SELF_NAME / "SKILL.md",
    "openclaw": HOME / ".openclaw" / "skills" / SELF_NAME / "SKILL.md",
}

RULES = matching.load_rules()


def load_scan_roots() -> list[tuple[Path, str]]:
    """内置扫描根 + 用户自定义目录（~/.skill-picker/config.json 的 extra_roots）。"""
    roots = list(SCAN_ROOTS)
    if CONFIG_JSON.exists():
        try:
            cfg = json.loads(CONFIG_JSON.read_text(encoding="utf-8"))
            for item in cfg.get("extra_roots", []):
                roots.append((Path(item["path"]), item.get("host", "custom")))
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            print(f"[warn] config.json 解析失败，忽略 extra_roots: {e}")
    return roots


# ---------------------------------------------------------------- 扫描与解析

def parse_frontmatter(text: str) -> dict:
    """极简 YAML frontmatter 解析：只取顶层 key，支持 >- / | 多行值。"""
    if not text.lstrip().startswith("---"):
        return {}
    lines = text.lstrip().splitlines()
    end = None
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            end = i
            break
    if end is None:
        return {}
    fm, key, buf = {}, None, []
    for line in lines[1:end]:
        m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if m and not line[:1].isspace():
            if key:
                fm[key] = " ".join(buf).strip()
            key = m.group(1).lower()
            value = m.group(2).strip()
            buf = [] if value in (">", ">-", "|", "|-", ">+", "|+") else [value]
        elif key is not None:
            buf.append(line.strip())
    if key:
        fm[key] = " ".join(buf).strip()
    return fm


def extract_keywords(text: str) -> str:
    """从 SKILL.md 正文提炼关键词：标题、加粗短语、行内代码名（≤400 字符）。"""
    body = text
    if body.lstrip().startswith("---"):
        parts = body.lstrip().split("---", 2)
        if len(parts) == 3:
            body = parts[2]
    heads = re.findall(r"^#{1,4}\s+(.+)$", body, re.M)
    bolds = re.findall(r"\*\*([^*\n]{2,30})\*\*", body)
    codes = re.findall(r"`([A-Za-z0-9_./\-]{3,40})`", body)
    out, seen = [], set()
    for t in heads + bolds + codes[:30]:
        t = re.sub(r"[#*`\[\]()]+", "", t).strip()
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return " ".join(out)[:400]


def fallback_meta(text: str) -> dict:
    """无 frontmatter 时：第一个标题当 name，其后第一段当 description。"""
    name, desc_lines, in_body = "", [], False
    for line in text.splitlines():
        stripped = line.strip()
        if not name and stripped.startswith("#"):
            name = stripped.lstrip("#").strip()
            in_body = True
            continue
        if in_body:
            if not stripped and desc_lines:
                break
            if stripped and not stripped.startswith("#"):
                desc_lines.append(stripped)
    return {"name": name, "description": " ".join(desc_lines)}


def scan_skills() -> list[dict]:
    skills, seen_paths = [], set()
    for root, host in load_scan_roots():
        if not root.is_dir():
            continue
        for skill_md in sorted(root.rglob("SKILL.md")):
            if "node_modules" in skill_md.parts:
                continue
            # 跳过 skill 包内部再嵌套的 plugins/.../skills/...（如 frontend-slides 自带插件包）
            parts = skill_md.parts
            if "skills" in parts:
                i = parts.index("skills")
                if "plugins" in parts[i + 1:]:
                    continue
            resolved = str(skill_md.resolve()).lower()
            if resolved in seen_paths:
                continue
            seen_paths.add(resolved)
            try:
                text = skill_md.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            fm = parse_frontmatter(text) or fallback_meta(text)
            name = (fm.get("name") or skill_md.parent.name).strip()
            if name == SELF_NAME:
                continue  # 不索引自己
            desc = (fm.get("description") or "").strip()
            primary, labels = matching.categorize(name, desc, RULES)
            skills.append({
                "name": name,
                "dir_name": skill_md.parent.name,
                "description": desc,
                "keywords": extract_keywords(text),
                "category": primary,
                "categories": labels,
                "path": str(skill_md),
                "host": host,
                "root": str(root),
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
            })
    return skills


# ---------------------------------------------------------------- 查重

def _bigrams(text: str) -> set:
    normalized = re.sub(r"[\s，。、；：！？,.;:!?/()（）\[\]【】\"'`*>-]+", "", text.lower())
    return {normalized[i:i + 2] for i in range(len(normalized) - 1)}


def jaccard(a: str, b: str) -> float:
    ga, gb = _bigrams(a), _bigrams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def _plugin_family(path_str: str):
    """同一插件家族（marketplace/官方套件）内部的相似是正常分工，不算重叠。"""
    parts = tuple(p.lower() for p in Path(path_str).parts)
    if "plugins" in parts:
        i = parts.index("plugins")
        return parts[:i + 3] if len(parts) > i + 2 else parts[:i + 1]
    return None


def find_duplicates(skills: list[dict]) -> dict:
    by_name: dict[str, list[dict]] = {}
    for s in skills:
        by_name.setdefault(s["dir_name"].lower(), []).append(s)

    def _distinct_external_plugins(group: list[dict]) -> bool:
        """discord/imessage/telegram 各自的 access/configure 只是同名，不是重复安装。"""
        products = set()
        for g in group:
            parts = Path(g["path"]).parts
            if "external_plugins" not in parts:
                return False
            i = parts.index("external_plugins")
            if i + 1 >= len(parts):
                return False
            products.add(parts[i + 1])
        return len(products) == len(group) and len(products) > 1

    same_name = []
    for name, group in sorted(by_name.items()):
        if len(group) < 2:
            continue
        if _distinct_external_plugins(group):
            continue
        hashes = {g["sha256"] for g in group}
        same_name.append({
            "name": name,
            "status": "identical" if len(hashes) == 1 else "drifted",
            "copies": [{"path": g["path"], "host": g["host"], "sha256": g["sha256"]} for g in group],
        })

    # 重叠检测去噪：仅同主分类内比较；同插件家族内部跳过
    threshold = RULES["overlap_threshold"]
    overlaps = []
    for i in range(len(skills)):
        for j in range(i + 1, len(skills)):
            a, b = skills[i], skills[j]
            if a["dir_name"].lower() == b["dir_name"].lower():
                continue
            if len(a["description"]) < 20 or len(b["description"]) < 20:
                continue
            if not (set(a["categories"]) & set(b["categories"])):
                continue
            fa, fb = _plugin_family(a["path"]), _plugin_family(b["path"])
            if fa is not None and fa == fb:
                continue
            score = jaccard(a["description"], b["description"])
            if score >= threshold:
                overlaps.append({
                    "a": {"name": a["name"], "path": a["path"]},
                    "b": {"name": b["name"], "path": b["path"]},
                    "similarity": round(score, 3),
                })
    overlaps.sort(key=lambda x: -x["similarity"])
    return {"same_name": same_name, "overlapping": overlaps}


# ---------------------------------------------------------------- 门禁

def discover_all_skill_files() -> list[Path]:
    """全盘发现：DISCOVER_BASES 下所有 SKILL.md（剪枝重型目录）。这是覆盖率的地面真值。"""
    found = []
    for base in DISCOVER_BASES:
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in PRUNE_DIRS]
            if "SKILL.md" in filenames:
                found.append(Path(dirpath) / "SKILL.md")
    return found


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def local_import_closure(entries: list[str], source: Path) -> set[str]:
    """从 entries 出发递归收集所有本地 import（解析到 source 下同名 .py 的那些）。

    只认单层文件模块，够用：本仓库是平铺结构，没有包。函数内的延迟 import 也要算进来
    （cmd_scan 里的 discover / dashboard 就是这么导的），所以用 ast.walk 而不是只看顶层。
    """
    seen: set[str] = set()
    queue = [e for e in entries if e.endswith(".py")]
    while queue:
        name = queue.pop()
        if name in seen:
            continue
        seen.add(name)
        f = source / name
        if not f.exists():
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                mods = [node.module]
            else:
                continue
            for m in mods:
                cand = m.split(".")[0] + ".py"
                if (source / cand).exists():
                    queue.append(cand)
    return seen


def read_install_manifest(path: Path | None = None) -> dict:
    try:
        return json.loads((path or INSTALL_MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_install_manifest(src_dir: Path, files: list[str], data_dir: Path | None = None) -> Path:
    """记下副本的来源与安装时哈希：G0 靠 source_dir 定位克隆，靠 files 认出就地改动。"""
    data_dir = data_dir or DATA_DIR
    target = data_dir / INSTALL_MANIFEST.name
    source = str(src_dir)
    if src_dir.resolve() == data_dir.resolve():
        # 从家目录自己跑 install，别把 source_dir 指向自己，否则永远比不出漂移
        source = read_install_manifest(target).get("source_dir", "")
    target.write_text(json.dumps({
        "source_dir": source,
        "installed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "files": {f: _sha256(data_dir / f) for f in files if (data_dir / f).exists()},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def tool_copy_drift(data_dir: Path | None = None, running_dir: Path | None = None) -> dict:
    """G0：家目录的工具副本是否与克隆目录一致、且没缺件。

    产品的真实调用路径是 ~/.skill-picker —— meta-skill、MCP 注册项、catalog.md 里写的
    命令全指向那份副本，而只有 install 会刷新它。`git pull` 后不重装，家目录跑的就还是
    旧代码：修好的回归会继续 FAIL，门禁再按 AGENTS.md 把用户打发去仓库报 issue。
    """
    data_dir = data_dir or DATA_DIR
    gate = {"id": "G0", "name": "工具副本", "items": [], "action": ""}
    if not any((data_dir / f).exists() for f in TOOL_FILES):
        gate.update(status="skip", detail=f"{data_dir} 下还没有工具副本（尚未 install）")
        return gate

    manifest = read_install_manifest(data_dir / INSTALL_MANIFEST.name)
    running = (running_dir or Path(__file__).resolve().parent).resolve()
    if running != data_dir.resolve():
        source = running          # 正从克隆跑，源就是自己
    elif manifest.get("source_dir"):
        source = Path(manifest["source_dir"])
    else:
        gate.update(status="skip",
                    detail="缺安装清单，无法定位克隆目录（重跑一次 install 即启用本门禁）")
        return gate
    if not (source / "skillpick.py").exists():
        gate.update(status="skip", detail=f"克隆目录已不在 {source}（自拷贝模型允许删掉克隆）")
        return gate

    # 需要拷过去的 = TOOL_FILES ∪ 工具文件递归 import 到的本地模块。求并集而不是只信
    # TOOL_FILES：version.py 就是这么漏的（mcp_server 导它，名单里没有，家目录那份
    # MCP 一启动就 ModuleNotFoundError）。
    required = sorted({f for f in TOOL_FILES if (source / f).exists()}
                      | local_import_closure(TOOL_FILES, source))
    recorded = manifest.get("files", {})
    missing, stale, edited, checked = [], [], [], []
    for f in required:
        src = source / f
        if not src.exists():
            continue
        checked.append(f)
        dst = data_dir / f
        if not dst.exists():
            missing.append(f"{f}（家目录没有这个文件）")
            continue
        dst_hash = _sha256(dst)
        if dst_hash != _sha256(src):
            stale.append(f"{f}（家目录副本 ≠ 克隆）")
        if recorded.get(f) and dst_hash != recorded[f]:
            edited.append(f"{f}（≠ 安装时记录，副本被就地改过）")

    since = manifest.get("installed_at", "未知时间")
    install_cmd = f'python "{source / "skillpick.py"}" install'
    if missing or stale:
        gate.update(
            status="fail", items=missing + stale + edited,
            detail=f"{len(missing) + len(stale)}/{len(checked)} 个工具文件缺失或与克隆不一致"
                   f"（家目录副本停留在 {since}）；G1–G4 的结论可能来自旧代码",
            action=f"重跑 {install_cmd} 刷新副本 —— 这不是引擎回归，别去报 issue")
    elif edited:
        gate.update(
            status="warn", items=edited,
            detail=f"{len(edited)} 个副本在安装后被就地改过（当前与克隆一致，但下次 install 会覆盖）",
            action="就地改动不进版本管理；要保留请改克隆目录再 install")
    else:
        gate.update(status="pass", detail=f"{len(checked)} 个工具文件与 {source} 逐字节一致")
    return gate


def run_gates(catalog: dict) -> list[dict]:
    """五道强制门禁：G0 工具副本 / G1 覆盖率 / G2 解析质量 / G3 漂移提醒 / G4 匹配黄金用例。"""
    # 用字面路径比较：skills 目录里常见符号链接，resolve 会解析到根目录之外造成误报
    roots = [os.path.normcase(str(r)) for r, _ in load_scan_roots() if r.is_dir()]

    def covered(p: Path) -> bool:
        n = os.path.normcase(str(p))
        return any(n.startswith(r + os.sep) or n == r for r in roots)

    all_files = discover_all_skill_files()
    uncovered = sorted(str(p) for p in all_files if not covered(p))
    gates = [tool_copy_drift(), {
        "id": "G1", "name": "覆盖率（全）",
        "status": "pass" if not uncovered else "fail",
        "detail": f"全盘发现 {len(all_files)} 个 SKILL.md，未被扫描根覆盖 {len(uncovered)} 个",
        "items": uncovered[:30],
        "action": "" if not uncovered else
                  f'把上述目录加入 {CONFIG_JSON} 的 extra_roots 后重新 scan',
    }]

    no_desc = [s["name"] for s in catalog["skills"] if len(s["description"]) < 10]
    ratio = len(no_desc) / max(len(catalog["skills"]), 1)
    gates.append({
        "id": "G2", "name": "解析质量（准）",
        "status": "pass" if ratio <= 0.10 else "warn",
        "detail": f"{len(no_desc)} 个 skill 缺有效描述（占 {ratio:.0%}，阈值 10%），已回退用正文首段/关键词参与匹配",
        "items": no_desc[:30],
        "action": "",
    })

    drifted = [d["name"] for d in catalog["duplicates"]["same_name"] if d["status"] == "drifted"]
    gates.append({
        "id": "G3", "name": "漂移提醒",
        "status": "pass" if not drifted else "warn",
        "detail": f"{len(drifted)} 组同名 skill 内容漂移（多端行为可能不一致）",
        "items": drifted,
        "action": "" if not drifted else "在「理技能」tab 查看差异，确认后自行合并（工具不代改）",
    })

    # G4：匹配质量黄金用例（期望 skill 未安装则跳过；装了却打不中 = FAIL）
    index = matching.build_index(catalog["skills"], RULES)
    golden = matching.run_golden(index, RULES)
    failed = [g for g in golden if g["status"] == "fail"]
    skipped = [g for g in golden if g["status"] == "skip"]
    gates.append({
        "id": "G4", "name": "匹配黄金用例",
        "status": "pass" if not failed else "fail",
        "detail": f"{len(golden)} 条用例：{len(golden) - len(failed) - len(skipped)} 过 / "
                  f"{len(failed)} 败 / {len(skipped)} 跳过",
        "items": [f"{g['query']} -> {g['detail']}" for g in failed[:10]],
        "action": "" if not failed else "匹配引擎回归：检查 rules.json 的 syn/权重或新装 skill 描述",
    })
    return gates


def print_gates(gates: list[dict]) -> None:
    mark = {"pass": "PASS", "warn": "WARN", "fail": "FAIL", "skip": "SKIP"}
    for g in gates:
        print(f"[gate {g['id']}] {mark[g['status']]}  {g['name']}: {g['detail']}")
        for it in g["items"][:5]:
            print(f"    - {it}")
        if g.get("action"):
            print(f"    => {g['action']}")


# ---------------------------------------------------------------- 输出

def build_catalog() -> dict:
    skills = scan_skills()
    duplicates = find_duplicates(skills)
    categories: dict[str, list] = {}
    for s in skills:
        categories.setdefault(s["category"], []).append(s["name"])
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "skill_count": len(skills),
        "categories": {k: sorted(set(v)) for k, v in sorted(categories.items())},
        "duplicates": duplicates,
        "skills": skills,
    }


def write_catalog_md(catalog: dict) -> None:
    """瘦身版 catalog：给 agent 读的只有 name/desc/分类/宿主/路径；keywords 留在 JSON。"""
    lines = [
        "# 本机 Skills Catalog",
        "",
        f"生成时间: {catalog['generated_at']}  |  共 {catalog['skill_count']} 个 skill",
        "",
        "> 由 skill-picker 自动生成。刷新: `python <HOME>/.skill-picker/skillpick.py scan`",
        "> 检索候选请优先用: `python <HOME>/.skill-picker/skillpick.py match \"意图\" --json`",
        "> （<HOME>=用户主目录；Windows 上 python 不认 `~`，用 $env:USERPROFILE）",
        "",
    ]
    for g in catalog.get("gates", []):
        mark = {"pass": "✅", "warn": "⚠️", "fail": "❌"}[g["status"]]
        lines.append(f"- {mark} **{g['id']} {g['name']}** {g['status'].upper()}：{g['detail']}")
        if g["status"] != "pass" and g.get("action"):
            lines.append(f"  - 处理：{g['action']}")
        for it in g["items"][:10]:
            if g["status"] != "pass":
                lines.append(f"  - `{it}`")
    lines.append("")

    merged = matching.merge_copies(catalog["skills"])
    by_cat: dict[str, list[dict]] = {}
    for m in merged:
        by_cat.setdefault(m["category"], []).append(m)
    for cat in sorted(by_cat):
        lines.append(f"## {cat}（{len(by_cat[cat])}）")
        lines.append("")
        for m in sorted(by_cat[cat], key=lambda x: x["name"].lower()):
            desc = m["description"][:160] + ("…" if len(m["description"]) > 160 else "")
            hosts = "/".join(m["hosts"])
            lines.append(f"- **{m['name']}** `[{hosts}]` — {desc}")
            for c in m["copies"]:
                lines.append(f"  - `[{c['host']}]` {c['path']}")
        lines.append("")

    dup = catalog["duplicates"]
    lines.append("## ⚠️ 重复与重叠")
    lines.append("")
    if dup["same_name"]:
        lines.append("### 同名多份")
        lines.append("")
        for d in dup["same_name"]:
            mark = "内容一致" if d["status"] == "identical" else "**内容已漂移**"
            lines.append(f"- `{d['name']}`（{mark}）")
            for c in d["copies"]:
                lines.append(f"  - `[{c['host']}]` {c['path']}")
        lines.append("")
    if dup["overlapping"]:
        lines.append("### 描述高度相似（可能功能重叠）")
        lines.append("")
        for o in dup["overlapping"][:30]:
            lines.append(f"- {o['similarity']:.0%} — **{o['a']['name']}** vs **{o['b']['name']}**")
        lines.append("")
    if not dup["same_name"] and not dup["overlapping"]:
        lines.append("未发现重复。")
        lines.append("")
    CATALOG_MD.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------- meta-skill

META_SKILL_TEMPLATE = """---
name: skill-picker
description: >-
  必用触发语（命中任一句即启用）：帮我选个 skill、用哪个 skill、有没有 skill、
  帮我找找、帮我看看本机有没有…能力、本机有没有…能力、你会…吗（想找 skill/本机能力时）、
  我要干…了哪个 skill 最适配、哪个 skills 最适配我的需求、整理一下我当前安装的所有 skills、
  查找本机最适合的 skills、使用本机 skills 帮我做、用本机的 skill 来做、
  本机装了哪些 skills、打开 skills 看板、理技能、skill 总览、看看重复 skill。
  Also use when 用户想不起 skill 名字、在多个相似 skills 间犹豫、要浏览/筛选/清理本机 skills、
  或用口语问「你会不会做 X / 本机能不能做 X」来找 skill。
  HARD GATE: 必须先打开看板（MCP skill_dashboard / skill_match 的 dashboard_url /
  serve / file://）再列候选；只在对话里列文字 = 失败。
  用户已点名具体 skill、或任务与选 skill 无关时不要使用。
---

# skill-picker：本机 skills 路由器

## Overview

读取本机 skills catalog（由扫描器生成、带五道门禁），用共享打分引擎给出 2-4 个候选，
标注 AI 推荐但**由用户点选**，选定后才执行对应 SKILL.md。只读，不改任何 skill。

**硬门禁（不可跳过）**：凡触发本 skill，必须先把看板弹到用户眼前，再在对话里给候选。
「只 match / 只在聊天里列清单、不打开看板」= 违规，必须立刻补开。

## When NOT to use

- 用户已点名具体 skill（如「用 gochina-weekly-review 跑周报」）→ 直接用那个 skill
- 普通编码/问答任务，与「选哪个 skill」无关
- 被作为 subagent 派发执行具体任务时

## 三级降级链（检索与看板都按此顺序，确保体验）

1. **① MCP 插件优先**：若工具列表里有 `skill-picker` MCP server 的
   `skill_match` / `skill_dashboard` 工具，直接调用——无需拼 shell 命令，
   无路径/PATH/编码问题。
2. **② CLI + 本地看板**：MCP 不可用（未注册/调用报错）时，降级跑下方的
   match CLI 与 serve 命令。
3. **③ 系统浏览器兜底**：serve 也起不来时，直接让用户用浏览器打开
   `<HOME>/.skill-picker/dashboard.html`（file:// 直开可用，搜索/复制功能完整）。

每一级失败要**静默降级到下一级**，不要卡住等用户排障。

## 工作流程（顺序强制）

### 0. 先开看板（HARD GATE — 禁止跳过）

在调用 match、在对话里写任何候选列表之前，必须完成：

1. **① MCP**：调用 `skill_dashboard`，参数 `{"intent": "<用户意图原话>"}`  
   （或调用 `skill_match`——其返回里带 `dashboard_url` / `dashboard_required: true`，
   同样视为已拿到看板地址，但**仍必须实际打开**该 URL）。
2. **立刻打开完整 URL**（拿到地址不等于打开；**禁止剥掉 `?q=`**）：
   - **Cursor**：用 `open_resource`（或内置浏览器）打开 `dashboard_url`（侧边）。
   - **终端宿主**：`Start-Process` / `open` / `xdg-open` 打开同一 URL。
   - 打开后看板须已自动填入意图并展示候选；若仍是空白搜索框 = 打开了错误 URL。
3. **② CLI 降级**：MCP 不可用时后台 `serve`，再打开返回的带 `?q=` 的 URL。
4. **③ 文件兜底**：优先 `fallback_url`（file:// 且已带 `?q=`）；否则 `dashboard_fallback_file`。

未完成步骤 0 → **禁止**进入步骤 1 的「向用户展示候选」。若已误列候选，先补开看板再继续。

适用话术（全部强制开看板；用户说下面任一句都必须走本流程）：

- 查找/选用：「帮我选个 skill」「用哪个 skill 做 X」「有没有 skill 能…」
  「帮我找找 X」「帮我看看本机有没有 X 能力」「本机有没有 X 能力」
  「你会 X 吗 / 你会不会 X」（意图是找 skill 或盘点本机能力时）
  「我要干 X 了，哪个 skill(s) 最适配我的需求」
  「查找本机最适合的 skills」「使用本机 skills 帮我做 X」「本机装了哪些 skills」
- 盘点/整理：「整理一下我当前安装的所有 skills」「本机 skills 清单」「skills 都装了啥」
- 浏览/清理：「打开 skills 看板」「理技能」「看看重复」「清理」「skill 总览」

看板 URL 带意图：`.../dashboard.html?q=<URL编码的意图>`（用查询参数，不用 #q=，
避免 Cursor 打开时丢掉 fragment；页面加载后自动填入并展示匹配结果）。

### 1. 取候选（与看板同一引擎）

- ① MCP `skill_match`，`{"query": "<用户意图原话>", "top": 4}`  
  （响应含 `dashboard_url`；若步骤 0 未开，用该 URL 立刻打开）
- ② CLI（Windows 的 python 不认 `~`）：

  ```
  # Windows (PowerShell)
  python "$env:USERPROFILE\\.skill-picker\\skillpick.py" match "<用户意图原话>" --top 4 --json
  # macOS / Linux
  python3 "$HOME/.skill-picker/skillpick.py" match "<用户意图原话>" --top 4 --json
  ```

  JSON 同样含 `dashboard_required` / `dashboard_url`；命令报错 → 先 `scan` 再重试。

### 2. 门禁检查

gates 中 G1 FAIL → 必须提醒用户补 `extra_roots` 并重扫，不得假装结果完整。

### 3. 对话内给选项（看板已开之后）

结合会话上下文把推荐项放第一并标「推荐」+ 一句话理由；用宿主提问工具让用户点选。
有同名漂移或功能重叠必须点明。页面供浏览，最终选择在对话内确认。

### 4–5. 选定后执行 / 无匹配

用户选定后读对应 SKILL.md 严格照做。无匹配 → 直说没有，不硬凑。

## Quick Reference

以下 `<HOME>` 指用户主目录（Windows PowerShell 用 `$env:USERPROFILE`，
macOS/Linux 用 `$HOME`；Windows 上 `python` 缺失时换 `py`，Unix 用 `python3`）：

| 入口 | 用途 |
|---|---|
| MCP 工具 `skill_dashboard` | ① 硬门禁：拿看板 URL 并**必须打开** |
| MCP 工具 `skill_match` | ① 检索候选（响应强制带 dashboard_url） |
| `python "<HOME>/.skill-picker/skillpick.py" match "意图" --top 4 --json` | ② 降级检索（JSON 同样带 dashboard_url） |
| `python "<HOME>/.skill-picker/skillpick.py" serve` | ② 降级：起本地看板（后台运行） |
| `<HOME>/.skill-picker/dashboard.html` | ③ 兜底：file:// 直开看板 |
| `python "<HOME>/.skill-picker/skillpick.py" scan` | 刷新 catalog（新装 skill 后 / 超 7 天） |
| `python "<HOME>/.skill-picker/skillpick.py" check` | 五道门禁体检（退出码 2=不可信） |
| `<HOME>/.skill-picker/catalog.md` | 人读/兜底用瘦身索引 |

## 只读铁律（不可违反）

- 职责仅限**展示、比对、提醒、路由**。
- **永远不要**因为发现漂移/重叠就修改、合并、移动、删除任何 skill 文件。
  用户明确要求清理属于独立任务，需逐项确认后另行执行。

## Common Mistakes

| 念头 | 纠正 |
|---|---|
| 「先在对话里列候选，看板以后再说」 | **违规**。必须先打开看板再列候选 |
| 「跑了 skill_match 就够了」 | match 返回的 `dashboard_url` 必须实际打开 |
| 「意图很明显，直接替用户选吧」 | 必须弹选项让用户点选，推荐≠代选 |
| 「catalog 我大概记得，不用跑命令」 | 必须跑 match，凭记忆排序=页面与会话两套结果 |
| 「发现两份重复，顺手合并掉」 | 只读铁律：只提醒，不动手 |
| 「没找到匹配，挑个最接近的凑数」 | 直说没有，让用户正常描述需求 |
| 「G1 FAIL 但先不管，继续推荐」 | 覆盖不全=结果不可信，必须先提醒处理 |
"""


def _mcp_entry() -> dict:
    """MCP server 配置项：指向 ~/.skill-picker 自拷贝副本，跨机器稳定。"""
    return {
        "command": sys.executable,
        "args": [str(DATA_DIR / "mcp_server.py")],
    }


def register_mcp_json(config_path: Path) -> str:
    """幂等注册到 JSON 型 MCP 配置（Cursor / Claude Code）。

    只增改 mcpServers.skill-picker 一个键；其余用户配置原样保留。
    返回 'created' / 'updated' / 'unchanged'。
    """
    entry = _mcp_entry()
    data = {}
    if config_path.exists():
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return "skipped(配置文件解析失败，未改动)"
    servers = data.setdefault("mcpServers", {})
    if servers.get(MCP_SERVER_NAME) == entry:
        return "unchanged"
    status = "updated" if MCP_SERVER_NAME in servers else \
             ("created" if config_path.exists() else "created(new file)")
    servers[MCP_SERVER_NAME] = entry
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    return status


def register_mcp_toml(config_path: Path) -> str:
    """幂等注册到 Codex 的 config.toml：已有 [mcp_servers.skill-picker] 则跳过。

    零依赖场景下不做完整 TOML 解析，只做「存在即不动、不存在则追加」——
    追加块自包含，不影响用户已有配置。
    """
    header = f'[mcp_servers."{MCP_SERVER_NAME}"]'
    header_bare = f"[mcp_servers.{MCP_SERVER_NAME}]"
    text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    if header in text or header_bare in text:
        return "unchanged"
    entry = _mcp_entry()
    args_toml = ", ".join(json.dumps(a) for a in entry["args"])
    block = (f"\n{header}\n"
             f"command = {json.dumps(entry['command'])}\n"
             f"args = [{args_toml}]\n")
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(text + block, encoding="utf-8")
    return "created" if text else "created(new file)"


def register_mcp_all() -> None:
    """把 MCP server 注册进各宿主配置（存在才注册对应宿主目录）。"""
    targets = [
        ("cursor", HOME / ".cursor" / "mcp.json", register_mcp_json),
        ("claude-code", HOME / ".claude" / "mcp.json", register_mcp_json),
        ("codex", HOME / ".codex" / "config.toml", register_mcp_toml),
    ]
    for host, path, fn in targets:
        if not path.parent.is_dir():
            print(f"[mcp] {host}: 宿主目录不存在，跳过")
            continue
        print(f"[mcp] {host}: {fn(path)} -> {path}")


CURSOR_RULE_TEMPLATE = """---
description: 用户说选/找 skill、本机有没有某能力、你会做X吗、整理已装 skills、理技能时必须走 skill-picker
alwaysApply: true
---

# skill-picker 强制唤起

当用户消息命中任一类意图时，**必须立刻执行 skill-picker 流程**，不要凭记忆猜 skill：

- 选/找：「帮我选个 skill」「用哪个 skill」「有没有 skill」「帮我找找…」
  「查找本机最适合的 skills」「使用本机 skills 帮我做…」「用本机的 skill 来做」
  「我要干…了，哪个 skill / skills 最适配我的需求」
- 能力盘点：「帮我看看本机有没有…能力」「本机有没有…能力」「本机装了哪些 skills」
  「你会…吗 / 你会不会…」（问的是本机/skill 能否覆盖某能力，不是纯闲聊）
- 整理清单：「整理一下我当前安装的所有 skills」「skills 清单」「都装了哪些 skill」
- 理/看：「打开 skills 看板」「理技能」「看看重复」「skill 总览」「清理 skills」

## 必做步骤（顺序强制）

1. 调用 MCP `skill_dashboard`（intent=用户原话/会话上下文意图），或 `skill_match`（query=同上, top=4）
   - 「整理/清单/装了哪些」类：intent 可为空或「全部」，以看板总览 + catalog 为主
2. 用 `open_resource`（或系统浏览器）**实际打开**返回的完整 `dashboard_url`（含 `?q=`；
   禁止剥掉查询参数；没有 URL 则开 `fallback_url` / `dashboard_fallback_file`）
3. 看板应已自动填入意图并展示匹配结果；再在对话里给出 2–4 个候选 +「推荐」，让用户点选
4. 用户选定后，再读对应 SKILL.md 执行

## 禁止

- 只在聊天里列候选、不打开看板
- 打开不带 `?q=` 的空白看板，让用户重新输入意图
- 凭记忆排序 / 直接替用户选定 skill
- G1 FAIL 时假装结果完整（须提醒补 `extra_roots`）
- 对「你会写 Python 吗」这类纯通识问答抢路由（仅当意图是找 skill / 盘点本机能力时启用）

用户已点名具体 skill 名（如「用 work-report」）时不要抢路由。
"""


def install_cursor_rule() -> str:
    """写入 Cursor alwaysApply 规则，确保会话内话术能稳定唤起（不依赖 skills 列表是否截断）。"""
    rules_dir = HOME / ".cursor" / "rules"
    if not (HOME / ".cursor").is_dir():
        return "skipped(无 .cursor 目录)"
    rules_dir.mkdir(parents=True, exist_ok=True)
    target = rules_dir / "skill-picker.mdc"
    old = target.read_text(encoding="utf-8") if target.exists() else ""
    # 兼容旧文件名：合并后删除仅看板门禁的旧规则，避免双份互相稀释
    legacy = rules_dir / "skill-picker-dashboard-mandatory.mdc"
    target.write_text(CURSOR_RULE_TEMPLATE, encoding="utf-8")
    if legacy.exists() and legacy.resolve() != target.resolve():
        try:
            legacy.unlink()
        except OSError:
            pass
    return "unchanged" if old == CURSOR_RULE_TEMPLATE else ("updated" if old else "created")


def copy_tools(src_dir: Path, data_dir: Path) -> list[str]:
    """把工具文件拷到 data_dir，返回实际拷过去的文件名。

    名单之外还要带上工具递归 import 到的本地模块，否则家目录那份一 import 就崩：
    version.py 就是这么漏的（mcp_server 导它，TOOL_FILES 里没有）。
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    wanted = sorted({f for f in TOOL_FILES if (src_dir / f).exists()}
                    | local_import_closure(TOOL_FILES, src_dir))
    copied = []
    for f in wanted:
        src, dst = src_dir / f, data_dir / f
        if not src.exists():
            continue
        # 从 ~/.skill-picker 自己跑 install 时 src 与 dst 是同一个文件，copy2 会抛
        if not (dst.exists() and src.samefile(dst)):
            shutil.copy2(src, dst)
        copied.append(f)
    return copied


def install_meta_skill() -> None:
    # 工具自拷贝到 ~/.skill-picker/，meta-skill 全部用 ~ 路径，跨机器可用
    src_dir = Path(__file__).resolve().parent
    copied = copy_tools(src_dir, DATA_DIR)
    write_install_manifest(src_dir, copied)
    for host, target in INSTALL_TARGETS.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(META_SKILL_TEMPLATE, encoding="utf-8")
        print(f"[install] {host}: {target}")
    register_mcp_all()
    print(f"[cursor-rule] {install_cursor_rule()}")
    print(f"[install] {len(copied)} 个工具文件已自拷贝到 {DATA_DIR}"
          f"（meta-skill 以 ~/.skill-picker 为准，清单写入 {INSTALL_MANIFEST.name}）")


# ---------------------------------------------------------------- CLI

def cmd_scan() -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    catalog = build_catalog()
    catalog["gates"] = run_gates(catalog)
    CATALOG_JSON.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    write_catalog_md(catalog)
    try:
        from discover import sync_discover_page
        disc = sync_discover_page(catalog)
        if disc:
            print(f"[scan] discover 子页已同步 {disc}")
        else:
            print("[scan] discover 子页未就绪（可选：先跑 skill-feed refresh，或使用公开 embed）")
    except Exception as e:
        print(f"[scan] discover 同步跳过：{e}")
    try:
        from dashboard import build_dashboard
        print(f"[scan] dashboard 已写入 {build_dashboard()}")
    except ImportError:
        pass
    print(f"[scan] 共 {catalog['skill_count']} 个 skill")
    for cat, names in catalog["categories"].items():
        print(f"  {cat}: {len(names)}")
    dup = catalog["duplicates"]
    drifted = [d for d in dup["same_name"] if d["status"] == "drifted"]
    print(f"[scan] 同名多份 {len(dup['same_name'])} 组（其中漂移 {len(drifted)} 组），"
          f"描述重叠 {len(dup['overlapping'])} 对")
    print(f"[scan] catalog 已写入 {CATALOG_MD}")
    print_gates(catalog["gates"])
    if any(g["status"] == "fail" for g in catalog["gates"]):
        print("[gate] 存在 FAIL 门禁：结果不可信，请先处理！")
    return catalog


def _load_or_scan_catalog() -> dict:
    if CATALOG_JSON.exists():
        catalog = json.loads(CATALOG_JSON.read_text(encoding="utf-8"))
        generated = catalog.get("generated_at", "1970-01-01")
        age_days = (datetime.now(timezone.utc)
                    - datetime.fromisoformat(generated)).days
        if age_days <= 7:
            return catalog
        print(f"[match] catalog 已 {age_days} 天未更新，自动重扫…")
    return cmd_scan()


def cmd_match(argv: list[str]) -> None:
    query, top, as_json = "", 4, False
    i = 0
    while i < len(argv):
        if argv[i] == "--top" and i + 1 < len(argv):
            top = int(argv[i + 1]); i += 2
        elif argv[i] == "--json":
            as_json = True; i += 1
        else:
            query = argv[i]; i += 1
    if not query:
        print('用法: python skillpick.py match "意图" [--top N] [--json]')
        sys.exit(1)
    catalog = _load_or_scan_catalog()
    index = matching.build_index(catalog["skills"], RULES)
    results = matching.match(index, query, top=top)
    gates_brief = [{"id": g["id"], "status": g["status"], "detail": g["detail"]}
                   for g in catalog.get("gates", [])]
    # 与 MCP skill_match 对齐：CLI 也强制附带看板 URL，避免 agent 只列文字
    try:
        from mcp_server import McpServer  # 延迟导入，避免模块级循环
        dash = McpServer().tool_skill_dashboard({"intent": query})
    except Exception as e:  # noqa: BLE001
        dash = {
            "url": "",
            "fallback_file": str(DATA_DIR / "dashboard.html"),
            "note": f"dashboard ensure failed: {e}",
        }
    dash_payload = {
        "dashboard_required": True,
        "dashboard_url": dash.get("url") or "",
        "dashboard_fallback_file": dash.get("fallback_file") or str(DATA_DIR / "dashboard.html"),
        "agent_must": (
            "HARD GATE: 在向用户列出任何候选之前，必须实际打开 dashboard_url "
            "（或 dashboard_fallback_file）。只返回文字候选而不打开看板 = 流程失败。"
        ),
    }
    if as_json:
        print(json.dumps({
            "query": query,
            "gates": gates_brief,
            "results": results,
            **dash_payload,
        }, ensure_ascii=False, indent=2))
        return
    for g in gates_brief:
        if g["status"] != "pass":
            print(f"[gate {g['id']}] {g['status'].upper()}  {g['detail']}")
    print(f"[dashboard] REQUIRED → "
          f"{dash_payload['dashboard_url'] or dash_payload['dashboard_fallback_file']}")
    if not results:
        print(f"[match] 「{query}」没有匹配的 skill")
        print("[match] 可打开看板「去 GitHub 发现」子页找远程 skill（自行安装后再 scan）")
        return
    for rank, r in enumerate(results, 1):
        hosts = "/".join(r["hosts"])
        print(f"{rank}. {r['name']}  score={r['score']}  [{r['category']}]  ({hosts})")
        print(f"   {r['description'][:100]}")
        print(f"   why: name={r['why']['name']} desc={r['why']['desc']} "
              f"kw={r['why']['kw']} cat_pinned={r['why']['cat_pinned']}")
        for c in r["copies"]:
            print(f"   [{c['host']}] {c['path']}")


def cmd_report() -> None:
    if not CATALOG_MD.exists():
        print("尚未扫描，先运行: python skillpick.py scan")
        return
    print(CATALOG_MD.read_text(encoding="utf-8"))


def cmd_serve(argv: list[str]) -> None:
    """本地预览看板：127.0.0.1 固定段端口，占用则顺延；单实例绑定（不复用端口）。

    额外能力：
    - GET /api/pending_intent → 返回会话意图（MCP match/dashboard 写入）
    - 打开 /dashboard.html 且无 ?q= 时，若有 pending 意图则 302 到 ?q=…（用户不用重输）
    """
    import http.server
    import socketserver
    import urllib.parse

    from mcp_server import load_pending_intent  # 延迟导入，与 CLI 共用落盘约定

    base_port = 8471
    if "--port" in argv:
        base_port = int(argv[argv.index("--port") + 1])
    if not (DATA_DIR / "dashboard.html").exists():
        cmd_scan()

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(DATA_DIR), **kwargs)

        def log_message(self, fmt, *args):  # noqa: A003 —— 降噪
            if "/api/" in (args[0] if args else ""):
                super().log_message(fmt, *args)

        def end_headers(self):  # noqa: N802
            # HTML/JS 发现页禁止缓存，避免仍渲染旧版滑窗关键词
            path = urllib.parse.urlparse(self.path).path or "/"
            if path.endswith((".html", ".js", ".css")) or path in ("/", "/dashboard.html", "/discover.html"):
                self.send_header("Cache-Control", "no-store, max-age=0, must-revalidate")
                self.send_header("Pragma", "no-cache")
            super().end_headers()

        def do_GET(self):  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path or "/"
            if path == "/api/pending_intent":
                intent = load_pending_intent()
                body = json.dumps({"intent": intent}, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path in ("/", "/dashboard.html"):
                qs = urllib.parse.parse_qs(parsed.query)
                if not (qs.get("q") and qs["q"][0].strip()):
                    intent = load_pending_intent()
                    if intent:
                        try:
                            from discover import compress_intent_query
                            short = compress_intent_query(intent, max_keys=2) or intent
                        except Exception:  # noqa: BLE001
                            short = intent
                        q = urllib.parse.urlencode({
                            "q": short,
                            "_": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
                        })
                        self.send_response(302)
                        self.send_header("Location", f"/dashboard.html?{q}")
                        self.send_header("Cache-Control", "no-store")
                        self.end_headers()
                        return
            return super().do_GET()

    httpd = None
    for port in range(base_port, base_port + 10):
        try:
            # 默认 allow_reuse_address=False：端口被占时直接失败顺延，杜绝双实例抢连接
            httpd = socketserver.TCPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            continue
    if httpd is None:
        print(f"[serve] {base_port}-{base_port + 9} 端口均被占用")
        sys.exit(1)
    print(f"[serve] http://127.0.0.1:{port}/dashboard.html  （Ctrl+C 停止）")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.server_close()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台默认 GBK，避免中文乱码
    cmd = sys.argv[1] if len(sys.argv) > 1 else "scan"
    if cmd in ("scan", "check"):
        catalog = cmd_scan()
        sys.exit(2 if any(g["status"] == "fail" for g in catalog["gates"]) else 0)
    elif cmd == "match":
        cmd_match(sys.argv[2:])
    elif cmd == "serve":
        cmd_serve(sys.argv[2:])
    elif cmd == "install":
        # 先刷副本再扫：这样 G0 比的是装完之后的状态，门禁也才落在输出末尾（AGENTS.md 的口径）
        install_meta_skill()
        cmd_scan()
    elif cmd == "report":
        cmd_report()
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
