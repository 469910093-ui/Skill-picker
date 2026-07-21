#!/usr/bin/env python3
"""skill-picker: 本机 agent skills 的扫描、查重、场景聚类与路由工具。

零依赖（仅 Python 标准库），零服务器，数据全在本地。
灵感来自 tab-out（https://github.com/zarazhangrui/tab-out）：
不管理数据，只读取已存在的事实（SKILL.md），自动聚类、暴露冗余、让用户决策。

用法:
  python skillpick.py scan       扫描所有 skill 目录，生成 catalog
  python skillpick.py install    scan + 把 skill-picker meta-skill 装进宿主
  python skillpick.py report     打印上次扫描的摘要
"""

import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
DATA_DIR = HOME / ".skill-picker"
CATALOG_JSON = DATA_DIR / "catalog.json"
CATALOG_MD = DATA_DIR / "catalog.md"
SELF_NAME = "skill-picker"

# 扫描根目录 -> 宿主标签。存在才扫，不存在跳过。
SCAN_ROOTS = [
    (HOME / ".claude" / "skills", "claude-code"),
    (HOME / ".cursor" / "skills", "cursor"),
    (HOME / ".cursor" / "skills-cursor", "cursor-builtin"),
    (HOME / ".agents" / "skills", "codex"),
    (HOME / ".openclaw" / "skills", "openclaw"),
]

# meta-skill 的安装目标（MVP: Cursor + Claude Code）
INSTALL_TARGETS = {
    "cursor": HOME / ".cursor" / "skills" / SELF_NAME / "SKILL.md",
    "claude-code": HOME / ".claude" / "skills" / SELF_NAME / "SKILL.md",
}

# 场景聚类规则：按顺序匹配，命中即归类（对 name + description 匹配，忽略大小写）
CATEGORY_RULES = [
    ("飞书/Lark 办公", ["lark", "飞书", "feishu", "bitable", "多维表格", "妙搭", "miaoda"]),
    ("周报/复盘/数据分析", ["复盘", "周报", "review", "weekly", "campaign", "bigquery", " bq ",
                            "增量", "归因", "gochina", "gosea", "gothai", "gojapan", "ka 分析",
                            "poiid", "okr", "shutdown", "工作汇报", "日报", "月报"]),
    ("PPT/演示", ["ppt", "slides", "presentation", "幻灯片", "deck", "演讲", "slide deck", "分享稿"]),
    ("图表/可视化", ["chart", "antv", "g2", "g6", "s2", "可视化", "infographic", "信息图",
                     "visualization", "whiteboard", "画板", "diagram", "xrd", "图表"]),
    ("视频/图像/创意", ["video", "短剧", "storyboard", "分镜", "manim", "hyperframes", "图片",
                        "image", "seedance", "即梦", "cowart", "生成图", "canvas"]),
    ("写作/内容运营", ["写作", "文章", "爆款", "文案", "小红书", "公众号", "咪蒙", "viral",
                       "内容 ip", "自媒体", "notebooklm", "解读", "播客", "digest"]),
    ("设计/Figma", ["figma", "figjam", "design system", "code connect"]),
    ("Notion", ["notion"]),
    ("云/AWS/运维", ["aws", "bedrock", "lambda", "cloudformation", "cdk", "iam", "datadog",
                     "serverless", "amplify", "ecs", "s3", "dynamodb", "boto3"]),
    ("Agent/开发工具链", ["cursor", "skill", "hook", "rule", "sdk", "subagent", "plan mode",
                          "worktree", "tdd", "debugging", "code review", "pr", "statusline",
                          "claude", "codex", "session", "brainstorm", "loop"]),
    ("出行/电商业务", ["trip.com", "酒店", "hotel", "机票", "flight", "火车票", "train",
                       "接送机", "transfer", "跟团游", "tor", "宠物", "抖音", "地图", "map",
                       "玩乐", "景点"]),
]
FALLBACK_CATEGORY = "其他"

OVERLAP_THRESHOLD = 0.50  # 描述 bigram Jaccard 相似度阈值


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
    for root, host in SCAN_ROOTS:
        if not root.is_dir():
            continue
        for skill_md in sorted(root.rglob("SKILL.md")):
            if "node_modules" in skill_md.parts:
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
            skills.append({
                "name": name,
                "dir_name": skill_md.parent.name,
                "description": (fm.get("description") or "").strip(),
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


def find_duplicates(skills: list[dict]) -> dict:
    by_name: dict[str, list[dict]] = {}
    for s in skills:
        by_name.setdefault(s["dir_name"].lower(), []).append(s)

    same_name = []
    for name, group in sorted(by_name.items()):
        if len(group) < 2:
            continue
        hashes = {g["sha256"] for g in group}
        same_name.append({
            "name": name,
            "status": "identical" if len(hashes) == 1 else "drifted",
            "copies": [{"path": g["path"], "host": g["host"], "sha256": g["sha256"]} for g in group],
        })

    overlaps = []
    for i in range(len(skills)):
        for j in range(i + 1, len(skills)):
            a, b = skills[i], skills[j]
            if a["dir_name"].lower() == b["dir_name"].lower():
                continue
            if len(a["description"]) < 20 or len(b["description"]) < 20:
                continue
            score = jaccard(a["description"], b["description"])
            if score >= OVERLAP_THRESHOLD:
                overlaps.append({
                    "a": {"name": a["name"], "path": a["path"]},
                    "b": {"name": b["name"], "path": b["path"]},
                    "similarity": round(score, 3),
                })
    overlaps.sort(key=lambda x: -x["similarity"])
    return {"same_name": same_name, "overlapping": overlaps}


# ---------------------------------------------------------------- 聚类

def categorize(skill: dict) -> str:
    haystack = f"{skill['name']} {skill['description']}".lower()
    for category, keywords in CATEGORY_RULES:
        if any(kw in haystack for kw in keywords):
            return category
    return FALLBACK_CATEGORY


# ---------------------------------------------------------------- 输出

def build_catalog() -> dict:
    skills = scan_skills()
    for s in skills:
        s["category"] = categorize(s)
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
    lines = [
        "# 本机 Skills Catalog",
        "",
        f"生成时间: {catalog['generated_at']}  |  共 {catalog['skill_count']} 个 skill",
        "",
        "> 由 skill-picker 自动生成。刷新: `python skillpick.py scan`",
        "",
    ]
    by_cat: dict[str, list[dict]] = {}
    for s in catalog["skills"]:
        by_cat.setdefault(s["category"], []).append(s)
    for cat in sorted(by_cat):
        lines.append(f"## {cat}（{len(by_cat[cat])}）")
        lines.append("")
        for s in sorted(by_cat[cat], key=lambda x: x["name"].lower()):
            desc = s["description"][:160] + ("…" if len(s["description"]) > 160 else "")
            lines.append(f"- **{s['name']}** `[{s['host']}]` — {desc}")
            lines.append(f"  - 路径: `{s['path']}`")
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


META_SKILL_TEMPLATE = """---
name: skill-picker
description: 本机 skills 路由器。当用户想不起某个 skill 的名字、不确定该用哪个 skill、
  想知道本机装了哪些 skills、多个相似 skills 不知道选哪个，或者说"帮我选个 skill"、
  "有没有 skill 能做 X"、"skill 太多了"、"用哪个 skill 做周报/PPT/图表/飞书文档"
  之类的模糊意图时使用。读取本地 skills catalog，给出 2-4 个候选并让用户自己选择。
---

# skill-picker：本机 skills 路由器

## 工作流程

1. 读取 catalog（本机 skills 索引，已按场景分组并标注重复）:
   `{catalog_md}`
   如果该文件不存在或超过 7 天未更新，先运行刷新命令（见下方）再读取。

2. 根据用户意图，在 catalog 中找出最匹配的 **2-4 个候选 skill**。
   匹配依据是各 skill 的 description 与场景分类，不要只靠名字猜。

3. **必须让用户选择，不要替用户决定**。使用宿主提供的提问工具
   （Cursor: AskQuestion；Claude Code: AskUserQuestion；无提问工具的宿主则在
   回复中列出编号选项等待用户回答）。每个候选给一行中文说明：它是干什么的、
   和其他候选的区别。如果 catalog 显示候选之间存在"同名漂移"或"功能重叠"，
   要明确提示用户。

4. 用户选定后，读取该 skill 的 SKILL.md（路径在 catalog 中），并严格按其内容执行。

5. 如果没有任何匹配的 skill，直接告诉用户"本机没有对应 skill"，
   不要硬凑，可建议用户直接描述需求由 agent 正常处理。

## 刷新 catalog

```
python "{script_path}" scan
```

## 关键事实

- catalog 覆盖的扫描目录: {roots}
- 纯本地，无服务器，无外部 API。
- catalog JSON 版（含完整字段）: `{catalog_json}`
"""


def install_meta_skill() -> None:
    roots = ", ".join(str(r) for r, _ in SCAN_ROOTS if r.is_dir())
    content = META_SKILL_TEMPLATE.format(
        catalog_md=CATALOG_MD,
        catalog_json=CATALOG_JSON,
        script_path=Path(__file__).resolve(),
        roots=roots,
    )
    for host, target in INSTALL_TARGETS.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        print(f"[install] {host}: {target}")


# ---------------------------------------------------------------- CLI

def cmd_scan() -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    catalog = build_catalog()
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
    return catalog


def cmd_report() -> None:
    if not CATALOG_MD.exists():
        print("尚未扫描，先运行: python skillpick.py scan")
        return
    print(CATALOG_MD.read_text(encoding="utf-8"))


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台默认 GBK，避免中文乱码
    cmd = sys.argv[1] if len(sys.argv) > 1 else "scan"
    if cmd == "scan":
        cmd_scan()
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
