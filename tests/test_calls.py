"""Integrity rules for the calls ledger.

A track record is only worth showing if it can't manufacture hits. These
tests exist because an earlier version resolved a call on an already-booked
artist as a 0-day-lead hit.
"""

from datetime import date, timedelta

import pytest

from scene_radar import calls as calls_mod


@pytest.fixture(autouse=True)
def isolated_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(calls_mod, "CALLS_FILE", tmp_path / "calls.json")


def _call(made_days_ago: int, bookings_at_call: int, horizon: int = 120, **kw) -> dict:
    made = date.today() - timedelta(days=made_days_ago)
    base = {
        "id": "call-001", "madeOn": made.isoformat(), "kind": "artist",
        "subject": "Test Artist", "subjectNorm": "test artist",
        "claim": "gets booked", "rationale": "", "horizonDays": horizon,
        "status": "open", "resolvedOn": None, "resolution": None, "leadDays": None,
        "evidence": {"bookingsAtCall": bookings_at_call},
    }
    base.update(kw)
    return base


class FakeCon:
    """Stands in for DuckDB: maps artist_norm -> first snapshot with a booking."""

    def __init__(self, first_booked: dict):
        self._rows = list(first_booked.items())

    def execute(self, *_args, **_kw):
        return self

    def fetchall(self):
        return self._rows


def test_hit_when_booking_appears_after_the_call():
    calls_mod.save([_call(made_days_ago=30, bookings_at_call=0)])
    con = FakeCon({"test artist": date.today() - timedelta(days=5)})
    changed = calls_mod.auto_resolve(con)
    assert len(changed) == 1
    assert changed[0]["status"] == "hit"
    assert changed[0]["leadDays"] == 25


def test_already_booked_artist_never_auto_hits():
    # regression: this used to resolve as a hit with 0-day lead
    calls_mod.save([_call(made_days_ago=0, bookings_at_call=1)])
    con = FakeCon({"test artist": date.today()})
    calls_mod.auto_resolve(con)
    assert calls_mod.load()[0]["status"] == "open"


def test_booking_on_the_same_day_is_not_a_hit():
    # same-day means it was already on the books in that snapshot
    calls_mod.save([_call(made_days_ago=10, bookings_at_call=0)])
    con = FakeCon({"test artist": date.today() - timedelta(days=10)})
    calls_mod.auto_resolve(con)
    assert calls_mod.load()[0]["status"] == "open"


def test_expired_call_becomes_a_miss():
    calls_mod.save([_call(made_days_ago=121, bookings_at_call=0, horizon=120)])
    changed = calls_mod.auto_resolve(FakeCon({}))
    assert changed[0]["status"] == "miss"
    assert changed[0]["leadDays"] is None


def test_call_inside_its_horizon_stays_open():
    calls_mod.save([_call(made_days_ago=119, bookings_at_call=0, horizon=120)])
    calls_mod.auto_resolve(FakeCon({}))
    assert calls_mod.load()[0]["status"] == "open"


def test_resolved_calls_are_not_reopened():
    resolved = _call(made_days_ago=200, bookings_at_call=0, status="miss")
    calls_mod.save([resolved])
    con = FakeCon({"test artist": date.today()})
    assert calls_mod.auto_resolve(con) == []
    assert calls_mod.load()[0]["status"] == "miss"


def test_scoreboard_counts_misses_against_the_hit_rate():
    calls_mod.save([
        _call(0, 0, id="call-001", status="hit", leadDays=10),
        _call(0, 0, id="call-002", status="hit", leadDays=30),
        _call(0, 0, id="call-003", status="miss"),
        _call(0, 0, id="call-004", status="open"),
    ])
    s = calls_mod.scoreboard(calls_mod.load())
    assert (s["hits"], s["resolved"], s["open"]) == (2, 3, 1)
    assert s["hitRate"] == 66.7
    assert s["medianLead"] == 30 and s["bestLead"] == 30


def test_scoreboard_is_empty_not_perfect_when_nothing_resolved():
    calls_mod.save([_call(0, 0, status="open")])
    s = calls_mod.scoreboard(calls_mod.load())
    assert s["hitRate"] is None  # not 100%


def test_notes_never_expire_into_misses():
    calls_mod.save([_call(made_days_ago=999, bookings_at_call=0, kind="note")])
    assert calls_mod.auto_resolve(FakeCon({})) == []
    assert calls_mod.load()[0]["status"] == "open"


def test_notes_and_voids_are_excluded_from_the_hit_rate():
    calls_mod.save([
        _call(0, 0, id="call-001", status="hit", leadDays=20),
        _call(0, 0, id="call-002", status="miss"),
        _call(0, 0, id="call-003", status="void"),   # broken premise
        _call(0, 0, id="call-004", kind="note"),      # an observation
    ])
    s = calls_mod.scoreboard(calls_mod.load())
    assert (s["total"], s["hits"], s["resolved"]) == (2, 1, 2)
    assert s["hitRate"] == 50.0
    assert (s["notes"], s["voided"]) == (1, 1)
