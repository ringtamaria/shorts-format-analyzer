"""Structural features at the granularity a creator can copy.

Everything here is derived from the transcript (timing + text) and the
video metadata (title, duration). No visual analysis, no colour, no product
placement: those are not units a creator can act on in a brief.

Features
  opening_type        first-3-second utterance type
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

from .transcript import Transcript
from .youtube import Video

OPENING_TYPES = ["結論先出し", "問いかけ", "商品名", "数値提示", "否定形", "呼びかけ", "その他"]
TITLE_TYPES = ["数値型", "疑問型", "否定型", "命令型", "Before/After型", "断定型", "その他"]
POSITIONS = ["前半", "中盤", "後半"]
OPENING_WINDOW_SEC = 3.0

_RE_QUESTION = re.compile(r"[？?]|(?:ですか|ますか|でしょうか|知ってる|知ってます|ありませんか|じゃない[？?]?|なんで|どうして|なぜ|どっち|どれ)")
_RE_NUMBER = re.compile(r"(?:\d|[０-９]|[一二三四五六七八九十百千万]{1,3}(?:つ|個|選|分|秒|円|倍|割|%|％|kg|g|日|回|位|種類|ステップ))")
_RE_NEGATION = re.compile(r"(?:やめて|やめろ|ダメ|だめ|NG|禁止|絶対に?(?:しないで|使わないで|買わないで)|間違い|間違って|損して|してない|できてない|知らないと|やってはいけない|してはいけない|ない[。！!]?$)")
_RE_CALL = re.compile(r"(?:みんな|皆さん|みなさん|あなた|君|お前|〜さん|の人|な人|ちゃん|ってる人|してる人|悩んでる|ですよね|よね[！!]?)")
_RE_CONCLUSION = re.compile(r"(?:結論|つまり|要するに|実は|ポイントは|正解は|答えは|おすすめは|一番は|これが|これで完成|完成|まとめ|以上)")
_RE_CTA = re.compile(r"(?:フォロー|いいね|コメント|保存|シェア|チャンネル登録|登録して|概要欄|プロフ|リンク|ハイライト|試して|作ってみて|やってみて|チェックして|見てね|次回|続き)")
_RE_BEFORE_AFTER = re.compile(r"(?:before|after|ビフォー|アフター|→|⇒|から.*に|が.*になる|変わる|激変|逆転)", re.I)
_RE_IMPERATIVE = re.compile(r"(?:しろ|して|しよう|やれ|やって|見て|作って|買って|使って|注意|必見|保存版|方法|やり方|コツ|裏技|レシピ)")
_RE_ASSERT = re.compile(r"(?:最強|最高|神|一番|絶対|本当に|ガチ|マジ|正直|結論)")
_RE_PRODUCT = re.compile(r"(?:[A-Za-zＡ-Ｚａ-ｚ]{3,}|の(?:新作|新商品|商品)|買ってみた|使ってみた|レビュー|開封|無印|ダイソー|セリア|ユニクロ|コストコ|業スー|カルディ|スタバ|マック|セブン|ローソン|ファミマ)")


def _bigrams(s: str) -> set[str]:
    s = re.sub(r"\s+", "", s)
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def _position_label(rel: float | None) -> str:
    if rel is None:
        return "なし"
    return POSITIONS[0] if rel < 1 / 3 else POSITIONS[1] if rel < 2 / 3 else POSITIONS[2]


def classify_opening(text: str) -> str:
    """Classify the first ~3 seconds of speech. Order matters: most specific first."""
    t = text.strip()
    if not t:
        return "その他"
    if _RE_QUESTION.search(t):
        return "問いかけ"
    if _RE_NEGATION.search(t):
        return "否定形"
    if _RE_NUMBER.search(t):
        return "数値提示"
    if _RE_CONCLUSION.search(t):
        return "結論先出し"
    if _RE_PRODUCT.search(t):
        return "商品名"
    if _RE_CALL.search(t):
        return "呼びかけ"
    # Short declarative opener that names the outcome ("〜が完成", "〜できます")
    if re.search(r"(?:できる|できます|完成|になります|作れます|方法|やり方)", t):
        return "結論先出し"
    return "その他"


def classify_title(title: str) -> str:
    t = title.strip()
    if _RE_NUMBER.search(t):
        return "数値型"
    if _RE_QUESTION.search(t):
        return "疑問型"
    if _RE_NEGATION.search(t):
        return "否定型"
    if _RE_BEFORE_AFTER.search(t):
        return "Before/After型"
    if _RE_IMPERATIVE.search(t):
        return "命令型"
    if _RE_ASSERT.search(t):
        return "断定型"
    return "その他"


def topic_shifts(tr: Transcript, *, window_sec: float = 5.0, threshold: float = 0.12) -> list[float]:
    """Relative timings (0..1) where the vocabulary changes sharply.

    Consecutive ``window_sec`` windows are compared with character-bigram
    Jaccard similarity; a drop below ``threshold`` marks a shift.
    """
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
    def vector_names() -> list[str]:
        return (["duration_sec", "speech_density", "n_topic_shifts", "conclusion_rel", "question_rel", "cta_rel",
                 "has_question", "has_cta"]
                + [f"open:{o}" for o in OPENING_TYPES] + [f"title:{t}" for t in TITLE_TYPES])

    def vector(self) -> list[float]:
        def rel(x: float | None) -> float:
            return -0.5 if x is None else x  # "absent" is its own point, away from 0..1
        return ([float(self.duration_sec), self.speech_density, float(self.n_topic_shifts),
                 rel(self.conclusion_rel), rel(self.question_rel), rel(self.cta_rel),
                 float(self.question_rel is not None), float(self.cta_rel is not None)]
                + [1.0 if self.opening_type == o else 0.0 for o in OPENING_TYPES]
                + [1.0 if self.title_type == t else 0.0 for t in TITLE_TYPES])


def extract_features(video: Video, tr: Transcript | None) -> Features:
    title_type = classify_title(video.title)
    if tr is None or not tr.segments:
        return Features(
            video_id=video.video_id, title=video.title, url=video.url, duration_sec=video.duration_sec,
            view_count=video.view_count, has_transcript=False,
            opening_type="その他", opening_text="", n_topic_shifts=0, topic_shift_positions=[],
            conclusion_pos="なし", conclusion_rel=None, question_pos="なし", question_rel=None,
            cta_pos="なし", cta_rel=None, speech_density=0.0, title_type=title_type,
        )
    opening = tr.text_between(0.0, OPENING_WINDOW_SEC)
    shifts = topic_shifts(tr)
    c_rel = _first_match_position(tr, _RE_CONCLUSION)
    q_rel = _first_match_position(tr, _RE_QUESTION)
    cta_rel = _first_match_position(tr, _RE_CTA)
    dur = video.duration_sec or max((s.end for s in tr.segments), default=0.0) or 1.0
    return Features(
        video_id=video.video_id, title=video.title, url=video.url, duration_sec=video.duration_sec,
        view_count=video.view_count, has_transcript=True,
        opening_type=classify_opening(opening), opening_text=opening[:80],
        n_topic_shifts=len(shifts), topic_shift_positions=shifts,
        conclusion_pos=_position_label(c_rel), conclusion_rel=c_rel,
        question_pos=_position_label(q_rel), question_rel=q_rel,
        cta_pos=_position_label(cta_rel), cta_rel=cta_rel,
        speech_density=round(tr.total_chars / float(dur), 2),
        title_type=title_type, transcript_chars=tr.total_chars,
    )
