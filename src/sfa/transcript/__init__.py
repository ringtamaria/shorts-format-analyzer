"""Transcript acquisition behind one interface.

    get_transcript(video_id) -> Transcript | None

Backends (chosen by ``TRANSCRIPT_BACKEND``):
  local   youtube-transcript-api. Verification only; residential IP, low rate.
  hosted  paid hosted API. Stub - raises NotImplementedError until wired up.
  null    always returns None (metadata-only mode).

Results, including failures, are cached in :class:`sfa.store.Store` so a
video is never requested twice.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..config import Settings
from ..store import Store


@dataclass
class Segment:
    start: float
    duration: float
    text: str

    @property
    def end(self) -> float:
        return self.start + self.duration


@dataclass
class Transcript:
    video_id: str
    language: str
    segments: list[Segment] = field(default_factory=list)
    source: str = ""  # backend name

    @property
    def text(self) -> str:
        return " ".join(s.text.strip() for s in self.segments if s.text.strip())

    @property
    def total_chars(self) -> int:
        return sum(len(s.text) for s in self.segments)

    def text_between(self, t0: float, t1: float) -> str:
        return " ".join(s.text.strip() for s in self.segments if s.start < t1 and s.end > t0)

    def to_rows(self) -> list[dict[str, Any]]:
        return [{"start": s.start, "duration": s.duration, "text": s.text} for s in self.segments]

    @classmethod
    def from_rows(cls, video_id: str, language: str, rows: list[dict[str, Any]], source: str) -> "Transcript":
        return cls(video_id, language, [Segment(float(r["start"]), float(r["duration"]), str(r["text"])) for r in rows], source)


class TranscriptUnavailable(Exception):
    """The backend answered, but there is no usable transcript for this video."""


class TranscriptBackend(Protocol):
    name: str

    def fetch(self, video_id: str) -> Transcript:
        """Return a transcript or raise TranscriptUnavailable / other errors."""


def make_backend(settings: Settings) -> TranscriptBackend:
    if settings.transcript_backend == "local":
        from .local import LocalBackend
        return LocalBackend(min_interval_sec=settings.transcript_min_interval_sec,
                            max_retries=settings.transcript_max_retries)
    if settings.transcript_backend == "hosted":
        from .hosted import HostedBackend
        return HostedBackend()
    from .null import NullBackend
    return NullBackend()


class TranscriptService:
    """Cache-aware wrapper. This is what the rest of the code uses."""

    def __init__(self, backend: TranscriptBackend, store: Store):
        self.backend = backend
        self.store = store
        self.stats = {"cache_hit": 0, "fetched": 0, "unavailable": 0, "error": 0}

    def get_transcript(self, video_id: str) -> Transcript | None:
        row = self.store.get_transcript_row(video_id)
        if row is not None:
            self.stats["cache_hit"] += 1
            if row["status"] == "ok" and row["segments"]:
                return Transcript.from_rows(video_id, row["language"] or "", json.loads(row["segments"]), row["backend"])
            return None
        if self.backend.name == "null":
            return None
        try:
            tr = self.backend.fetch(video_id)
        except TranscriptUnavailable as e:
            self.stats["unavailable"] += 1
            self.store.put_transcript(video_id, self.backend.name, "unavailable", error=str(e))
            return None
        except NotImplementedError:
            raise
        except Exception as e:  # noqa: BLE001 - recorded, never crashes the run
            self.stats["error"] += 1
            self.store.put_transcript(video_id, self.backend.name, "error", error=f"{type(e).__name__}: {e}")
            return None
        self.stats["fetched"] += 1
        self.store.put_transcript(video_id, self.backend.name, "ok", language=tr.language, segments=tr.to_rows())
        return tr


__all__ = ["Segment", "Transcript", "TranscriptUnavailable", "TranscriptBackend",
           "TranscriptService", "make_backend"]
