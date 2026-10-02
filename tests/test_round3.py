"""Round 3: brand from title, channels route, pre-exclusion, language filter, credits in build_report."""
import json

import pytest

from sfa.channels import load_channels


# ---- brand from title: see test_features.py (brand is a separate feature now) ----


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
