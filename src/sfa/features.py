"""Structural features at the granularity a creator can copy.

Everything here is derived from the transcript (timing + text) and the
video metadata (title, duration). No visual analysis, no colour, no product
placement: those are not units a creator can act on in a brief.

The regexes, brand lists, CTA verbs and thresholds are NOT in this file.
They come from config/rules.yaml (private) or config/rules.example.yaml via
:mod:`sfa.rules`, so the framework can be public while the judgement rules
stay with the operator.

Features
  opening_type        first-N-second utterance type
  topic_shifts        count + relative timings of lexical topic changes
  conclusion_pos      前半 / 中盤 / 後半 / なし
  question            present? + relative position
  cta                 present? + relative position
  speech_density      characters per second
  duration_sec
  title_type          title pattern
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from .rules import OTHER, Rules, load_rules
from .transcript import Transcript
from .youtube import Video

POSITIONS = ["前半", "中盤", "後半"]  # 0-33% / 34-66% / 67-100%

_DEFAULT_RULES: Rules | None = None


def get_rules() -> Rules:
    global _DEFAULT_RULES
    if _DEFAULT_RULES is None:
        _DEFAULT_RULES = load_rules()
    return _DEFAULT_RULES


def set_rules(rules: Rules | None) -> None:
    """Override the process-wide rules (tests, experiments)."""
    global _DEFAULT_RULES
    _DEFAULT_RULES = rules


def _bigrams(s: str) -> set[str]:
    s = re.sub(r"\s+", "", s)
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def position_label(rel: float | None) -> str:
    if rel is None:
        return "なし"
    pct = rel * 100
    return POSITIONS[0] if pct <= 33 else POSITIONS[1] if pct <= 66 else POSITIONS[2]


def classify_opening(text: str, rules: Rules | None = None) -> str:
    """Classify the opening utterance. Evaluated in ``rules.opening_order``; first match wins."""
    r = rules or get_rules()
    t = text.strip()
    if not t:
        return OTHER
    for name in r.opening_order:
        if r.opening_patterns[name].search(t):
            return name
    if r.opening_fallback_conclusion and r.opening_fallback_conclusion.search(t):
        return "結論先出し" if "結論先出し" in r.opening_patterns else OTHER
    return OTHER


def classify_title(title: str, rules: Rules | None = None) -> str:
    r = rules or get_rules()
    t = title.strip()
    for name in r.title_order:
        if r.title_patterns[name].search(t):
            return name
    return OTHER


def topic_shifts(tr: Transcript, *, window_sec: float | None = None, threshold: float | None = None,
                 rules: Rules | None = None) -> list[float]:
    """Relative timings (0..1) where the vocabulary changes sharply.

    Consecutive windows are compared with character-bigram Jaccard
    similarity; a drop below the threshold marks a shift.
    """
    r = rules or get_rules()
    window_sec = r.topic_shift_window_sec if window_sec is None else window_sec
    threshold = r.topic_shift_jaccard if threshold is None else threshold
    if not tr.segments:
        return []
    end = max(s.end for s in tr.segments)
    if end <= window_sec:
        return []
    shifts: list[float] = []
    prev: set[str] | None = None
    t = 0.0
    while t < end:
        cur = _bigrams(tr.text_between(t, t + window_sec))
        if prev is not None and cur and prev and _jaccard(prev, cur) < threshold:
            shifts.append(round(t / end, 3))
        if cur:
            prev = cur
        t += window_sec
    return shifts


def _first_match_position(tr: Transcript, pattern: re.Pattern[str]) -> float | None:
    end = max((s.end for s in tr.segments), default=0.0)
    if end <= 0:
        return None
    for s in tr.segments:
        if pattern.search(s.text):
            return round(min(1.0, s.start / end), 3)
    return None


@dataclass
class Features:
    video_id: str
    title: str
    url: str
    duration_sec: int
    view_count: int
    has_transcript: bool
    opening_type: str
    opening_text: str
    n_topic_shifts: int
    topic_shift_positions: list[float]
    conclusion_pos: str
    conclusion_rel: float | None
    question_pos: str
    question_rel: float | None
    cta_pos: str
    cta_rel: float | None
    speech_density: float
    title_type: str
    transcript_chars: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    # -- numeric vector for clustering -------------------------------------
    @staticmethod
    def vector_names(rules: Rules | None = None) -> list[str]:
        r = rules or get_rules()
        return (["duration_sec", "speech_density", "n_topic_shifts", "conclusion_rel", "question_rel", "cta_rel",
                 "has_question", "has_cta"]
                + [f"open:{o}" for o in r.opening_types] + [f"title:{t}" for t in r.title_types])

    def vector(self, rules: Rules | None = None) -> list[float]:
        r = rules or get_rules()

        def rel(x: float | None) -> float:
            return -0.5 if x is None else x  # "absent" is its own point, away from 0..1
        return ([float(self.duration_sec), self.speech_density, float(self.n_topic_shifts),
                 rel(self.conclusion_rel), rel(self.question_rel), rel(self.cta_rel),
                 float(self.question_rel is not None), float(self.cta_rel is not None)]
                + [1.0 if self.opening_type == o else 0.0 for o in r.opening_types]
                + [1.0 if self.title_type == t else 0.0 for t in r.title_types])


def extract_features(video: Video, tr: Transcript | None, rules: Rules | None = None) -> Features:
    r = rules or get_rules()
    title_type = classify_title(video.title, r)
    if tr is None or not tr.segments:
        return Features(
            video_id=video.video_id, title=video.title, url=video.url, duration_sec=video.duration_sec,
            view_count=video.view_count, has_transcript=False,
            opening_type=OTHER, opening_text="", n_topic_shifts=0, topic_shift_positions=[],
            conclusion_pos="なし", conclusion_rel=None, question_pos="なし", question_rel=None,
            cta_pos="なし", cta_rel=None, speech_density=0.0, title_type=title_type,
        )
    opening = tr.text_between(0.0, r.opening_window_sec)
    shifts = topic_shifts(tr, rules=r)
    c_rel = _first_match_position(tr, r.conclusion)
    q_rel = _first_match_position(tr, r.question)
    cta_rel = _first_match_position(tr, r.cta)
    dur = video.duration_sec or max((s.end for s in tr.segments), default=0.0) or 1.0
    return Features(
        video_id=video.video_id, title=video.title, url=video.url, duration_sec=video.duration_sec,
        view_count=video.view_count, has_transcript=True,
        opening_type=classify_opening(opening, r), opening_text=opening[:80],
        n_topic_shifts=len(shifts), topic_shift_positions=shifts,
        conclusion_pos=position_label(c_rel), conclusion_rel=c_rel,
        question_pos=position_label(q_rel), question_rel=q_rel,
        cta_pos=position_label(cta_rel), cta_rel=cta_rel,
        speech_density=round(tr.total_chars / float(dur), 2),
        title_type=title_type, transcript_chars=tr.total_chars,
    )
