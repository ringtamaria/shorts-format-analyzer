"""Loader for the classification rules (format taxonomy + feature markers).

  config/rules.yaml          real rules, git-ignored, written by the operator
  config/rules.example.yaml  minimal structure, committed

``load_rules()`` prefers rules.yaml and falls back to the example, so a fresh
clone works and the tests never depend on the private file.

Opening types are a human-defined taxonomy. Each video gets at most one
type: the FIRST entry in ``opening_types`` whose ``match`` is satisfied by
the opening text. Order is priority.

``match`` grammar (strings are Python regular expressions):
    match: "やばい"                       # one pattern
    match: ["やばい", "禁断"]              # list = any
    match: {any: [...]}                   # at least one
    match: {all: [...]}                   # every one
    match: {not: ...}                     # negation
    # any / all / not nest freely, e.g.
    match: {all: [{any: ["やばい", "禁断"]}, {any: ["紹介します", "作ります"]}]}
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

from .config import ROOT

RULES_PATH = ROOT / "config" / "rules.yaml"
EXAMPLE_PATH = ROOT / "config" / "rules.example.yaml"

OTHER = "その他"
SILENT_ID = "silent"
UNCLASSIFIED_ID = "unclassified"

Matcher = Callable[[str], bool]


def compile_match(spec: Any, where: str = "match") -> Matcher:
    """Compile the any/all/not grammar into a predicate over a text."""
    if isinstance(spec, str):
        try:
            rx = re.compile(spec)
        except re.error as e:
            raise ValueError(f"{where}: invalid regular expression {spec!r}: {e}") from e
        return lambda t: bool(rx.search(t))
    if isinstance(spec, list):
        subs = [compile_match(s, f"{where}[{i}]") for i, s in enumerate(spec)]
        return lambda t: any(f(t) for f in subs)
    if isinstance(spec, dict):
        unknown = set(spec) - {"any", "all", "not"}
        if unknown or not spec:
            raise ValueError(f"{where}: use 'any', 'all' or 'not' (got {sorted(spec) or 'empty'})")
        parts: list[Matcher] = []
        if "any" in spec:
            parts.append(compile_match(list(spec["any"]), f"{where}.any"))
        if "all" in spec:
            subs = [compile_match(s, f"{where}.all[{i}]") for i, s in enumerate(spec["all"])]
            parts.append(lambda t, subs=subs: all(f(t) for f in subs))
        if "not" in spec:
            inner = compile_match(spec["not"], f"{where}.not")
            parts.append(lambda t, inner=inner: not inner(t))
        return lambda t: all(p(t) for p in parts)
    raise ValueError(f"{where}: expected a string, list or mapping, got {type(spec).__name__}")


def _alt(words: list[str]) -> re.Pattern[str] | None:
    """Alternation of regexes (each item is a regex)."""
    words = [w for w in words if w]
    return re.compile("|".join(f"(?:{w})" for w in words)) if words else None


def _literal_alt(words: list[str]) -> re.Pattern[str] | None:
    words = [w for w in words if w]
    return re.compile("|".join(re.escape(w) for w in words)) if words else None


@dataclass
class OpeningType:
    id: str
    name: str
    one_line: str
    matcher: Matcher

    def matches(self, text: str) -> bool:
        return bool(text) and self.matcher(text)


@dataclass
class Rules:
    opening_types: list[OpeningType]
    silent_name: str = "無音・テロップ型"
    silent_one_line: str = "発話がなく、BGM とテロップで見せる"
    silent_max_chars: int = 10
    completion: re.Pattern[str] | None = None
    bulk_input: re.Pattern[str] | None = None
    brands: list[str] = field(default_factory=list)
    brand_pattern: re.Pattern[str] | None = None
    question: re.Pattern[str] | None = None
    cta: re.Pattern[str] | None = None
    title_order: list[str] = field(default_factory=list)
    title_patterns: dict[str, re.Pattern[str]] = field(default_factory=dict)
    opening_window_sec: float = 3.0
    presence_share: float = 0.5
    min_type_size: int = 5
    position_bands: dict[str, tuple[float, float]] = field(
        default_factory=lambda: {"前半": (0, 33), "中盤": (34, 66), "後半": (67, 100)})
    topic_shift_window_sec: float = 5.0
    topic_shift_jaccard: float = 0.12
    discovery_min_unclassified: int = 5
    source: Path | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    # back-compat name used by report code
    @property
    def min_cluster_for_ranges(self) -> int:
        return self.min_type_size

    @property
    def title_types(self) -> list[str]:
        return [*self.title_order, OTHER]

    def type_name(self, type_id: str) -> str:
        if type_id == SILENT_ID:
            return self.silent_name
        if type_id == UNCLASSIFIED_ID:
            return "未分類"
        return next((t.name for t in self.opening_types if t.id == type_id), type_id)

    def type_one_line(self, type_id: str) -> str:
        if type_id == SILENT_ID:
            return self.silent_one_line
        return next((t.one_line for t in self.opening_types if t.id == type_id), "")


_BAND_ALIASES = {"front": "前半", "mid": "中盤", "back": "後半"}


def build_rules(data: dict[str, Any], source: Path | None = None) -> Rules:
    types: list[OpeningType] = []
    seen: set[str] = set()
    for i, t in enumerate(data.get("opening_types", []) or []):
        where = f"opening_types[{i}]"
        if not isinstance(t, dict) or "id" not in t or "match" not in t:
            raise ValueError(f"{where}: each type needs at least 'id' and 'match'")
        tid = str(t["id"])
        if tid in seen or tid in (SILENT_ID, UNCLASSIFIED_ID):
            raise ValueError(f"{where}: duplicate or reserved id {tid!r}")
        seen.add(tid)
        types.append(OpeningType(tid, str(t.get("name", tid)), str(t.get("one_line", "")),
                                 compile_match(t["match"], f"{where}.match")))
    silent = data.get("silent_type", {}) or {}
    ti = data.get("title", {}) or {}
    tpat = {k: re.compile(v) for k, v in (ti.get("patterns", {}) or {}).items()}
    torder = [t for t in (ti.get("order") or list(tpat)) if t in tpat]
    th = data.get("thresholds", {}) or {}
    bands_raw = th.get("position_bands") or {}
    bands = {_BAND_ALIASES.get(k, k): (float(v[0]), float(v[1])) for k, v in bands_raw.items()} or None
    brands = [str(b) for b in (data.get("brands", []) or [])]
    question = data.get("question_pattern")
    r = Rules(
        opening_types=types,
        silent_name=str(silent.get("name", "無音・テロップ型")),
        silent_one_line=str(silent.get("one_line", "発話がなく、BGM とテロップで見せる")),
        silent_max_chars=int(silent.get("max_speech_chars", 10)),
        completion=_alt(list(data.get("completion_markers", []) or [])),
        bulk_input=_alt(list(data.get("bulk_input", []) or [])),
        brands=brands,
        brand_pattern=_literal_alt(brands),
        question=re.compile(question) if question else None,
        cta=_literal_alt(list(data.get("cta_verbs", []) or [])),
        title_order=torder,
        title_patterns=tpat,
        opening_window_sec=float(th.get("opening_window_sec", 3.0)),
        presence_share=float(th.get("presence_share", 0.5)),
        min_type_size=int(th.get("min_type_size", th.get("min_cluster_for_ranges", 5))),
        topic_shift_window_sec=float(th.get("topic_shift_window_sec", 5.0)),
        topic_shift_jaccard=float(th.get("topic_shift_jaccard", 0.12)),
        discovery_min_unclassified=int(th.get("discovery_min_unclassified", 5)),
        source=source,
        raw=data,
    )
    if bands:
        r.position_bands = bands
    return r


def load_rules(path: Path | str | None = None) -> Rules:
    """Load rules.yaml if present, else rules.example.yaml. ``path`` forces a file."""
    p = Path(path) if path else (RULES_PATH if RULES_PATH.exists() else EXAMPLE_PATH)
    with open(p, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return build_rules(data, source=p)
