"""把 skill-feed 的 lite 发现页同步到 ~/.skill-picker/discover.html。

链路：本机 skill 匹配为空 → 看板「去 GitHub 发现」子页 → 打开 GitHub 自行安装。
不引入关注 / 发布 / 个人后台；数据来自本机 ~/.skill-feed 或兄弟仓库 skill-feed。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

HOME = Path.home()
DATA_DIR = HOME / ".skill-picker"
DISCOVER_HTML = DATA_DIR / "discover.html"
PUBLIC_EMBED = "https://469910093-ui.github.io/skillfeed/embed.html"

# 兄弟仓库常见位置（与 skill-picker 同级）
_SIBLING_FEED = [
    Path(__file__).resolve().parent.parent / "skill-feed",
    HOME / "Projects" / "skill-feed",
    HOME / "projects" / "skill-feed",
]

# 高信号短语（长的优先匹配）；输出最多 2 个词
_PHRASES = [
    ("去ai味", "去AI味"),
    ("ai味", "去AI味"),
    ("stop-slop", "stop-slop"),
    ("周报复盘", "周报"),
    ("产品设品设计", "产品设计"),  # 历史滑窗拼贴残骸
    ("产品设计", "产品设计"),
    ("交互设计", "交互设计"),
    ("视觉设计", "视觉设计"),
    ("跟团选品", "选品"),
    ("选品工具", "选品"),
    ("剪视频", "剪视频"),
    ("短视频", "短视频"),
    ("周报", "周报"),
    ("复盘", "复盘"),
    ("文案", "文案"),
    ("写作", "写作"),
    ("润色", "润色"),
    ("飞书", "飞书"),
    ("figma", "figma"),
    ("选品", "选品"),
    ("跟团", "选品"),
    ("设计", "设计"),
    ("图表", "图表"),
    ("ppt", "ppt"),
    ("mcp", "mcp"),
    ("看板", "看板"),
]

_STOP_CJK = set("的了呢吗啊把被在是有我要帮做一份一个能否可以怎么如何请帮忙去掉删除去除一下工具设计用来实现功能需求")


def compress_intent_query(intent: str, max_keys: int = 2) -> str:
    """长意图压成最多 max_keys 个短关键词（默认 2）。

    供看板 ?q= 预填与 GitHub / Feeds 发现；本地 match 引擎仍可用完整 query。
    """
    src = (intent or "").strip()
    if not src:
        return ""
    compact = "".join(src.lower().split())
    parts = [p for p in re.split(r"\s+", src) if p]
    cjk_n = sum(1 for ch in compact if "\u4e00" <= ch <= "\u9fff")
    # 已是短查询才直通：词数少 + 总长短 + 中文不宜一整句糊弄过去
    if len(parts) <= max_keys and len(compact) <= 12 and cjk_n <= 6:
        return " ".join(parts[:max_keys])

    keys: list[str] = []

    def push(k: str) -> None:
        t = (k or "").strip()
        if len(t) < 2:
            return
        tl = t.lower()
        if any(tl == x.lower() or tl in x.lower() or x.lower() in tl for x in keys):
            return
        keys.append(t)

    for needle, label in _PHRASES:
        if needle in compact:
            push(label)
            if len(keys) >= max_keys:
                return " ".join(keys[:max_keys])

    if "文案" in compact and ("ai" in compact or "味" in compact):
        push("去AI味")
        push("文案")
        if len(keys) >= max_keys:
            return " ".join(keys[:max_keys])

    # 英文/数字 token（ui、mcp、sku…）
    for w in re.findall(r"[a-z][a-z0-9\-]{1,24}", src.lower()):
        if w in {"the", "and", "for", "with", "skill", "skills", "http", "https", "www"}:
            continue
        push(w.upper() if len(w) <= 3 else w)
        if len(keys) >= max_keys:
            return " ".join(keys[:max_keys])

    # 中文：禁止滑动二元组造假词。短串整段保留；长串只在词表未命中时取末 2 字题眼
    cjk = "".join(ch for ch in compact if "\u4e00" <= ch <= "\u9fff" and ch not in _STOP_CJK)
    if not keys and cjk:
        if len(cjk) <= 6:
            push(cjk)
        else:
            tail = cjk[-2:]
            if tail and not all(ch in _STOP_CJK for ch in tail):
                push(tail)

    if keys:
        return " ".join(keys[:max_keys])
    # 最后兜底：截断整串，仍是 1 个词
    return (parts[0] if parts else compact)[:8]


def discover_url(intent: str = "") -> str:
    """相对看板的 discover 路径，或公开 embed 回退。"""
    q = compress_intent_query(intent, max_keys=2)
    suffix = ("?q=" + quote(q)) if q else ""
    if DISCOVER_HTML.exists():
        return "discover.html" + suffix
    return PUBLIC_EMBED + suffix


def _try_import_feed_dashboard():
    skill_feed_home = HOME / ".skill-feed"
    candidates = []
    if (skill_feed_home / "feed_dashboard.py").exists():
        candidates.append(skill_feed_home)
    for p in _SIBLING_FEED:
        if (p / "feed_dashboard.py").exists():
            candidates.append(p)
    for root in candidates:
        root_s = str(root)
        if root_s not in sys.path:
            sys.path.insert(0, root_s)
        try:
            import feed_dashboard  # type: ignore

            return feed_dashboard, root
        except Exception:  # noqa: BLE001
            continue
    return None, None


def sync_discover_page() -> Path | None:
    """生成或复制 lite 页到 ~/.skill-picker/discover.html。成功返回路径。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # 1) 已有 skill-feed 构建好的 lite 页，直接拷贝
    lite = HOME / ".skill-feed" / "feed.lite.html"
    if lite.exists():
        shutil.copy2(lite, DISCOVER_HTML)
        return DISCOVER_HTML

    # 2) 用 feed.json + feed_dashboard 现生成 lite
    feed_json = HOME / ".skill-feed" / "feed.json"
    mod, _root = _try_import_feed_dashboard()
    if mod and feed_json.exists():
        try:
            feed = json.loads(feed_json.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            feed = None
        if feed is not None:
            try:
                html = mod.build_feed_html(feed, variant="lite")
                DISCOVER_HTML.write_text(html, encoding="utf-8")
                return DISCOVER_HTML
            except Exception:  # noqa: BLE001
                pass

    # 3) 尝试本机 skill-feed refresh/build（可选，失败不阻断）
    for root in _SIBLING_FEED:
        cli = root / "skillfeed.py"
        if not cli.exists():
            continue
        try:
            subprocess.run(
                [sys.executable, str(cli), "build"],
                cwd=str(root),
                capture_output=True,
                timeout=120,
                check=False,
            )
        except Exception:  # noqa: BLE001
            pass
        if lite.exists():
            shutil.copy2(lite, DISCOVER_HTML)
            return DISCOVER_HTML

    return DISCOVER_HTML if DISCOVER_HTML.exists() else None
