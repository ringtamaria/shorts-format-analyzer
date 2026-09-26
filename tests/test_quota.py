import json

import pytest

from sfa.quota import COST, QuotaExhausted, QuotaTracker


def test_reserve_and_persist(tmp_path):
    p = tmp_path / "q.json"
    q = QuotaTracker(p, budget=250)
    assert q.reserve("search.list") == 100
    q.reserve("videos.list")
    assert q.used == 101 and q.remaining == 149
    # A fresh tracker on the same file sees the same usage.
    q2 = QuotaTracker(p, budget=250)
    assert q2.used == 101


def test_exhaustion_is_graceful_exception(tmp_path):
    q = QuotaTracker(tmp_path / "q.json", budget=150)
    q.reserve("search.list")
    assert not q.can_afford("search.list")
    assert q.can_afford("videos.list", 50)
    with pytest.raises(QuotaExhausted) as ei:
        q.reserve("search.list")
    assert q.used == 100  # failed reservation does not charge
    assert "resets" in str(ei.value)
    assert "re-run" in q.resume_hint()


def test_rollover_on_new_pacific_date(tmp_path):
    p = tmp_path / "q.json"
    p.write_text(json.dumps({"date": "2000-01-01", "used": 9000, "calls": 3}))
    q = QuotaTracker(p, budget=9700)
    assert q.used == 0


def test_costs_match_official_table():
    assert COST["search.list"] == 100 and COST["videos.list"] == 1 and COST["captions.list"] == 50


def _spend(args):
    path, n = args
    from sfa.quota import QuotaTracker
    q = QuotaTracker(path, budget=10_000)
    for _ in range(n):
        q.reserve("videos.list")
    return q.used


def test_concurrent_processes_do_not_lose_increments(tmp_path):
    import multiprocessing as mp
    p = tmp_path / "q.json"
    QuotaTracker(p, budget=10_000)
    ctx = mp.get_context("spawn")
    with ctx.Pool(4) as pool:
        pool.map(_spend, [(str(p), 50)] * 4)
    assert QuotaTracker(p, budget=10_000).used == 200


def test_exhaustion_check_uses_fresh_file_state(tmp_path):
    p = tmp_path / "q.json"
    a = QuotaTracker(p, budget=150)
    b = QuotaTracker(p, budget=150)
    a.reserve("search.list")
    with pytest.raises(QuotaExhausted):  # b's in-memory view was 0, file says 100
        b.reserve("search.list")
