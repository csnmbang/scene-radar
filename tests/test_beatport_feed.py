"""The committed Beatport feed: round trip, staleness limits, and when the
pipeline is allowed to use it instead of a live fetch."""

from datetime import date, timedelta

import pytest

from scene_radar import beatport, beatport_feed
from scene_radar.beatport import BeatportBlocked, BeatportParseError, ChartEntry
from scene_radar.config import BEATPORT_GENRES

TODAY = date(2026, 11, 24)


def _entries(genres=None):
    return [
        ChartEntry(g, 1, 100 + i, f"Track {g}", "Original Mix", f"Artist {g}", f"artist {g}", None, "Label")
        for i, g in enumerate(genres or BEATPORT_GENRES)
    ]


def test_round_trip(tmp_path):
    path = tmp_path / "feed.json"
    beatport_feed.write_feed(_entries(), TODAY, path)
    feed = beatport_feed.load_feed(TODAY, path)
    assert feed.chart_date == TODAY
    assert feed.entries == _entries()


def test_refuses_to_write_an_incomplete_feed(tmp_path):
    with pytest.raises(ValueError, match="missing charts"):
        beatport_feed.write_feed(_entries(list(BEATPORT_GENRES)[:3]), TODAY, tmp_path / "f.json")
    assert not (tmp_path / "f.json").exists()


def test_stale_feed_is_refused_loudly(tmp_path):
    path = tmp_path / "feed.json"
    beatport_feed.write_feed(_entries(), TODAY - timedelta(days=8), path)
    with pytest.raises(BeatportParseError, match="8 days old"):
        beatport_feed.load_feed(TODAY, path)


def test_feed_at_the_age_limit_is_still_usable(tmp_path):
    path = tmp_path / "feed.json"
    beatport_feed.write_feed(_entries(), TODAY - timedelta(days=beatport_feed.MAX_FEED_AGE_DAYS), path)
    assert beatport_feed.load_feed(TODAY, path).entries


def test_missing_and_corrupt_feeds_fail_loudly(tmp_path):
    with pytest.raises(BeatportParseError, match="no committed feed"):
        beatport_feed.load_feed(TODAY, tmp_path / "absent.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(BeatportParseError, match="unreadable"):
        beatport_feed.load_feed(TODAY, bad)


def test_feed_from_before_a_new_chart_was_added_is_refused(tmp_path):
    path = tmp_path / "feed.json"
    genres = list(BEATPORT_GENRES)
    beatport_feed.write_feed(_entries(genres), TODAY, path)
    text = path.read_text().replace(f'"chart_genre":"{genres[-1]}"', '"chart_genre":"some-other"')
    path.write_text(text)
    with pytest.raises(BeatportParseError, match="missing charts"):
        beatport_feed.load_feed(TODAY, path)


def _stub_collect(monkeypatch, outcome):
    def fake(snapshot_date=None, force=False):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(beatport, "collect", fake)


def test_live_fetch_is_used_when_it_works(tmp_path, monkeypatch):
    live = _entries()
    _stub_collect(monkeypatch, live)
    entries, chart_date, mode = beatport.collect_with_provenance(TODAY, feed_path=tmp_path / "none.json")
    assert (entries, chart_date, mode) == (live, TODAY, "live")


def test_block_falls_back_to_the_feed_with_its_real_date(tmp_path, monkeypatch):
    path = tmp_path / "feed.json"
    beatport_feed.write_feed(_entries(), TODAY - timedelta(days=2), path)
    _stub_collect(monkeypatch, BeatportBlocked("challenge"))
    entries, chart_date, mode = beatport.collect_with_provenance(TODAY, feed_path=path)
    assert mode == "feed"
    assert chart_date == TODAY - timedelta(days=2)  # never relabelled as today's
    assert entries == _entries()


def test_block_with_no_usable_feed_still_fails(tmp_path, monkeypatch):
    _stub_collect(monkeypatch, BeatportBlocked("challenge"))
    with pytest.raises(BeatportParseError, match="no committed feed"):
        beatport.collect_with_provenance(TODAY, feed_path=tmp_path / "none.json")


def test_a_structural_change_is_never_masked_by_the_feed(tmp_path, monkeypatch):
    """If Beatport changes its page, stale data must not paper over it."""
    path = tmp_path / "feed.json"
    beatport_feed.write_feed(_entries(), TODAY, path)  # a perfectly good feed exists
    _stub_collect(monkeypatch, BeatportParseError("unexpected __NEXT_DATA__ shape"))
    with pytest.raises(BeatportParseError, match="__NEXT_DATA__"):
        beatport.collect_with_provenance(TODAY, feed_path=path)


def test_provenance_is_recorded_and_replaced(tmp_path):
    import duckdb

    from scene_radar.db import SCHEMA

    con = duckdb.connect(str(tmp_path / "t.duckdb"))
    con.execute(SCHEMA)
    beatport.record_provenance(con, TODAY, TODAY - timedelta(days=1), "feed")
    beatport.record_provenance(con, TODAY, TODAY, "live")  # same-day rerun replaces
    assert con.execute("SELECT chart_date, mode FROM beatport_provenance").fetchall() == [(TODAY, "live")]
