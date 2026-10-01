"""Transcript caching rules: blocks are never cached, transient errors are retried."""
import pytest

from conftest import make_transcript
from sfa.store import Store
from sfa.transcript import TranscriptBlocked, TranscriptService, TranscriptUnavailable


class FakeBackend:
    name = "local"

    def __init__(self, script):
        self.script = list(script)  # per call: "ok" | "unavailable" | "blocked" | "error"
        self.calls = []

    def fetch(self, video_id):
        self.calls.append(video_id)
        what = self.script.pop(0)
        if what == "ok":
            return make_transcript(video_id, ["こんにちは"])
        if what == "unavailable":
            raise TranscriptUnavailable("TranscriptsDisabled")
        if what == "blocked":
            raise TranscriptBlocked("IpBlocked")
        raise ConnectionError("transient")


def test_block_propagates_and_writes_no_row(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    svc = TranscriptService(FakeBackend(["blocked"]), store)
    with pytest.raises(TranscriptBlocked):
        svc.get_transcript("v1")
    assert store.get_transcript_row("v1") is None
    assert svc.stats["blocked"] == 1


def test_error_rows_are_retried_and_final_rows_are_not(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.put_transcript("v_err", "local", "error", error="RuntimeError: IpBlocked earlier in this run")
    store.put_transcript("v_na", "local", "unavailable", error="TranscriptsDisabled")
    be = FakeBackend(["ok"])
    svc = TranscriptService(be, store)
    assert svc.get_transcript("v_err") is not None      # retried
    assert svc.get_transcript("v_na") is None            # final, not retried
    assert be.calls == ["v_err"]
    assert store.get_transcript_row("v_err")["status"] == "ok"


def test_transient_error_is_recorded_but_retryable(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    be = FakeBackend(["error", "ok"])
    svc = TranscriptService(be, store)
    assert svc.get_transcript("v1") is None
    assert store.get_transcript_row("v1")["status"] == "error"
    assert svc.get_transcript("v1") is not None
    assert be.calls == ["v1", "v1"]
