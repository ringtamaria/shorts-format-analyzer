import pytest

from sfa.quota import QuotaExhausted, QuotaTracker
from sfa.youtube import Video, YouTubeClient, parse_iso8601_duration


@pytest.mark.parametrize("s,sec", [("PT59S", 59), ("PT1M5S", 65), ("PT2M", 120), ("PT1H", 3600), ("", 0), ("garbage", 0)])
def test_duration(s, sec):
    assert parse_iso8601_duration(s) == sec


def test_video_from_api_and_short_filter():
    item = {"id": "abc", "snippet": {"title": "t", "channelId": "c", "channelTitle": "ct", "publishedAt": "p"},
            "contentDetails": {"duration": "PT45S", "caption": "true"},
            "statistics": {"viewCount": "12", "likeCount": "3"}}
    v = Video.from_api(item)
    assert v.is_short and v.has_caption_flag and v.view_count == 12 and v.url.endswith("/shorts/abc")
    assert Video.from_dict(v.to_dict()) == v
    long = Video.from_api({**item, "contentDetails": {"duration": "PT4M"}})
    assert not long.is_short


def test_client_charges_quota_before_network(tmp_path, monkeypatch):
    q = QuotaTracker(tmp_path / "q.json", budget=50)
    c = YouTubeClient("k", q)
    called = []
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: called.append(1) or (_ for _ in ()).throw(AssertionError))
    with pytest.raises(QuotaExhausted):
        c.search_video_ids("x")
    assert called == []  # never touched the network
    assert q.used == 0


def test_client_requires_key(tmp_path):
    with pytest.raises(ValueError):
        YouTubeClient("", QuotaTracker(tmp_path / "q.json", budget=10))
