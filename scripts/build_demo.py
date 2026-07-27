#!/usr/bin/env python3
"""用合成 fixture 生成可公开的静态 Demo（无真实本机路径）。

输出：
  docs/demo/index.html   ← GitHub Pages 入口
  docs/demo/catalog.json ← 可复现的演示 catalog
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import dashboard  # noqa: E402
import matching  # noqa: E402
import skillpick  # noqa: E402
from fixtures import FIXTURE_SKILLS  # noqa: E402

OUT_DIR = ROOT / "docs" / "demo"


def _extra_demo_skills() -> list[dict]:
    """额外造几份副本，让「理技能」tab 有漂移/重叠可看。"""
    rules = matching.load_rules()
    extras = []

    # 同名漂移：work-report 在 cursor 端一份改过的描述
    primary, labels = matching.categorize(
        "work-report",
        "Generate weekly work reports from local git history.",
        rules,
    )
    extras.append({
        "name": "work-report",
        "dir_name": "work-report",
        "description": "Generate weekly work reports from local git history. "
                       "Slightly different copy from the Claude version — demo drift.",
        "keywords": "weekly report git summary",
        "category": primary,
        "categories": labels,
        "host": "cursor",
        "path": "~/.cursor/skills/work-report/SKILL.md",
        "root": "~/.cursor/skills",
        "sha256": "drift001",
    })

    # 功能重叠：另一个 PPT skill
    primary, labels = matching.categorize(
        "frontend-slides",
        "Build beautiful HTML slide decks for product launches and tech talks. "
        "Use when the user asks for slides, PPT, deck, 幻灯片.",
        rules,
    )
    extras.append({
        "name": "frontend-slides",
        "dir_name": "frontend-slides",
        "description": "Build beautiful HTML slide decks for product launches and tech talks. "
                       "Use when the user asks for slides, PPT, deck, 幻灯片.",
        "keywords": "ppt slides deck presentation 幻灯片",
        "category": primary,
        "categories": labels,
        "host": "cursor-plugin",
        "path": "~/.cursor/plugins/cache/frontend-slides/SKILL.md",
        "root": "~/.cursor/plugins/cache",
        "sha256": "overlap01",
    })
    return extras


def build_demo_catalog() -> dict:
    skills = []
    for s in FIXTURE_SKILLS:
        item = dict(s)
        item["path"] = f"~/.{item['host']}/skills/{item['dir_name']}/SKILL.md"
        item["root"] = f"~/.{item['host']}/skills"
        skills.append(item)
    skills.extend(_extra_demo_skills())

    # 再复制几条到不同宿主，模拟「多端一致副本」
    for name, host in (("lark-doc", "cursor"), ("chart-visualization", "codex")):
        base = next(x for x in skills if x["name"] == name)
        clone = dict(base)
        clone["host"] = host
        clone["path"] = f"~/.{host}/skills/{clone['dir_name']}/SKILL.md"
        clone["root"] = f"~/.{host}/skills"
        clone["sha256"] = base["sha256"]  # 内容一致
        skills.append(clone)

    by_cat: dict[str, list[str]] = {}
    for s in skills:
        by_cat.setdefault(s["category"], []).append(s["name"])

    catalog = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "skill_count": len(skills),
        "categories": {k: sorted(set(v)) for k, v in sorted(by_cat.items())},
        "duplicates": skillpick.find_duplicates(skills),
        "skills": skills,
        "gates": [
            {"id": "G1", "name": "覆盖率", "status": "pass",
             "detail": "Demo catalog：合成数据，覆盖率门禁跳过", "items": [], "action": ""},
            {"id": "G2", "name": "解析质量", "status": "pass",
             "detail": "全部 skill 均有有效描述", "items": [], "action": ""},
            {"id": "G3", "name": "漂移提醒", "status": "warn",
             "detail": "发现同名漂移（演示用）", "items": ["work-report"],
             "action": "打开「理技能」tab 查看，自行决定是否合并"},
            {"id": "G4", "name": "匹配黄金用例", "status": "pass",
             "detail": "Demo 环境：黄金用例由真实 install 校验", "items": [], "action": ""},
        ],
        "_demo": True,
        "_note": "Synthetic fixture data for public demo. Not from any real machine.",
    }
    return catalog


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    catalog = build_demo_catalog()
    catalog_path = OUT_DIR / "catalog.json"
    catalog_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")

    # 临时改写 dashboard 输出路径
    dashboard.CATALOG_JSON = catalog_path
    dashboard.DASHBOARD_HTML = OUT_DIR / "index.html"
    out = dashboard.build_dashboard()

    # Demo 页标题加 Live Demo 标记
    html = out.read_text(encoding="utf-8")
    html = html.replace(
        "<title>Skill Picker — 本机 Skills 总览</title>",
        "<title>Skill Picker — Live Demo</title>",
    )
    banner = (
        '<div style="margin:0 0 18px;padding:10px 16px;border-radius:12px;'
        'background:rgba(96,165,250,.1);border:1px solid rgba(96,165,250,.35);'
        'color:#93c5fd;font-size:13px">'
        '<b>Live Demo</b> · 合成数据，可直接试「找技能 / 理技能」。'
        '装到本机：'
        '<code style="background:rgba(0,0,0,.35);padding:2px 8px;border-radius:6px">'
        'python skillpick.py install</code>'
        ' · <a href="https://github.com/469910093-ui/Skill-picker" '
        'style="color:#bfdbfe">GitHub</a></div>'
    )
    html = html.replace("<header>", banner + "<header>", 1)
    out.write_text(html, encoding="utf-8")
    print(f"[demo] catalog  → {catalog_path}")
    print(f"[demo] dashboard → {out}")
    print(f"[demo] skills    = {catalog['skill_count']}")


if __name__ == "__main__":
    main()
