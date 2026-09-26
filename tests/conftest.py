import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sfa.transcript import Segment, Transcript  # noqa: E402
from sfa.youtube import Video  # noqa: E402


def make_video(i: int, *, duration: int = 45, views: int = 100000, title: str | None = None) -> Video:
    return Video(
        video_id=f"vid{i:03d}", title=title or f"3分で作れる時短レシピ{i}", description="", channel_id=f"ch{i}",
        channel_title=f"チャンネル{i}", published_at="2026-09-01T00:00:00Z", duration_sec=duration,
        view_count=views, like_count=1000, comment_count=10, tags=[], default_audio_language="ja",
        has_caption_flag=i % 2 == 0,
    )


def make_transcript(video_id: str, texts: list[str], *, seg_dur: float = 3.0) -> Transcript:
    segs = [Segment(i * seg_dur, seg_dur, t) for i, t in enumerate(texts)]
    return Transcript(video_id, "ja", segs, "test")


@pytest.fixture
def env_tmp(tmp_path, monkeypatch):
    """Point every path at tmp and use the null transcript backend."""
    monkeypatch.setenv("YOUTUBE_API_KEY", "test-key")
    monkeypatch.setenv("SFA_DB_PATH", str(tmp_path / "sfa.db"))
    monkeypatch.setenv("SFA_QUOTA_PATH", str(tmp_path / "quota.json"))
    monkeypatch.setenv("SFA_OUT_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("TRANSCRIPT_BACKEND", "null")
    monkeypatch.setenv("YOUTUBE_DAILY_QUOTA", "10000")
    monkeypatch.setenv("YOUTUBE_QUOTA_SAFETY_MARGIN", "300")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return tmp_path
