#!/usr/bin/env python3
"""skill-picker 本地 MCP server（stdio，纯标准库，零第三方依赖）。

宿主化"插件"形态：注册进 Cursor / Claude Code / Codex 的 MCP 配置后，
agent 可直接调用工具而不必拼 shell 命令——三级降级链的第①级。

暴露两个 tool（与 CLI / dashboard 共用 matching.py + rules.json 单一真相源）：
- skill_match(query, top): 共享打分引擎检索候选 skills
- skill_dashboard(intent?): 确保本地看板服务在跑，返回可打开的 URL（可带 #q= 意图预填）

协议：MCP stdio 传输（每行一条 JSON-RPC 2.0 消息，newline-delimited）。
"""

import json
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import matching  # noqa: E402

DATA_DIR = Path.home() / ".skill-picker"
CATALOG_JSON = DATA_DIR / "catalog.json"
DASHBOARD_HTML = DATA_DIR / "dashboard.html"
SERVE_PORTS = range(8471, 8481)

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "skill-picker", "version": "1.0.0"}

TOOLS = [
    {
        "name": "skill_match",
        "description": (
            "在本机全部已安装的 agent skills 中检索最匹配用户意图的候选（共享打分引擎，"
            "与 skill-picker 看板同一结果）。返回候选列表（含名称/分数/描述/宿主/路径/"
            "匹配依据）与索引门禁状态。用于\"用哪个 skill 做 X\"类模糊意图的路由。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "用户意图原话，如：做一份周报"},
                "top": {"type": "integer", "description": "返回候选数，默认 4", "default": 4},
            },
            "required": ["query"],
        },
    },
    {
        "name": "skill_dashboard",
        "description": (
            "确保本机 skills 看板服务在运行，返回可打开的 URL。可选传入用户意图，"
            "URL 会带 #q= 预填让看板直接呈现候选。调用方拿到 URL 后：Cursor 用内置"
            "浏览器侧边打开；终端宿主用系统默认浏览器打开。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "intent": {"type": "string",
                           "description": "可选：用户意图，用于看板输入框预填"},
            },
        },
    },
]


class McpServer:
    """协议处理与业务解耦：handle() 是纯函数式入口，便于单测注入。"""

    def __init__(self):
        self._index = None
        self._catalog = None

    # ---------------- 业务 ----------------

    def _load_catalog(self) -> dict:
        if self._catalog is None:
            if not CATALOG_JSON.exists():
                self._rescan()
            self._catalog = json.loads(CATALOG_JSON.read_text(encoding="utf-8"))
            generated = self._catalog.get("generated_at", "1970-01-01T00:00:00+00:00")
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(generated)).days
            if age > 7:
                self._rescan()
                self._catalog = json.loads(CATALOG_JSON.read_text(encoding="utf-8"))
        return self._catalog

    def _rescan(self) -> None:
        subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "skillpick.py"),
                        "scan"], capture_output=True, timeout=120)

    def get_index(self):
        if self._index is None:
            self._index = matching.build_index(self._load_catalog()["skills"])
        return self._index

    def gates_brief(self) -> list:
        return [{"id": g["id"], "status": g["status"], "detail": g["detail"]}
                for g in self._load_catalog().get("gates", [])]

    def tool_skill_match(self, args: dict) -> dict:
        query = (args.get("query") or "").strip()
        if not query:
            return {"error": "query 不能为空"}
        top = int(args.get("top") or 4)
        results = matching.match(self.get_index(), query, top=top)
        return {"query": query, "gates": self.gates_brief(), "results": results,
                "note": "" if results else "本机没有匹配的 skill，不要硬凑"}

    @staticmethod
    def _probe_dashboard() -> str:
        for port in SERVE_PORTS:
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/dashboard.html", timeout=1) as resp:
                    if resp.status == 200:
                        return f"http://127.0.0.1:{port}/dashboard.html"
            except OSError:
                continue
        return ""

    def tool_skill_dashboard(self, args: dict) -> dict:
        url = self._probe_dashboard()
        if not url:
            # 后台拉起 serve（脱离本进程生命周期），再探测
            kwargs = {}
            if sys.platform == "win32":
                kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED | NEW_PROCESS_GROUP
            else:
                kwargs["start_new_session"] = True
            subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve().parent / "skillpick.py"), "serve"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)
            import time
            for _ in range(20):
                time.sleep(0.25)
                url = self._probe_dashboard()
                if url:
                    break
        fallback = str(DASHBOARD_HTML)
        if not url:
            return {"url": "", "fallback_file": fallback,
                    "note": "serve 启动失败，请直接用浏览器打开 fallback_file（file:// 也可用）"}
        intent = (args.get("intent") or "").strip()
        if intent:
            url += "#q=" + urllib.parse.quote(intent)
        return {"url": url, "fallback_file": fallback,
                "note": "Cursor: 内置浏览器 side 打开；终端宿主: 系统默认浏览器打开"}

    # ---------------- 协议 ----------------

    def handle(self, msg: dict):
        """处理一条 JSON-RPC 消息；通知返回 None，请求返回 response dict。"""
        method = msg.get("method", "")
        msg_id = msg.get("id")
        if method == "initialize":
            return self._ok(msg_id, {
                "protocolVersion": msg.get("params", {}).get("protocolVersion",
                                                             PROTOCOL_VERSION),
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            })
        if method.startswith("notifications/"):
            return None
        if method == "tools/list":
            return self._ok(msg_id, {"tools": TOOLS})
        if method == "tools/call":
            params = msg.get("params", {})
            name = params.get("name")
            args = params.get("arguments") or {}
            try:
                if name == "skill_match":
                    payload = self.tool_skill_match(args)
                elif name == "skill_dashboard":
                    payload = self.tool_skill_dashboard(args)
                else:
                    return self._err(msg_id, -32602, f"unknown tool: {name}")
            except Exception as e:  # noqa: BLE001 —— 工具内部错误按 MCP 规范回 isError
                return self._ok(msg_id, {
                    "content": [{"type": "text", "text": f"tool error: {e}"}],
                    "isError": True,
                })
            return self._ok(msg_id, {
                "content": [{"type": "text",
                             "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
            })
        if method == "ping":
            return self._ok(msg_id, {})
        if msg_id is None:
            return None
        return self._err(msg_id, -32601, f"method not found: {method}")

    @staticmethod
    def _ok(msg_id, result) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    @staticmethod
    def _err(msg_id, code, message) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id,
                "error": {"code": code, "message": message}}


def main() -> None:
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
    server = McpServer()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = server.handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
