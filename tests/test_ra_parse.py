"""Parsing tests against saved real RA GraphQL listings."""

import json
from datetime import date
from pathlib import Path

import pytest

from scene_radar.ra import RACollectError, parse_graphql

FIXTURE = Path(__file__).parent / "fixtures" / "ra_miami_listings.json"


def test_parse_listings():
    listings = json.loads(FIXTURE.read_text())
    events = parse_graphql(listings)
    assert len(events) > 0
    ev = events[0]
    assert ev.event_id
    assert isinstance(ev.event_date, date)
    assert ev.event_name
    assert ev.source == "graphql"
    # at least some events carry lineups and venues
    assert any(e.artists for e in events)
    assert any(e.venue_name for e in events)


def test_dedupes_multi_day_listings():
    listings = json.loads(FIXTURE.read_text())
    doubled = listings + listings  # same events listed twice
    events = parse_graphql(doubled)
    ids = [e.event_id for e in events]
    assert len(ids) == len(set(ids))


def test_fails_loudly_on_empty():
    with pytest.raises(RACollectError):
        parse_graphql([])
