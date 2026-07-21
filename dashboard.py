"""从 catalog.json 生成单文件本地 HTML dashboard（tab-out 风格）。

无服务器、无外部资源、无第三方库；生成后直接用浏览器打开 file:// 即可。
"""

import html
import json
from pathlib import Path

DATA_DIR = Path.home() / ".skill-picker"
CATALOG_JSON = DATA_DIR / "catalog.json"
DASHBOARD_HTML = DATA_DIR / "dashboard.html"

HOST_LABELS = {
    "claude-code": ("Claude Code", "#d97757"),
    "cursor": ("Cursor", "#7c8cf8"),
    "cursor-builtin": ("Cursor 内置", "#5560c8"),
    "codex": ("Codex", "#3aa981"),
    "openclaw": ("OpenClaw", "#b58a3d"),
}

PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>Skill Picker — 本机 Skills 总览</title>
<style>
  :root {
    --bg: #0e1013; --panel: #16191e; --panel2: #1c2027; --line: #262b33;
    --text: #e8eaed; --dim: #9aa3af; --faint: #6b7280;
    --amber: #f0b429; --purple: #a78bfa; --green: #4ade80;
  }
  * { box-sizing: border-box; margin: 0; }
  body { background: var(--bg); color: var(--text);
         font: 14px/1.6 "Microsoft YaHei", "Segoe UI", system-ui, sans-serif; padding: 28px 32px 80px; }
  header { display: flex; flex-wrap: wrap; align-items: baseline; gap: 14px; margin-bottom: 6px; }
  h1 { font-size: 22px; font-weight: 700; letter-spacing: .5px; }
  .stats { color: var(--dim); font-size: 13px; }
  .hint { color: var(--faint); font-size: 12px; margin-bottom: 18px; }
  #search { width: 100%; max-width: 520px; margin-bottom: 26px; padding: 10px 14px;
            background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
            color: var(--text); font-size: 14px; outline: none; }
  #search:focus { border-color: #4b5563; }

  .dupbox { background: #1d1a12; border: 1px solid #3d3417; border-radius: 12px;
            padding: 14px 18px; margin-bottom: 26px; }
  .dupbox h2 { font-size: 14px; color: var(--amber); margin-bottom: 8px; }
  .dupbox li { color: var(--dim); font-size: 13px; margin-left: 18px; }
  .dupbox b { color: var(--text); font-weight: 600; }

  section h2 { font-size: 15px; color: var(--dim); font-weight: 600; margin: 26px 0 12px;
               border-bottom: 1px solid var(--line); padding-bottom: 6px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 12px; }
  .card { background: var(--panel); border: 1px solid var(--line); border-radius: 12px;
          padding: 14px 16px; transition: border-color .15s; }
  .card:hover { border-color: #3b4250; background: var(--panel2); }
  .card .top { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 6px; }
  .card .name { font-weight: 700; font-size: 14.5px; }
  .badge { font-size: 11px; padding: 1px 8px; border-radius: 999px; white-space: nowrap; }
  .host { color: #0e1013; font-weight: 600; }
  .drift { background: rgba(240,180,41,.15); color: var(--amber); border: 1px solid rgba(240,180,41,.4); }
  .overlap { background: rgba(167,139,250,.12); color: var(--purple); border: 1px solid rgba(167,139,250,.4); cursor: help; }
  .desc { color: var(--dim); font-size: 12.5px; display: -webkit-box; -webkit-line-clamp: 3;
          -webkit-box-orient: vertical; overflow: hidden; }
  .card.open .desc { -webkit-line-clamp: unset; }
  .path { color: var(--faint); font-size: 11px; margin-top: 8px; word-break: break-all;
          font-family: Consolas, monospace; display: none; }
  .card.open .path { display: block; }
  .empty { color: var(--faint); padding: 40px 0; text-align: center; display: none; }
</style>
</head>
<body>
<header><h1>Skill Picker</h1><span class="stats">__STATS__</span></header>
<div class="hint">点击卡片展开完整描述与路径 · 数据仅来自本机 SKILL.md，刷新请运行 <code>python skillpick.py scan</code></div>
<input id="search" type="search" placeholder="搜索 skill 名称 / 描述 / 场景…（比如：周报、PPT、飞书）" autofocus>
__DUPBOX__
__SECTIONS__
<div class="empty" id="empty">没有匹配的 skill</div>
<script>
  const q = document.getElementById('search');
  const cards = [...document.querySelectorAll('.card')];
  const sections = [...document.querySelectorAll('section')];
  cards.forEach(c => c.addEventListener('click', () => c.classList.toggle('open')));
  q.addEventListener('input', () => {
    const kw = q.value.trim().toLowerCase();
    cards.forEach(c => { c.style.display = c.dataset.text.includes(kw) ? '' : 'none'; });
    let any = false;
    sections.forEach(s => {
      const visible = [...s.querySelectorAll('.card')].some(c => c.style.display !== 'none');
      s.style.display = visible ? '' : 'none';
      any = any || visible;
    });
    document.getElementById('empty').style.display = any ? 'none' : 'block';
  });
</script>
</body>
</html>
"""


def build_dashboard() -> Path:
    catalog = json.loads(CATALOG_JSON.read_text(encoding="utf-8"))
    skills = catalog["skills"]
    dup = catalog["duplicates"]

    drifted_names = {g["name"] for g in dup["same_name"] if g["status"] == "drifted"}
    overlap_partners: dict[str, list[str]] = {}
    for o in dup["overlapping"]:
        overlap_partners.setdefault(o["a"]["path"], []).append(o["b"]["name"])
        overlap_partners.setdefault(o["b"]["path"], []).append(o["a"]["name"])

    by_cat: dict[str, list[dict]] = {}
    for s in skills:
        by_cat.setdefault(s["category"], []).append(s)

    sections = []
    for cat in sorted(by_cat, key=lambda c: -len(by_cat[c])):
        cards = []
        for s in sorted(by_cat[cat], key=lambda x: x["name"].lower()):
            label, color = HOST_LABELS.get(s["host"], (s["host"], "#888"))
            badges = [f'<span class="badge host" style="background:{color}">{html.escape(label)}</span>']
            if s["dir_name"].lower() in drifted_names:
                badges.append('<span class="badge drift">同名漂移</span>')
            partners = overlap_partners.get(s["path"])
            if partners:
                tip = html.escape("可能与这些 skill 功能重叠: " + ", ".join(sorted(set(partners))))
                badges.append(f'<span class="badge overlap" title="{tip}">重叠 ×{len(set(partners))}</span>')
            text = html.escape(f"{s['name']} {s['description']} {cat}".lower(), quote=True)
            cards.append(
                f'<div class="card" data-text="{text}">'
                f'<div class="top"><span class="name">{html.escape(s["name"])}</span>{"".join(badges)}</div>'
                f'<div class="desc">{html.escape(s["description"]) or "（无描述）"}</div>'
                f'<div class="path">{html.escape(s["path"])}</div></div>'
            )
        sections.append(f"<section><h2>{html.escape(cat)}（{len(cards)}）</h2>"
                        f'<div class="grid">{"".join(cards)}</div></section>')

    dupbox = ""
    if dup["same_name"]:
        items = []
        for g in dup["same_name"]:
            status = "内容已漂移，唤醒时行为可能不一致" if g["status"] == "drifted" else "内容一致"
            hosts = " + ".join(c["host"] for c in g["copies"])
            items.append(f"<li><b>{html.escape(g['name'])}</b>（{hosts}）— {status}</li>")
        dupbox = (f'<div class="dupbox"><h2>⚠ 同名多份 {len(dup["same_name"])} 组'
                  f'（漂移 {len(drifted_names)} 组）— 建议合并，工具只提示不代删</h2>'
                  f'<ul>{"".join(items)}</ul></div>')

    stats = (f"{catalog['skill_count']} 个 skill · {len(by_cat)} 个场景 · "
             f"描述重叠 {len(dup['overlapping'])} 对 · 生成于 {catalog['generated_at'][:16]} UTC")

    page = (PAGE.replace("__STATS__", stats)
                .replace("__DUPBOX__", dupbox)
                .replace("__SECTIONS__", "".join(sections)))
    DASHBOARD_HTML.write_text(page, encoding="utf-8")
    return DASHBOARD_HTML


if __name__ == "__main__":
    print(build_dashboard())
