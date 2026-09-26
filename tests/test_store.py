import time

from conftest import make_video
from sfa.store import Store


def test_video_roundtrip_and_ttl(tmp_path):
    s = Store(tmp_path / "db.sqlite", ttl_sec=3600)
    v = make_video(1)
    s.put_videos([v], genre="g")
    assert s.get_video("vid001") == v
    assert s.videos_for_genre("g") == [v]
    # Expire by forcing a tiny TTL.
    s.ttl = 0
    time.sleep(0.01)
    assert s.get_video("vid001") is None
    assert s.get_video("vid001", ignore_ttl=True) == v


def test_search_cache_key_and_transcripts(tmp_path):
    s = Store(tmp_path / "db.sqlite")
    k = s.search_key("レシピ", "viewCount", None, None)
    assert s.get_search(k) is None
    s.put_search(k, ["a", "b"], "tok")
    assert s.get_search(k) == (["a", "b"], "tok")
    s.put_transcript("a", "local", "ok", language="ja", segments=[{"start": 0, "duration": 1, "text": "x"}])
    s.put_transcript("b", "local", "unavailable", error="NoTranscriptFound")
    assert s.get_transcript_row("a")["status"] == "ok"
    assert s.get_transcript_row("b")["error"] == "NoTranscriptFound"
    s.put_caption_check("a", [{"language": "ja", "trackKind": "asr", "name": ""}])
    assert s.get_caption_check("a")[0]["trackKind"] == "asr"
    assert s.counts()["videos"] == 0
