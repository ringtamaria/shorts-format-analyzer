from datetime import date

from conftest import make_transcript, make_video
from sfa.features import extract_features
from sfa.formats import extract_formats, fmt_position, fmt_range, profile_cluster, quartiles, rule_based_name
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


def test_quartiles_and_formatting():
    q = quartiles([20, 22, 27, 30, 34], 0)
    assert q["median"] == 27 and q["q1"] < q["median"] < q["q3"]
    assert fmt_range(q, "秒") == f"{q['q1']:.0f}〜{q['q3']:.0f}秒（中央値27秒）"
    assert fmt_range(q, "秒", small=True) == "中央値27秒（参考値）"
    assert fmt_range(None) == "なし"
    pres = {"label": "後半", "pos": {"q1": 0.65, "median": 0.71, "q3": 0.80, "n": 5}}
    assert fmt_position(pres) == "後半（65〜80%地点）"
    assert fmt_position(pres, small=True) == "後半（71%地点、参考値）"
    assert quartiles([5], 0) == {"q1": 5, "median": 5, "q3": 5, "n": 1}
    assert fmt_range(quartiles([5, 5, 5], 0), "秒") == "5秒（全本ほぼ同値）"
    assert fmt_position({"label": "前半", "pos": {"q1": 0.0, "median": 0.0, "q3": 0.0, "n": 3}}) == "前半（0%地点）"


def test_colliding_rule_names_are_disambiguated_by_duration():
    feats = []
    for i in range(1, 7):
        v = make_video(i, duration=20, views=i)
        feats.append(extract_features(v, make_transcript(v.video_id, QUESTION_OPEN, seg_dur=20 / 6)))
    for i in range(7, 13):
        v = make_video(i, duration=55, views=i)
        feats.append(extract_features(v, make_transcript(v.video_id, QUESTION_OPEN, seg_dur=55 / 6)))
    clusters, _, _ = extract_formats(feats, use_llm=False, k=2)
    names = [c.name for c in clusters]
    assert len(set(names)) == 2 and all("秒前後" in n for n in names)


def test_profile_uses_ranges_and_flags_small_samples():
    feats = [f for f in _feats(6) if f.has_transcript]
    p = profile_cluster(feats)
    assert p["n"] == 12 and p["small_sample"] is False
    assert set(p["duration"]) == {"q1", "median", "q3", "n"}
    assert p["conclusion"]["label"] in ("前半", "中盤", "後半")
    assert p["conclusion"]["pos"]["q1"] <= p["conclusion"]["pos"]["median"] <= p["conclusion"]["pos"]["q3"]
    assert p["cta"]["label"] == "後半" and p["cta"]["share"] == 1.0
    small = profile_cluster(feats[:3])
    assert small["small_sample"] is True


def test_extract_formats_rule_based_two_groups():
    clusters, without, err = extract_formats(_feats(), use_llm=False, k=2)
    assert err is None and len(without) == 1
    assert len(clusters) == 2
    assert {next(iter(c.profile["opening_type"])) for c in clusters} == {"問いかけ", "数値提示"}
    for c in clusters:
        assert c.name.endswith("型") and c.naming_source == "rule"
        assert 3 <= len(c.recipe) <= 8 and len(c.examples) == 3
        assert all(e["url"].startswith("https://www.youtube.com/shorts/") for e in c.examples)
        assert "〜" in c.one_line  # range, not a point


def test_extract_formats_llm_failure_falls_back(monkeypatch):
    import sfa.formats as fm
    def boom(*a, **k):
        raise RuntimeError("no network")
    monkeypatch.setattr(fm, "llm_name_clusters", boom)
    clusters, _, err = extract_formats(_feats(), use_llm=True, llm_model="claude-sonnet-5", k=2)
    assert "no network" in err and all(c.naming_source == "rule" for c in clusters)


def test_extract_formats_no_transcripts():
    feats = [extract_features(make_video(i), None) for i in range(5)]
    clusters, without, err = extract_formats(feats, use_llm=False)
    assert clusters == [] and len(without) == 5


def test_rule_based_name_has_no_abstract_adjectives_and_uses_ranges():
    feats = _feats()
    name, one_line, recipe = rule_based_name(profile_cluster([f for f in feats if f.has_transcript]))
    text = " ".join([name, one_line, *recipe])
    for bad in ("インパクト", "テンポ", "魅力的", "面白い"):
        assert bad not in text
    assert "秒" in text and "〜" in text and "%地点" in text


def test_small_cluster_gets_reference_note_not_ranges():
    feats = _feats(2)  # 4 with transcripts -> single cluster of 4 (< 5)
    clusters, without, _ = extract_formats(feats, use_llm=False)
    assert len(clusters) == 1 and clusters[0].small_sample
    md = render_report(ReportMeta("g", date.today(), 10, len(feats), 4, "null", 0, 9700), feats, clusters, without)
    assert "n=4 のため傾向の参考値" in md
    assert "参考値" in clusters[0].one_line and "〜" not in clusters[0].one_line.split("尺は")[1]


def test_render_report_all_paths():
    feats = _feats(10)  # 20 with transcripts, 10 per cluster -> "他 7 本"
    clusters, without, _ = extract_formats(feats, use_llm=False, k=2)
    meta = ReportMeta(genre="レシピ 料理", report_date=date(2026, 9, 26), n_requested=100, n_videos=len(feats),
                      n_with_transcript=len(feats) - 1, transcript_backend="null", quota_used=203, quota_budget=9700,
                      partial=True, llm_error="RuntimeError: x", naming_model="claude-sonnet-5")
    md = render_report(meta, feats, clusters, without)
    assert md.startswith("# YouTube Shorts 構成フォーマット分析: レシピ 料理")
    assert "上限に達した" in md and "フォーマット 1:" in md and "字幕が取得できなかった動画" in md
    assert "目視確認" in md
    assert "%地点）" in md and "（Q1〜Q3 の範囲" in md
    # URL limit: 3 per cluster + up to 3 in the no-transcript section
    assert md.count("https://www.youtube.com/shorts/") == 3 * len(clusters) + 1
    assert "- 他 7 本" in md
    # Zero-transcript variant
    md0 = render_report(ReportMeta("g", date.today(), 10, 3, 0, "null", 0, 9700), [f for f in feats if not f.has_transcript], [], without)
    assert "字幕が1本も取得できなかった" in md0
