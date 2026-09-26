"""Group videos into structural formats and give each one a name a client
can act on ("冒頭3秒クローズアップ型", not "cluster 3").

Pipeline
  1. standardise feature vectors (only videos that have a transcript)
  2. KMeans, k chosen by silhouette in a small range
  3. build a numeric profile per cluster (medians / shares)
  4. name each cluster: LLM if ANTHROPIC_API_KEY is set, else rule-based
Videos without transcripts are reported separately from metadata only.
"""
from __future__ import annotations

import json
import re
import statistics
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any

from .features import Features, OPENING_TYPES

MIN_VIDEOS_FOR_CLUSTERING = 6


@dataclass
class FormatCluster:
    cluster_id: int
    name: str
    one_line: str
    recipe: list[str]              # concrete, copyable steps (seconds / order / structure)
    size: int
    share: float
    profile: dict[str, Any]
    examples: list[dict[str, Any]]  # [{title, url, view_count, duration_sec}]
    naming_source: str = "rule"     # rule | llm

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---- clustering ------------------------------------------------------------

def _standardise(rows: list[list[float]]) -> list[list[float]]:
    import numpy as np
    X = np.asarray(rows, dtype=float)
    mu, sd = X.mean(axis=0), X.std(axis=0)
    sd[sd == 0] = 1.0
    return ((X - mu) / sd).tolist()


def cluster_features(feats: list[Features], *, k: int | None = None, max_k: int = 6, seed: int = 0) -> list[int]:
    """Return a cluster label per feature row (same order). Single cluster when too few rows."""
    n = len(feats)
    if n < MIN_VIDEOS_FOR_CLUSTERING:
        return [0] * n
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score
    X = _standardise([f.vector() for f in feats])
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

def _share(counter: Counter, total: int) -> dict[str, float]:
    return {k: round(v / total, 2) for k, v in counter.most_common()} if total else {}


def _median(xs: list[float]) -> float:
    return round(statistics.median(xs), 2) if xs else 0.0


def profile_cluster(members: list[Features]) -> dict[str, Any]:
    n = len(members)
    with_c = [m for m in members if m.conclusion_rel is not None]
    with_q = [m for m in members if m.question_rel is not None]
    with_cta = [m for m in members if m.cta_rel is not None]
    return {
        "n": n,
        "duration_median_sec": _median([m.duration_sec for m in members]),
        "duration_p25_p75": [
            _median(sorted(m.duration_sec for m in members)[: max(1, n // 2)]),
            _median(sorted(m.duration_sec for m in members)[n // 2:]),
        ],
        "speech_density_median": _median([m.speech_density for m in members]),
        "topic_shifts_median": _median([m.n_topic_shifts for m in members]),
        "opening_type": _share(Counter(m.opening_type for m in members), n),
        "opening_examples": list(dict.fromkeys(m.opening_text for m in members if m.opening_text))[:5],
        "conclusion_pos": _share(Counter(m.conclusion_pos for m in members), n),
        "conclusion_rel_median": _median([m.conclusion_rel for m in with_c]) if with_c else None,
        "question_share": round(len(with_q) / n, 2) if n else 0,
        "question_rel_median": _median([m.question_rel for m in with_q]) if with_q else None,
        "cta_share": round(len(with_cta) / n, 2) if n else 0,
        "cta_rel_median": _median([m.cta_rel for m in with_cta]) if with_cta else None,
        "title_type": _share(Counter(m.title_type for m in members), n),
        "view_median": _median([m.view_count for m in members]),
    }


def _dominant(share: dict[str, float]) -> str:
    return next(iter(share), "その他") if share else "その他"


def rule_based_name(p: dict[str, Any]) -> tuple[str, str, list[str]]:
    """Fallback naming from the profile alone. Concrete, no adjectives."""
    opening = _dominant(p["opening_type"])
    concl = _dominant(p["conclusion_pos"])
    dur = int(p["duration_median_sec"])
    name = f"冒頭{opening}・結論{concl}型" if concl != "なし" else f"冒頭{opening}・展開型"
    one_line = (f"0〜3秒で{opening}、結論は{concl}に置く、尺は約{dur}秒、"
                f"話題転換は中央値{int(p['topic_shifts_median'])}回")
    recipe = [f"0〜3秒: {opening}で入る（例: {p['opening_examples'][0]}）" if p["opening_examples"] else f"0〜3秒: {opening}で入る"]
    if p["conclusion_rel_median"] is not None:
        recipe.append(f"結論・完成の提示: 尺の{int(p['conclusion_rel_median'] * 100)}%地点（約{int(dur * p['conclusion_rel_median'])}秒）")
    if p["question_share"] >= 0.5 and p["question_rel_median"] is not None:
        recipe.append(f"問いかけ: {int(p['question_share'] * 100)}%の動画にあり、尺の{int(p['question_rel_median'] * 100)}%地点")
    if p["cta_share"] >= 0.5 and p["cta_rel_median"] is not None:
        recipe.append(f"CTA: {int(p['cta_share'] * 100)}%の動画にあり、尺の{int(p['cta_rel_median'] * 100)}%地点")
    recipe.append(f"話題転換: 中央値{int(p['topic_shifts_median'])}回、発話密度: {p['speech_density_median']}文字/秒")
    recipe.append(f"尺: 中央値{dur}秒（四分位 {int(p['duration_p25_p75'][0])}〜{int(p['duration_p25_p75'][1])}秒）")
    recipe.append(f"タイトル: {_dominant(p['title_type'])}が最多")
    return name, one_line, recipe


# ---- LLM naming ------------------------------------------------------------

LLM_SYSTEM = """あなたはショート動画の構成分析を、企業のSNS担当者向けレポートにまとめる編集者です。
与えられるのは、YouTube Shorts をクラスタリングした各グループの数値プロファイルです。
各グループに、制作者がそのまま真似できる「◯◯型」という名前を付け、構成レシピを書いてください。

厳守事項:
- 名前は「冒頭3秒クローズアップ型」「Before/After反転型」「数値訴求型」のような粒度の日本語。末尾は「型」。
- 抽象的な形容詞（インパクトのある、テンポの良い、魅力的、面白い 等）は禁止。秒数・語順・構造で書く。
- recipe は 3〜6 行。各行は「0〜3秒: 〜」「尺の40%地点で〜」のように、時間か順序を含む。
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
                    k: int | None = None) -> tuple[list[FormatCluster], list[Features], str | None]:
    """Returns (clusters, videos_without_transcript, llm_error_or_None)."""
    with_tr = [f for f in feats if f.has_transcript]
    without = [f for f in feats if not f.has_transcript]
    if not with_tr:
        return [], without, None

    labels = cluster_features(with_tr, k=k)
    groups: dict[int, list[Features]] = {}
    for f, lab in zip(with_tr, labels):
        groups.setdefault(lab, []).append(f)
    # Renumber clusters by size, largest first.
    ordered = sorted(groups.items(), key=lambda kv: -len(kv[1]))
    profiles: list[dict[str, Any]] = []
    for new_id, (_, members) in enumerate(ordered):
        p = profile_cluster(members)
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
    for new_id, (_, members) in enumerate(ordered):
        p = profiles[new_id]
        if new_id in names:
            name, one_line, recipe = names[new_id]
            src = "llm"
        else:
            name, one_line, recipe = rule_based_name(p)
            src = "rule"
        examples = sorted(members, key=lambda m: -m.view_count)[:3]
        clusters.append(FormatCluster(
            cluster_id=new_id, name=name, one_line=one_line, recipe=recipe,
            size=len(members), share=round(len(members) / total, 2), profile=p,
            examples=[{"title": m.title, "url": m.url, "view_count": m.view_count, "duration_sec": m.duration_sec}
                      for m in examples],
            naming_source=src,
        ))
    return clusters, without, llm_error
