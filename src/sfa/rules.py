"""Loader for the classification rules.

Rules (regexes, brand lists, CTA verbs, thresholds) are operational know-how
and live outside the code:

  config/rules.yaml          real rules, git-ignored
  config/rules.example.yaml  minimal generic set, committed

``load_rules()`` prefers rules.yaml and falls back to the example, so a fresh
clone works and the tests never depend on the private file.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .config import ROOT

RULES_PATH = ROOT / "config" / "rules.yaml"
EXAMPLE_PATH = ROOT / "config" / "rules.example.yaml"

OTHER = "その他"


def _alt(words: list[str]) -> str:
    return "|".join(re.escape(w) for w in words if w)


@dataclass
class Rules:
    opening_order: list[str]
    opening_patterns: dict[str, re.Pattern[str]]
    opening_fallback_conclusion: re.Pattern[str] | None
    title_order: list[str]
    title_patterns: dict[str, re.Pattern[str]]
    conclusion: re.Pattern[str]
    question: re.Pattern[str]
    cta: re.Pattern[str]
    opening_window_sec: float = 3.0
    topic_shift_window_sec: float = 5.0
    topic_shift_jaccard: float = 0.12
    presence_share: float = 0.5
    min_cluster_for_ranges: int = 5
    source: Path | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def opening_types(self) -> list[str]:
        return [*self.opening_order, OTHER] if OTHER not in self.opening_order else list(self.opening_order)

    @property
    def title_types(self) -> list[str]:
        return [*self.title_order, OTHER] if OTHER not in self.title_order else list(self.title_order)


def build_rules(data: dict[str, Any], source: Path | None = None) -> Rules:
    op = data.get("opening", {}) or {}
    patterns = dict(op.get("patterns", {}) or {})
    brands = list(data.get("brands", []) or [])
    extra = data.get("product_extra") or ""
    product_parts = [p for p in (extra, _alt(brands)) if p]
    if product_parts and "商品名" not in patterns:
        patterns["商品名"] = "(?:" + "|".join(product_parts) + ")"
    order = [t for t in (op.get("order") or list(patterns)) if t in patterns]
    ti = data.get("title", {}) or {}
    tpat = dict(ti.get("patterns", {}) or {})
    torder = [t for t in (ti.get("order") or list(tpat)) if t in tpat]
    th = data.get("thresholds", {}) or {}
    fallback = op.get("fallback_conclusion")
    question_src = patterns.get("問いかけ") or tpat.get("疑問型") or "[？?]"
    return Rules(
        opening_order=order,
        opening_patterns={k: re.compile(v) for k, v in patterns.items()},
        opening_fallback_conclusion=re.compile(fallback) if fallback else None,
        title_order=torder,
        title_patterns={k: re.compile(v) for k, v in tpat.items()},
        conclusion=re.compile("(?:" + _alt(list(data.get("conclusion_markers", []) or ["結論"])) + ")"),
        question=re.compile(question_src),
        cta=re.compile("(?:" + _alt(list(data.get("cta_verbs", []) or ["フォロー"])) + ")"),
        opening_window_sec=float(th.get("opening_window_sec", 3.0)),
        topic_shift_window_sec=float(th.get("topic_shift_window_sec", 5.0)),
        topic_shift_jaccard=float(th.get("topic_shift_jaccard", 0.12)),
        presence_share=float(th.get("presence_share", 0.5)),
        min_cluster_for_ranges=int(th.get("min_cluster_for_ranges", 5)),
        source=source,
        raw=data,
    )


def load_rules(path: Path | str | None = None) -> Rules:
    """Load rules.yaml if present, else rules.example.yaml. ``path`` forces a file."""
    p = Path(path) if path else (RULES_PATH if RULES_PATH.exists() else EXAMPLE_PATH)
    with open(p, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return build_rules(data, source=p)
