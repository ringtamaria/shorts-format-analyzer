"""Fallback backend: never returns a transcript."""
from __future__ import annotations

from . import Transcript, TranscriptUnavailable


class NullBackend:
    name = "null"

    def fetch(self, video_id: str) -> Transcript:
        raise TranscriptUnavailable("null backend")
