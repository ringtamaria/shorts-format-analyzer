"""Rules loader: works without the private rules.yaml, and a custom file overrides behaviour."""
from pathlib import Path

import yaml

from sfa import rules as R
from sfa.features import classify_opening, classify_title, get_rules, set_rules


def test_private_rules_absent_falls_back_to_example(monkeypatch, tmp_path):
    monkeypatch.setattr(R, "RULES_PATH", tmp_path / "does-not-exist.yaml")
    r = R.load_rules()
    assert r.source == R.EXAMPLE_PATH
    assert r.opening_order[0] == "問いかけ" and "商品名" in r.opening_patterns
    assert r.topic_shift_jaccard == 0.12 and r.min_cluster_for_ranges == 5


def test_private_rules_preferred_when_present(monkeypatch, tmp_path):
    private = tmp_path / "rules.yaml"
    private.write_text(yaml.safe_dump({"opening": {"order": ["問いかけ", "商品名"], "patterns": {"問いかけ": "[？?]"}},
                                       "brands": ["デパコスX"], "thresholds": {"topic_shift_jaccard": 0.5}},
                                      allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(R, "RULES_PATH", private)
    r = R.load_rules()
    assert r.source == private and r.topic_shift_jaccard == 0.5
    assert classify_opening("デパコスXの新作", r) == "商品名"
    assert classify_opening("3分でできる", r) == "その他"  # 数値提示 not defined in this rule set


def test_order_is_priority():
    base = yaml.safe_load(R.EXAMPLE_PATH.read_text(encoding="utf-8"))
    base["opening"]["order"] = ["数値提示", "問いかけ"]
    r = R.build_rules(base)
    assert classify_opening("3つ知ってました？", r) == "数値提示"
    r2 = R.build_rules({**base, "opening": {**base["opening"], "order": ["問いかけ", "数値提示"]}})
    assert classify_opening("3つ知ってました？", r2) == "問いかけ"


def test_example_file_is_committed_and_private_is_ignored():
    root = Path(__file__).resolve().parents[1]
    assert (root / "config" / "rules.example.yaml").exists()
    assert "config/rules.yaml" in (root / ".gitignore").read_text()


def test_set_rules_override_and_reset():
    custom = R.build_rules({"title": {"order": ["X型"], "patterns": {"X型": "X"}}})
    set_rules(custom)
    try:
        assert classify_title("X") == "X型" and classify_title("3分") == "その他"
    finally:
        set_rules(None)
    assert classify_title("3分") == "数値型"
    assert get_rules().source is not None
