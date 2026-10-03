from datetime import date

from conftest import make_transcript, make_video
from sfa.features import extract_features
from sfa.formats import (classify_formats, common_phrases, discover_unclassified, fmt_position, fmt_range,
                         profile_group, quartiles)
from sfa.report import ASR_NOTE, ReportMeta, render_discovery, render_report
from sfa.transcript import Segment, Transcript

HYPE = ["やばいレシピ紹介しますまずは大量のニンニク", "炒めます", "味付けします", "盛ります", "これで完成", "保存してね"]
WARN = ["平日には食べないでください究極の", "大根を切ります", "煮ます", "盛ります", "完成です", "またね"]
OTHER = ["最近食べすぎちゃったな。", "今日はこれ", "切ります", "焼きます", "盛ります", "以上"]


def _feats(rules, n_hype=6, n_warn=3, n_other=6, n_silent=2, n_music=1, n_en=1):
    feats, i = [], 0
    for texts, dur, n in ((HYPE, 50, n_hype), (WARN, 58, n_warn), (OTHER, 40, n_other)):
        for _ in range(n):
            i += 1
            v = make_video(i, duration=dur + (i % 4), views=1000 * i)
            feats.append(extract_features(v, make_transcript(v.video_id, texts, seg_dur=dur / len(texts)), rules))
    for _ in range(n_silent):
        i += 1
        feats.append(extract_features(make_video(i, duration=20 + i % 3, views=500 * i), None, rules))
    for _ in range(n_music):
        i += 1
        v = make_video(i, duration=22)
        feats.append(extract_features(v, Transcript(v.video_id, "en", [Segment(0, 3, "[Music]")]), rules))
    for _ in range(n_en):
        i += 1
        v = make_video(i, duration=55)
        feats.append(extract_features(v, Transcript(v.video_id, "en", [Segment(0, 3, "They are eggs to die for, crack them now")]), rules))
    return feats


def test_quartiles_and_formatting():
    q = quartiles([20, 22, 27, 30, 34], 0)
    assert q["median"] == 27 and q["min"] == 20 and q["max"] == 34
    assert fmt_range(q, "秒") == f"{q['q1']:.0f}〜{q['q3']:.0f}秒（中央値27秒）"
    assert fmt_range(q, "秒", small=True) == "中央値27秒（参考値）"
    pres = {"share": 0.41, "label": "後半", "pos": {"q1": 0.59, "median": 0.7, "q3": 0.77, "n": 7}}
    assert fmt_position(pres) == "41%の動画にあり、尺の後半（59〜77%地点）"  # rate first
    assert fmt_position({"share": 0.0, "label": "なし", "pos": None}) == "0%の動画にあり"


def test_groups_follow_rules_order_then_silent_then_unclassified(recipe_rules):
    groups = classify_formats(_feats(recipe_rules), recipe_rules)
    assert [g.format_id for g in groups] == ["hype_declaration", "warning", "silent", "unclassified"]
    hype, warn, silent, un = groups
    assert hype.name == "煽り宣言型" and hype.size == 6 and not hype.small_sample
    assert warn.size == 3 and warn.small_sample
    assert silent.size == 3 and silent.profile["speech_kinds"] == {"no_transcript": 2, "silent": 1}
    assert un.size == 6
    # English video is excluded from every group and from the share denominator
    assert sum(g.size for g in groups) == 18 and abs(hype.share - 6 / 18) < 0.01
    assert all(len(g.examples) <= 3 for g in groups)


def test_one_line_has_length_and_speaking_speed(recipe_rules):
    hype = classify_formats(_feats(recipe_rules), recipe_rules)[0]
    assert hype.one_line.startswith("煽り語で入って宣言し") and "尺 " in hype.one_line and "文字/秒" in hype.one_line


def test_recipe_drops_lines_where_every_video_is_identical(recipe_rules):
    feats = _feats(recipe_rules, n_hype=6)
    for f in feats:
        f.duration_sec = 50  # identical length
    hype = classify_formats(feats, recipe_rules)[0]
    assert not any(step.startswith("尺:") for step in hype.recipe)
    assert any(step.startswith("完成の提示: 100%の動画にあり") for step in hype.recipe)
    assert any(step.startswith("大量投入: 100%") for step in hype.recipe)
    assert not any("話題転換" in step for step in hype.recipe)


def test_profile_has_no_topic_shift_axis(recipe_rules):
    p = profile_group([f for f in _feats(recipe_rules) if f.speech == "speech"], recipe_rules)
    assert not any("topic" in k for k in p)


def test_discovery_only_for_unclassified_and_only_above_threshold(recipe_rules):
    assert discover_unclassified(_feats(recipe_rules, n_other=4), recipe_rules) is None
    clusters = discover_unclassified(_feats(recipe_rules, n_other=6), recipe_rules)
    assert clusters and sum(c["size"] for c in clusters) == 6
    assert all(o["text"] for c in clusters for o in c["openings"])
    md = render_discovery("g", date(2026, 10, 2), clusters, 6)
    assert "納品物ではない" in md and md.count("最近食べすぎちゃったな。") == 6


def test_common_phrases_use_3_4_grams_and_document_frequency():
    texts = ["やばいレシピ紹介します", "禁断のレシピ紹介します", "唐揚げの作り方まずは", "今日はレシピ"]
    got = common_phrases(texts)
    grams = dict(got)
    assert all(3 <= len(g) <= 4 for g in grams)
    assert grams.get("レシピ") == 3             # in three openings
    assert grams.get("紹介しま") == 2 or grams.get("介します") == 2
    assert all(c >= 2 for c in grams.values())  # single-document grams dropped
    assert [c for _, c in got] == sorted((c for _, c in got), reverse=True)
    assert "リの" not in grams and "にな" not in grams


def _meta(**kw):
    base = dict(genre="レシピ 料理", report_date=date(2026, 10, 2), n_requested=50, n_collected=19,
                transcript_backend="hosted", quota_used=352, quota_budget=9700, n_other_lang=1)
    base.update(kw)
    return ReportMeta(**base)


def test_render_report(recipe_rules):
    feats = _feats(recipe_rules)
    groups = classify_formats(feats, recipe_rules)
    md = render_report(_meta(), feats, groups)
    assert "## フォーマット 1: 煽り宣言型" in md
    assert "## 少数の型" in md and "### 警告・禁止型（3 本" in md       # n=3 < 5 -> minor section
    assert "### 無音・テロップ型（3 本" in md                           # silent n=3 is minor too
    assert "## フォーマット 2:" not in md
    assert "| 少数 | 警告・禁止型 | 3 |" in md and "| 少数 | 無音・テロップ型 | 3 |" in md
    assert "字幕が取得できなかった動画" not in md                      # no longer a 'missing data' section
    assert "字幕が音楽・効果音の表記だけ（発話なしと確認）: 1 本" in md
    assert "字幕がない（発話の有無は未確認）: 2 本" in md and "目視確認" in md
    assert "## 未分類" in md and md.count("「最近食べすぎちゃったな。」") == 6  # every unclassified opening
    assert ASR_NOTE in md
    assert "| 完成の提示 | 100%の動画にあり、尺の" in md
    assert "話題転換" not in md
    assert "字幕が日本語以外だった 1 本" in md
    assert "サムネイル" in md and "解析もしていない" in md
    assert "掲載URLは納品前に目視確認すること" in md


# ---- round 5 -----------------------------------------------------------------

def test_spanning_band_label(recipe_rules):
    from sfa.formats import band_label
    q = {"q1": 0.47, "median": 0.7, "q3": 0.77}
    assert band_label(q, recipe_rules) == "中盤〜後半"
    pres = {"share": 0.86, "label": "中盤〜後半", "median_label": "後半", "pos": {**q, "n": 6}}
    assert fmt_position(pres) == "86%の動画にあり、尺の中盤〜後半（47〜77%地点）"
    assert fmt_position(pres, small=True) == "86%の動画にあり、尺の後半（70%地点、参考値）"
    assert band_label({"q1": 0.7, "median": 0.75, "q3": 0.8}, recipe_rules) == "後半"


def test_modifiers_are_multi_valued_and_profiled(recipe_rules):
    from sfa.rules import build_rules
    from conftest import RECIPE_RULES
    from sfa.features import set_rules
    rules = build_rules({**RECIPE_RULES, "modifiers": {
        "hype": {"name": "煽り", "patterns": ["やば", "禁断"]},
        "superlative": {"name": "最上級", "patterns": ["世界一", "最強"]}}})
    set_rules(rules)
    v = make_video(1)
    f = extract_features(v, make_transcript(v.video_id, ["やばい世界一のレシピ紹介します", "完成"]), rules)
    assert f.format_id == "hype_declaration" and sorted(f.modifiers) == ["hype", "superlative"]
    feats = [extract_features(make_video(i), make_transcript(f"vid{i:03d}", [t, "完成"]), rules)
             for i, t in enumerate(["やばいレシピ紹介します", "禁断のレシピ紹介します", "やばい最強レシピ作ります",
                                    "マジでうまいレシピ紹介します", "マジで最強を作ります"], 1)]
    p = profile_group(feats, rules)
    assert p["modifiers"] == {"煽り": 0.6, "最上級": 0.4}
    g = classify_formats(feats, rules)[0]
    assert any(s.startswith("冒頭の装飾: 煽り 60%") for s in g.recipe)
    md = render_report(_meta(n_collected=5, n_other_lang=0), feats, [g])
    assert "| 冒頭の装飾（複数付く） | 煽り 60%、最上級 40% |" in md


def test_rules_without_modifiers_still_work(recipe_rules):
    assert recipe_rules.modifiers == []
    f = extract_features(make_video(1), make_transcript("vid001", ["やばいレシピ紹介します", "完成"]), recipe_rules)
    assert f.modifiers == []
    assert profile_group([f], recipe_rules)["modifiers"] == {}


def test_opening_starts_at_first_real_utterance(recipe_rules):
    v = make_video(1, duration=30)
    tr = Transcript(v.video_id, "ja", [Segment(0, 2, "[音楽]"), Segment(2, 2.5, "[拍手]"),
                                       Segment(4.5, 2, "知ってる？これ"), Segment(6.5, 3, "まず切ります"),
                                       Segment(20, 3, "完成")])
    f = extract_features(v, tr, recipe_rules)
    assert f.speech_start_sec == 4.5
    assert f.opening_text.startswith("知ってる？") and "[音楽]" not in f.opening_text
    assert f.format_id == "question" and f.music_intro is True
    plain = extract_features(v, make_transcript(v.video_id, ["知ってる？", "まず切ります", "完成"]), recipe_rules)
    assert plain.music_intro is False and plain.speech_start_sec == 0.0


def test_music_inside_the_opening_window_counts(recipe_rules):
    v = make_video(2)
    tr = Transcript(v.video_id, "ja", [Segment(0, 2, "ヒ水1つおくましミ [音楽]"), Segment(2, 5, "まず切ります完成")])
    f = extract_features(v, tr, recipe_rules)
    assert f.music_intro is True and "[音楽]" not in f.opening_text


def test_transcript_source_breakdown_in_report(recipe_rules):
    feats = _feats(recipe_rules)
    for i, f in enumerate(feats):
        if f.speech in ("speech", "silent"):
            f.transcript_source = "local" if i < 4 else "hosted"
    md = render_report(_meta(), feats, classify_formats(feats, recipe_rules))
    assert "字幕取得手段（字幕が取れた動画）: `hosted`" in md and "`local` 4 本" in md
