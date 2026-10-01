"""Round 3: brand from title, channels route, pre-exclusion, language filter, credits in build_report."""
import json

import pytest

from conftest import make_transcript, make_video
from sfa.channels import load_channels
from sfa.features import classify_opening, extract_features, title_for_matching


# ---- brand from title ----------------------------------------------------------

def test_brand_is_matched_on_title_not_transcript():
    # ASR mangled the brand in the transcript; the title has it right.
    v = make_video(1, title="ダイソーの新作グッズで時短")
    tr = make_transcript(v.video_id, ["だいそのしんさくぐっずで", "まず切ります", "完成"])
    assert extract_features(v, tr).opening_type == "商品名"
    # Brand only in the transcript, not in the title -> not 商品名.
    v2 = make_video(2, title="いつもの晩ごはん")
    tr2 = make_transcript(v2.video_id, ["ダイソーの", "まず切ります", "完成"])
    assert extract_features(v2, tr2).opening_type != "商品名"


def test_hashtags_are_ignored_for_title_matching():
    assert title_for_matching("簡単レシピ #shorts #cooking") == "簡単レシピ"
    v = make_video(3, title="簡単レシピ #shorts #ダイソー")
    tr = make_transcript(v.video_id, ["きょうはこれ", "まず切ります", "完成"])
    assert extract_features(v, tr).opening_type != "商品名"


def test_other_opening_types_still_use_the_transcript():
    assert classify_opening("知ってました？", title="ダイソー") == "問いかけ"  # earlier in priority order


# ---- channels loader -----------------------------------------------------------

def test_channels_missing_file_or_genre_returns_empty(tmp_path):
    assert load_channels(tmp_path / "nope.yaml", "g") == []
    p = tmp_path / "channels.yaml"
    p.write_text("genres:\n  other:\n    - id: UCabcdefghijklmnopqrstuv\n", encoding="utf-8")
    assert load_channels(p, "g") == []
    assert load_channels(p, "other")[0].id == "UCabcdefghijklmnopqrstuv"


def test_channels_invalid_id_is_rejected(tmp_path):
    p = tmp_path / "channels.yaml"
    p.write_text("genres:\n  g:\n    - id: '@handle'\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_channels(p, "g")


def test_example_channels_file_is_committed_and_private_is_ignored():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    assert (root / "config" / "channels.example.yaml").exists()
    assert "config/channels.yaml" in (root / ".gitignore").read_text()
