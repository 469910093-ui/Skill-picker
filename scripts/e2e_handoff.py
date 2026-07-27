#!/usr/bin/env python3
"""本机无缝衔接自检：唤起语 → match → pending_intent/URL 预填 → 目标 skill 可读。

用法:
  python scripts/e2e_handoff.py
  python scripts/e2e_handoff.py "帮我选个 skill 做周报"
退出码 0=通过，1=失败。
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path.home() / ".skill-picker"))

from mcp_server import McpServer, load_pending_intent  # noqa: E402


def main() -> int:
    query = (sys.argv[1] if len(sys.argv) > 1 else "帮我选个 skill 做数据分析").strip()
    print(f"[e2e] query = {query}")

    srv = McpServer()
    payload = srv.tool_skill_match({"query": query, "top": 4})
    if payload.get("error"):
        print("[FAIL] match error:", payload["error"])
        return 1

    url = payload.get("dashboard_url") or ""
    pending = load_pending_intent()
    names = [r["name"] for r in payload.get("results") or []]
    print(f"[e2e] candidates = {names}")
    print(f"[e2e] dashboard_url = {url}")
    print(f"[e2e] pending_intent = {pending}")

    ok = True
    if "?q=" not in url:
        print("[FAIL] dashboard_url 缺少 ?q=")
        ok = False
    if "#q=" in url:
        print("[FAIL] dashboard_url 仍使用脆弱的 #q=")
        ok = False
    if not pending:
        print("[FAIL] pending_intent 未写入")
        ok = False
    if not names:
        print("[FAIL] 无候选 skill")
        ok = False

    # serve 302 / API（serve 未起则跳过）
    try:
        class NoRedir(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None

        opener = urllib.request.build_opener(NoRedir)
        try:
            opener.open("http://127.0.0.1:8471/dashboard.html", timeout=2)
        except urllib.error.HTTPError as e:
            loc = e.headers.get("Location") or ""
            print(f"[e2e] serve 302 → {loc}")
            if e.code != 302 or "q=" not in loc:
                print("[FAIL] serve 未按 pending 意图重定向")
                ok = False
        with urllib.request.urlopen("http://127.0.0.1:8471/api/pending_intent", timeout=2) as r:
            api = json.loads(r.read().decode())
            print(f"[e2e] api = {api}")
            if not api.get("intent"):
                print("[FAIL] /api/pending_intent 空")
                ok = False
    except OSError as e:
        print(f"[WARN] serve 未运行，跳过 HTTP 检查: {e}")

    # 衔接到 Top1 skill 文件
    top = (payload.get("results") or [None])[0]
    if top:
        path = Path(top["copies"][0]["path"])
        exists = path.exists()
        print(f"[e2e] top1 = {top['name']} → {path} exists={exists}")
        if not exists:
            print("[FAIL] Top1 SKILL.md 不存在，无法衔接执行")
            ok = False
        else:
            head = path.read_text(encoding="utf-8")[:200]
            print(f"[e2e] skill_head = {head.splitlines()[:3]}")

    print("[PASS] 无缝衔接 OK" if ok else "[FAIL] 无缝衔接未通过")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
