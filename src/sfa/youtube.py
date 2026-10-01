"""Thin YouTube Data API v3 client.

Standard library only (urllib). Every request goes through
:meth:`YouTubeClient._get`, which charges :class:`sfa.quota.QuotaTracker`
*before* the HTTP call. There is deliberately no way to call the API without
a tracker.

Not implemented on purpose (see README "やらないこと"):
  * captions.download - owner-only, 403 for other people's videos
  * anything that downloads media
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from .quota import QuotaTracker

BASE = "https://www.googleapis.com/youtube/v3"
# YouTube Shorts can be up to 3 minutes since late 2024.
SHORTS_MAX_SEC = 180


class YouTubeAPIError(Exception):
    def __init__(self, status: int, reason: str, message: str):
        self.status, self.reason = status, reason
        super().__init__(f"HTTP {status} {reason}: {message}")


def parse_iso8601_duration(s: str) -> int:
    """'PT1M5S' -> 65. Returns 0 for unparsable input."""
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s or "")
    if not m:
        return 0
    d, h, mi, se = (int(x) if x else 0 for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + se


@dataclass
class Video:
    video_id: str
    title: str
    description: str
    channel_id: str
    channel_title: str
    published_at: str
    duration_sec: int
    view_count: int
    like_count: int
    comment_count: int
    tags: list[str]
    default_audio_language: str
    has_caption_flag: bool  # contentDetails.caption == "true" (owner-uploaded captions)

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/shorts/{self.video_id}"

    @property
    def is_short(self) -> bool:
        return 0 < self.duration_sec <= SHORTS_MAX_SEC

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Video":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__})

    @classmethod
    def from_api(cls, item: dict[str, Any]) -> "Video":
        sn, cd, st = item.get("snippet", {}), item.get("contentDetails", {}), item.get("statistics", {})
        return cls(
            video_id=item["id"],
            title=sn.get("title", ""),
            description=sn.get("description", ""),
            channel_id=sn.get("channelId", ""),
            channel_title=sn.get("channelTitle", ""),
            published_at=sn.get("publishedAt", ""),
            duration_sec=parse_iso8601_duration(cd.get("duration", "")),
            view_count=int(st.get("viewCount", 0) or 0),
            like_count=int(st.get("likeCount", 0) or 0),
            comment_count=int(st.get("commentCount", 0) or 0),
            tags=list(sn.get("tags", []) or []),
            default_audio_language=sn.get("defaultAudioLanguage", "") or sn.get("defaultLanguage", "") or "",
            has_caption_flag=str(cd.get("caption", "false")).lower() == "true",
        )


class YouTubeClient:
    def __init__(self, api_key: str, quota: QuotaTracker, *, region_code: str = "JP",
                 relevance_language: str = "ja", max_retries: int = 3, timeout: float = 20.0):
        if not api_key:
            raise ValueError("YOUTUBE_API_KEY is empty. Put it in .env (see .env.example).")
        self.api_key = api_key
        self.quota = quota
        self.region_code = region_code
        self.relevance_language = relevance_language
        self.max_retries = max_retries
        self.timeout = timeout

    # ---- transport -------------------------------------------------------
    def _get(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Charge quota, then GET. Raises QuotaExhausted before any network I/O."""
        self.quota.reserve(method)
        resource = method.split(".")[0]
        q = {k: v for k, v in params.items() if v not in (None, "", [])}
        q["key"] = self.api_key
        url = f"{BASE}/{resource}?{urllib.parse.urlencode(q, doseq=True)}"
        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with urllib.request.urlopen(url, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "replace")
                try:
                    err = json.loads(body).get("error", {})
                    reason = (err.get("errors") or [{}])[0].get("reason", "")
                    message = err.get("message", body)
                except json.JSONDecodeError:
                    reason, message = "", body
                api_err = YouTubeAPIError(e.code, reason, message)
                # 403 quotaExceeded means Google's counter disagrees with ours; do not retry.
                if e.code == 403 and reason == "quotaExceeded":
                    raise api_err
                if e.code in (500, 502, 503, 504) and attempt < self.max_retries:
                    last = api_err
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise api_err
            except (urllib.error.URLError, TimeoutError) as e:
                last = e
                if attempt < self.max_retries:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise
        assert last is not None
        raise last

    # ---- methods ---------------------------------------------------------
    def search_video_ids(self, query: str, *, max_results: int = 50, page_token: str | None = None,
                         order: str = "viewCount", published_after: str | None = None,
                         published_before: str | None = None) -> tuple[list[str], str | None]:
        """search.list (100 units). Returns (video_ids, next_page_token).

        ``videoDuration=short`` restricts to < 4 minutes; callers must still
        filter with :attr:`Video.is_short` after ``videos.list``.
        """
        data = self._get("search.list", {
            "part": "id",
            "q": query,
            "type": "video",
            "videoDuration": "short",
            "order": order,
            "maxResults": min(max(1, max_results), 50),
            "pageToken": page_token,
            "regionCode": self.region_code,
            "relevanceLanguage": self.relevance_language,
            "publishedAfter": published_after,
            "publishedBefore": published_before,
            "safeSearch": "none",
        })
        ids = [it["id"]["videoId"] for it in data.get("items", []) if it.get("id", {}).get("videoId")]
        return ids, data.get("nextPageToken")

    def uploads_playlists(self, channel_ids: list[str]) -> dict[str, str]:
        """channels.list (1 unit per 50 ids) -> {channel_id: uploads playlist id}."""
        out: dict[str, str] = {}
        for i in range(0, len(channel_ids), 50):
            data = self._get("channels.list", {"part": "contentDetails", "id": ",".join(channel_ids[i:i + 50]),
                                               "maxResults": 50})
            for it in data.get("items", []):
                pid = it.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
                if pid:
                    out[it["id"]] = pid
        return out

    def playlist_items(self, playlist_id: str, *, page_token: str | None = None
                       ) -> tuple[list[tuple[str, str]], str | None]:
        """playlistItems.list (1 unit, up to 50) -> ([(video_id, videoPublishedAt), ...], next_page_token).

        Uploads playlists are newest first, so callers can stop paging once
        items are older than their window.
        """
        data = self._get("playlistItems.list", {"part": "contentDetails", "playlistId": playlist_id,
                                                "maxResults": 50, "pageToken": page_token})
        items = []
        for it in data.get("items", []):
            cd = it.get("contentDetails", {})
            if cd.get("videoId"):
                items.append((cd["videoId"], cd.get("videoPublishedAt", "")))
        return items, data.get("nextPageToken")

    def list_videos(self, video_ids: list[str]) -> list[Video]:
        """videos.list (1 unit per 50 ids)."""
        out: list[Video] = []
        for i in range(0, len(video_ids), 50):
            chunk = video_ids[i:i + 50]
            data = self._get("videos.list", {
                "part": "snippet,contentDetails,statistics",
                "id": ",".join(chunk),
                "maxResults": 50,
            })
            out.extend(Video.from_api(it) for it in data.get("items", []))
        return out

    def list_caption_tracks(self, video_id: str) -> list[dict[str, Any]]:
        """captions.list (50 units!). Returns [{language, trackKind, name}, ...].

        trackKind == "asr" means auto-generated. Use only for sampling.
        """
        data = self._get("captions.list", {"part": "snippet", "videoId": video_id})
        return [
            {
                "language": it.get("snippet", {}).get("language", ""),
                "trackKind": it.get("snippet", {}).get("trackKind", ""),
                "name": it.get("snippet", {}).get("name", ""),
            }
            for it in data.get("items", [])
        ]
