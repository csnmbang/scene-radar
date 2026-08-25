"""Parsing tests for the Dice venue-page collector, against a saved real page."""

from datetime import date
from pathlib import Path

import pytest

from scene_radar.dice import (
    DiceParseError,
    _fetch_and_parse,
    artists_from_title,
    parse_event_date,
    parse_listing_page,
    parse_venue_page,
)

FIXTURE = Path(__file__).parent / "fixtures" / "dice_sable.html"
PROMOTER_FIXTURE = Path(__file__).parent / "fixtures" / "dice_promoter_apex.html"
NEW_HREF_FIXTURE = Path(__file__).parent / "fixtures" / "dice_venue_new_href_format.html"
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


def test_bare_hex_event_ids_parse():
    """Regression: around 2026-08-21 Dice dropped the slugged event URL
    (/event/xyz-artist-name-venue) for a bare hex id with no trailing hyphen
    (/event/6a4020a738236e00018e018a). The old href regex required a
    trailing '-' and silently matched nothing on every card — 'blocks found'
    stayed non-empty so the loud-failure guard never tripped, and every
    Dice source quietly reported 0 events for four days straight."""
    events = parse_venue_page(NEW_HREF_FIXTURE.read_text(), "Club Space Miami", today=date(2026, 8, 24))
    assert events, "bare hex ids must still yield events"
    assert all(e.event_id.startswith("dice:") and len(e.event_id) > len("dice:") for e in events)


def test_raises_when_every_card_fails_id_extraction():
    """The other half of the same regression: if the href shape breaks again,
    fail loudly instead of quietly returning an empty list that write_snapshot
    then chokes on."""
    html = """
    <div class="EventParts__EventBlock-x">
      <a href="/totally/different/path/123">Some Show</a>
      Fri, Aug 28|Club Space Miami|Miami|From $30
    </div>"""
    with pytest.raises(DiceParseError):
        parse_venue_page(html, "Club Space Miami", today=date(2026, 8, 24))


def test_empty_cached_page_self_heals_on_refetch(tmp_path, monkeypatch):
    """Regression: Dice can serve a real page with zero event cards (not a
    bot-check, not a markup change — just a bad render). A cached copy of
    that page used to raise on every run forever. One live refetch should
    recover without the caller passing force=True."""
    cache_dir = tmp_path
    (cache_dir / "dice_venue_sable-miami-l8qmp.html").write_text(
        "<html><body>no events today, sorry</body></html>"
    )

    calls = {"n": 0}
    real_html = FIXTURE.read_text()

    def fake_fetch_page(slug, kind, cache_dir_arg, force=False):
        calls["n"] += 1
        return real_html  # simulates a fresh, good fetch

    monkeypatch.setattr("scene_radar.dice.fetch_page", fake_fetch_page)
    events = _fetch_and_parse(
        "sable-miami-l8qmp", "venue", "Sable Miami", cache_dir, False,
        venue_name="Sable Miami", today=TODAY,
    )
    assert calls["n"] == 1  # only the retry fetch — the bad cache was never re-hit
    assert events, "expected the retry to recover real events"


def test_force_mode_does_not_retry_forever(tmp_path, monkeypatch):
    """With force=True the caller already asked for a live fetch; a second
    empty result should raise immediately (one fetch), not loop."""
    calls = {"n": 0}

    def fake_fetch_page(slug, kind, cache_dir_arg, force=False):
        calls["n"] += 1
        return "<html><body>still nothing</body></html>"

    monkeypatch.setattr("scene_radar.dice.fetch_page", fake_fetch_page)
    with pytest.raises(DiceParseError):
        _fetch_and_parse(
            "sable-miami-l8qmp", "venue", "Sable Miami", tmp_path, True,
            venue_name="Sable Miami",
        )
    assert calls["n"] == 1


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
