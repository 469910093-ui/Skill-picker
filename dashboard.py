"""从 catalog.json 生成单文件本地 HTML dashboard。

设计原则（用户铁律）：
1. skills 聚合聚类展示；
2. 高度相似 / 同名漂移的 skills 圈成聚簇、红色感叹号标记，展示相似度与漂移；
3. 意图输入框：模糊意图 -> 候选 skills + 描述 + AI 建议；
4. 只读：本工具永不修改任何 skill，仅展示与提醒。

打分常量单一真相源：rules.json（与 matching.py / match CLI / meta-skill 共用），
JS 是同构镜像，禁止在本文件手写 SYN/权重。

无服务器、无外部资源、无第三方库。
"""

import html
import json
from pathlib import Path

import matching

DATA_DIR = Path.home() / ".skill-picker"
CATALOG_JSON = DATA_DIR / "catalog.json"
DASHBOARD_HTML = DATA_DIR / "dashboard.html"

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
  .tabpane { display: none; }
  .tabpane.active { display: block; }

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
<div class="readonly">✓ 只读模式 — 本工具不会修改、移动或删除任何 skill，仅展示与提醒</div>
<div style="display:flex;gap:8px;flex-wrap:wrap;margin:-6px 0 16px">__GATES__</div>

<div class="tabs">
  <button class="tabbtn active" data-tab="find">🔍 找技能<span class="n">意图匹配 · AI 建议</span></button>
  <button class="tabbtn t2" data-tab="tidy">🩺 理技能<span class="n">相似/漂移自查 · __NCLUSTER__ 组</span></button>
</div>

<div class="tabpane active" id="tab-find">
  <div class="intent-wrap">
    <input id="intent" type="search" placeholder="输入你的意图，比如：我要做一份周报 / 帮我画个图表 / 写飞书文档…">
    <div class="intent-hint">输入后即时给出候选 skills、描述与 AI 建议；同时按关联度过滤下方卡片。会话内的建议由 skill-picker 结合真实上下文给出，这里为本地近似。</div>
    <div id="reco"></div>
  </div>
  <div id="sections">__SECTIONS__</div>
  <div class="empty" id="empty">没有匹配的 skill</div>
</div>

<div class="tabpane" id="tab-tidy">
  __GATE_DETAIL__
  <div class="clusters">
    <h2><span class="bang">!</span>相似 / 漂移聚簇检查（__NCLUSTER__ 组）— 建议人工确认后自行取舍，工具不代改</h2>
    <div class="cluster-grid">__CLUSTERS__</div>
  </div>
</div>

<script>
const SKILLS = __DATA__;
const R = __RULES__;   // 单一真相源 rules.json（与 Python matching.py 共用）
const W = R.weights;
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

// 预处理字段 token + IDF（token 在越多 skill 里出现，权重越低）
const DF = new Map();
SKILLS.forEach(s => {
  s._name = tokenize(s.name); s._desc = tokenize(s.desc); s._kw = tokenize(s.kw || ''); s._cat = norm(s.cat);
  s._nt = norm(s.name); s._dt = norm(s.desc); s._kt = norm(s.kw || '');
  new Set([...s._name, ...s._desc, ...s._kw]).forEach(t => DF.set(t, (DF.get(t) || 0) + 1));
});
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

// tabs 切换
document.querySelectorAll('.tabbtn').forEach(b => b.addEventListener('click', () => {
  document.querySelectorAll('.tabbtn').forEach(x => x.classList.toggle('active', x === b));
  document.querySelectorAll('.tabpane').forEach(p => p.classList.toggle('active', p.id === 'tab-' + b.dataset.tab));
  if (b.dataset.tab === 'find') document.getElementById('intent').focus();
}));

const intentEl = document.getElementById('intent');
const recoEl = document.getElementById('reco');
const cards = [...document.querySelectorAll('.card')];
const sections = [...document.querySelectorAll('section')];
const sectionsBox = document.getElementById('sections');
const originalOrder = [...sections];
cards.forEach(c => c.addEventListener('click', () => c.classList.toggle('open')));

intentEl.addEventListener('input', () => {
  const raw = intentEl.value.trim();
  const q = (norm(stripStop(raw)) || norm(raw)).replace(/ /g, '');

  // 无意图（或太短）：全部显示，恢复默认顺序
  if (q.length < 2) {
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

  // 过滤：以得分为准；文本命中仅作补充且要求长度≥2 的意图词
  const qWords = [...qToks].filter(t => t.length >= 2);
  const cScore = c => scoreMap.get(c.dataset.name) || 0;
  cards.forEach(c => {
    const hit = cScore(c) > W.min_score || qWords.some(t => c.dataset.text.includes(t) && t.length >= 2);
    c.style.display = hit ? '' : 'none';
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

  if (!scored.length) {
    recoEl.className = 'show';
    recoEl.innerHTML = '<div class="reco-card"><div class="reco-body">本机没有明显匹配的 skill——可以直接把需求交给 agent 正常处理。</div></div>';
    return;
  }
  const max = scored[0].score;
  recoEl.className = 'show';
  recoEl.innerHTML = scored.map((x, i) => {
    const nameRuns = matchedRuns(q, x.s._nt.replace(/ /g, ''));
    const descRuns = matchedRuns(q, x.s._dt.replace(/ /g, '')).filter(r => !nameRuns.includes(r));
    const kwRuns = matchedRuns(q, x.s._kt.replace(/ /g, '')).filter(r => !nameRuns.includes(r) && !descRuns.includes(r)).slice(0, 3);
    const synHits = qw.filter(([t, f]) => f < 1 && (x.s._name.has(t) || x.s._desc.has(t) || x.s._kw.has(t)))
                      .slice(0, 4).map(([t]) => '<span class="kw">近义·' + t + '</span>').join('');
    let kws = nameRuns.map(r => '<span class="kw">名称·' + r + '</span>').join('') +
              descRuns.map(r => '<span class="kw">描述·' + r + '</span>').join('') +
              kwRuns.map(r => '<span class="kw">正文·' + r + '</span>').join('') + synHits;
    const cross = x.ns > 0.08 && x.ds > 0.08 ? '<span class="kw" style="color:var(--green);background:rgba(74,222,128,.1)">名称+描述交叉命中</span>' : '';
    return '<div class="reco-card' + (i === 0 ? ' best' : '') + '">' +
      '<div class="reco-rank">' + (i + 1) + '</div><div class="reco-body">' +
      '<div>' + (i === 0 ? '<span class="ai-badge">AI 建议</span>' : '') +
      '<span class="reco-name">' + x.s.name + '</span>' +
      x.s.hosts.map(h => '<span class="badge host" style="background:' + h[1] + ';margin-right:4px">' + h[0] + '</span>').join('') +
      (x.s.warn ? ' <span class="badge warn">⚠ ' + x.s.warn + '</span>' : '') + '</div>' +
      '<div class="reco-desc">' + x.s.desc + '</div>' +
      '<div class="reco-why">匹配依据：' + (kws || '<span class="kw">弱相关</span>') + cross +
      '　场景：' + x.s.cat + '</div>' +
      '<div class="scorebar"><i style="width:' + Math.round(100 * x.score / max) + '%"></i></div>' +
      (i === 0 ? '<div class="reco-note">仅为建议——最终请自行选择；会话内 skill-picker 会结合你的真实上下文重新给出候选。</div>' : '') +
      '</div></div>';
  }).join('');
});
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
            text = html.escape(f"{m['name']} {m['description']} {m['keywords']} {cat}".lower(), quote=True)
            text = "".join(ch for ch in text if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
            paths = "<br>".join(
                f'[{html.escape(HOST_LABELS.get(c["host"], (c["host"], ""))[0])}] {html.escape(c["path"])}'
                for c in m["copies"])
            cards.append(
                f'<div class="card" data-text="{text}" data-name="{html.escape(m["name"], quote=True)}">'
                f'<div class="top"><span class="name">{html.escape(m["name"])}</span>'
                f'{host_badges(m)}{warn_badge}</div>'
                f'<div class="desc">{html.escape(m["description"]) or "（无描述）"}</div>'
                f'<div class="path">{paths}</div></div>')
        sections.append(f"<section><h2>{html.escape(cat)}（{len(cards)}）</h2>"
                        f'<div class="grid">{"".join(cards)}</div></section>')

    # 给 JS 的数据（同样是合并后的条目；cats 为多标签）
    js_data = []
    for m in merged:
        js_data.append({
            "name": m["name"], "desc": m["description"][:220], "cat": m["category"],
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
            f'background:{bg};color:{fg};border:1px solid {bd}" title="{html.escape(g["detail"])}">'
            f'{mark} {g["id"]} {html.escape(g["name"])}</span>')
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
                .replace("__RULES__", rules_json))
    DASHBOARD_HTML.write_text(page, encoding="utf-8")
    return DASHBOARD_HTML


if __name__ == "__main__":
    print(build_dashboard())
