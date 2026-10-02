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


RECIPE_RULES = {
    "opening_types": [
        {"id": "hype_declaration", "name": "煽り宣言型", "one_line": "煽り語で入って宣言し、間を置かず素材投入へ",
         "match": {"all": [{"any": ["やばい", "禁断", "マジで"]}, {"any": ["紹介します", "作ります"]}]}},
        {"id": "warning", "name": "警告・禁止型", "one_line": "否定・禁止で入ってスクロールを止める",
         "match": {"any": ["しないでください", "作ったらあかん", "食べないで"]}},
        {"id": "question", "name": "問いかけ型", "one_line": "問いかけで入る", "match": ["知ってる", "[？?]"]},
    ],
    "completion_markers": ["完成", "できました", "いただきます"],
    "bulk_input": ["大量の", r"\d+\s*(キロ|kg|k)"],
    "brands": ["ダイソー", "無印"],
    "question_pattern": "[？?]|知ってる",
    "cta_verbs": ["フォロー", "保存"],
    "title": {"order": ["数値型", "疑問型"], "patterns": {"数値型": r"\d", "疑問型": "[？?]"}},
    "thresholds": {"min_type_size": 5, "presence_share": 0.5, "discovery_min_unclassified": 5},
}


@pytest.fixture
def recipe_rules():
    from sfa.features import set_rules
    from sfa.rules import build_rules
    r = build_rules(RECIPE_RULES)
    set_rules(r)
    yield r
    set_rules(None)


@pytest.fixture(autouse=True)
def isolate_private_config(tmp_path, monkeypatch):
    """Tests must not depend on the operator's private files or live keys.

    config/rules.yaml and config/channels.yaml are written by the operator
    (genre "レシピ 料理" included), and .env holds a live Supadata key.
    """
    from sfa import rules as R
    from sfa.features import set_rules
    monkeypatch.setattr(R, "RULES_PATH", tmp_path / "no-private-rules.yaml")
    monkeypatch.setenv("SFA_CHANNELS_PATH", str(tmp_path / "no-private-channels.yaml"))
    monkeypatch.setenv("SUPADATA_CREDITS_PATH", str(tmp_path / "supadata_credits.json"))
    monkeypatch.setenv("SUPADATA_API_KEY", "")
    set_rules(None)
    yield
    set_rules(None)
