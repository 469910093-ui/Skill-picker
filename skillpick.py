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
SELF_NAME = "skill-picker"
TOOL_FILES = ["skillpick.py", "matching.py", "dashboard.py", "rules.json"]

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


def _plugin_family(path_str: str) -> tuple | None:
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


def run_gates(catalog: dict) -> list[dict]:
    """四道强制门禁：G1 覆盖率 / G2 解析质量 / G3 漂移提醒 / G4 匹配黄金用例。"""
    # 用字面路径比较：skills 目录里常见符号链接，resolve 会解析到根目录之外造成误报
    roots = [os.path.normcase(str(r)) for r, _ in load_scan_roots() if r.is_dir()]

    def covered(p: Path) -> bool:
        n = os.path.normcase(str(p))
        return any(n.startswith(r + os.sep) or n == r for r in roots)

    all_files = discover_all_skill_files()
    uncovered = sorted(str(p) for p in all_files if not covered(p))
    gates = [{
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
    mark = {"pass": "PASS", "warn": "WARN", "fail": "FAIL"}
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
        "> 由 skill-picker 自动生成。刷新: `python ~/.skill-picker/skillpick.py scan`",
        "> 检索候选请优先用: `python ~/.skill-picker/skillpick.py match \"意图\" --json`",
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
  Use when 用户想不起某个 skill 的名字、不确定该用哪个 skill、
  询问"有没有 / 用哪个 skill 能做 X"、想知道本机装了哪些 skills、
  在多个相似 skills 之间犹豫不决，或要求打开 skills 看板、
  浏览/筛选/清理本机 skills 时使用。
  用户已明确点名某个具体 skill、或任务本身与 skill 选择无关时不要使用。
---

# skill-picker：本机 skills 路由器

## Overview

读取本机 skills catalog（由扫描器生成、带四道门禁），用共享打分引擎给出 2-4 个候选，
标注 AI 推荐但**由用户点选**，选定后才执行对应 SKILL.md。只读，不改任何 skill。

## When NOT to use

- 用户已点名具体 skill（如「用 gochina-weekly-review 跑周报」）→ 直接用那个 skill
- 普通编码/问答任务，与「选哪个 skill」无关
- 被作为 subagent 派发执行具体任务时

## 工作流程

1. **必须先跑共享检索命令**（与 dashboard 同一引擎，禁止凭记忆翻 catalog）：

   ```
   python ~/.skill-picker/skillpick.py match "<用户意图原话>" --top 4 --json
   ```

   命令不存在或报错 → 先跑 `python ~/.skill-picker/skillpick.py scan` 再重试。

2. **门禁检查**：输出的 gates 中若 G1 覆盖率 FAIL，说明本机存在未被索引的 skills，
   匹配结果不完整——必须提醒用户，并给出把未覆盖目录加入
   `~/.skill-picker/config.json` 的 `extra_roots` 的具体写法，然后重新 scan。

3. **给出 AI 建议，但必须让用户选择**：结合会话上下文（用户原话、工作区、最近文件）
   把推荐项放第一个选项并标「推荐」+ 一句话理由；其余按分数排列。
   用宿主提问工具（Cursor: AskQuestion；Claude Code: AskUserQuestion；
   其他宿主列编号选项等用户回答）。候选之间若有「同名漂移」或「功能重叠」必须点明。

4. 用户选定后，读取该 skill 的 SKILL.md（路径在 match 输出里），严格照做。

5. 无匹配（match 返回空）→ 直说「本机没有对应 skill」，不要硬凑。

## 打开看板（对话侧边栏的筛选 / 清理界面）

用户说「打开 skills 看板 / 理技能 / 看看重复的 skills / 清理 skills / skill 总览」时：

1. 后台启动本地预览（自动选端口，输出访问 URL）：

   ```
   python ~/.skill-picker/skillpick.py serve
   ```

2. **Cursor 宿主**：用内置浏览器以 side（侧边）位置打开输出的 URL——
   看板就出现在用户与 AI 的对话框旁边，可直接输意图筛选、看「理技能」tab 的重复体检。
   **其他宿主**（Claude Code / Codex / OpenClaw 为终端应用，无侧边栏）：
   用系统默认浏览器打开（Windows `Start-Process <url>` / macOS `open` / Linux `xdg-open`）。

3. 看板是只读体检：漂移/重叠仅提示。用户看完点名要清理时，
   属于独立任务，逐项确认后再动手（见只读铁律）。

## Quick Reference

| 命令 | 用途 |
|---|---|
| `python ~/.skill-picker/skillpick.py match "意图" --top 4 --json` | 检索候选（第一步必跑） |
| `python ~/.skill-picker/skillpick.py serve` | 起本地看板，Cursor 内侧边打开 |
| `python ~/.skill-picker/skillpick.py scan` | 刷新 catalog（新装 skill 后 / 超 7 天） |
| `python ~/.skill-picker/skillpick.py check` | 四道门禁体检（退出码 2=不可信） |
| `~/.skill-picker/catalog.md` | 人读/兜底用瘦身索引 |

## 只读铁律（不可违反）

- 职责仅限**展示、比对、提醒、路由**。
- **永远不要**因为发现漂移/重叠就修改、合并、移动、删除任何 skill 文件。
  用户明确要求清理属于独立任务，需逐项确认后另行执行。

## Common Mistakes

| 念头 | 纠正 |
|---|---|
| 「意图很明显，直接替用户选吧」 | 必须弹选项让用户点选，推荐≠代选 |
| 「catalog 我大概记得，不用跑命令」 | 必须跑 match CLI，凭记忆排序=页面与会话两套结果 |
| 「发现两份重复，顺手合并掉」 | 只读铁律：只提醒，不动手 |
| 「没找到匹配，挑个最接近的凑数」 | 直说没有，让用户正常描述需求 |
| 「G1 FAIL 但先不管，继续推荐」 | 覆盖不全=结果不可信，必须先提醒处理 |
"""


def install_meta_skill() -> None:
    # 工具自拷贝到 ~/.skill-picker/，meta-skill 全部用 ~ 路径，跨机器可用
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    src_dir = Path(__file__).resolve().parent
    for f in TOOL_FILES:
        src = src_dir / f
        if src.exists():
            shutil.copy2(src, DATA_DIR / f)
    for host, target in INSTALL_TARGETS.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(META_SKILL_TEMPLATE, encoding="utf-8")
        print(f"[install] {host}: {target}")
    print(f"[install] 工具已自拷贝到 {DATA_DIR}（meta-skill 以 ~/.skill-picker 为准）")


# ---------------------------------------------------------------- CLI

def cmd_scan() -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    catalog = build_catalog()
    catalog["gates"] = run_gates(catalog)
    CATALOG_JSON.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    write_catalog_md(catalog)
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
    if as_json:
        print(json.dumps({"query": query, "gates": gates_brief, "results": results},
                         ensure_ascii=False, indent=2))
        return
    for g in gates_brief:
        if g["status"] != "pass":
            print(f"[gate {g['id']}] {g['status'].upper()}  {g['detail']}")
    if not results:
        print(f"[match] 「{query}」没有匹配的 skill")
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
    """本地预览看板：127.0.0.1 固定段端口，占用则顺延；单实例绑定（不复用端口）。"""
    import functools
    import http.server
    import socketserver

    base_port = 8471
    if "--port" in argv:
        base_port = int(argv[argv.index("--port") + 1])
    if not (DATA_DIR / "dashboard.html").exists():
        cmd_scan()
    handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                directory=str(DATA_DIR))
    httpd = None
    for port in range(base_port, base_port + 10):
        try:
            # 默认 allow_reuse_address=False：端口被占时直接失败顺延，杜绝双实例抢连接
            httpd = socketserver.TCPServer(("127.0.0.1", port), handler)
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
        cmd_scan()
        install_meta_skill()
    elif cmd == "report":
        cmd_report()
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
