"""Shared helpers for the scripts in scripts/."""
from __future__ import annotations

import sys
from pathlib import Path

# Make `import sfa` work when a script is run directly from the repo.
SRC = Path(__file__).resolve().parents[1]
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from .config import Settings, load_settings  # noqa: E402
from .quota import QuotaExhausted, QuotaTracker  # noqa: E402
from .store import Store  # noqa: E402
from .youtube import YouTubeClient  # noqa: E402


def bootstrap(*, need_api_key: bool = True) -> tuple[Settings, QuotaTracker, Store, YouTubeClient | None]:
    settings = load_settings()
    quota = QuotaTracker(settings.quota_path, settings.quota_budget)
    store = Store(settings.db_path)
    client = None
    if need_api_key:
        client = YouTubeClient(settings.youtube_api_key, quota,
                               region_code=settings.region_code,
                               relevance_language=settings.relevance_language)
    return settings, quota, store, client


def graceful_quota_stop(e: QuotaExhausted) -> int:
    """Print the standard 'stopped, here is how to resume' message. Returns exit code 0."""
    print()
    print(f"[stop] {e}")
    print(f"[stop] {e.tracker.resume_hint()}")
    print(e.tracker.status_line())
    return 0


def genre_slug(genre: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in genre.strip()).strip("_") or "genre"
