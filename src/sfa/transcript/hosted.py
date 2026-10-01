"""Commercial backend: Supadata hosted transcript API.

    GET https://api.supadata.ai/v1/transcript?url=...&lang=ja&mode=native&text=false
    header x-api-key: <SUPADATA_API_KEY>

    200 -> {"lang": "ja", "availableLangs": [...],
            "content": [{"text": "...", "offset": <ms>, "duration": <ms>, "lang": "ja"}, ...]}
    202 -> {"jobId": "..."}  (async; poll GET /v1/transcript/{jobId})

Fixed on purpose, not configurable:
  * mode=native  - only existing captions (1 credit). "auto"/"generate" would
                   fall back to AI transcription at 2 credits per minute and
                   burn the free tier.
  * text=false   - timestamped segments; features need seconds.

Terms of service: this removes our own IP from the picture, but the data
still originates from YouTube. See README "規約".
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from typing import Any, Callable

from . import Segment, Transcript, TranscriptBlocked, TranscriptUnavailable
from ..quota import CreditTracker

BASE = "https://api.supadata.ai/v1"
MODE = "native"  # never "auto" or "generate" - see module docstring


class TranscriptPending(Exception):
    """Async job did not finish in time. Recorded as a transient error (retried next run)."""


def ms_to_sec(ms: Any) -> float:
    """Supadata returns offset/duration in milliseconds; the rest of the code uses seconds."""
    return round(float(ms or 0) / 1000.0, 3)


def segments_from_supadata(content: list[dict[str, Any]]) -> list[Segment]:
    return [Segment(ms_to_sec(c.get("offset")), ms_to_sec(c.get("duration")), str(c.get("text", "")))
            for c in content if str(c.get("text", "")).strip()]


def majority_lang(content: list[dict[str, Any]], fallback: str = "") -> str:
    """Language that covers the most characters across segments."""
    weights: Counter[str] = Counter()
    for c in content:
        lang = str(c.get("lang") or fallback or "")
        weights[lang.split("-")[0].lower()] += len(str(c.get("text", "")))
    return weights.most_common(1)[0][0] if weights else (fallback or "").split("-")[0].lower()


class HostedBackend:
    name = "hosted"

    def __init__(self, api_key: str, credits: CreditTracker, *, lang: str = "ja", min_interval_sec: float = 1.0,
                 poll_interval_sec: float = 2.0, max_polls: int = 10, timeout: float = 30.0,
                 opener: Callable[..., Any] | None = None, sleep: Callable[[float], None] = time.sleep):
        if not api_key:
            raise ValueError("SUPADATA_API_KEY is empty. Put it in .env (see .env.example).")
        self.api_key, self.credits, self.lang = api_key, credits, lang
        self.min_interval, self.poll_interval, self.max_polls = min_interval_sec, poll_interval_sec, max_polls
        self.timeout = timeout
        self._open = opener or urllib.request.urlopen
        self._sleep = sleep
        self._last = 0.0

    @property
    def mode(self) -> str:
        return MODE

    # ---- transport -------------------------------------------------------
    def _request(self, path: str, params: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
        wait = self.min_interval - (time.time() - self._last)
        if wait > 0:
            self._sleep(wait)
        self._last = time.time()
        url = f"{BASE}{path}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
        req = urllib.request.Request(url, headers={"x-api-key": self.api_key, "Accept": "application/json",
                                                "User-Agent": "shorts-format-analyzer/0.1"})
        try:
            with self._open(req, timeout=self.timeout) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                data = {"message": body}
            return e.code, data

    @staticmethod
    def _err(data: dict[str, Any]) -> str:
        return str(data.get("error") or data.get("message") or data.get("details") or data)[:200]

    def _handle_error(self, status: int, data: dict[str, Any]) -> None:
        msg = self._err(data)
        if status in (401, 403):
            raise TranscriptBlocked(f"Supadata auth failed ({status}): {msg}. Check SUPADATA_API_KEY.")
        if status == 402:
            raise TranscriptBlocked(f"Supadata credits exhausted on the provider side ({status}): {msg}")
        if status == 429:
            raise TranscriptBlocked(f"Supadata rate limit ({status}): {msg}. Stop and retry later.")
        if status in (400, 404) or "unavailable" in msg.lower() or "not found" in msg.lower():
            raise TranscriptUnavailable(f"supadata {status}: {msg}")
        raise RuntimeError(f"supadata {status}: {msg}")

    def _poll(self, job_id: str) -> dict[str, Any]:
        for _ in range(self.max_polls):
            self._sleep(self.poll_interval)
            status, data = self._request(f"/transcript/{urllib.parse.quote(job_id)}")
            if status >= 400:
                self._handle_error(status, data)
            st = str(data.get("status", "")).lower()
            if st in ("completed", "complete", "done") or "content" in data and st not in ("queued", "active"):
                return data
            if st in ("failed", "error"):
                raise TranscriptUnavailable(f"supadata job failed: {self._err(data)}")
        raise TranscriptPending(f"supadata job {job_id} still running after {self.max_polls} polls")

    # ---- backend API -----------------------------------------------------
    def fetch(self, video_id: str) -> Transcript:
        self.credits.reserve("transcript")  # charged before the request; QuotaExhausted -> graceful stop
        status, data = self._request("/transcript", {
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "lang": self.lang,
            "mode": MODE,
            "text": "false",
        })
        if status == 202 or ("jobId" in data and "content" not in data):
            data = self._poll(str(data.get("jobId")))
        elif status >= 400:
            self._handle_error(status, data)
        content = data.get("content")
        if not isinstance(content, list) or not content:
            raise TranscriptUnavailable("supadata returned no segments")
        segs = segments_from_supadata(content)
        if not segs:
            raise TranscriptUnavailable("supadata returned empty segments")
        lang = majority_lang(content, str(data.get("lang") or ""))
        return Transcript(video_id, lang, segs, self.name)
