"""从 catalog.json 生成单文件本地 HTML dashboard。

设计原则（用户铁律）：
1. skills 聚合聚类展示；
2. 高度相似 / 同名漂移的 skills 圈成聚簇、红色感叹号标记，展示相似度与漂移；
3. 意图输入框：模糊意图 -> 候选 skills + 描述 + AI 建议；
4. 只读：本工具永不修改任何 skill，仅展示与提醒。

打分常量单一真相源：rules.json（与 matching.py / match CLI / meta-skill 共用），
JS 是同构镜像，禁止在本文件手写 SYN/权重。

无服务器、无第三方库。找技能 / 理技能两个 tab 不加载任何外部资源；
「去 GitHub 发现」tab 例外：本机未同步 discover.html 时，iframe 会加载
PUBLIC_EMBED 指向的公开发现页（切到该 tab 才发起，见 __DISCOVER_HREF__）。
"""

import html
import json
import re
from pathlib import Path

import matching

DATA_DIR = Path.home() / ".skill-picker"
CATALOG_JSON = DATA_DIR / "catalog.json"
DASHBOARD_HTML = DATA_DIR / "dashboard.html"
TRANSLATIONS_PATH = Path(__file__).resolve().parent / "translations.json"

# 分类名双语
CAT_EN = {
    "飞书/Lark 办公": "Feishu / Lark Office",
    "周报/复盘/数据分析": "Reports & Analytics",
    "PPT/演示": "Slides & Decks",
    "图表/可视化": "Charts & Visualization",
    "视频/图像/创意": "Video / Image / Creative",
    "写作/内容运营": "Writing & Content",
    "设计/Figma": "Design / Figma",
    "Notion": "Notion",
    "云/AWS/运维": "Cloud / AWS / Ops",
    "Agent/开发工具链": "Agent & Dev Toolchain",
    "出行/电商业务": "Travel & E-commerce",
    "其他": "Others",
}
GATE_EN = {"G1": "Coverage", "G2": "Parse quality", "G3": "Drift", "G4": "Golden queries"}


def _is_zh(text: str) -> bool:
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    return cjk >= max(6, len(text) * 0.12)


def load_translations() -> dict:
    if TRANSLATIONS_PATH.exists():
        data = json.loads(TRANSLATIONS_PATH.read_text(encoding="utf-8"))
        data.pop("_comment", None)
        return data
    return {}

HOST_LABELS = {
    "claude-code": ("Claude Code", "#d97757"),
    "cursor": ("Cursor", "#7c8cf8"),
    "cursor-builtin": ("Cursor 内置", "#5560c8"),
    "cursor-plugin": ("Cursor 插件", "#38bdf8"),
    "claude-plugin": ("Claude 插件", "#e8956d"),
    "codex": ("Codex", "#3aa981"),
    "codex-plugin": ("Codex 插件", "#2dd4bf"),
    "openclaw": ("OpenClaw", "#b58a3d"),
    "gemini": ("Gemini", "#8ab4f8"),
    "opencode": ("OpenCode", "#c084fc"),
    "custom": ("自定义", "#9ca3af"),
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
    --red: #f87171; --amber: #f0b429; --purple: #a78bfa; --green: #4ade80; --blue: #60a5fa;
  }
  * { box-sizing: border-box; margin: 0; }
  body { background: var(--bg); color: var(--text);
         font: 14px/1.6 "Microsoft YaHei", "Segoe UI", system-ui, sans-serif; padding: 26px 32px 90px; }
  header { display: flex; flex-wrap: wrap; align-items: baseline; gap: 14px; }
  h1 { font-size: 22px; font-weight: 700; }
  .stats { color: var(--dim); font-size: 13px; }
  .readonly { display: inline-flex; align-items: center; gap: 6px; margin: 10px 0 16px;
              background: rgba(74,222,128,.08); border: 1px solid rgba(74,222,128,.35);
              color: var(--green); font-size: 12.5px; border-radius: 999px; padding: 3px 14px; }

  /* 顶部 tabs */
  .tabs { display: flex; gap: 8px; margin-bottom: 22px; border-bottom: 1px solid var(--line); }
  .tabbtn { background: none; border: none; color: var(--dim); font: inherit; font-size: 15px;
            font-weight: 600; padding: 9px 18px 11px; cursor: pointer; border-bottom: 2px solid transparent; }
  .tabbtn:hover { color: var(--text); }
  .tabbtn.active { color: var(--text); border-bottom-color: var(--blue); }
  .tabbtn .n { font-size: 11.5px; color: var(--faint); margin-left: 6px; }
  .tabbtn.t2.active { border-bottom-color: var(--red); }
  .tabbtn.t3.active { border-bottom-color: var(--green); }
  .tabpane { display: none; }
  .tabpane.active { display: block; }
  #tab-discover.active { display: flex; flex-direction: column; min-height: 70vh; }
  .discover-bar {
    display: flex; flex-wrap: wrap; align-items: center; gap: 10px;
    margin-bottom: 12px; color: var(--dim); font-size: 13px;
  }
  .discover-bar b { color: var(--text); }
  .discover-cta, .discover-open {
    appearance: none; border: 1px solid rgba(74,222,128,.45); background: rgba(74,222,128,.12);
    color: var(--green); border-radius: 999px; padding: 6px 14px; font: inherit; font-size: 12.5px;
    font-weight: 700; cursor: pointer; text-decoration: none; display: inline-flex; align-items: center;
  }
  .discover-cta:hover, .discover-open:hover { background: rgba(74,222,128,.2); }
  .discover-frame-wrap {
    flex: 1; min-height: 640px; border: 1px solid var(--line); border-radius: 14px; overflow: hidden;
    background: #efefef;
  }
  .discover-frame-wrap iframe { width: 100%; height: min(82vh, 900px); border: 0; display: block; }
  .discover-fallback {
    padding: 28px 20px; color: var(--dim); font-size: 13.5px; line-height: 1.55; max-width: 560px;
  }
  .discover-fallback code { color: var(--blue); font-size: 12px; }
  .empty-actions { margin-top: 14px; display: flex; gap: 10px; justify-content: center; flex-wrap: wrap; }
  .reco-card .discover-cta { margin-top: 10px; }

  /* 意图输入 */
  .intent-wrap { margin-bottom: 30px; }
  #intent { width: 100%; max-width: 680px; padding: 12px 16px; font-size: 15px;
            background: var(--panel); border: 1px solid #3b4250; border-radius: 12px;
            color: var(--text); outline: none; }
  #intent:focus { border-color: var(--blue); box-shadow: 0 0 0 3px rgba(96,165,250,.15); }
  .intent-hint { color: var(--faint); font-size: 12px; margin-top: 6px; }
  #reco { max-width: 980px; margin-top: 14px; display: none; }
  #reco.show { display: block; }
  .reco-card { display: flex; gap: 14px; align-items: flex-start; background: var(--panel);
               border: 1px solid var(--line); border-radius: 12px; padding: 13px 16px; margin-bottom: 8px; }
  .reco-card.best { border-color: rgba(96,165,250,.65); background: #151b26; }
  .reco-rank { font-size: 18px; width: 26px; text-align: center; color: var(--faint); flex: none; }
  .reco-body { min-width: 0; }
  .reco-name { font-weight: 700; font-size: 15px; margin-right: 8px; }
  .ai-badge { background: var(--blue); color: #0e1013; font-weight: 700; font-size: 11px;
              border-radius: 999px; padding: 1px 10px; margin-right: 6px; }
  .reco-desc { color: var(--dim); font-size: 12.5px; margin-top: 3px; }
  .reco-why { margin-top: 6px; font-size: 12px; color: var(--faint); }
  .kw { display: inline-block; background: rgba(96,165,250,.12); color: var(--blue);
        border-radius: 6px; padding: 0 6px; margin: 0 3px 3px 0; }
  .scorebar { height: 4px; background: var(--line); border-radius: 2px; margin-top: 8px; width: 180px; }
  .scorebar i { display: block; height: 100%; background: var(--blue); border-radius: 2px; }
  .reco-note { color: var(--faint); font-size: 11.5px; margin-top: 4px; }

  /* 相似/漂移聚簇 */
  .clusters h2 { font-size: 16px; margin: 6px 0 12px; color: var(--red); display: flex; align-items: center; gap: 8px; }
  .bang { display: inline-flex; width: 20px; height: 20px; border-radius: 50%;
          background: var(--red); color: #fff; font-weight: 800; font-size: 13px;
          align-items: center; justify-content: center; flex: none; }
  .cluster-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(360px, 1fr)); gap: 14px; margin-bottom: 34px; }
  .cluster { border: 1.5px solid rgba(248,113,113,.55); border-radius: 14px; padding: 13px 16px;
             background: linear-gradient(180deg, rgba(248,113,113,.06), transparent 55%), var(--panel); }
  .cluster .chead { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; font-weight: 700; font-size: 13.5px; }
  .ctag { font-size: 11px; border-radius: 999px; padding: 1px 9px; }
  .ctag.drift { background: rgba(240,180,41,.15); color: var(--amber); border: 1px solid rgba(240,180,41,.4); }
  .ctag.sim { background: rgba(248,113,113,.13); color: var(--red); border: 1px solid rgba(248,113,113,.4); }
  .member { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; padding: 3px 0; font-size: 13px; }
  .pairs { margin-top: 8px; border-top: 1px dashed var(--line); padding-top: 7px; }
  .pair { font-size: 12px; color: var(--dim); padding: 1px 0; }
  .pct { color: var(--red); font-weight: 700; }

  /* 场景聚类 */
  section h2 { font-size: 15px; color: var(--dim); font-weight: 600; margin: 26px 0 12px;
               border-bottom: 1px solid var(--line); padding-bottom: 6px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 12px; }
  .card { background: var(--panel); border: 1px solid var(--line); border-radius: 12px;
          padding: 14px 16px; cursor: pointer; }
  .card:hover { border-color: #3b4250; background: var(--panel2); }
  .card .top { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 6px; }
  .card .name { font-weight: 700; font-size: 14.5px; }
  .badge { font-size: 11px; padding: 1px 8px; border-radius: 999px; white-space: nowrap; }
  .host { color: #0e1013; font-weight: 600; }
  .warn { background: rgba(248,113,113,.12); color: var(--red); border: 1px solid rgba(248,113,113,.4); }
  .langbtn { background: none; border: 1px solid var(--line); color: var(--dim);
             border-radius: 8px; font-size: 12px; padding: 4px 12px; cursor: pointer;
             font-family: inherit; }
  .langbtn.active { color: var(--text); border-color: var(--blue); background: rgba(96,165,250,.1); }
  .mt-note { color: var(--faint); font-size: 10.5px; margin-top: 4px; }
  .copybtn { background: none; border: 1px solid var(--line); color: var(--faint);
             border-radius: 6px; font-size: 11px; line-height: 1; padding: 3px 7px;
             cursor: pointer; font-family: inherit; flex: none; }
  .copybtn:hover { color: var(--text); border-color: #4b5563; background: var(--panel2); }
  .copybtn.ok { color: var(--green); border-color: rgba(74,222,128,.5); }
  .desc { color: var(--dim); font-size: 12.5px; display: -webkit-box; -webkit-line-clamp: 3;
          -webkit-box-orient: vertical; overflow: hidden; }
  .card.open .desc { -webkit-line-clamp: unset; }
  .path { color: var(--faint); font-size: 11px; margin-top: 8px; word-break: break-all;
          font-family: Consolas, monospace; display: none; }
  .card.open .path { display: block; }
  .empty { color: var(--faint); padding: 40px 0; text-align: center; display: none; }
  .empty .empty-title { color: var(--text); font-size: 16px; font-weight: 700; margin-bottom: 6px; }
</style>
</head>
<body>
<header>
  <h1>Skill Picker</h1><span class="stats">__STATS__</span>
  <span style="margin-left:auto;display:inline-flex;gap:4px">
    <button class="langbtn" data-lang="zh">中文</button>
    <button class="langbtn" data-lang="en">EN</button>
  </span>
</header>
<div class="readonly" data-zh="✓ 只读模式 — 本工具不会修改、移动或删除任何 skill，仅展示与提醒"
     data-en="✓ Read-only — this tool never modifies, moves or deletes any skill; display & remind only"></div>
<div style="display:flex;gap:8px;flex-wrap:wrap;margin:-6px 0 16px">__GATES__</div>

<div class="tabs">
  <button class="tabbtn active" data-tab="find">🔍 <span data-zh="找技能" data-en="Find"></span><span class="n" data-zh="意图匹配 · AI 建议" data-en="intent match · AI pick"></span></button>
  <button class="tabbtn t2" data-tab="tidy">🩺 <span data-zh="理技能" data-en="Tidy"></span><span class="n" data-zh="相似/漂移自查 · __NCLUSTER__ 组" data-en="similarity/drift check · __NCLUSTER__ groups"></span></button>
  <button class="tabbtn t3" data-tab="discover">🌐 <span data-zh="去 GitHub 发现" data-en="Discover on GitHub"></span><span class="n" data-zh="Feeds · 本机无解时" data-en="Feeds · when local miss"></span></button>
</div>

<div class="tabpane active" id="tab-find">
  <div class="intent-wrap">
    <input id="intent" type="search"
           data-ph-zh="输入你的意图，比如：我要做一份周报 / 帮我画个图表 / 写飞书文档…"
           data-ph-en="Describe your intent, e.g. make a weekly report / draw a chart / edit a video…">
    <div class="intent-hint"
         data-zh="会话唤起时会自动填入你的意图并展示候选，无需再手输；也可在此继续改。本机没有匹配时，可切到「去 GitHub 发现」。"
         data-en="When opened from chat, your intent is prefilled. If nothing matches locally, open Discover on GitHub."></div>
    <div id="reco"></div>
  </div>
  <div id="sections">__SECTIONS__</div>
  <div class="empty" id="empty">
    <div class="empty-title" data-zh="本机没有匹配的 skill" data-en="No matching skill on this machine"></div>
    <div data-zh="可以把需求直接交给 agent，或去 GitHub 发现远程 skill（自行安装后再 scan）。"
         data-en="Ask the agent directly, or discover remote skills on GitHub (install yourself, then scan)."></div>
    <div class="empty-actions">
      <button type="button" class="discover-cta" id="emptyDiscover">🌐 <span data-zh="去 GitHub 发现" data-en="Discover on GitHub"></span></button>
    </div>
  </div>
</div>

<div class="tabpane" id="tab-tidy">
  __GATE_DETAIL__
  <div class="clusters">
    <h2><span class="bang">!</span><span data-zh="相似 / 漂移聚簇检查（__NCLUSTER__ 组）— 建议人工确认后自行取舍，工具不代改"
        data-en="Similarity / drift clusters (__NCLUSTER__ groups) — review manually; the tool never auto-fixes"></span></h2>
    <div class="cluster-grid">__CLUSTERS__</div>
  </div>
</div>

<div class="tabpane" id="tab-discover">
  <div class="discover-bar">
    <span data-zh="Feeds：本机 catalog 无解时，用≤2 个短关键词浏览远程线索 → 打开 GitHub 自行安装。无关注 / 无发布 / 无个人后台。"
          data-en="Feeds: when local catalog misses, browse with ≤2 short keywords → open GitHub to install yourself. No follow / publish / account."></span>
    <a class="discover-open" id="discoverOpenNew" href="__DISCOVER_HREF__" target="_blank" rel="noopener">↗ <span data-zh="新窗口打开" data-en="Open in new window"></span></a>
  </div>
  <div class="discover-frame-wrap" id="discoverFrameWrap">
    <iframe id="discoverFrame" title="skill-feed lite" src="about:blank"></iframe>
  </div>
  <div class="discover-fallback" id="discoverFallback" style="display:none">
    <p data-zh="尚未同步发现页。请先在本机准备 skill-feed 数据，再重新 scan："
       data-en="Discover page not synced yet. Prepare skill-feed data, then scan again:"></p>
    <p><code>cd skill-feed && python skillfeed.py refresh</code></p>
    <p><code>python skillpick.py scan</code></p>
    <p style="margin-top:12px"><a class="discover-open" href="__PUBLIC_EMBED__" target="_blank" rel="noopener"
         data-zh="或直接打开公开发现页 →" data-en="Or open the public discover page →"></a></p>
  </div>
</div>

<script>
const SKILLS = __DATA__;
const R = __RULES__;   // 单一真相源 rules.json（与 Python matching.py 共用）
const W = R.weights;
const DISCOVER_READY = __DISCOVER_READY__;
const DISCOVER_HREF = __DISCOVER_HREF_JSON__;
const PUBLIC_EMBED = __PUBLIC_EMBED_JSON__;
const stripStop = s => { R.stopwords.forEach(w => { s = s.split(w).join(''); }); return s; };
const norm = s => s.toLowerCase().replace(/[^a-z0-9\\u4e00-\\u9fff]+/g, ' ').replace(/\\s+/g, ' ').trim();
const isCJK = ch => ch >= '\\u4e00' && ch <= '\\u9fff';

// 分词：文档侧保留中文单字+二元组；查询侧只用长度≥2 的词（避免「设」「计」满天飞）
function tokenize(s, {query=false} = {}) {
  const toks = new Set();
  for (const word of norm(s).split(' ')) {
    if (!word) continue;
    if (/^[a-z0-9]+$/.test(word)) { toks.add(word); continue; }
    const chars = [...word];
    for (let i = 0; i < chars.length; i++) {
      if (isCJK(chars[i])) {
        if (!query) toks.add(chars[i]);  // 单字仅索引侧保留
        if (i + 1 < chars.length && isCJK(chars[i]) && isCJK(chars[i+1]))
          toks.add(chars[i] + chars[i+1]);
      } else {
        let j = i; while (j < chars.length && !isCJK(chars[j])) j++;
        toks.add(chars.slice(i, j).join('')); i = j - 1;
      }
    }
    if (query && word.length >= 2) toks.add(word);  // 整词「设计」本身
  }
  return toks;
}

// 预处理字段 token + IDF；描述用中英双语一起建索引（中文意图也能打中英文 skill，反之亦然）
const DF = new Map();
SKILLS.forEach(s => {
  s._name = tokenize(s.name);
  s._desc = tokenize((s.descZh || '') + ' ' + (s.descEn || ''));
  s._kw = tokenize(s.kw || ''); s._cat = norm(s.cat);
  s._nt = norm(s.name); s._dt = norm((s.descZh || '') + ' ' + (s.descEn || '')); s._kt = norm(s.kw || '');
  new Set([...s._name, ...s._desc, ...s._kw]).forEach(t => DF.set(t, (DF.get(t) || 0) + 1));
});
const byName = new Map(SKILLS.map(s => [s.name, s]));

// 界面文案双语
const STR = {
  zh: {aiPick:'AI 建议', match:'匹配依据：', scene:'　场景：', weak:'弱相关', cross:'名称+描述交叉命中',
       note:'仅为建议——最终请自行选择；会话内 skill-picker 会结合你的真实上下文重新给出候选。',
       none:'本机没有明显匹配的 skill——可以直接交给 agent，或去 GitHub 发现远程 skill。',
       goDiscover:'去 GitHub 发现',
       name:'名称·', desc:'描述·', body:'正文·', syn:'近义·',
       mt:'AI 译文，原文以 SKILL.md 为准'},
  en: {aiPick:'AI pick', match:'Matched: ', scene:'　Category: ', weak:'weak match', cross:'name+desc cross-hit',
       note:'Suggestion only — you decide; the in-chat skill-picker re-ranks with real session context.',
       none:'No obvious local match — ask the agent, or discover remote skills on GitHub.',
       goDiscover:'Discover on GitHub',
       name:'name·', desc:'desc·', body:'body·', syn:'syn·',
       mt:'AI-translated; the SKILL.md is the source of truth'}
};
let LANG = localStorage.getItem('sp-lang') || 'zh';
const sDesc = s => (LANG === 'zh' ? s.descZh : s.descEn) || s.desc;
const sMt = s => LANG === 'zh' ? s.mtZh : s.mtEn;
const sCat = s => LANG === 'zh' ? s.cat : s.catEn;
const N = SKILLS.length;
const idf = t => DF.has(t) ? Math.log(1 + N / DF.get(t)) : 0;

// 近义词/权重全部来自 R（rules.json 单源），与 Python 端同构
const WEAK_SYN = new Set(R.weak_syn);

function fieldScore(qw, fieldToks) {   // qw: [token, weight][]
  let hitW = 0, totW = 0;
  qw.forEach(([t, f]) => {
    const w = Math.max(idf(t), W.idf_floor) * (t.length >= 2 ? W.len2_boost : 1) * f;
    totW += w;
    if (fieldToks.has(t)) hitW += w;
  });
  return totW ? hitW / totW : 0;
}

function expandIntent(qToks, qCompact) {  // -> [token, weight][]，含近义词（与 Python 同构）
  const out = new Map();
  qToks.forEach(t => { if (t.length >= 2) out.set(t, 1); });
  for (const key of Object.keys(R.syn)) {
    if (!qToks.has(key) && !(qCompact && qCompact.includes(key))) continue;
    for (const syn of tokenize(R.syn[key], {query: true})) {
      if (syn.length < 2 || out.has(syn)) continue;
      out.set(syn, WEAK_SYN.has(syn) ? W.weak_syn : W.syn);
    }
  }
  if (R.design_triggers.some(t => qToks.has(t) || (qCompact && qCompact.includes(t)))) {
    for (const syn of R.design_fallback)
      if (!out.has(syn)) out.set(syn, 0.3);
  }
  return [...out.entries()];
}

function matchedRuns(intent, text) {  // 贪心找出意图中命中 skill 文本的连续片段（长度>=2）
  const runs = []; let i = 0;
  while (i < intent.length) {
    let best = 0;
    for (let len = Math.min(12, intent.length - i); len >= 2; len--) {
      if (text.includes(intent.slice(i, i + len))) { best = len; break; }
    }
    if (best >= 2) { runs.push(intent.slice(i, i + best)); i += best; } else i++;
  }
  return [...new Set(runs)].slice(0, 5);
}

function compressDiscoverQuery(intent) {
  // 与 discover.compress_intent_query 对齐：最多 2 个短关键词
  const src = (intent || '').trim();
  if (!src) return '';
  const compact = src.toLowerCase().replace(/\\s+/g, '');
  const parts = src.split(/\\s+/).filter(Boolean);
  const cjkN = [...compact].filter(ch => /[\\u4e00-\\u9fff]/.test(ch)).length;
  if (parts.length <= 2 && compact.length <= 12 && cjkN <= 6) return parts.slice(0, 2).join(' ');
  const phrases = [
    ['去ai味','去AI味'],['ai味','去AI味'],['stop-slop','stop-slop'],['周报复盘','周报'],
    ['产品设计','产品设计'],['交互设计','交互设计'],['跟团选品','选品'],['选品工具','选品'],
    ['剪视频','剪视频'],['短视频','短视频'],['周报','周报'],['复盘','复盘'],['文案','文案'],
    ['写作','写作'],['润色','润色'],['飞书','飞书'],['figma','figma'],['选品','选品'],
    ['跟团','选品'],['设计','设计'],['图表','图表'],['ppt','ppt'],['mcp','mcp'],['看板','看板']
  ].sort((a,b) => b[0].length - a[0].length);
  const keys = [];
  const push = (k) => {
    const t = (k || '').trim();
    if (t.length < 2 || keys.length >= 2) return;
    if (keys.some(x => x.toLowerCase() === t.toLowerCase() || x.toLowerCase().includes(t.toLowerCase()) || t.toLowerCase().includes(x.toLowerCase()))) return;
    keys.push(t);
  };
  for (const [p, label] of phrases) {
    if (compact.includes(p)) { push(label); if (keys.length >= 2) return keys.slice(0, 2).join(' '); }
  }
  if (compact.includes('文案') && (compact.includes('ai') || compact.includes('味'))) {
    push('去AI味'); push('文案');
    if (keys.length >= 2) return keys.slice(0, 2).join(' ');
  }
  const en = src.toLowerCase().match(/[a-z][a-z0-9\\-]{1,24}/g) || [];
  for (const w of en) {
    if (['the','and','for','with','skill','skills','http','https','www'].includes(w)) continue;
    push(w.length <= 3 ? w.toUpperCase() : w);
    if (keys.length >= 2) return keys.slice(0, 2).join(' ');
  }
  // 禁止滑动切碎；短串整段，长串仅末 2 字题眼
  const stop = new Set('的了呢吗啊把被在是有我要帮做一份一个能否可以怎么如何请帮忙去掉删除去除一下工具设计用来实现功能需求'.split(''));
  const cjk = [...compact].filter(ch => /[\\u4e00-\\u9fff]/.test(ch) && !stop.has(ch)).join('');
  if (!keys.length && cjk) {
    if (cjk.length <= 6) push(cjk);
    else {
      const tail = cjk.slice(-2);
      if (tail && ![...tail].every(ch => stop.has(ch))) push(tail);
    }
  }
  return (keys.slice(0, 2).join(' ') || compact.slice(0, 8));
}

function discoverSrc(intent) {
  // 本机就绪时强制用本地 discover.html，避免 GitHub Pages 旧 embed 仍含滑窗假词
  const q = compressDiscoverQuery(intent);
  const base = DISCOVER_READY
    ? 'discover.html'
    : (PUBLIC_EMBED || DISCOVER_HREF || '').split('?')[0];
  const qs = ['_kw=2', '_=' + Date.now()];
  if (q) qs.unshift('q=' + encodeURIComponent(q));
  return base + '?' + qs.join('&');
}

function openDiscoverTab() {
  const btn = document.querySelector('.tabbtn[data-tab="discover"]');
  if (btn) btn.click();
}

function loadDiscoverFrame() {
  const frame = document.getElementById('discoverFrame');
  const wrap = document.getElementById('discoverFrameWrap');
  const fallback = document.getElementById('discoverFallback');
  const openNew = document.getElementById('discoverOpenNew');
  const intent = document.getElementById('intent');
  const src = discoverSrc(intent ? intent.value : '');
  if (openNew) openNew.href = src;
  if (!DISCOVER_READY && !PUBLIC_EMBED) {
    wrap.style.display = 'none';
    fallback.style.display = 'block';
    return;
  }
  // 本机无 discover.html 时仍可用公开 embed；file:// 下 iframe 跨域可能被拦，提供新窗口
  wrap.style.display = '';
  fallback.style.display = DISCOVER_READY ? 'none' : 'block';
  // 每次切入都带时间戳重载，杜绝 iframe 缓存旧 JS
  frame.setAttribute('data-src', src);
  frame.src = src;
}

// tabs 切换
document.querySelectorAll('.tabbtn').forEach(b => b.addEventListener('click', () => {
  document.querySelectorAll('.tabbtn').forEach(x => x.classList.toggle('active', x === b));
  document.querySelectorAll('.tabpane').forEach(p => p.classList.toggle('active', p.id === 'tab-' + b.dataset.tab));
  if (b.dataset.tab === 'find') document.getElementById('intent').focus();
  if (b.dataset.tab === 'discover') loadDiscoverFrame();
}));

document.getElementById('emptyDiscover').addEventListener('click', openDiscoverTab);

const intentEl = document.getElementById('intent');
const recoEl = document.getElementById('reco');
const cards = [...document.querySelectorAll('.card')];
const sections = [...document.querySelectorAll('section')];
const sectionsBox = document.getElementById('sections');
const originalOrder = [...sections];
cards.forEach(c => c.addEventListener('click', e => {
  if (e.target.closest('.copybtn')) return;   // 点复制角标不触发展开
  c.classList.toggle('open');
}));

// 一键复制 skill 名（含 reco 面板动态节点，用事件委托）
async function copyText(text, btn) {
  try { await navigator.clipboard.writeText(text); }
  catch (err) {
    const ta = document.createElement('textarea');
    ta.value = text; document.body.appendChild(ta);
    ta.select(); document.execCommand('copy'); ta.remove();
  }
  const prev = btn.textContent;
  btn.textContent = '✓'; btn.classList.add('ok');
  setTimeout(() => { btn.textContent = prev; btn.classList.remove('ok'); }, 1200);
}
document.addEventListener('click', e => {
  const b = e.target.closest('.copybtn');
  if (b) copyText(b.dataset.copy, b);
});

// 会话意图自动预填（用户无需再手输）
// 优先级：?q= → #q= → /api/pending_intent（MCP 写入的会话上下文）
// 注意：首次调用在脚本末尾（须等 input 监听器注册完成）
function readPrefillIntentFromUrl() {
  try {
    const q = new URLSearchParams(location.search).get('q');
    if (q && q.trim()) return q.trim();
  } catch (_) {}
  const m = location.hash.match(/^#q=(.+)$/);
  if (!m) return '';
  try { return decodeURIComponent(m[1]).trim(); }
  catch (_) { return m[1].trim(); }
}
function applyIntentToInput(intent, source) {
  if (!intent) return;
  const short = compressDiscoverQuery(intent);
  const findBtn = document.querySelector('.tabbtn[data-tab="find"]');
  if (findBtn && !findBtn.classList.contains('active')) findBtn.click();
  intentEl.value = short || intent;
  intentEl.dispatchEvent(new Event('input'));
  intentEl.focus();
  const hint = document.querySelector('.intent-hint');
  if (hint && source) {
    const mark = source === 'session'
      ? (LANG === 'en' ? ' · prefilled from chat (≤2 keywords)' : ' · 已从会话自动填入（≤2 关键词）')
      : (LANG === 'en' ? ' · prefilled from URL (≤2 keywords)' : ' · 已从链接自动填入（≤2 关键词）');
    if (!hint.dataset.baseZh) {
      hint.dataset.baseZh = hint.getAttribute('data-zh') || hint.textContent;
      hint.dataset.baseEn = hint.getAttribute('data-en') || hint.textContent;
    }
    hint.textContent = (LANG === 'en' ? hint.dataset.baseEn : hint.dataset.baseZh) + mark;
  }
}
function readTabFromUrl() {
  try { return (new URLSearchParams(location.search).get('tab') || '').trim(); }
  catch (_) { return ''; }
}
function applyPrefillQuery() {
  const fromUrl = readPrefillIntentFromUrl();
  const wantDiscover = readTabFromUrl() === 'discover';
  const after = () => {
    if (wantDiscover) openDiscoverTab();
    else if (intentEl.value.trim() && document.getElementById('empty') &&
             document.getElementById('empty').style.display === 'block') {
      // 本机无匹配时突出 Feeds 入口（不强制跳转，避免抢走找技能结果）
    }
  };
  if (fromUrl) { applyIntentToInput(fromUrl, 'url'); after(); return; }
  // file:// 无法打 API；http(s) 看板拉取 MCP 落盘的会话意图
  if (location.protocol === 'http:' || location.protocol === 'https:') {
    fetch('/api/pending_intent', { cache: 'no-store' })
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        const intent = (data && data.intent || '').trim();
        if (intent) applyIntentToInput(intent, 'session');
        after();
      })
      .catch(() => { after(); });
    return;
  }
  after();
}
window.addEventListener('hashchange', applyPrefillQuery);
window.addEventListener('popstate', applyPrefillQuery);
window.addEventListener('pageshow', applyPrefillQuery);

intentEl.addEventListener('input', () => {
  const raw = intentEl.value.trim();
  const q = norm(stripStop(raw)) || norm(raw);       // 保留空格：英文分词依赖词边界
  const qCompact = q.replace(/ /g, '');              // 紧凑形式只用于子串比较

  // 无意图（或太短）：全部显示，恢复默认顺序
  if (qCompact.length < 2) {
    recoEl.className = ''; recoEl.innerHTML = '';
    cards.forEach(c => { c.style.display = ''; });
    sections.forEach(s => { s.style.display = ''; });
    originalOrder.forEach(s => sectionsBox.appendChild(s));
    document.getElementById('empty').style.display = 'none';
    return;
  }
  const qToks = tokenize(q, {query: true});
  const qw = expandIntent(qToks, q);
  // 场景名是否被意图点名（「设计」→「设计/Figma」）：该区整体置顶（多标签全参与）
  const catPinned = new Set(
    [...new Set(SKILLS.flatMap(s => s.cats))].filter(cat => {
      const cn = norm(cat).replace(/ /g, '');
      return cn.includes(q) || [...qToks].some(t => t.length >= 2 && cn.includes(t));
    })
  );
  const all = SKILLS.map(s => {
    const ns = fieldScore(qw, s._name);              // 名称命中
    const ds = fieldScore(qw, s._desc);              // 描述命中
    const ks = fieldScore(qw, s._kw);                // MD 正文关键词命中
    let score = W.name * ns + W.desc * ds + W.kw * ks;
    if (ns > 0.08 && ds > 0.08) score *= W.cross;
    else if (ks > 0.1 && (ns > 0.08 || ds > 0.08)) score *= W.kw_cross;
    // 整串命中：名称强加分；描述仅当该词不常见才加，避免「设计」泛词刷分
    const qCompact = q.replace(/ /g, '');
    if (s._nt.replace(/ /g, '').includes(qCompact)) score += W.name_substr;
    else if (s._dt.replace(/ /g, '').includes(qCompact)) {
      const dfRatio = (DF.get(qCompact) || 0) / Math.max(N, 1);
      if (dfRatio <= W.desc_substr_max_df) score += W.desc_substr;
    }
    if (s.cats.some(c => catPinned.has(c))) score += W.cat_pin;  // 多标签任一被点名即抬升
    return { s, score, ns, ds };
  });
  const scoreMap = new Map(all.map(x => [x.s.name, x.score]));
  const scored = all.filter(x => x.score > W.min_score).sort((a, b) => b.score - a.score).slice(0, 4);

  // 过滤：纯打分 + 相对阈值。绝对线 min_score 之外，再砍掉低于第一名 30% 的长尾——
  // 不相关的内容一律不展示（此前 "train" 会因子串误中 training/constraint 拖出无关分区）
  const cScore = c => scoreMap.get(c.dataset.name) || 0;
  const topScore = Math.max(...all.map(x => x.score), 0);
  const cutoff = Math.max(W.min_score, 0.3 * topScore);
  cards.forEach(c => {
    c.style.display = cScore(c) >= cutoff ? '' : 'none';
  });
  let any = false;
  const secBest = new Map();
  sections.forEach(s => {
    const vis = [...s.querySelectorAll('.card')].filter(c => c.style.display !== 'none');
    const title = (s.querySelector('h2')?.textContent || '').replace(/（.*$/, '');
    let best = vis.length ? Math.max(...vis.map(cScore)) : -1;
    if (catPinned.has(title)) best += 10;            // 点名场景强制排最前
    secBest.set(s, best);
    s.style.display = vis.length ? '' : 'none'; any = any || vis.length > 0;
    const grid = s.querySelector('.grid');
    vis.sort((a, b) => cScore(b) - cScore(a)).forEach(c => grid.appendChild(c));
  });
  document.getElementById('empty').style.display = any ? 'none' : 'block';
  [...sections].sort((a, b) => secBest.get(b) - secBest.get(a)).forEach(s => sectionsBox.appendChild(s));

  const T = STR[LANG];
  if (!scored.length) {
    recoEl.className = 'show';
    recoEl.innerHTML = '<div class="reco-card"><div class="reco-body">' + T.none +
      '<div><button type="button" class="discover-cta js-go-discover">🌐 ' + T.goDiscover + '</button></div>' +
      '</div></div>';
    recoEl.querySelector('.js-go-discover')?.addEventListener('click', openDiscoverTab);
    return;
  }
  const max = scored[0].score;
  recoEl.className = 'show';
  recoEl.innerHTML = scored.map((x, i) => {
    const nameRuns = matchedRuns(q, x.s._nt.replace(/ /g, ''));
    const descRuns = matchedRuns(q, x.s._dt.replace(/ /g, '')).filter(r => !nameRuns.includes(r));
    const kwRuns = matchedRuns(q, x.s._kt.replace(/ /g, '')).filter(r => !nameRuns.includes(r) && !descRuns.includes(r)).slice(0, 3);
    const synHits = qw.filter(([t, f]) => f < 1 && (x.s._name.has(t) || x.s._desc.has(t) || x.s._kw.has(t)))
                      .slice(0, 4).map(([t]) => '<span class="kw">' + T.syn + t + '</span>').join('');
    let kws = nameRuns.map(r => '<span class="kw">' + T.name + r + '</span>').join('') +
              descRuns.map(r => '<span class="kw">' + T.desc + r + '</span>').join('') +
              kwRuns.map(r => '<span class="kw">' + T.body + r + '</span>').join('') + synHits;
    const cross = x.ns > 0.08 && x.ds > 0.08 ? '<span class="kw" style="color:var(--green);background:rgba(74,222,128,.1)">' + T.cross + '</span>' : '';
    const mtNote = sMt(x.s) ? '<div class="mt-note">⟡ ' + T.mt + '</div>' : '';
    return '<div class="reco-card' + (i === 0 ? ' best' : '') + '">' +
      '<div class="reco-rank">' + (i + 1) + '</div><div class="reco-body">' +
      '<div>' + (i === 0 ? '<span class="ai-badge">' + T.aiPick + '</span>' : '') +
      '<span class="reco-name">' + x.s.name + '</span>' +
      x.s.hosts.map(h => '<span class="badge host" style="background:' + h[1] + ';margin-right:4px">' + h[0] + '</span>').join('') +
      (x.s.warn ? ' <span class="badge warn">⚠ ' + x.s.warn + '</span>' : '') +
      ' <button class="copybtn" data-copy="' + x.s.name + '">⧉</button></div>' +
      '<div class="reco-desc">' + sDesc(x.s) + '</div>' + mtNote +
      '<div class="reco-why">' + T.match + (kws || '<span class="kw">' + T.weak + '</span>') + cross +
      T.scene + sCat(x.s) + '</div>' +
      '<div class="scorebar"><i style="width:' + Math.round(100 * x.score / max) + '%"></i></div>' +
      (i === 0 ? '<div class="reco-note">' + T.note + '</div>' : '') +
      '</div></div>';
  }).join('');
});

// 语言切换：UI 文案 + 卡片描述 + 分类名 + 推荐面板全部跟随
function applyLang() {
  document.querySelectorAll('[data-zh]').forEach(el => { el.textContent = el.dataset[LANG] || el.dataset.zh; });
  intentEl.placeholder = LANG === 'zh' ? intentEl.dataset.phZh : intentEl.dataset.phEn;
  document.querySelectorAll('.langbtn').forEach(b => b.classList.toggle('active', b.dataset.lang === LANG));
  cards.forEach(c => {
    const s = byName.get(c.dataset.name);
    if (!s) return;
    const mt = sMt(s) ? (LANG === 'zh' ? '　⟡AI 译' : '　⟡AI-translated') : '';
    c.querySelector('.desc').textContent = (sDesc(s) || '') + mt;
  });
  intentEl.dispatchEvent(new Event('input'));
}
document.querySelectorAll('.langbtn').forEach(b => b.addEventListener('click', () => {
  LANG = b.dataset.lang; localStorage.setItem('sp-lang', LANG); applyLang();
}));
applyLang();

applyPrefillQuery();   // input 监听器已就绪，此时消费 ?q= / #q= 才能触发匹配
</script>
</body>
</html>
"""


def _union_find_clusters(catalog: dict) -> list[dict]:
    """把同名多份 + 描述重叠的 skill 连成聚簇。"""
    skills = catalog["skills"]
    dup = catalog["duplicates"]
    by_path = {s["path"]: s for s in skills}

    parent: dict[str, str] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        parent[find(a)] = find(b)

    pair_info: dict[frozenset, str] = {}
    for g in dup["same_name"]:
        paths = [c["path"] for c in g["copies"]]
        for p in paths[1:]:
            union(paths[0], p)
        label = "同名漂移" if g["status"] == "drifted" else "同名一致"
        for i in range(len(paths)):
            for j in range(i + 1, len(paths)):
                pair_info[frozenset((paths[i], paths[j]))] = label
    for o in dup["overlapping"]:
        union(o["a"]["path"], o["b"]["path"])
        pair_info[frozenset((o["a"]["path"], o["b"]["path"]))] = f"{o['similarity']:.0%}"

    groups: dict[str, list[str]] = {}
    for p in parent:
        groups.setdefault(find(p), []).append(p)

    clusters = []
    for members in groups.values():
        if len(members) < 2:
            continue
        pairs = []
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                key = frozenset((members[i], members[j]))
                if key in pair_info:
                    pairs.append((by_path[members[i]], by_path[members[j]], pair_info[key]))
        drift = any(v == "同名漂移" for _, _, v in pairs)
        clusters.append({
            "members": [by_path[m] for m in members if m in by_path],
            "pairs": pairs,
            "drift": drift,
        })
    clusters.sort(key=lambda c: (not c["drift"], -len(c["members"])))
    return clusters


def _warn_maps(catalog: dict):
    dup = catalog["duplicates"]
    drifted = {g["name"] for g in dup["same_name"] if g["status"] == "drifted"}
    overlap: dict[str, int] = {}
    for o in dup["overlapping"]:
        overlap[o["a"]["path"]] = overlap.get(o["a"]["path"], 0) + 1
        overlap[o["b"]["path"]] = overlap.get(o["b"]["path"], 0) + 1
    return drifted, overlap


def build_dashboard() -> Path:
    catalog = json.loads(CATALOG_JSON.read_text(encoding="utf-8"))
    skills = catalog["skills"]
    rules = matching.load_rules()
    drifted, overlap = _warn_maps(catalog)
    clusters = _union_find_clusters(catalog)

    discover_ready = False
    discover_href = "https://469910093-ui.github.io/skillfeed/embed.html"
    public_embed = discover_href
    try:
        from discover import PUBLIC_EMBED, sync_discover_page

        public_embed = PUBLIC_EMBED
        synced = sync_discover_page(catalog)
        if synced and synced.exists():
            discover_ready = True
            discover_href = "discover.html"
    except Exception:
        pass
    # 合并逻辑复用共享引擎，展示层只补充 drift/overlap 标记
    merged = matching.merge_copies(skills)
    for m in merged:
        m["drift"] = m["dir_name"].lower() in drifted
        m["overlap"] = max((overlap.get(c["path"], 0) for c in m["copies"]), default=0)

    def warn_text(m):
        w = []
        if m["drift"]:
            w.append("同名漂移")
        if m["overlap"]:
            w.append(f"重叠×{m['overlap']}")
        return " / ".join(w)

    def host_badges(m):
        return "".join(
            f'<span class="badge host" style="background:{HOST_LABELS.get(h, (h, "#888"))[1]}">'
            f'{html.escape(HOST_LABELS.get(h, (h, "#888"))[0])}</span>'
            for h in m["hosts"])

    # 聚簇卡片
    cluster_html = []
    for c in clusters:
        names = {m["name"] for m in c["members"]}
        tag = '<span class="ctag drift">含同名漂移</span>' if c["drift"] else '<span class="ctag sim">高度相似</span>'
        member_rows = []
        for m in sorted(c["members"], key=lambda x: (x["name"].lower(), x["host"])):
            label, color = HOST_LABELS.get(m["host"], (m["host"], "#888"))
            member_rows.append(
                f'<div class="member"><span class="bang" style="width:14px;height:14px;font-size:10px">!</span>'
                f'<b>{html.escape(m["name"])}</b>'
                f'<span class="badge host" style="background:{color}">{html.escape(label)}</span></div>')
        pair_rows = []
        for a, b, v in sorted(c["pairs"], key=lambda x: x[2], reverse=True):
            val = f'<span class="pct">{v}</span>' if v.endswith("%") else v
            pair_rows.append(f'<div class="pair">{html.escape(a["name"])} ({a["host"]}) ↔ '
                             f'{html.escape(b["name"])} ({b["host"]})：{val}</div>')
        title = " · ".join(sorted(names)) if len(names) > 1 else next(iter(names))
        cluster_html.append(
            f'<div class="cluster"><div class="chead"><span class="bang">!</span>'
            f'{html.escape(title[:60])}{tag}</div>'
            f'{"".join(member_rows)}<div class="pairs">{"".join(pair_rows)}</div></div>')

    # 双语描述：原文缺哪种语言就用译文库补齐，缺译文回退原文并打 mt 标
    translations = load_translations()

    def bilingual(m):
        orig = m["description"]
        entry = translations.get(m["dir_name"].lower(), {})
        if _is_zh(orig):
            return orig, entry.get("en") or orig, False, bool(entry.get("en"))
        return entry.get("zh") or orig, orig, bool(entry.get("zh")), False

    for m in merged:
        m["desc_zh"], m["desc_en"], m["mt_zh"], m["mt_en"] = bilingual(m)

    # 场景分区（合并后的条目；多端副本一张卡、宿主 label 逐一展示）
    by_cat: dict[str, list[dict]] = {}
    for m in merged:
        by_cat.setdefault(m["category"], []).append(m)
    sections = []
    for cat in sorted(by_cat, key=lambda c: -len(by_cat[c])):
        cards = []
        for m in sorted(by_cat[cat], key=lambda x: x["name"].lower()):
            w = warn_text(m)
            warn_badge = f'<span class="badge warn">! {html.escape(w)}</span>' if w else ""
            text = html.escape(
                f"{m['name']} {m['desc_zh']} {m['desc_en']} {m['keywords']} {cat}".lower(), quote=True)
            text = "".join(ch for ch in text if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
            paths = "<br>".join(
                f'[{html.escape(HOST_LABELS.get(c["host"], (c["host"], ""))[0])}] {html.escape(c["path"])}'
                for c in m["copies"])
            copy_btn = (f'<button class="copybtn" data-copy="{html.escape(m["name"], quote=True)}"'
                        f'>⧉</button>')
            cards.append(
                f'<div class="card" data-text="{text}" data-name="{html.escape(m["name"], quote=True)}">'
                f'<div class="top"><span class="name">{html.escape(m["name"])}</span>'
                f'{host_badges(m)}{warn_badge}{copy_btn}</div>'
                f'<div class="desc">{html.escape(m["desc_zh"]) or "（无描述）"}</div>'
                f'<div class="path">{paths}</div></div>')
        cat_en = CAT_EN.get(cat, cat)
        sections.append(
            f'<section data-cat="{html.escape(cat, quote=True)}">'
            f'<h2><span data-zh="{html.escape(cat, quote=True)}（{len(cards)}）" '
            f'data-en="{html.escape(cat_en, quote=True)} ({len(cards)})"></span></h2>'
            f'<div class="grid">{"".join(cards)}</div></section>')

    # 给 JS 的数据（同样是合并后的条目；cats 为多标签；双语描述 + 机翻标记）
    js_data = []
    for m in merged:
        js_data.append({
            "name": m["name"],
            "desc": m["description"][:220],
            "descZh": m["desc_zh"][:220], "descEn": m["desc_en"][:220],
            "mtZh": m["mt_zh"], "mtEn": m["mt_en"],
            "cat": m["category"], "catEn": CAT_EN.get(m["category"], m["category"]),
            "cats": m.get("categories", [m["category"]]),
            "kw": m["keywords"][:300],
            "hosts": [[HOST_LABELS.get(h, (h, "#888"))[0], HOST_LABELS.get(h, (h, "#888"))[1]]
                      for h in m["hosts"]],
            "warn": warn_text(m),
        })
    data_json = json.dumps(js_data, ensure_ascii=False).replace("</", "<\\/")
    rules_json = json.dumps({
        "syn": rules["syn"], "weak_syn": rules["weak_syn"],
        "design_triggers": rules["design_triggers"], "design_fallback": rules["design_fallback"],
        "stopwords": rules["stopwords"], "weights": rules["weights"],
    }, ensure_ascii=False).replace("</", "<\\/")

    stats = (f"{len(merged)} 个 skill（含多端副本共 {catalog['skill_count']} 份） · "
             f"{len(by_cat)} 个场景 · {len(clusters)} 组相似/漂移聚簇 · "
             f"生成于 {catalog['generated_at'][:16]} UTC")

    # 门禁徽章与详情
    gate_colors = {"pass": ("rgba(74,222,128,.1)", "#4ade80", "rgba(74,222,128,.35)"),
                   "warn": ("rgba(240,180,41,.1)", "#f0b429", "rgba(240,180,41,.4)"),
                   "fail": ("rgba(248,113,113,.12)", "#f87171", "rgba(248,113,113,.5)")}
    gate_pills, gate_detail = [], []
    for g in catalog.get("gates", []):
        bg, fg, bd = gate_colors[g["status"]]
        mark = {"pass": "✓", "warn": "⚠", "fail": "✗"}[g["status"]]
        gate_pills.append(
            f'<span style="font-size:12px;border-radius:999px;padding:2px 12px;'
            f'background:{bg};color:{fg};border:1px solid {bd}" title="{html.escape(g["detail"])}"'
            f' data-zh="{mark} {g["id"]} {html.escape(g["name"], quote=True)}"'
            f' data-en="{mark} {g["id"]} {html.escape(GATE_EN.get(g["id"], g["name"]), quote=True)}"></span>')
        if g["status"] != "pass":
            items = "".join(f'<li style="color:var(--dim);font-size:12px;margin-left:18px">'
                            f'<code>{html.escape(it)}</code></li>' for it in g["items"][:15])
            action = (f'<div style="color:{fg};font-size:12.5px;margin-top:4px">处理：'
                      f'{html.escape(g["action"])}</div>' if g.get("action") else "")
            gate_detail.append(
                f'<div style="border:1px solid {bd};border-radius:12px;padding:12px 16px;'
                f'margin-bottom:14px;background:{bg}">'
                f'<b style="color:{fg}">{mark} {g["id"]} {html.escape(g["name"])} '
                f'{g["status"].upper()}</b>'
                f'<div style="color:var(--dim);font-size:13px;margin-top:2px">{html.escape(g["detail"])}</div>'
                f'<ul>{items}</ul>{action}</div>')

    page = (PAGE.replace("__STATS__", stats)
                .replace("__GATES__", "".join(gate_pills))
                .replace("__GATE_DETAIL__", "".join(gate_detail))
                .replace("__NCLUSTER__", str(len(clusters)))
                .replace("__CLUSTERS__", "".join(cluster_html) or
                         '<div style="color:var(--dim)">未发现相似或漂移的 skills。</div>')
                .replace("__SECTIONS__", "".join(sections))
                .replace("__DATA__", data_json)
                .replace("__RULES__", rules_json)
                .replace("__DISCOVER_READY__", "true" if discover_ready else "false")
                .replace("__DISCOVER_HREF__", html.escape(discover_href, quote=True))
                .replace("__DISCOVER_HREF_JSON__", json.dumps(discover_href))
                .replace("__PUBLIC_EMBED__", html.escape(public_embed, quote=True))
                .replace("__PUBLIC_EMBED_JSON__", json.dumps(public_embed)))
    DASHBOARD_HTML.write_text(page, encoding="utf-8")
    return DASHBOARD_HTML


if __name__ == "__main__":
    print(build_dashboard())
