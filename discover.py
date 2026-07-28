"""把 skill-feed 的 lite 发现页同步到 ~/.skill-picker/discover.html。

链路：本机 skill 匹配为空 → 看板「去 GitHub 发现」子页 → 打开 GitHub 自行安装。
不引入关注 / 发布 / 个人后台；数据来自本机 ~/.skill-feed 或兄弟仓库 skill-feed。
"""

from __future__ import annotations

import json
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


def discover_url(intent: str = "") -> str:
    """相对看板的 discover 路径，或公开 embed 回退。"""
    q = (intent or "").strip()
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
        except ImportError:
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
            mod.write_feed_html(feed, DISCOVER_HTML, variant="lite")
            return DISCOVER_HTML

    # 3) 尝试调用兄弟仓 skillfeed.py build（不联网）
    for root in _SIBLING_FEED:
        skillfeed_py = root / "skillfeed.py"
        if not skillfeed_py.exists():
            continue
        try:
            subprocess.run(
                [sys.executable, str(skillfeed_py), "build"],
                cwd=str(root),
                check=False,
                capture_output=True,
                timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
        if lite.exists():
            shutil.copy2(lite, DISCOVER_HTML)
            return DISCOVER_HTML

    return DISCOVER_HTML if DISCOVER_HTML.exists() else None
