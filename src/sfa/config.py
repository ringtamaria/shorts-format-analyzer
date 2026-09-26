"""Settings loaded from .env / environment variables.

Everything the rest of the package needs from the environment goes through
``load_settings()`` so that scripts and tests share one source of truth.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Repository root (…/shorts-format-analyzer). Paths in .env are resolved
# relative to it so scripts behave the same from any working directory.
ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    youtube_api_key: str
    daily_quota: int
    quota_safety_margin: int
    transcript_backend: str  # local | hosted | null
    transcript_min_interval_sec: float
    transcript_max_retries: int
    anthropic_api_key: str
    llm_model: str
    region_code: str
    relevance_language: str
    db_path: Path
    quota_path: Path
    out_dir: Path

    @property
    def quota_budget(self) -> int:
        """Units we allow ourselves to spend per day."""
        return max(0, self.daily_quota - self.quota_safety_margin)


def _resolve(p: str) -> Path:
    path = Path(p).expanduser()
    return path if path.is_absolute() else ROOT / path


def load_settings(env_file: str | os.PathLike | None = None) -> Settings:
    """Load settings. ``env_file`` defaults to ``<root>/.env`` if it exists.

    Existing environment variables take precedence over the file, so CI and
    tests can override without touching .env.
    """
    load_dotenv(env_file or ROOT / ".env", override=False)
    env = os.environ
    backend = env.get("TRANSCRIPT_BACKEND", "local").strip().lower()
    if backend not in {"local", "hosted", "null"}:
        raise ValueError(f"TRANSCRIPT_BACKEND must be local|hosted|null, got {backend!r}")
    return Settings(
        youtube_api_key=env.get("YOUTUBE_API_KEY", "").strip(),
        daily_quota=int(env.get("YOUTUBE_DAILY_QUOTA", "10000")),
        quota_safety_margin=int(env.get("YOUTUBE_QUOTA_SAFETY_MARGIN", "300")),
        transcript_backend=backend,
        transcript_min_interval_sec=float(env.get("TRANSCRIPT_MIN_INTERVAL_SEC", "4")),
        transcript_max_retries=int(env.get("TRANSCRIPT_MAX_RETRIES", "3")),
        anthropic_api_key=env.get("ANTHROPIC_API_KEY", "").strip(),
        llm_model=env.get("SFA_LLM_MODEL", "claude-sonnet-5").strip(),
        region_code=env.get("YOUTUBE_REGION_CODE", "JP").strip(),
        relevance_language=env.get("YOUTUBE_RELEVANCE_LANGUAGE", "ja").strip(),
        db_path=_resolve(env.get("SFA_DB_PATH", "data/sfa.db")),
        quota_path=_resolve(env.get("SFA_QUOTA_PATH", "data/quota.json")),
        out_dir=_resolve(env.get("SFA_OUT_DIR", "out")),
    )
