from datetime import date

from conftest import make_transcript, make_video
from sfa.features import extract_features
from sfa.formats import extract_formats, rule_based_name, profile_cluster
from sfa.report import ReportMeta, render_report

QUESTION_OPEN = ["知ってました？これ実は逆です", "まず材料を切ります", "次に炒めます", "味付けします", "これで完成", "保存してね"]
NUMBER_OPEN = ["3分で作れる卵料理", "卵を割ります", "レンジで2分", "チーズをのせる", "完成です", "フォローしてね"]


def _feats(n_each: int = 6):
    feats = []
    i = 0
    for texts, dur in ((QUESTION_OPEN, 30), (NUMBER_OPEN, 45)):
        for _ in range(n_each):
            i += 1
            v = make_video(i, duration=dur + (i % 3), views=1000 * i)
            feats.append(extract_features(v, make_transcript(v.video_id, texts, seg_dur=dur / len(texts))))
    feats.append(extract_features(make_video(99, duration=20), None))  # no transcript
    return feats


def test_extract_formats_rule_based_two_groups():
    clusters, without, err = extract_formats(_feats(), use_llm=False, k=2)
    assert err is None and len(without) == 1
    assert len(clusters) == 2
    assert {c.profile["opening_type"] and next(iter(c.profile["opening_type"])) for c in clusters} == {"問いかけ", "数値提示"}
    for c in clusters:
        assert c.name.endswith("型") and c.naming_source == "rule"
        assert 3 <= len(c.recipe) <= 8 and len(c.examples) == 3
        assert all(e["url"].startswith("https://www.youtube.com/shorts/") for e in c.examples)


def test_extract_formats_llm_failure_falls_back(monkeypatch):
    import sfa.formats as fm
    def boom(*a, **k):
        raise RuntimeError("no network")
    monkeypatch.setattr(fm, "llm_name_clusters", boom)
    clusters, _, err = extract_formats(_feats(), use_llm=True, llm_model="claude-opus-5", k=2)
    assert "no network" in err and all(c.naming_source == "rule" for c in clusters)


def test_extract_formats_no_transcripts():
    feats = [extract_features(make_video(i), None) for i in range(5)]
    clusters, without, err = extract_formats(feats, use_llm=False)
    assert clusters == [] and len(without) == 5


def test_rule_based_name_has_no_abstract_adjectives():
    feats = _feats()
    name, one_line, recipe = rule_based_name(profile_cluster([f for f in feats if f.has_transcript]))
    text = " ".join([name, one_line, *recipe])
    for bad in ("インパクト", "テンポ", "魅力的", "面白い"):
        assert bad not in text
    assert "秒" in text


def test_render_report_all_paths():
    feats = _feats()
    clusters, without, _ = extract_formats(feats, use_llm=False, k=2)
    meta = ReportMeta(genre="レシピ 料理", report_date=date(2026, 9, 26), n_requested=100, n_videos=len(feats),
                      n_with_transcript=len(feats) - 1, transcript_backend="null", quota_used=203, quota_budget=9700,
                      partial=True, llm_error="RuntimeError: x", naming_model="claude-opus-5")
    md = render_report(meta, feats, clusters, without)
    assert md.startswith("# YouTube Shorts 構成フォーマット分析: レシピ 料理")
    assert "上限に達した" in md and "フォーマット 1:" in md and "字幕が取得できなかった動画" in md
    assert md.count("https://www.youtube.com/shorts/") >= 6
    # Zero-transcript variant
    md0 = render_report(ReportMeta("g", date.today(), 10, 3, 0, "null", 0, 9700), [f for f in feats if not f.has_transcript], [], without)
    assert "字幕が1本も取得できなかった" in md0
