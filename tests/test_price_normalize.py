"""Price normalization — the sources disagree, the column shouldn't show it.

Every input below is a real value observed in a live snapshot.
"""

import pytest

from build_dashboard import normalize_price


@pytest.mark.parametrize(
    ("raw", "display", "value"),
    [
        (None, "—", None),          # RA leaves cost unset on most events
        ("", "—", None),
        ("0", "Free", 0.0),          # RA free events
        ("00", "Free", 0.0),
        ("From Free", "Free", 0.0),  # Dice
        ("20", "$20", 20.0),         # RA bare number
        ("34.10", "$34", 34.1),      # RA with fees
        ("18,95", "$19", 18.95),     # European decimal comma
        ("$12", "$12", 12.0),
        ("$20+", "$20+", 20.0),
        ("$219-$439", "$219–439", 219.0),
        ("From $30", "from $30", 30.0),
        ("From $19.99", "from $20", 19.99),
        ("$24.25", "$24", 24.25),
    ],
)
def test_normalize_price(raw, display, value):
    assert normalize_price(raw) == (display, value)


def test_bare_dollar_tier_is_kept_but_unsortable():
    # RA's relative '$'/'$$' tier carries meaning but isn't an amount —
    # keep the label, give it no sort value so it doesn't rank as free.
    assert normalize_price("$") == ("$", None)
    assert normalize_price("$$") == ("$$", None)


def test_unparseable_falls_back_to_dash():
    assert normalize_price("call venue") == ("—", None)
