"""Persistent YouTube Data API quota ledger.

The API gives 10,000 units/day by default and resets at midnight Pacific time.
Every call made through :mod:`sfa.youtube` must first ``reserve()`` its cost
here. When the budget would be exceeded the tracker raises
:class:`QuotaExhausted`, which callers treat as a *normal stop* (print the
remaining budget and how to resume), never as a crash.

Usage is written to a small JSON file after every call so that a crash or
Ctrl-C never loses the count. The file is keyed by the Pacific-time date so it
rolls over automatically at reset time.
"""
from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")
JST = timezone(timedelta(hours=9))

# Unit costs from the official quota calculator.
COST = {
    "search.list": 100,
    "videos.list": 1,
    "captions.list": 50,
    "channels.list": 1,
    "playlistItems.list": 1,
}


class QuotaExhausted(Exception):
    """Raised when a reservation would push usage over the daily budget.

    This is an expected, graceful stop condition. Scripts catch it, print
    :meth:`QuotaTracker.status_line` and exit 0.
    """

    def __init__(self, method: str, cost: int, tracker: "QuotaTracker"):
        self.method, self.cost, self.tracker = method, cost, tracker
        super().__init__(
            f"{method} needs {cost} units but only {tracker.remaining} remain "
            f"(used {tracker.used}/{tracker.budget}); resets {tracker.reset_time_jst():%Y-%m-%d %H:%M} JST"
        )


def pacific_date(now: datetime | None = None) -> str:
    now = now or datetime.now(tz=timezone.utc)
    return now.astimezone(PACIFIC).date().isoformat()


@dataclass
class QuotaTracker:
    path: Path
    budget: int
    _date: str = ""
    _used: int = 0
    _calls: int = 0

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._locked():
            self._load()

    # ---- locking ---------------------------------------------------------
    @property
    def lock_path(self) -> Path:
        return self.path.with_suffix(self.path.suffix + ".lock")

    @contextmanager
    def _locked(self):
        """Exclusive advisory lock so two processes never lose an increment."""
        with open(self.lock_path, "a+") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    # ---- persistence -----------------------------------------------------
    def _load(self) -> None:
        today = pacific_date()
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text())
            except (json.JSONDecodeError, OSError):
                data = {}
            if data.get("date") == today:
                self._date, self._used, self._calls = today, int(data.get("used", 0)), int(data.get("calls", 0))
                return
        self._date, self._used, self._calls = today, 0, 0
        self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"date": self._date, "used": self._used, "calls": self._calls, "budget": self.budget}))
        os.replace(tmp, self.path)

    def _rollover_if_needed(self) -> None:
        if pacific_date() != self._date:
            self._load()

    # ---- public API ------------------------------------------------------
    @property
    def used(self) -> int:
        self._rollover_if_needed()
        return self._used

    @property
    def remaining(self) -> int:
        return max(0, self.budget - self.used)

    def can_afford(self, method: str, n_calls: int = 1) -> bool:
        return COST[method] * n_calls <= self.remaining

    def reserve(self, method: str) -> int:
        """Charge ``method``'s cost. Raises :class:`QuotaExhausted` if it does not fit.

        Load -> add -> save happens under the file lock, so concurrent
        processes sharing the same quota file cannot lose an increment.
        """
        cost = COST[method]
        with self._locked():
            self._load()  # re-read: another process may have spent units
            if cost > max(0, self.budget - self._used):
                raise QuotaExhausted(method, cost, self)
            self._used += cost
            self._calls += 1
            self._save()
        return cost

    def reset_time_jst(self) -> datetime:
        now_pt = datetime.now(tz=PACIFIC)
        next_midnight = (now_pt + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return next_midnight.astimezone(JST)

    def status_line(self) -> str:
        return (
            f"[quota] used {self.used}/{self.budget} units, remaining {self.remaining} "
            f"(resets {self.reset_time_jst():%m/%d %H:%M} JST)"
        )

    def resume_hint(self) -> str:
        return (
            f"Daily quota budget reached. Fetched data is cached in SQLite, so re-run the same "
            f"command after {self.reset_time_jst():%m/%d %H:%M} JST to continue where it stopped."
        )
