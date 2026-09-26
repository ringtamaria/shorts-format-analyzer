"""SQLite cache. Nothing fetched from the network is fetched twice.

Tables
  searches     query-key -> list of video ids (TTL applies; search.list is 100 units)
  videos       video_id  -> metadata JSON (TTL applies; videos.list is cheap but
                            we still want reproducible reports)
  transcripts  video_id  -> transcript JSON or a recorded failure
  caption_checks video_id -> captions.list result (never expires; 50 units)
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from .youtube import Video

DEFAULT_TTL_SEC = 3600  # metadata cache validity: at least one hour per spec

SCHEMA = """
CREATE TABLE IF NOT EXISTS searches (
  key TEXT PRIMARY KEY,
  video_ids TEXT NOT NULL,
  next_page_token TEXT,
  fetched_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS videos (
  video_id TEXT PRIMARY KEY,
  genre TEXT,
  data TEXT NOT NULL,
  fetched_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS transcripts (
  video_id TEXT PRIMARY KEY,
  backend TEXT NOT NULL,
  status TEXT NOT NULL,          -- ok | unavailable | error
  language TEXT,
  segments TEXT,                 -- JSON [{"start":..,"duration":..,"text":..}]
  error TEXT,
  fetched_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS caption_checks (
  video_id TEXT PRIMARY KEY,
  tracks TEXT NOT NULL,          -- JSON list from captions.list
  fetched_at REAL NOT NULL
);
"""


class Store:
    def __init__(self, path: Path | str, ttl_sec: int = DEFAULT_TTL_SEC):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.ttl = ttl_sec
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        with self.conn:
            self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _fresh(self, fetched_at: float) -> bool:
        return (time.time() - fetched_at) < self.ttl

    # ---- searches --------------------------------------------------------
    @staticmethod
    def search_key(query: str, order: str, page_token: str | None, published_after: str | None) -> str:
        return json.dumps({"q": query, "order": order, "page": page_token or "", "after": published_after or ""},
                          ensure_ascii=False, sort_keys=True)

    def get_search(self, key: str, *, ignore_ttl: bool = False) -> tuple[list[str], str | None] | None:
        row = self.conn.execute("SELECT * FROM searches WHERE key=?", (key,)).fetchone()
        if row is None or (not ignore_ttl and not self._fresh(row["fetched_at"])):
            return None
        return json.loads(row["video_ids"]), row["next_page_token"]

    def put_search(self, key: str, video_ids: list[str], next_page_token: str | None) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO searches VALUES (?,?,?,?)",
                (key, json.dumps(video_ids), next_page_token, time.time()),
            )

    # ---- videos ----------------------------------------------------------
    def get_video(self, video_id: str, *, ignore_ttl: bool = False) -> Video | None:
        row = self.conn.execute("SELECT * FROM videos WHERE video_id=?", (video_id,)).fetchone()
        if row is None or (not ignore_ttl and not self._fresh(row["fetched_at"])):
            return None
        return Video.from_dict(json.loads(row["data"]))

    def put_videos(self, videos: list[Video], genre: str | None = None) -> None:
        now = time.time()
        with self.conn:
            self.conn.executemany(
                "INSERT OR REPLACE INTO videos VALUES (?,?,?,?)",
                [(v.video_id, genre, json.dumps(v.to_dict(), ensure_ascii=False), now) for v in videos],
            )

    def videos_for_genre(self, genre: str) -> list[Video]:
        rows = self.conn.execute("SELECT data FROM videos WHERE genre=?", (genre,)).fetchall()
        return [Video.from_dict(json.loads(r["data"])) for r in rows]

    # ---- transcripts -----------------------------------------------------
    def get_transcript_row(self, video_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM transcripts WHERE video_id=?", (video_id,)).fetchone()

    def put_transcript(self, video_id: str, backend: str, status: str, *, language: str | None = None,
                       segments: list[dict[str, Any]] | None = None, error: str | None = None) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO transcripts VALUES (?,?,?,?,?,?,?)",
                (video_id, backend, status, language,
                 json.dumps(segments, ensure_ascii=False) if segments is not None else None,
                 error, time.time()),
            )

    # ---- caption checks --------------------------------------------------
    def get_caption_check(self, video_id: str) -> list[dict[str, Any]] | None:
        row = self.conn.execute("SELECT tracks FROM caption_checks WHERE video_id=?", (video_id,)).fetchone()
        return json.loads(row["tracks"]) if row else None

    def put_caption_check(self, video_id: str, tracks: list[dict[str, Any]]) -> None:
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO caption_checks VALUES (?,?,?)",
                              (video_id, json.dumps(tracks, ensure_ascii=False), time.time()))

    # ---- stats -----------------------------------------------------------
    def counts(self) -> dict[str, int]:
        out = {}
        for t in ("searches", "videos", "transcripts", "caption_checks"):
            out[t] = self.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        return out
