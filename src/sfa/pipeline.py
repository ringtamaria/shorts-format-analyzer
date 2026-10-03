"""Shared analysis step: cached data -> features -> format groups -> files.

Both scripts end here:
  build_report.py  after collecting metadata and fetching transcripts
  reclassify.py    straight from the cache, after editing config/rules.yaml

This module never talks to an API. Transcripts are read from the SQLite cache
only, so re-running with new rules costs nothing and is reproducible.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

from .features import Features, extract_features, get_rules
from .formats import FormatGroup, classify_formats, discover_unclassified
from .report import ReportMeta, render_discovery, render_report
from .rules import UNCLASSIFIED_ID, Rules
from .store import Store
from .transcript import Transcript
from .youtube import Video


@dataclass
class Analysis:
    feats: list[Features]
    groups: list[FormatGroup]
    other_lang: list[str]
    not_fetched: list[str]
    discovery: list[dict[str, Any]] | None


def cached_transcript(store: Store, video_id: str) -> tuple[str, Transcript | None, str]:
    """(status, transcript, backend) from the cache. status: ok | unavailable | missing."""
    row = store.get_transcript_row(video_id)
    if row is None or row["status"] not in ("ok", "unavailable"):
        return "missing", None, ""
    if row["status"] == "unavailable" or not row["segments"]:
        return "unavailable", None, row["backend"] or ""
    tr = Transcript.from_rows(video_id, row["language"] or "", json.loads(row["segments"]), row["backend"])
    return "ok", tr, row["backend"] or ""


def analyse(videos: list[Video], store: Store, *, lang: str = "ja", rules: Rules | None = None) -> Analysis:
    r = rules or get_rules()
    feats: list[Features] = []
    other_lang: list[str] = []
    not_fetched: list[str] = []
    for v in videos:
        status, tr, backend = cached_transcript(store, v.video_id)
        if status == "missing":
            not_fetched.append(v.video_id)
            feats.append(extract_features(v, None, r, speech="not_fetched", lang=lang, source=""))
            continue
        f = extract_features(v, tr, r, lang=lang, source=backend)
        if f.speech == "other_lang":
            other_lang.append(v.video_id)
        feats.append(f)
    groups = classify_formats(feats, r)
    discovery = discover_unclassified(feats, r)
    return Analysis(feats, groups, other_lang, not_fetched, discovery)


def write_outputs(an: Analysis, meta: ReportMeta, out: Path, *, video_ids: list[str],
                  run_info: dict[str, Any]) -> dict[str, Path]:
    """Report, features JSON (enough to reclassify later) and the discovery file."""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_report(meta, an.feats, an.groups), encoding="utf-8")
    fj = out.with_suffix(".features.json")
    fj.write_text(json.dumps({
        "video_ids": video_ids,
        "run": run_info,
        "features": [f.to_dict() for f in an.feats],
        "groups": [g.to_dict() for g in an.groups],
        "other_lang": an.other_lang,
        "not_fetched": an.not_fetched,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    paths = {"report": out, "features": fj}
    n_un = sum(f.format_id == UNCLASSIFIED_ID and f.speech == "speech" for f in an.feats)
    if an.discovery is not None:
        stem = out.name.replace("report_", "unclassified_", 1)
        dp = out.with_name(stem)
        dp.write_text(render_discovery(meta.genre, meta.report_date, an.discovery, n_un), encoding="utf-8")
        paths["discovery"] = dp
    return paths


def _rel(p: Path | None) -> str:
    if p is None:
        return ""
    try:
        return str(p.resolve().relative_to(Path(__file__).resolve().parents[2]))
    except ValueError:
        return str(p)


def meta_from_run(run: dict[str, Any], an: Analysis, *, quota_used: int, quota_budget: int,
                  rules: Rules, report_date: date, reclassified: bool) -> ReportMeta:
    return ReportMeta(
        genre=run["genre"], report_date=report_date, n_requested=int(run.get("n_requested", len(an.feats))),
        n_collected=len(an.feats), transcript_backend=run.get("transcript_backend", ""),
        quota_used=quota_used, quota_budget=quota_budget,
        n_other_lang=len(an.other_lang), n_not_fetched=len(an.not_fetched),
        transcript_lang=run.get("transcript_lang", "ja"), collection_route=run.get("collection_route", "search"),
        partial=bool(run.get("partial")), credits_exhausted=bool(run.get("credits_exhausted")),
        transcripts_aborted=run.get("transcripts_aborted"), transcripts_capped=bool(run.get("transcripts_capped")),
        rules_source=_rel(rules.source),
        reclassified_from_cache=reclassified, notes=list(run.get("notes") or []),
    )
