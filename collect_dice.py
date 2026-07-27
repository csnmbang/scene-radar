"""Collect Dice.fm events for venues that don't list on RA (Club Space, Sable, M2).

Usage: uv run python collect_dice.py [--force]
"""

import sys

from scene_radar import dice
from scene_radar.db import connect


def main() -> None:
    force = "--force" in sys.argv
    print("Collecting Dice venue events…")
    events = dice.collect(force=force)
    con = connect()
    n = dice.write_snapshot(con, events)
    con.close()
    print(f"Wrote {n} events (source: dice).")


if __name__ == "__main__":
    main()
