"""Collect confirmed bookings in comparable markets (NYC, LA, Mexico City...).

Usage: uv run python collect_markets.py [--force]

This is the long-horizon signal: an artist with dates in these markets and
none in Miami is either about to be announced here or is skipping us, and
that's visible months before Miami's own announcement.
"""

import sys

from scene_radar import markets
from scene_radar.db import connect


def main() -> None:
    force = "--force" in sys.argv
    print("Collecting comparable-market bookings…")
    rows = markets.collect(force=force)
    con = connect()
    n = markets.write_snapshot(con, rows)
    con.close()
    print(f"Wrote {n} artist-market rows.")


if __name__ == "__main__":
    main()
