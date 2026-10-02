from conftest import make_transcript, make_video
from sfa.features import (classify_opening, classify_speech, extract_features, find_brand, position_label,
                          speech_chars, title_for_matching)
from sfa.rules import SILENT_ID, UNCLASSIFIED_ID
from sfa.transcript import Segment, Transcript


def test_opening_type_from_rules(recipe_rules):
    assert classify_opening("やばいレシピ紹介しますまずは大量の", recipe_rules) == "hype_declaration"
    assert classify_opening("平日には食べないでください", recipe_rules) == "warning"
    assert classify_opening("最近食べすぎちゃったな。", recipe_rules) is None
    assert classify_opening("", recipe_rules) is None


def test_three_way_speech_classification(recipe_rules):
    music = Transcript("v", "en", [Segment(0, 3, "[Music]"), Segment(9, 4, "[Applause]"), Segment(11, 3, "bre")])
    thai = Transcript("v", "th", [Segment(14, 3, " 온 ")])
    english = Transcript("v", "en", [Segment(0, 3, "They are eggs to die for. Crack them into the pan now.")])
    japanese = make_transcript("v", ["まず卵を割ります", "完成"])
    assert classify_speech(music, "ja", recipe_rules) == "silent"
    assert classify_speech(thai, "ja", recipe_rules) == "silent"
    assert classify_speech(english, "ja", recipe_rules) == "other_lang"
    assert classify_speech(japanese, "ja", recipe_rules) == "speech"
    assert classify_speech(None, "ja", recipe_rules) == "no_transcript"
    assert speech_chars("[Music] ♪ (拍手) あ") == 1


def test_features_for_speech_video(recipe_rules):
    v = make_video(1, duration=30, title="ダイソーの道具で 3分 #shorts")
    tr = make_transcript("vid001", [
        "やばいレシピ紹介しますまずは大量のニンニク",
        "フライパンで炒めます", "醤油を入れます", "知ってる？ここがコツ",
        "盛り付けます", "これで完成です", "保存してね", "またね",
    ], seg_dur=3.75)
    f = extract_features(v, tr, recipe_rules)
    assert f.speech == "speech" and f.format_id == "hype_declaration"
    assert f.bulk_input is True and f.brand == "ダイソー"
    assert position_label(f.completion_rel, recipe_rules) == "中盤"  # 6th of 8 segments = 62.5%
    assert position_label(f.question_rel, recipe_rules) == "前半" or position_label(f.question_rel, recipe_rules) == "中盤"
    assert f.cta_rel is not None and f.speech_density > 0
    assert f.thumbnail_url == "https://i.ytimg.com/vi/vid001/hqdefault.jpg"


def test_unmatched_speech_is_unclassified_and_silent_goes_to_silent_type(recipe_rules):
    v = make_video(2)
    assert extract_features(v, make_transcript("vid002", ["最近食べすぎちゃったな。", "完成"]), recipe_rules).format_id == UNCLASSIFIED_ID
    assert extract_features(v, None, recipe_rules).format_id == SILENT_ID
    music = Transcript("vid002", "en", [Segment(0, 3, "[Music]")])
    f = extract_features(v, music, recipe_rules)
    assert f.speech == "silent" and f.format_id == SILENT_ID


def test_brand_is_matched_on_title_only(recipe_rules):
    v = make_video(3, title="いつもの晩ごはん")
    tr = make_transcript("vid003", ["ダイソーの", "まず切ります", "完成"])
    assert extract_features(v, tr, recipe_rules).brand is None          # brand only in ASR -> ignored
    assert find_brand("簡単レシピ #shorts #ダイソー", recipe_rules) is None  # hashtag only -> ignored
    assert find_brand("無印のお鍋で", recipe_rules) == "無印"
    assert title_for_matching("簡単 #shorts #cooking") == "簡単"


def test_position_bands(recipe_rules):
    assert [position_label(x, recipe_rules) for x in (0.0, 0.33, 0.335, 0.34, 0.66, 0.67, 1.0)] == \
        ["前半", "前半", "前半", "中盤", "中盤", "後半", "後半"]
    assert position_label(None, recipe_rules) == "なし"
