"""Collect Beatport Top 100 charts into DuckDB. Idempotent per calendar day.

Usage: uv run python collect_beatport.py [--force]
--force re-fetches even if today's raw HTML is already cached.
"""

import sys

from scene_radar import beatport
from scene_radar.db import connect


def main() -> None:
    force = "--force" in sys.argv
    print("Collecting Beatport charts…")
    entries = beatport.collect(force=force)
    con = connect()
    n = beatport.write_snapshot(con, entries)
    con.close()
    print(f"Wrote {n} chart rows.")


if __name__ == "__main__":
    main()
