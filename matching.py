"""共享匹配引擎（单一真相源）。

dashboard（前端 JS 镜像同一份 rules.json 常量）、`skillpick.py match` CLI、
meta-skill 会话路由、unittest 黄金用例，全部走这里的打分逻辑，
保证「页面一套脑、会话另一套脑」不再发生。

零第三方依赖，仅标准库。
"""

import json
import math
import re
from pathlib import Path

RULES_PATH = Path(__file__).resolve().parent / "rules.json"


def load_rules(path: Path | None = None) -> dict:
    return json.loads((path or RULES_PATH).read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 分词

def _is_cjk(ch: str) -> bool:
    return "\u4e00" <= ch <= "\u9fff"


def norm(s: str) -> str:
    s = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def tokenize(s: str, query: bool = False) -> set:
    """文档侧：中文单字 + 二元组 + 英文词；查询侧：仅 ≥2 长度 token（防单字误命中）。"""
    toks = set()
    for word in norm(s).split(" "):
        if not word:
            continue
        if re.fullmatch(r"[a-z0-9]+", word):
            toks.add(word)
            continue
        chars = list(word)
        i = 0
        while i < len(chars):
            if _is_cjk(chars[i]):
                if not query:
                    toks.add(chars[i])
                if i + 1 < len(chars) and _is_cjk(chars[i + 1]):
                    toks.add(chars[i] + chars[i + 1])
                i += 1
            else:
                j = i
                while j < len(chars) and not _is_cjk(chars[j]):
                    j += 1
                toks.add("".join(chars[i:j]))
                i = j
        if query and len(word) >= 2:
            toks.add(word)
    if query:
        toks = {t for t in toks if len(t) >= 2}
    return toks


# ---------------------------------------------------------------- 分类（多标签）

def categorize(name: str, description: str, rules: dict) -> tuple[str, list]:
    """返回 (主分类, 全部标签)。名称强路由优先，其余按规则表顺序累积多标签。"""
    lname = name.lower()
    labels = []
    for mode, pat, cat in rules["name_routes"]:
        if (mode == "prefix" and lname.startswith(pat)) or \
           (mode == "contains" and pat in lname) or \
           (mode == "exact" and lname == pat):
            labels.append(cat)
            break
    haystack = f"{name} {description}".lower()
    for cat, keywords in rules["category_rules"]:
        if cat in labels:
            continue
        if any(kw in haystack for kw in keywords):
            labels.append(cat)
    if not labels:
        labels.append(rules["fallback_category"])
    return labels[0], labels


# ---------------------------------------------------------------- 合并多端副本

def merge_copies(skills: list) -> list:
    """同一 skill 散在不同客户端的副本合并为一条（展示与匹配都用合并视图）。"""
    groups: dict[str, list] = {}
    for s in skills:
        groups.setdefault(s["dir_name"].lower(), []).append(s)
    merged = []
    for copies in groups.values():
        primary = max(copies, key=lambda c: (len(c.get("description", "")),
                                             len(c.get("keywords", ""))))
        merged.append({
            "name": primary["name"],
            "dir_name": primary["dir_name"],
            "description": primary.get("description", ""),
            "keywords": primary.get("keywords", ""),
            "category": primary.get("category", ""),
            "categories": primary.get("categories", [primary.get("category", "")]),
            "hosts": sorted({c["host"] for c in copies}),
            "copies": [{"host": c["host"], "path": c["path"]} for c in copies],
        })
    return merged


# ---------------------------------------------------------------- 打分

class MatchIndex:
    def __init__(self, merged: list, rules: dict):
        self.rules = rules
        self.skills = merged
        self.df: dict[str, int] = {}
        for s in merged:
            s["_name"] = tokenize(s["name"])
            s["_desc"] = tokenize(s["description"])
            s["_kw"] = tokenize(s.get("keywords", ""))
            s["_nt"] = norm(s["name"]).replace(" ", "")
            s["_dt"] = norm(s["description"]).replace(" ", "")
            for t in s["_name"] | s["_desc"] | s["_kw"]:
                self.df[t] = self.df.get(t, 0) + 1
        self.n = max(len(merged), 1)

    def idf(self, t: str) -> float:
        return math.log(1 + self.n / self.df[t]) if t in self.df else 0.0


def build_index(skills: list, rules: dict | None = None) -> MatchIndex:
    rules = rules or load_rules()
    return MatchIndex(merge_copies(skills), rules)


def expand_intent(q_toks: set, rules: dict, q_compact: str = "") -> list:
    """意图 token + 近义词扩展 -> [(token, weight)]。

    近义词按「SYN key 是意图子串」触发（单字 key 如「剪」也能生效，
    尽管单字不直接参与打分）；泛词折价，设计类补弱近义。
    """
    w = rules["weights"]
    weak = set(rules["weak_syn"])
    out: dict[str, float] = {t: 1.0 for t in q_toks if len(t) >= 2}
    for key, syn in rules["syn"].items():
        if key not in q_toks and (not q_compact or key not in q_compact):
            continue
        for s in tokenize(syn, query=True):
            if len(s) >= 2 and s not in out:
                out[s] = w["weak_syn"] if s in weak else w["syn"]
    if any(t in q_toks or (q_compact and t in q_compact) for t in rules["design_triggers"]):
        for s in rules["design_fallback"]:
            out.setdefault(s, 0.3)
    return list(out.items())


def _field_score(qw: list, field_toks: set, idx: MatchIndex) -> float:
    w = idx.rules["weights"]
    hit = tot = 0.0
    for t, f in qw:
        weight = max(idx.idf(t), w["idf_floor"]) * (w["len2_boost"] if len(t) >= 2 else 1) * f
        tot += weight
        if t in field_toks:
            hit += weight
    return hit / tot if tot else 0.0


def strip_stopwords(raw: str, rules: dict) -> str:
    s = raw
    for word in rules["stopwords"]:
        s = s.replace(word, "")
    return s


def match(index: MatchIndex, raw_query: str, top: int = 4) -> list:
    """与 dashboard JS 完全同构的打分。返回按分数降序的候选列表。"""
    rules = index.rules
    w = rules["weights"]
    q = norm(strip_stopwords(raw_query.strip(), rules)) or norm(raw_query.strip())
    q_compact = q.replace(" ", "")
    if len(q_compact) < 2:
        return []
    q_toks = tokenize(q, query=True)
    qw = expand_intent(q_toks, rules, q_compact)

    all_cats = {c for s in index.skills for c in s.get("categories", [s.get("category", "")])}
    cat_pinned = {c for c in all_cats
                  if q_compact in norm(c).replace(" ", "")
                  or any(t in norm(c).replace(" ", "") for t in q_toks)}

    results = []
    for s in index.skills:
        ns = _field_score(qw, s["_name"], index)
        ds = _field_score(qw, s["_desc"], index)
        ks = _field_score(qw, s["_kw"], index)
        score = w["name"] * ns + w["desc"] * ds + w["kw"] * ks
        if ns > 0.08 and ds > 0.08:
            score *= w["cross"]
        elif ks > 0.1 and (ns > 0.08 or ds > 0.08):
            score *= w["kw_cross"]
        if q_compact in s["_nt"]:
            score += w["name_substr"]
        elif q_compact in s["_dt"]:
            if (index.df.get(q_compact, 0) / index.n) <= w["desc_substr_max_df"]:
                score += w["desc_substr"]
        cats = set(s.get("categories", [s.get("category", "")]))
        if cats & cat_pinned:
            score += w["cat_pin"]
        if score > w["min_score"]:
            results.append({
                "name": s["name"],
                "dir_name": s["dir_name"],
                "score": round(score, 4),
                "category": s.get("category", ""),
                "categories": s.get("categories", []),
                "hosts": s["hosts"],
                "copies": s["copies"],
                "description": s["description"][:220],
                "why": {"name": round(ns, 3), "desc": round(ds, 3), "kw": round(ks, 3),
                        "cat_pinned": bool(cats & cat_pinned)},
            })
    results.sort(key=lambda x: -x["score"])
    return results[:top]


# ---------------------------------------------------------------- 黄金用例

def run_golden(index: MatchIndex, rules: dict | None = None) -> list:
    """对 rules.json 的 golden 用例逐条跑 match；期望 skill 未安装则跳过。"""
    rules = rules or index.rules
    installed = {s["dir_name"].lower() for s in index.skills} | \
                {s["name"].lower() for s in index.skills}
    report = []
    for case in rules.get("golden", []):
        expect = [e.lower() for e in case["expect_any"]]
        present = [e for e in expect if e in installed]
        if not present:
            report.append({"query": case["query"], "status": "skip",
                           "detail": "期望 skill 未安装"})
            continue
        ranked = match(index, case["query"], top=case.get("top", 4))
        got = [r["dir_name"].lower() for r in ranked] + [r["name"].lower() for r in ranked]
        ok = any(e in got for e in present)
        report.append({
            "query": case["query"],
            "status": "pass" if ok else "fail",
            "detail": f"期望 {present} 命中于 top{case.get('top', 4)}" if ok
                      else f"期望 {present}，实际 top{case.get('top', 4)}={got[:case.get('top', 4)]}",
        })
    return report
