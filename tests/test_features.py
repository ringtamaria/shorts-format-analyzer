from conftest import make_transcript, make_video
from sfa.features import classify_opening, classify_title, extract_features, topic_shifts


def test_opening_classification():
    assert classify_opening("知ってました？実は簡単です") == "問いかけ"
    assert classify_opening("これ絶対にやめて") == "否定形"
    assert classify_opening("3分でできる卵料理") == "数値提示"
    assert classify_opening("結論から言うとこれが正解") == "結論先出し"
    assert classify_opening("ダイソーの新商品") == "商品名"
    assert classify_opening("料理が苦手な人") == "呼びかけ"
    assert classify_opening("") == "その他"


def test_title_classification():
    assert classify_title("5分で完成！時短レシピ") == "数値型"
    assert classify_title("なんで誰も教えてくれないの？") == "疑問型"
    assert classify_title("絶対にやってはいけない味付け") == "否定型"
    assert classify_title("最強の卵かけご飯") == "断定型"


def test_extract_features_with_transcript():
    v = make_video(1, duration=30)
    tr = make_transcript("vid001", [
        "知ってました？卵は冷蔵庫から出してすぐ使わない",   # 0-3s question
        "まず卵を常温に戻します",
        "次にフライパンを温めて油をひきます",
        "バターを入れて溶かします",
        "卵を流し込んでゆっくり混ぜます",
        "火を止めて余熱で仕上げます",
        "これで完成です",                                   # conclusion late
        "レシピは保存して作ってみてね",                      # CTA
    ], seg_dur=3.75)
    f = extract_features(v, tr)
    assert f.has_transcript and f.opening_type == "問いかけ"
    assert f.question_pos == "前半"
    assert f.conclusion_pos == "後半"
    assert f.cta_pos == "後半"
    assert f.speech_density > 0
    assert len(f.vector()) == len(f.vector_names())


def test_extract_features_without_transcript():
    f = extract_features(make_video(2), None)
    assert not f.has_transcript and f.conclusion_pos == "なし" and f.speech_density == 0.0


def test_topic_shifts_detects_vocabulary_change():
    same = make_transcript("x", ["卵を割ってかき混ぜる"] * 8, seg_dur=5)
    changed = make_transcript("y", ["卵を割ってかき混ぜる", "卵を割ってかき混ぜる", "レンジで温めてチーズをのせる",
                                    "レンジで温めてチーズをのせる", "盛り付けてパセリを散らす", "盛り付けてパセリを散らす"], seg_dur=5)
    assert topic_shifts(same) == []
    assert len(topic_shifts(changed)) >= 2
