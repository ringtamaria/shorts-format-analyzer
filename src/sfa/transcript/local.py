"""Verification backend built on youtube-transcript-api.

Terms-of-service grey area: use only from a residential IP, at low frequency.
Datacenter / VPN / corporate-proxy IPs are blocked by YouTube (IpBlocked), and
a residential IP is blocked too if the request rate is too high (observed on
2026-10-01: blocked after ~17 fetches at a 4 s interval).
This backend enforces a minimum interval between requests and retries a few
times with backoff on transient errors; it never retries an IP block and it
does not support proxies (that would be block evasion).
"""
from __future__ import annotations

import random
import time

from . import Segment, Transcript, TranscriptBlocked, TranscriptUnavailable

PREFERRED_LANGS = ["ja", "ja-JP", "en"]


class LocalBackend:
    name = "local"

    def __init__(self, min_interval_sec: float = 30.0, max_retries: int = 3, languages: list[str] | None = None):
        self.min_interval = min_interval_sec
        self.max_retries = max_retries
        self.languages = languages or PREFERRED_LANGS
        self._last_call = 0.0
        self.ip_blocked = False

    def _throttle(self) -> None:
        wait = self.min_interval - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait + random.uniform(0, 0.5))
        self._last_call = time.time()

    def fetch(self, video_id: str) -> Transcript:
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
            from youtube_transcript_api import _errors as yt_err
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("pip install youtube-transcript-api") from e

        if self.ip_blocked:
            raise TranscriptBlocked("blocked earlier in this run; not retrying from this IP")

        unavailable = tuple(
            getattr(yt_err, n) for n in ("NoTranscriptFound", "TranscriptsDisabled", "VideoUnavailable",
                                          "AgeRestricted", "VideoUnplayable", "NotTranslatable")
            if hasattr(yt_err, n)
        )
        blocked = tuple(getattr(yt_err, n) for n in ("IpBlocked", "RequestBlocked", "PoTokenRequired") if hasattr(yt_err, n))

        api = YouTubeTranscriptApi()
        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                fetched = api.fetch(video_id, languages=self.languages)
                segs = [Segment(float(s.start), float(s.duration), str(s.text)) for s in fetched]
                if not segs:
                    raise TranscriptUnavailable("empty transcript")
                return Transcript(video_id, getattr(fetched, "language_code", "") or "", segs, self.name)
            except unavailable as e:
                raise TranscriptUnavailable(type(e).__name__) from e
            except blocked as e:
                self.ip_blocked = True
                raise TranscriptBlocked(
                    f"{type(e).__name__}: YouTube blocked this IP. Stop for today and retry later at a lower rate."
                ) from e
            except (TranscriptUnavailable, TranscriptBlocked):
                raise
            except Exception as e:  # noqa: BLE001 - transient; retry with backoff
                last = e
                if attempt < self.max_retries:
                    time.sleep(2.0 * (attempt + 1))
        assert last is not None
        raise last
