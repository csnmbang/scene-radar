"""Parsing tests against a saved real Beatport chart page.

If Beatport changes their page structure these fail — that's the point.
"""

from pathlib import Path

import pytest

from scene_radar.beatport import BeatportParseError, parse_chart

FIXTURE = Path(__file__).parent / "fixtures" / "beatport_tech_house.html"


def test_parse_full_chart():
    entries = parse_chart(FIXTURE.read_text(), "tech-house")
    ranks = {e.rank for e in entries}
    assert ranks == set(range(1, 101))  # every rank present
    assert len(entries) >= 100          # collab tracks add extra artist rows
    e1 = next(e for e in entries if e.rank == 1)
    assert e1.track_title
    assert e1.artist_raw
    assert e1.artist_norm == e1.artist_norm.lower()
    assert e1.chart_genre == "tech-house"
    labels = [e.label for e in entries if e.label]
    assert len(labels) > 50  # labels present for the vast majority


def test_parse_fails_loudly_on_garbage():
    with pytest.raises(BeatportParseError):
        parse_chart("<html><body>nope</body></html>", "tech-house")


def test_parse_fails_loudly_on_truncated_chart():
    html = FIXTURE.read_text()
    # sabotage: valid page shape but wrong query key content
    with pytest.raises(BeatportParseError):
        parse_chart(html.replace("top-100", "top-1OO"), "tech-house")
