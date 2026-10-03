"""Structural features at the granularity a creator can copy.

Everything here is derived from the transcript (timing + text) and the
video metadata (title, duration). No visual analysis, no colour, no product
placement: those are not units a creator can act on in a brief.
(A thumbnail URL is carried for the operator to look at; it is never fetched
or analysed.)

The patterns, brand lists, markers and thresholds are NOT in this file. They
come from config/rules.yaml (private) or config/rules.example.yaml via
:mod:`sfa.rules`, so the framework can be public while the judgement rules
stay with the operator.

Brand names are matched against the TITLE only (hashtags removed): automatic
captions mangle katakana proper nouns (ポンデポテイト, 相引きにグ), so a keyword
list cannot hit them in ASR text. Brand is a subject feature, not an opening
type.

The opening window starts at the FIRST REAL UTTERANCE, not at 0 s: segments
that only carry caption annotations ([音楽], [Music], [拍手]) are skipped, so a
video that opens on music is typed by what it says once it starts talking.
Opening on music is kept as its own feature (music_intro).

Features
  speech              speech | silent | no_transcript | not_fetched
  format_id           opening type id from the rules, "silent" or "unclassified"
  completion          present? + relative position   (旧「結論位置」)
  question / cta      present? + relative position
  bulk_input          present? (independent of the opening type)
  modifiers           decorations on the opening (煽り, 最上級 ...), multi-valued
  music_intro         caption annotations ([音楽] ...) before / at the start of speech
  transcript_source   which backend produced the transcript (hosted, local ...)
  brand               brand found in the title, or None
  speech_density      characters per second
  duration_sec, title_type
  topic_shifts        reference only: NOT used for typing or profiles
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from .rules import OTHER, SILENT_ID, UNCLASSIFIED_ID, Rules, load_rules
from .transcript import Transcript
from .youtube import Video

_DEFAULT_RULES: Rules | None = None
_RE_HASHTAG = re.compile(r"[#＃]\S+")
# Caption annotations that are not speech: [Music], [音楽], (拍手), ♪ ...
_RE_NON_SPEECH = re.compile(r"\[[^\]]*\]|［[^］]*］|\([^)]*\)|（[^）]*）|[♪♫♬]")
_RE_NON_CHARS = re.compile(r"[\s\W_]+", re.UNICODE)


def get_rules() -> Rules:
    global _DEFAULT_RULES
    if _DEFAULT_RULES is None:
        _DEFAULT_RULES = load_rules()
    return _DEFAULT_RULES


def set_rules(rules: Rules | None) -> None:
    """Override the process-wide rules (tests, experiments)."""
    global _DEFAULT_RULES
    _DEFAULT_RULES = rules


def title_for_matching(title: str) -> str:
    """Title without hashtags (#shorts, #料理 ...), which would otherwise match broad rules."""
    return _RE_HASHTAG.sub(" ", title or "").strip()


def speech_chars(text: str) -> int:
    """Characters of actual speech: caption annotations, symbols and spaces removed."""
    return len(_RE_NON_CHARS.sub("", _RE_NON_SPEECH.sub("", text or "")))


def clean_speech(text: str) -> str:
    """Opening text without caption annotations, whitespace collapsed."""
    return re.sub(r"\s+", " ", _RE_NON_SPEECH.sub(" ", text or "")).strip()


def first_speech_start(tr: Transcript) -> float | None:
    """Start of the first segment that contains actual speech (annotation-only segments skipped)."""
    for s in sorted(tr.segments, key=lambda s: s.start):
        if speech_chars(s.text) > 0:
            return s.start
    return None


def opening_from_first_speech(tr: Transcript, window_sec: float) -> tuple[str, float]:
    """(opening text without annotations, start time of the first utterance)."""
    t0 = first_speech_start(tr)
    if t0 is None:
        return "", 0.0
    return clean_speech(tr.text_between(t0, t0 + window_sec)), t0


def has_music_intro(tr: Transcript, window_sec: float) -> bool:
    """A caption annotation appears before speech starts, or within the opening window."""
    t0 = first_speech_start(tr)
    limit = max(t0 if t0 is not None else 0.0, window_sec)
    return any(_RE_NON_SPEECH.search(s.text) for s in tr.segments if s.start < limit)


def find_modifiers(text: str, rules: Rules | None = None) -> list[str]:
    r = rules or get_rules()
    return [m.id for m in r.modifiers if m.pattern.search(text or "")]


def classify_speech(tr: Transcript | None, lang: str, rules: Rules | None = None) -> str:
    """speech | silent | other_lang | no_transcript.

    Silent is decided BEFORE the language: a caption track that only says
    [Music] / [Applause] is tagged 'en' by YouTube, but the video simply has
    no speech, so it belongs to the silent type, not to the excluded languages.
    """
    r = rules or get_rules()
    if tr is None or not tr.segments:
        return "no_transcript"
    if speech_chars(tr.text) < r.silent_max_chars:
        return "silent"
    return "speech" if tr.is_lang(lang) else "other_lang"


def position_label(rel: float | None, rules: Rules | None = None) -> str:
    if rel is None:
        return "なし"
    r = rules or get_rules()
    pct = rel * 100
    # Bands are ordered and contiguous in whole percent (0-33 / 34-66 / 67-100); 33.4% is still 前半.
    for name, (_lo, hi) in r.position_bands.items():
        if pct < hi + 1:
            return name
    return list(r.position_bands)[-1]


def classify_opening(text: str, rules: Rules | None = None) -> str | None:
    """Opening type id: the first type in rules order whose match holds. None = unclassified."""
    r = rules or get_rules()
    t = (text or "").strip()
    if not t:
        return None
    for ot in r.opening_types:
        if ot.matches(t):
            return ot.id
    return None


def classify_title(title: str, rules: Rules | None = None) -> str:
    r = rules or get_rules()
    t = title_for_matching(title)
    for name in r.title_order:
        if r.title_patterns[name].search(t):
            return name
    return OTHER


def find_brand(title: str, rules: Rules | None = None) -> str | None:
    r = rules or get_rules()
    if r.brand_pattern is None:
        return None
    m = r.brand_pattern.search(title_for_matching(title))
    return m.group(0) if m else None


def _bigrams(s: str) -> set[str]:
    s = re.sub(r"\s+", "", s)
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def topic_shifts(tr: Transcript, *, window_sec: float | None = None, threshold: float | None = None,
                 rules: Rules | None = None) -> list[float]:
    """Reference only. Relative timings (0..1) where the vocabulary changes sharply."""
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


def _first_match_position(tr: Transcript, pattern: re.Pattern[str] | None) -> float | None:
    if pattern is None:
        return None
    end = max((s.end for s in tr.segments), default=0.0)
    if end <= 0:
        return None
    for s in tr.segments:
        if pattern.search(s.text):
            return round(min(1.0, s.start / end), 3)
    return None


def thumbnail_url(video_id: str) -> str:
    """Public thumbnail URL for the operator to look at. Never fetched by this tool."""
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


@dataclass
class Features:
    video_id: str
    title: str
    url: str
    duration_sec: int
    view_count: int
    speech: str                      # speech | silent | no_transcript | not_fetched
    format_id: str                   # opening type id | silent | unclassified
    opening_text: str
    completion_rel: float | None
    question_rel: float | None
    cta_rel: float | None
    bulk_input: bool
    brand: str | None
    speech_density: float
    title_type: str
    language: str = ""
    transcript_chars: int = 0
    n_topic_shifts: int = 0          # reference only
    thumbnail_url: str = ""
    modifiers: list[str] = field(default_factory=list)
    music_intro: bool = False
    transcript_source: str = ""
    speech_start_sec: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def has_transcript(self) -> bool:
        return self.speech == "speech"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    # -- numeric vector, used only by the discovery clustering of unclassified videos
    @staticmethod
    def vector_names() -> list[str]:
        return ["duration_sec", "speech_density", "completion_rel", "question_rel", "cta_rel",
                "has_completion", "has_question", "has_cta", "bulk_input"]

    def vector(self, rules: Rules | None = None) -> list[float]:
        def rel(x: float | None) -> float:
            return -0.5 if x is None else x
        return [float(self.duration_sec), self.speech_density, rel(self.completion_rel), rel(self.question_rel),
                rel(self.cta_rel), float(self.completion_rel is not None), float(self.question_rel is not None),
                float(self.cta_rel is not None), float(self.bulk_input)]


def extract_features(video: Video, tr: Transcript | None, rules: Rules | None = None, *,
                     speech: str | None = None, lang: str = "ja", source: str | None = None) -> Features:
    """``speech`` may be given by the caller (e.g. 'not_fetched'); otherwise it is derived from ``tr``."""
    r = rules or get_rules()
    kind = speech or classify_speech(tr, lang, r)
    base = dict(video_id=video.video_id, title=video.title, url=video.url, duration_sec=video.duration_sec,
                view_count=video.view_count, title_type=classify_title(video.title, r),
                brand=find_brand(video.title, r), thumbnail_url=thumbnail_url(video.video_id),
                language=(tr.language if tr else ""),
                transcript_source=source if source is not None else (tr.source if tr else ""))
    if kind != "speech" or tr is None:
        fid = SILENT_ID if kind in ("silent", "no_transcript") else UNCLASSIFIED_ID
        return Features(**base, speech=kind, format_id=fid, opening_text="", completion_rel=None,
                        question_rel=None, cta_rel=None, bulk_input=False, speech_density=0.0,
                        transcript_chars=tr.total_chars if tr else 0)
    opening, t0 = opening_from_first_speech(tr, r.opening_window_sec)
    dur = video.duration_sec or max((s.end for s in tr.segments), default=0.0) or 1.0
    return Features(
        **base, speech="speech",
        format_id=classify_opening(opening, r) or UNCLASSIFIED_ID,
        opening_text=opening[:80],
        completion_rel=_first_match_position(tr, r.completion),
        question_rel=_first_match_position(tr, r.question),
        cta_rel=_first_match_position(tr, r.cta),
        bulk_input=bool(r.bulk_input and r.bulk_input.search(tr.text)),
        speech_density=round(tr.total_chars / float(dur), 2),
        transcript_chars=tr.total_chars,
        n_topic_shifts=len(topic_shifts(tr, rules=r)),
        modifiers=find_modifiers(opening, r),
        music_intro=has_music_intro(tr, r.opening_window_sec),
        speech_start_sec=round(t0, 2),
    )
