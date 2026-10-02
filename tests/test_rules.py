"""Rules loader: taxonomy grammar, priority, fallback to the example file."""
from pathlib import Path

import pytest
import yaml

from sfa import rules as R
from sfa.rules import build_rules, compile_match


def test_private_rules_absent_falls_back_to_example(monkeypatch, tmp_path):
    monkeypatch.setattr(R, "RULES_PATH", tmp_path / "does-not-exist.yaml")
    r = R.load_rules()
    assert r.source == R.EXAMPLE_PATH
    assert [t.id for t in r.opening_types] == ["hype_declaration", "warning"]
    assert r.min_type_size == 5 and r.completion is not None and r.brands == ["無印", "ダイソー"]


def test_private_rules_preferred_when_present(monkeypatch, tmp_path):
    private = tmp_path / "rules.yaml"
    private.write_text(yaml.safe_dump({"opening_types": [{"id": "x", "name": "X型", "match": "テスト"}]},
                                      allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(R, "RULES_PATH", private)
    r = R.load_rules()
    assert r.source == private and [t.name for t in r.opening_types] == ["X型"]


@pytest.mark.parametrize("spec,text,expected", [
    ("やばい", "やばいレシピ", True),
    (["禁断", "やばい"], "禁断の", True),
    ({"any": ["a", "b"]}, "xbx", True),
    ({"all": ["a", "b"]}, "xax", False),
    ({"all": [{"any": ["やばい", "禁断"]}, {"any": ["紹介します", "作ります"]}]}, "禁断のレシピ紹介します", True),
    ({"all": [{"any": ["やばい", "禁断"]}, {"any": ["紹介します", "作ります"]}]}, "禁断のレシピ", False),
    ({"all": ["レシピ", {"not": "広告"}]}, "レシピ広告", False),
    (r"\d+\s*(キロ|kg)", "ラード2 キロ", True),
])
def test_match_grammar(spec, text, expected):
    assert compile_match(spec)(text) is expected


@pytest.mark.parametrize("bad", [{"some": ["x"]}, {}, 3, "("])
def test_match_grammar_rejects_bad_specs(bad):
    with pytest.raises(ValueError):
        compile_match(bad)


def test_order_is_priority():
    """'作ったらあかん' + '紹介します' hits both; the first listed wins."""
    hype = {"id": "hype", "name": "煽り宣言型", "match": {"all": ["危な", "紹介します"]}}
    warn = {"id": "warn", "name": "警告・禁止型", "match": "作ったらあかん"}
    text = "今日は絶対に作ったらあかん危なすぎるやつ紹介します"
    from sfa.features import classify_opening
    assert classify_opening(text, build_rules({"opening_types": [hype, warn]})) == "hype"
    assert classify_opening(text, build_rules({"opening_types": [warn, hype]})) == "warn"


def test_reserved_and_duplicate_ids_are_rejected():
    with pytest.raises(ValueError):
        build_rules({"opening_types": [{"id": "silent", "match": "x"}]})
    with pytest.raises(ValueError):
        build_rules({"opening_types": [{"id": "a", "match": "x"}, {"id": "a", "match": "y"}]})


def test_example_file_is_committed_and_private_is_ignored():
    root = Path(__file__).resolve().parents[1]
    assert (root / "config" / "rules.example.yaml").exists()
    assert "config/rules.yaml" in (root / ".gitignore").read_text()


def test_example_has_no_generic_ascii_brand_rule():
    text = R.EXAMPLE_PATH.read_text(encoding="utf-8")
    assert "A-Za-z" not in text  # #shorts / ASMR / vlog would match a generic latin-letters rule
