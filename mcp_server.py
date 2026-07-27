#!/usr/bin/env python3
"""skill-picker 本地 MCP server（stdio，纯标准库，零第三方依赖）。

宿主化"插件"形态：注册进 Cursor / Claude Code / Codex 的 MCP 配置后，
agent 可直接调用工具而不必拼 shell 命令——三级降级链的第①级。

暴露两个 tool（与 CLI / dashboard 共用 matching.py + rules.json 单一真相源）：
- skill_match(query, top): 共享打分引擎检索候选 skills
- skill_dashboard(intent?): 确保本地看板服务在跑，返回可打开的 URL（可带 ?q= 意图预填）

协议：MCP stdio 传输（每行一条 JSON-RPC 2.0 消息，newline-delimited）。
"""

import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import matching  # noqa: E402

DATA_DIR = Path.home() / ".skill-picker"
CATALOG_JSON = DATA_DIR / "catalog.json"
DASHBOARD_HTML = DATA_DIR / "dashboard.html"
PENDING_INTENT_JSON = DATA_DIR / "pending_intent.json"
SERVE_PORTS = range(8471, 8481)
PENDING_INTENT_TTL_SEC = 300  # 5 分钟内看板可自动消费会话意图

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "skill-picker", "version": "1.0.0"}

DASHBOARD_AGENT_MUST = (
    "HARD GATE: 在向用户列出任何候选之前，必须用 open_resource / 系统浏览器实际打开 "
    "dashboard_url（或 fallback_file）。只返回文字候选而不打开看板 = 流程失败。"
)

TOOLS = [
    {
        "name": "skill_match",
        "description": (
            "当用户说「帮我选个 skill / 用哪个 skill / 有没有 skill / 本机 skills / "
            "查找本机最适合的 skills」时调用。"
            "在本机全部 skills 中检索候选（与看板同一引擎）。返回候选 + 门禁 + dashboard_url。"
            "HARD GATE: dashboard_required=true；必须先打开 dashboard_url 再列候选。"
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
            "硬门禁入口：确保本机 skills 看板服务在运行，返回可打开的 URL。"
            "可选传入用户意图，URL 会带 ?q= 预填并自动展示匹配结果（勿让用户重输）。"
            "调用方拿到 URL 后必须立刻打开："
            "Cursor 用 open_resource / 内置浏览器侧边打开；终端宿主用系统默认浏览器。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "intent": {"type": "string",
                           "description": "可选：用户意图，写入 URL ?q= 让看板自动匹配"},
            },
        },
    },
]


def dashboard_url_with_intent(base_url: str, intent: str = "", *, bust: bool = True) -> str:
    """把意图写进看板 URL 的 ?q=（不用 #q=：Cursor/Electron 打开时常丢掉 fragment）。

    bust=True 时附加 &_=<ms>，迫使已打开的看板标签页导航刷新，避免仍停在空白搜索。
    """
    intent = (intent or "").strip()
    if not base_url:
        return base_url
    parsed = urllib.parse.urlparse(base_url)
    qs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    qs = [(k, v) for k, v in qs if k not in ("q", "_")]
    if intent:
        qs.append(("q", intent))
        if bust:
            qs.append(("_", str(int(time.time() * 1000))))
    new_query = urllib.parse.urlencode(qs, quote_via=urllib.parse.quote)
    # 清掉旧 #q= fragment，避免与 ?q= 双源冲突
    frag = "" if (intent or parsed.fragment.startswith("q=")) else parsed.fragment
    return urllib.parse.urlunparse(parsed._replace(query=new_query, fragment=frag))


def save_pending_intent(intent: str, source: str = "mcp") -> None:
    """把会话意图落到本地，供看板/serve 在 URL 参数丢失时自动预填。"""
    intent = (intent or "").strip()
    if not intent:
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PENDING_INTENT_JSON.write_text(
        json.dumps({
            "intent": intent,
            "source": source,
            "ts": datetime.now(timezone.utc).isoformat(),
        }, ensure_ascii=False),
        encoding="utf-8",
    )


def load_pending_intent(*, max_age_sec: int = PENDING_INTENT_TTL_SEC) -> str:
    """读取未过期的会话意图；过期或不存在返回空串。"""
    if not PENDING_INTENT_JSON.exists():
        return ""
    try:
        data = json.loads(PENDING_INTENT_JSON.read_text(encoding="utf-8"))
        intent = (data.get("intent") or "").strip()
        ts = data.get("ts") or ""
        if not intent or not ts:
            return ""
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds()
        if age < 0 or age > max_age_sec:
            return ""
        return intent
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return ""


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
        dash = self.tool_skill_dashboard({"intent": query})
        return {
            "query": query,
            "gates": self.gates_brief(),
            "results": results,
            "dashboard_required": True,
            "dashboard_url": dash.get("url") or "",
            "dashboard_fallback_file": dash.get("fallback_file") or str(DASHBOARD_HTML),
            "agent_must": DASHBOARD_AGENT_MUST,
            "note": ("" if results else "本机没有匹配的 skill，不要硬凑。")
                    + " 必须先打开 dashboard_url 再向用户展示候选。",
        }

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
        intent = (args.get("intent") or "").strip()
        # 会话意图落盘：即使 Cursor 打开时丢掉 ?q=，serve/看板仍能自动预填
        if intent:
            save_pending_intent(intent, source="skill_dashboard")
        if not url:
            fallback_uri = dashboard_url_with_intent(
                DASHBOARD_HTML.resolve().as_uri(), intent, bust=False)
            return {
                "url": "",
                "fallback_file": fallback,
                "fallback_url": fallback_uri,
                "dashboard_required": True,
                "agent_must": DASHBOARD_AGENT_MUST,
                "note": "serve 启动失败，请用浏览器打开 fallback_url（已带 ?q= 意图，file:// 可用）",
            }
        url = dashboard_url_with_intent(url, intent, bust=True)
        return {
            "url": url,
            "fallback_file": fallback,
            "fallback_url": dashboard_url_with_intent(
                DASHBOARD_HTML.resolve().as_uri(), intent, bust=False),
            "dashboard_required": True,
            "agent_must": DASHBOARD_AGENT_MUST,
            "note": "Cursor: open_resource 必须打开完整 url（含 ?q=），禁止手改成无参数空白页。"
                    " 意图已写入 pending_intent，看板无 ?q= 时也会自动预填。",
        }

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
