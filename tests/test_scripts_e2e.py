"""End-to-end runs of the scripts against a fake YouTube API (no network)."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from sfa import youtube  # noqa: E402


def _item(i: int, dur: str = "PT40S") -> dict:
    return {"id": f"v{i:03d}", "snippet": {"title": f"5分レシピ{i}", "channelId": "c", "channelTitle": "c", "publishedAt": "p"},
            "contentDetails": {"duration": dur, "caption": "true" if i % 2 else "false"},
            "statistics": {"viewCount": str(1000 * i)}}


class FakeAPI:
    """Replaces YouTubeClient._get. Still charges quota through the real tracker."""

    def __init__(self, n_pages: int = 3, per_page: int = 50):
        self.n_pages, self.per_page, self.calls = n_pages, per_page, []

    def __call__(self, client, method, params):
        client.quota.reserve(method)
        self.calls.append(method)
        if method == "search.list":
            page = int(params.get("pageToken") or 0)
            ids = [f"v{page * self.per_page + j:03d}" for j in range(self.per_page)]
            out = {"items": [{"id": {"videoId": i}} for i in ids]}
            if page + 1 < self.n_pages:
                out["nextPageToken"] = str(page + 1)
            return out
        if method == "videos.list":
            ids = params["id"].split(",")
            return {"items": [_item(int(i[1:]), "PT5M" if int(i[1:]) % 10 == 0 else "PT40S") for i in ids]}
        if method == "captions.list":
            vid = int(params["videoId"][1:])
            return {"items": [{"snippet": {"language": "ja", "trackKind": "asr"}}] if vid % 3 else []}
        raise AssertionError(method)


@pytest.fixture
def fake_api(monkeypatch):
    api = FakeAPI()
    monkeypatch.setattr(youtube.YouTubeClient, "_get", lambda self, m, p: api(self, m, p))
    return api


def test_check_captions_runs_and_prints_verdict(env_tmp, fake_api, capsys):
    import check_captions
    rc = check_captions.main(["--genres", "レシピ 料理", "--sample", "6"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "| レシピ 料理 |" in out and any(v in out for v in ("GO", "CAUTION", "NG"))
    assert fake_api.calls.count("search.list") == 1 and fake_api.calls.count("captions.list") == 6
    # Second run is served from cache: no new API calls.
    n = len(fake_api.calls)
    assert check_captions.main(["--genres", "レシピ 料理", "--sample", "6"]) == 0
    assert len(fake_api.calls) == n


def test_build_report_writes_markdown_with_null_backend(env_tmp, fake_api, capsys):
    import build_report
    rc = build_report.main(["--genre", "レシピ 料理", "--n", "100", "--no-llm"])
    out = capsys.readouterr().out
    assert rc == 0, out
    reports = list((env_tmp / "out").glob("report_レシピ_料理_*.md"))
    assert len(reports) == 1
    md = reports[0].read_text()
    assert "字幕が1本も取得できなかった" in md  # null backend => metadata-only, no crash
    assert "YouTube Data API 使用量" in md
    feats = json.loads(reports[0].with_suffix(".features.json").read_text())
    assert len(feats["features"]) == 100
    # 100 shorts needed 3 search pages (10% of items are >3min and filtered out)
    assert fake_api.calls.count("search.list") == 3


def test_build_report_stops_gracefully_on_quota(env_tmp, fake_api, capsys, monkeypatch):
    monkeypatch.setenv("YOUTUBE_DAILY_QUOTA", "450")  # budget 150: one search page + videos.list, then stop
    import build_report
    rc = build_report.main(["--genre", "レシピ 料理", "--n", "100", "--no-llm"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "[stop]" in out and "re-run" in out
    md = next((env_tmp / "out").glob("report_*.md")).read_text()
    assert "上限に達した" in md  # partial report flagged


def test_build_report_zero_budget_exits_zero(env_tmp, fake_api, capsys, monkeypatch):
    monkeypatch.setenv("YOUTUBE_DAILY_QUOTA", "300")  # budget 0
    import build_report
    assert build_report.main(["--genre", "x", "--n", "10", "--no-llm"]) == 0
    assert "[stop]" in capsys.readouterr().out
