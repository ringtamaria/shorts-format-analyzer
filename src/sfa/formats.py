"""Group videos by the human-defined format types and profile each group.

Formats are no longer discovered by clustering. Each video was already given
one type by :func:`sfa.features.extract_features` (rules order = priority),
plus two built-in groups:

  silent        no speech (no caption track, or only [Music] / [Applause])
  unclassified  speech that matched no rule; its opening texts are listed in
                full in the report, as input for new rules

Clustering survives only as a discovery tool over the unclassified videos
(:func:`discover_unclassified`), written to a separate file, never to the
delivered report.

Numbers are quartile ranges, never a lone point: with ~15 videos per group a
single median reads as a target it is not. Groups smaller than
``rules.min_type_size`` are flagged as reference-only.
"""
from __future__ import annotations

import re
import statistics
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any

from .features import Features, get_rules, position_label
from .rules import SILENT_ID, UNCLASSIFIED_ID, Rules

N_EXAMPLES = 3


@dataclass
class FormatGroup:
    format_id: str
    kind: str                      # type | silent | unclassified
    name: str
    one_line: str
    recipe: list[str]
    size: int
    share: float                   # of analysed videos
    profile: dict[str, Any]
    examples: list[dict[str, Any]]  # top N_EXAMPLES by views
    members: list[str] = field(default_factory=list)
    order: int = 0

    @property
    def small_sample(self) -> bool:
        return bool(self.profile.get("small_sample"))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---- statistics ------------------------------------------------------------

def quartiles(xs: list[float], nd: int = 2) -> dict[str, float] | None:
    """{"q1", "median", "q3", "n", "min", "max"} or None when empty."""
    xs = [float(x) for x in xs]
    if not xs:
        return None
    if len(xs) == 1:
        v = round(xs[0], nd)
        return {"q1": v, "median": v, "q3": v, "n": 1, "min": v, "max": v}
    q1, med, q3 = statistics.quantiles(xs, n=4, method="inclusive")
    return {"q1": round(q1, nd), "median": round(med, nd), "q3": round(q3, nd), "n": len(xs),
            "min": round(min(xs), nd), "max": round(max(xs), nd)}


def _share(counter: Counter, total: int) -> dict[str, float]:
    return {k: round(v / total, 2) for k, v in counter.most_common()} if total else {}


def _presence(members: list[Features], attr: str, rules: Rules) -> dict[str, Any]:
    vals = [getattr(m, attr) for m in members if getattr(m, attr) is not None]
    q = quartiles(vals, 3)
    return {
        "share": round(len(vals) / len(members), 2) if members else 0.0,
        "pos": q,
        "label": position_label(q["median"], rules) if q else "なし",
    }


def profile_group(members: list[Features], rules: Rules | None = None) -> dict[str, Any]:
    r = rules or get_rules()
    n = len(members)
    spoken = [m for m in members if m.speech == "speech"]
    return {
        "n": n,
        "n_spoken": len(spoken),
        "small_sample": n < r.min_type_size,
        "duration": quartiles([m.duration_sec for m in members], 0),
        "speech_density": quartiles([m.speech_density for m in spoken], 1),
        "opening_examples": list(dict.fromkeys(m.opening_text for m in spoken if m.opening_text))[:5],
        "completion": _presence(spoken, "completion_rel", r),
        "question": _presence(spoken, "question_rel", r),
        "cta": _presence(spoken, "cta_rel", r),
        "bulk_input_share": round(sum(m.bulk_input for m in spoken) / len(spoken), 2) if spoken else 0.0,
        "brand_share": round(sum(m.brand is not None for m in members) / n, 2) if n else 0.0,
        "brands": dict(Counter(m.brand for m in members if m.brand).most_common()),
        "title_type": _share(Counter(m.title_type for m in members), n),
        "speech_kinds": dict(Counter(m.speech for m in members)),
        "view_median": quartiles([m.view_count for m in members], 0),
    }


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
    """Rate FIRST, then where: '41%の動画にあり、尺の後半（59〜77%地点）'."""
    rate = f"{int(round(p['share'] * 100))}%の動画にあり"
    q = p.get("pos")
    if not q:
        return rate
    pct = lambda x: f"{int(round(x * 100))}"  # noqa: E731
    if small:
        where = f"{p['label']}（{pct(q['median'])}%地点、参考値）"
    elif pct(q["q1"]) == pct(q["q3"]):
        where = f"{p['label']}（{pct(q['median'])}%地点）"
    else:
        where = f"{p['label']}（{pct(q['q1'])}〜{pct(q['q3'])}%地点）"
    return f"{rate}、尺の{where}"


def _all_same(q: dict[str, float] | None) -> bool:
    return q is None or q.get("min") == q.get("max")


def group_one_line(base: str, p: dict[str, Any]) -> str:
    """Rule one-liner + the two numbers a creator can act on: length and speaking speed."""
    small = bool(p.get("small_sample"))
    bits = [base] if base else []
    if p["duration"]:
        bits.append(f"尺 {fmt_range(p['duration'], '秒', small=small)}")
    if p["speech_density"]:
        bits.append(f"発話 {fmt_range(p['speech_density'], '文字/秒', small=small, nd=1)}")
    return "。".join(bits)


def group_recipe(p: dict[str, Any], rules: Rules, *, opening_line: str | None) -> list[str]:
    """Steps a creator can follow. Lines with no information (every video identical) are dropped."""
    small = bool(p.get("small_sample"))
    out: list[str] = []
    if opening_line:
        ex = p["opening_examples"][0] if p["opening_examples"] else None
        out.append(f"0〜{int(rules.opening_window_sec)}秒: {opening_line}" + (f"（例: 「{ex}」）" if ex else ""))
    for key, label in (("completion", "完成の提示"), ("question", "問いかけ"), ("cta", "CTA")):
        pr = p[key]
        if pr["share"] >= rules.presence_share and pr["pos"]:
            out.append(f"{label}: {fmt_position(pr, small=small)}")
    if p["bulk_input_share"] >= rules.presence_share:
        out.append(f"大量投入: {int(p['bulk_input_share'] * 100)}%の動画にあり")
    if p["speech_density"] and not _all_same(p["speech_density"]):
        out.append(f"発話密度: {fmt_range(p['speech_density'], '文字/秒', small=small, nd=1)}")
    if p["duration"] and not _all_same(p["duration"]):
        out.append(f"尺: {fmt_range(p['duration'], '秒', small=small)}")
    if p["title_type"] and len(p["title_type"]) > 0:
        top, share = next(iter(p["title_type"].items()))
        out.append(f"タイトル: {top}が{int(share * 100)}%")
    return out


# ---- grouping ----------------------------------------------------------------

def classify_formats(feats: list[Features], rules: Rules | None = None) -> list[FormatGroup]:
    """Groups in rules order, then the silent type, then unclassified. Empty groups are omitted."""
    r = rules or get_rules()
    analysed = [f for f in feats if f.speech in ("speech", "silent", "no_transcript")]
    total = len(analysed)
    order = [(t.id, "type") for t in r.opening_types] + [(SILENT_ID, "silent"), (UNCLASSIFIED_ID, "unclassified")]
    groups: list[FormatGroup] = []
    for idx, (fid, kind) in enumerate(order):
        members = [f for f in analysed if f.format_id == fid]
        if not members:
            continue
        p = profile_group(members, r)
        if kind == "type":
            one = r.type_one_line(fid)
            recipe = group_recipe(p, r, opening_line=one)
        elif kind == "silent":
            one = r.silent_one_line
            recipe = group_recipe(p, r, opening_line=None)
        else:
            one = "どの型のルールにも当たらなかった動画"
            recipe = []
        groups.append(FormatGroup(
            format_id=fid, kind=kind, name=r.type_name(fid), one_line=group_one_line(one, p), recipe=recipe,
            size=len(members), share=round(len(members) / total, 2) if total else 0.0, profile=p,
            examples=[{"title": m.title, "url": m.url, "view_count": m.view_count, "duration_sec": m.duration_sec,
                       "thumbnail_url": m.thumbnail_url}
                      for m in sorted(members, key=lambda m: -m.view_count)[:N_EXAMPLES]],
            members=[m.video_id for m in members], order=idx,
        ))
    return groups


# ---- discovery (unclassified only) ------------------------------------------

def _standardise(rows: list[list[float]]) -> list[list[float]]:
    import numpy as np
    X = np.asarray(rows, dtype=float)
    mu, sd = X.mean(axis=0), X.std(axis=0)
    sd[sd == 0] = 1.0
    return ((X - mu) / sd).tolist()


def cluster_features(feats: list[Features], *, k: int | None = None, max_k: int = 6, seed: int = 0,
                     min_size: int = 2) -> list[int]:
    """KMeans labels for discovery. Single cluster when there is too little data."""
    n = len(feats)
    max_k = min(max_k, n // max(1, min_size))
    if max_k < 2:
        return [0] * n
    import warnings
    from sklearn.cluster import KMeans
    from sklearn.exceptions import ConvergenceWarning
    from sklearn.metrics import silhouette_score
    warnings.filterwarnings("ignore", category=ConvergenceWarning)  # duplicate points in tiny discovery sets
    X = _standardise([f.vector() for f in feats])
    if k is None:
        best_k, best_s = 2, -1.0
        for kk in range(2, max_k + 1):
            labels = KMeans(n_clusters=kk, n_init=10, random_state=seed).fit_predict(X)
            if len(set(labels)) < 2:
                continue
            s = silhouette_score(X, labels)
            if s > best_s:
                best_k, best_s = kk, s
        k = best_k
    return [int(x) for x in KMeans(n_clusters=max(1, min(k, n)), n_init=10, random_state=seed).fit_predict(X)]


_RE_KANA_WORD = re.compile(r"[ぁ-んァ-ヶー一-龠々]{2,12}")


def common_phrases(texts: list[str], top: int = 10) -> list[tuple[str, int]]:
    """Character n-grams (2-12) that appear in at least two of the texts, longest-first among ties."""
    counts: Counter[str] = Counter()
    for t in texts:
        grams: set[str] = set()
        s = re.sub(r"\s+", "", t)
        for n in range(2, 13):
            grams.update(s[i:i + n] for i in range(len(s) - n + 1))
        counts.update(g for g in grams if _RE_KANA_WORD.fullmatch(g))
    hits = [(g, c) for g, c in counts.items() if c >= 2]
    # drop n-grams fully contained in a longer n-gram with the same count
    hits.sort(key=lambda gc: (-gc[1], -len(gc[0])))
    kept: list[tuple[str, int]] = []
    for g, c in hits:
        if any(g in k and c == kc for k, kc in kept):
            continue
        kept.append((g, c))
        if len(kept) >= top:
            break
    return kept


def discover_unclassified(feats: list[Features], rules: Rules | None = None) -> list[dict[str, Any]] | None:
    """Clusters of unclassified videos, or None when there are fewer than the threshold."""
    r = rules or get_rules()
    un = [f for f in feats if f.format_id == UNCLASSIFIED_ID and f.speech == "speech"]
    if len(un) < r.discovery_min_unclassified:
        return None
    labels = cluster_features(un)
    out = []
    for lab in sorted(set(labels), key=lambda l: -labels.count(l)):
        members = [f for f, l in zip(un, labels) if l == lab]
        out.append({
            "size": len(members),
            "openings": [{"text": m.opening_text, "title": m.title, "url": m.url} for m in members],
            "common_phrases": common_phrases([m.opening_text for m in members]),
            "profile": profile_group(members, r),
        })
    return out
