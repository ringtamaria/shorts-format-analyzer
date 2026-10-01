"""Supadata backend: ms->s, fixed native mode, async jobs, errors, credits."""
import io
import json
import urllib.error
import urllib.parse

import pytest

from sfa.quota import CreditTracker, QuotaExhausted
from sfa.store import Store
from sfa.transcript import TranscriptBlocked, TranscriptService, TranscriptUnavailable
from sfa.transcript import hosted as H


class Resp:
    def __init__(self, status, body):
        self.status, self._b = status, json.dumps(body).encode()
    def read(self):
        return self._b
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


class FakeOpener:
    """Scripted responses; records every requested URL."""
    def __init__(self, *responses):
        self.responses, self.urls = list(responses), []
    def __call__(self, req, timeout=None):
        self.urls.append(req.full_url)
        assert req.headers.get("X-api-key") == "k"
        status, body = self.responses.pop(0)
        if status >= 400:
            raise urllib.error.HTTPError(req.full_url, status, "err", {}, io.BytesIO(json.dumps(body).encode()))
        return Resp(status, body)


SEGMENTS = [
    {"text": "知ってました？", "offset": 0, "duration": 2500, "lang": "ja"},
    {"text": "まず卵を割ります", "offset": 2500, "duration": 3000, "lang": "ja"},
    {"text": "これで完成", "offset": 41250, "duration": 1750, "lang": "ja"},
]


def backend(tmp_path, opener, budget=100):
    credits = CreditTracker(tmp_path / "credits.json", budget)
    return H.HostedBackend("k", credits, opener=opener, sleep=lambda s: None), credits


# ---- ms -> seconds -----------------------------------------------------------

def test_ms_to_sec():
    assert H.ms_to_sec(0) == 0.0
    assert H.ms_to_sec(2500) == 2.5
    assert H.ms_to_sec(41250) == 41.25
    assert H.ms_to_sec(None) == 0.0


def test_segments_offset_and_duration_are_converted_to_seconds():
    segs = H.segments_from_supadata(SEGMENTS)
    assert [(s.start, s.duration) for s in segs] == [(0.0, 2.5), (2.5, 3.0), (41.25, 1.75)]
    assert segs[-1].end == 43.0
    # The opening window (3 s) must see the first segment only and part of the second.
    from sfa.transcript import Transcript
    tr = Transcript("v", "ja", segs)
    assert tr.text_between(0, 3.0) == "知ってました？ まず卵を割ります"
    assert "完成" not in tr.text_between(0, 3.0)


# ---- request shape -----------------------------------------------------------

def test_request_uses_native_mode_and_timestamps(tmp_path):
    op = FakeOpener((200, {"lang": "ja", "content": SEGMENTS}))
    be, credits = backend(tmp_path, op)
    tr = be.fetch("abc")
    q = urllib.parse.parse_qs(urllib.parse.urlparse(op.urls[0]).query)
    assert q["mode"] == ["native"] and q["text"] == ["false"] and q["lang"] == ["ja"]
    assert q["url"] == ["https://www.youtube.com/watch?v=abc"]
    assert tr.language == "ja" and len(tr.segments) == 3 and tr.segments[2].start == 41.25
    assert credits.used == 1


def test_mode_cannot_be_changed_by_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPADATA_MODE", "auto")
    monkeypatch.setenv("TRANSCRIPT_MODE", "generate")
    op = FakeOpener((200, {"content": SEGMENTS}))
    be, _ = backend(tmp_path, op)
    assert be.mode == "native"
    with pytest.raises(AttributeError):
        be.mode = "auto"  # read-only property
    be.fetch("abc")
    assert "mode=native" in op.urls[0]
    import inspect
    assert "mode" not in inspect.signature(H.HostedBackend.__init__).parameters


# ---- async jobs --------------------------------------------------------------

def test_async_job_is_polled_until_complete(tmp_path):
    op = FakeOpener((202, {"jobId": "j1"}), (200, {"status": "active"}), (200, {"status": "completed", "content": SEGMENTS}))
    be, credits = backend(tmp_path, op)
    tr = be.fetch("abc")
    assert len(tr.segments) == 3 and op.urls[1].endswith("/transcript/j1")
    assert credits.used == 1  # polling does not charge again


def test_async_job_that_never_finishes_does_not_crash_and_is_retryable(tmp_path):
    op = FakeOpener((202, {"jobId": "j1"}), *[(200, {"status": "queued"})] * 10)
    be, _ = backend(tmp_path, op)
    store = Store(tmp_path / "db.sqlite")
    svc = TranscriptService(be, store)
    assert svc.get_transcript("abc") is None          # no exception
    assert store.get_transcript_row("abc")["status"] == "error"  # retried next run
    assert "still running" in store.get_transcript_row("abc")["error"]


def test_async_job_failed_is_unavailable(tmp_path):
    op = FakeOpener((202, {"jobId": "j1"}), (200, {"status": "failed", "error": "no captions"}))
    be, _ = backend(tmp_path, op)
    with pytest.raises(TranscriptUnavailable):
        be.fetch("abc")


# ---- errors ------------------------------------------------------------------

@pytest.mark.parametrize("status", [401, 403, 402, 429])
def test_auth_rate_and_billing_errors_stop_the_run(tmp_path, status):
    be, _ = backend(tmp_path, FakeOpener((status, {"error": "x"})))
    with pytest.raises(TranscriptBlocked):
        be.fetch("abc")


def test_not_found_is_unavailable(tmp_path):
    be, _ = backend(tmp_path, FakeOpener((404, {"error": "transcript-unavailable"})))
    with pytest.raises(TranscriptUnavailable):
        be.fetch("abc")


def test_missing_key_is_a_clear_error(tmp_path):
    with pytest.raises(ValueError, match="SUPADATA_API_KEY"):
        H.HostedBackend("", CreditTracker(tmp_path / "c.json", 10))


# ---- language ----------------------------------------------------------------

def test_majority_language_by_characters():
    content = [{"text": "Hello there everyone", "lang": "en"}, {"text": "こんにちは", "lang": "ja"}]
    assert H.majority_lang(content) == "en"
    assert H.majority_lang([{"text": "卵", "lang": "ja-JP"}]) == "ja"
    assert H.majority_lang([{"text": "x"}], fallback="ja") == "ja"


# ---- credits -----------------------------------------------------------------

def test_credits_stop_before_request_when_exhausted(tmp_path):
    op = FakeOpener((200, {"content": SEGMENTS}))
    be, credits = backend(tmp_path, op, budget=1)
    be.fetch("a")
    with pytest.raises(QuotaExhausted):
        be.fetch("b")
    assert len(op.urls) == 1  # second call never hit the network
    assert "credits" in credits.status_line() and "re-run" in credits.resume_hint()


def test_credit_ledger_persists_and_resets_monthly(tmp_path):
    p = tmp_path / "c.json"
    c = CreditTracker(p, 100)
    c.reserve("transcript")
    c.reserve("transcript")
    assert CreditTracker(p, 100).used == 2
    p.write_text(json.dumps({"date": "2000-01", "used": 99, "calls": 99}))
    assert CreditTracker(p, 100).used == 0
