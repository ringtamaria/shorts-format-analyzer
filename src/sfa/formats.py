"""Group videos into structural formats and give each one a name a client
can act on ("冒頭3秒クローズアップ型", not "cluster 3").

Pipeline
  1. standardise feature vectors (only videos that have a transcript)
  2. KMeans, k chosen by silhouette in a small range
  3. build a numeric profile per cluster: quartile ranges (Q1-median-Q3), shares
  4. name each cluster: LLM if available, else rule-based
Videos without transcripts are reported separately from metadata only.

Numbers are reported as ranges, never as a single point: with ~15 videos per
cluster a lone median reads as a target it is not. Clusters smaller than
``rules.min_cluster_for_ranges`` are flagged as reference-only.
"""
from __future__ import annotations

import json
import re
import statistics
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any

from .features import Features, get_rules, position_label
from .rules import Rules

MIN_VIDEOS_FOR_CLUSTERING = 6
N_EXAMPLES = 3


@dataclass
class FormatCluster:
    cluster_id: int
    name: str
    one_line: str
    recipe: list[str]              # concrete, copyable steps (seconds / order / structure)
    size: int
    share: float
    profile: dict[str, Any]
    examples: list[dict[str, Any]]  # top N_EXAMPLES by views: [{title, url, view_count, duration_sec}]
    naming_source: str = "rule"     # rule | llm

    @property
    def small_sample(self) -> bool:
        return bool(self.profile.get("small_sample"))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---- clustering ------------------------------------------------------------

def _standardise(rows: list[list[float]]) -> list[list[float]]:
    import numpy as np
    X = np.asarray(rows, dtype=float)
    mu, sd = X.mean(axis=0), X.std(axis=0)
    sd[sd == 0] = 1.0
    return ((X - mu) / sd).tolist()


def cluster_features(feats: list[Features], *, k: int | None = None, max_k: int = 6, seed: int = 0,
                     rules: Rules | None = None) -> list[int]:
    """Return a cluster label per feature row (same order). Single cluster when too few rows."""
    n = len(feats)
    if n < MIN_VIDEOS_FOR_CLUSTERING:
        return [0] * n
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score
    X = _standardise([f.vector(rules) for f in feats])
    if k is None:
        best_k, best_s = 2, -1.0
        for kk in range(2, min(max_k, n // 3) + 1):
            labels = KMeans(n_clusters=kk, n_init=10, random_state=seed).fit_predict(X)
            if len(set(labels)) < 2:
                continue
            s = silhouette_score(X, labels)
            if s > best_s:
                best_k, best_s = kk, s
        k = best_k
    return [int(x) for x in KMeans(n_clusters=k, n_init=10, random_state=seed).fit_predict(X)]


# ---- profiling -------------------------------------------------------------

def quartiles(xs: list[float], nd: int = 2) -> dict[str, float] | None:
    """{"q1", "median", "q3", "n"} or None when empty."""
    xs = [float(x) for x in xs]
    if not xs:
        return None
    if len(xs) == 1:
        v = round(xs[0], nd)
        return {"q1": v, "median": v, "q3": v, "n": 1}
    q1, med, q3 = statistics.quantiles(xs, n=4, method="inclusive")
    return {"q1": round(q1, nd), "median": round(med, nd), "q3": round(q3, nd), "n": len(xs)}


def _share(counter: Counter, total: int) -> dict[str, float]:
    return {k: round(v / total, 2) for k, v in counter.most_common()} if total else {}


def _presence(members: list[Features], attr: str) -> dict[str, Any]:
    vals = [getattr(m, attr) for m in members if getattr(m, attr) is not None]
    q = quartiles(vals, 3)
    return {
        "share": round(len(vals) / len(members), 2) if members else 0.0,
        "pos": q,
        "label": position_label(q["median"]) if q else "なし",
    }


def profile_cluster(members: list[Features], rules: Rules | None = None) -> dict[str, Any]:
    r = rules or get_rules()
    n = len(members)
    return {
        "n": n,
        "small_sample": n < r.min_cluster_for_ranges,
        "duration": quartiles([m.duration_sec for m in members], 0),
        "speech_density": quartiles([m.speech_density for m in members], 1),
        "topic_shifts": quartiles([m.n_topic_shifts for m in members], 0),
        "opening_type": _share(Counter(m.opening_type for m in members), n),
        "opening_examples": list(dict.fromkeys(m.opening_text for m in members if m.opening_text))[:5],
        "conclusion_pos": _share(Counter(m.conclusion_pos for m in members), n),
        "conclusion": _presence(members, "conclusion_rel"),
        "question": _presence(members, "question_rel"),
        "cta": _presence(members, "cta_rel"),
        "title_type": _share(Counter(m.title_type for m in members), n),
        "view_median": quartiles([m.view_count for m in members], 0),
    }


def _dominant(share: dict[str, float]) -> str:
    return next(iter(share), "その他") if share else "その他"


# ---- text helpers shared with report.py ------------------------------------

def fmt_range(q: dict[str, float] | None, unit: str = "", *, small: bool = False, nd: int = 0) -> str:
    """'20〜34秒（中央値27秒）'; small samples show the median only, marked as reference."""
    if q is None:
        return "なし"
    f = (lambda x: f"{x:.{nd}f}")
    if small:
        return f"中央値{f(q['median'])}{unit}（参考値）"
    if f(q["q1"]) == f(q["q3"]):
        return f"{f(q['median'])}{unit}（全本ほぼ同値）"
    return f"{f(q['q1'])}〜{f(q['q3'])}{unit}（中央値{f(q['median'])}{unit}）"


def fmt_position(p: dict[str, Any], *, small: bool = False) -> str:
    """'後半（65〜80%地点）' from a presence dict; '' when absent."""
    q = p.get("pos")
    if not q:
        return "なし"
    pct = lambda x: f"{int(round(x * 100))}"  # noqa: E731
    if small:
        return f"{p['label']}（{pct(q['median'])}%地点、参考値）"
    if pct(q["q1"]) == pct(q["q3"]):
        return f"{p['label']}（{pct(q['median'])}%地点）"
    return f"{p['label']}（{pct(q['q1'])}〜{pct(q['q3'])}%地点）"


def rule_based_name(p: dict[str, Any], rules: Rules | None = None) -> tuple[str, str, list[str]]:
    """Fallback naming from the profile alone. Concrete, ranges, no adjectives."""
    r = rules or get_rules()
    small = bool(p.get("small_sample"))
    opening = _dominant(p["opening_type"])
    concl_label = p["conclusion"]["label"]
    dur = p["duration"]
    name = f"冒頭{opening}・結論{concl_label}型" if concl_label != "なし" else f"冒頭{opening}・展開型"
    one_line = (f"0〜{int(r.opening_window_sec)}秒で{opening}、結論は{fmt_position(p['conclusion'], small=small)}、"
                f"尺は{fmt_range(dur, '秒', small=small)}")
    ex = p["opening_examples"][0] if p["opening_examples"] else None
    recipe = [f"0〜{int(r.opening_window_sec)}秒: {opening}で入る" + (f"（例: {ex}）" if ex else "")]
    if p["conclusion"]["pos"]:
        recipe.append(f"結論・完成の提示: 尺の{fmt_position(p['conclusion'], small=small)}")
    for key, label in (("question", "問いかけ"), ("cta", "CTA")):
        if p[key]["share"] >= r.presence_share and p[key]["pos"]:
            recipe.append(f"{label}: {int(p[key]['share'] * 100)}%の動画にあり、尺の{fmt_position(p[key], small=small)}")
    recipe.append(f"話題転換: {fmt_range(p['topic_shifts'], '回', small=small)}、"
                  f"発話密度: {fmt_range(p['speech_density'], '文字/秒', small=small, nd=1)}")
    recipe.append(f"尺: {fmt_range(dur, '秒', small=small)}")
    recipe.append(f"タイトル: {_dominant(p['title_type'])}が最多")
    return name, one_line, recipe


# ---- LLM naming ------------------------------------------------------------

LLM_SYSTEM = """あなたはショート動画の構成分析を、企業のSNS担当者向けレポートにまとめる編集者です。
与えられるのは、YouTube Shorts をクラスタリングした各グループの数値プロファイルです。
各グループに、制作者がそのまま真似できる「◯◯型」という名前を付け、構成レシピを書いてください。

厳守事項:
- 名前は「冒頭3秒クローズアップ型」「Before/After反転型」「数値訴求型」のような粒度の日本語。末尾は「型」。
- 抽象的な形容詞（インパクトのある、テンポの良い、魅力的、面白い 等）は禁止。秒数・語順・構造で書く。
- 数値は必ず範囲で書く。プロファイルの q1〜q3 を「20〜34秒」「尺の65〜80%地点」の形で使い、中央値を単独の目標値として書かない。
- 位置は 前半（0〜33%）／中盤（34〜66%）／後半（67〜100%）の区分名に範囲を添える。例: 「後半（65〜80%地点）」
- small_sample が true のグループは、範囲を出さず「n=◯ のため傾向の参考値」と one_line の冒頭に書く。
- recipe は 3〜6 行。各行は「0〜3秒: 〜」「尺の40〜55%地点で〜」のように、時間か順序を含む。
- プロファイルにない事実を作らない。数値はプロファイルの値をそのまま使う。
- 出力は JSON のみ。説明文やコードフェンスを付けない。

出力形式:
{"clusters": [{"cluster_id": 0, "name": "…型", "one_line": "…", "recipe": ["…", "…"]}, …]}
"""


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1])


def llm_name_clusters(profiles: list[dict[str, Any]], *, model: str, api_key: str | None = None,
                      genre: str = "") -> dict[int, tuple[str, str, list[str]]]:
    """Ask Claude to name the clusters. Raises on failure; callers fall back."""
    import anthropic

    client = anthropic.Anthropic(api_key=api_key or None)
    payload = json.dumps({"genre": genre, "clusters": profiles}, ensure_ascii=False, indent=1)
    kwargs: dict[str, Any] = dict(
        model=model,
        max_tokens=8000,
        system=LLM_SYSTEM,
        messages=[{"role": "user", "content": f"以下のクラスタに名前とレシピを付けてください。\n\n{payload}"}],
    )
    try:
        # Server-side refusal fallback: if the safety layer declines, the API re-runs on a fallback model.
        resp = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
    except (anthropic.BadRequestError, TypeError):
        resp = client.messages.create(**kwargs)
    if resp.stop_reason == "refusal":
        raise RuntimeError("LLM refused the naming request")
    text = "".join(b.text for b in resp.content if b.type == "text")
    data = _extract_json(text)
    out: dict[int, tuple[str, str, list[str]]] = {}
    for c in data.get("clusters", []):
        cid = int(c["cluster_id"])
        name = str(c.get("name", "")).strip()
        if not name:
            continue
        out[cid] = (name, str(c.get("one_line", "")).strip(), [str(r) for r in c.get("recipe", [])])
    return out


# ---- entry point -----------------------------------------------------------

def extract_formats(feats: list[Features], *, genre: str = "", llm_model: str | None = None,
                    anthropic_api_key: str | None = None, use_llm: bool = True,
                    k: int | None = None, rules: Rules | None = None
                    ) -> tuple[list[FormatCluster], list[Features], str | None]:
    """Returns (clusters, videos_without_transcript, llm_error_or_None)."""
    r = rules or get_rules()
    with_tr = [f for f in feats if f.has_transcript]
    without = [f for f in feats if not f.has_transcript]
    if not with_tr:
        return [], without, None

    labels = cluster_features(with_tr, k=k, rules=r)
    groups: dict[int, list[Features]] = {}
    for f, lab in zip(with_tr, labels):
        groups.setdefault(lab, []).append(f)
    ordered = sorted(groups.items(), key=lambda kv: -len(kv[1]))  # largest first
    profiles: list[dict[str, Any]] = []
    for new_id, (_, members) in enumerate(ordered):
        p = profile_cluster(members, r)
        p["cluster_id"] = new_id
        profiles.append(p)

    names: dict[int, tuple[str, str, list[str]]] = {}
    llm_error: str | None = None
    if use_llm and llm_model:
        try:
            names = llm_name_clusters(profiles, model=llm_model, api_key=anthropic_api_key, genre=genre)
        except Exception as e:  # noqa: BLE001 - never block the report on the LLM
            llm_error = f"{type(e).__name__}: {e}"

    clusters: list[FormatCluster] = []
    total = len(with_tr)
    resolved: list[tuple[str, str, list[str], str]] = []
    for new_id in range(len(ordered)):
        p = profiles[new_id]
        if new_id in names:
            resolved.append((*names[new_id], "llm"))
        else:
            resolved.append((*rule_based_name(p, r), "rule"))
    # Rule-based names can collide (same opening, same conclusion band); tell them apart by length.
    counts = Counter(n for n, _, _, _ in resolved)
    for new_id, (name, one_line, recipe, src) in enumerate(resolved):
        if counts[name] > 1 and src == "rule" and profiles[new_id]["duration"]:
            resolved[new_id] = (f"{name}（{int(profiles[new_id]['duration']['median'])}秒前後）", one_line, recipe, src)
    for new_id, (_, members) in enumerate(ordered):
        p = profiles[new_id]
        name, one_line, recipe, src = resolved[new_id]
        examples = sorted(members, key=lambda m: -m.view_count)[:N_EXAMPLES]
        clusters.append(FormatCluster(
            cluster_id=new_id, name=name, one_line=one_line, recipe=recipe,
            size=len(members), share=round(len(members) / total, 2), profile=p,
            examples=[{"title": m.title, "url": m.url, "view_count": m.view_count, "duration_sec": m.duration_sec}
                      for m in examples],
            naming_source=src,
        ))
    return clusters, without, llm_error
