"""Parsing tests for the Dice venue-page collector, against a saved real page."""

from datetime import date
from pathlib import Path

import pytest

from scene_radar.dice import (
    DiceParseError,
    artists_from_title,
    parse_event_date,
    parse_listing_page,
    parse_venue_page,
)

FIXTURE = Path(__file__).parent / "fixtures" / "dice_sable.html"
PROMOTER_FIXTURE = Path(__file__).parent / "fixtures" / "dice_promoter_apex.html"
TODAY = date(2026, 7, 27)  # fixture captured this day


def test_parse_venue_page():
    events = parse_venue_page(FIXTURE.read_text(), "Sable Miami", today=TODAY)
    assert len(events) >= 3
    ev = events[0]
    assert ev.event_id.startswith("dice:")
    assert ev.source == "dice"
    assert ev.venue_name == "Sable Miami"
    assert ev.event_date >= TODAY
    names = {a for e in events for a in e.artists}
    assert "Oliver Koletzki" in names


def test_fails_loudly_on_garbage():
    with pytest.raises(DiceParseError):
        parse_venue_page("<html><body>redesigned!</body></html>", "Sable Miami")


def test_promoter_page_reads_venue_from_each_card():
    """A promoter moves between rooms, so the venue can't come from the page."""
    events = parse_listing_page(
        PROMOTER_FIXTURE.read_text(), "Apex Presents", venue_name=None, today=date(2026, 7, 28)
    )
    assert events, "expected Miami events on the promoter page"
    venues = {e.venue_name for e in events}
    assert "La Otra" in venues
    # the promoter's own name must never leak into the venue column
    assert "Apex Presents" not in venues
    assert all(e.source == "dice" for e in events)


def test_promoter_page_keeps_only_miami():
    events = parse_listing_page(
        PROMOTER_FIXTURE.read_text(), "Apex Presents", venue_name=None, today=date(2026, 7, 28)
    )
    # every kept card had 'Miami' as its city; nothing venue-less slipped through
    assert all(e.venue_name for e in events)


def test_event_date_year_rollover():
    assert parse_event_date("Fri, Jul 31", today=TODAY) == date(2026, 7, 31)
    # a January date seen in July must roll into next year
    assert parse_event_date("Sat, Jan 10", today=TODAY) == date(2027, 1, 10)


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Oliver Koletzki + Manumat", ["Oliver Koletzki", "Manumat"]),
        ("Rossi., Traumer & Marsolo", ["Rossi.", "Traumer", "Marsolo"]),
        ("After Midnight: Matroda x San Pacho", ["Matroda", "San Pacho"]),
        ("ACRAZE & CID", ["ACRAZE", "CID"]),
        ("TBA", []),
        ("Franky Rizardo presents FLOW", ["Franky Rizardo"]),
    ],
)
def test_artists_from_title(title, expected):
    assert artists_from_title(title) == expected
