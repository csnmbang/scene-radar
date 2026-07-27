"""Collect Resident Advisor Miami events into DuckDB. Idempotent per calendar day.

Usage: uv run python collect_ra.py [--force]
Tries the Apify actor when APIFY_TOKEN is set in .env, otherwise (or on any
actor failure) falls back to RA's public GraphQL endpoint.
"""

import sys

from dotenv import load_dotenv

from scene_radar import ra
from scene_radar.db import connect


def main() -> None:
    load_dotenv()
    force = "--force" in sys.argv
    print("Collecting RA Miami events…")
    events = ra.collect(force=force)
    con = connect()
    n = ra.write_snapshot(con, events)
    con.close()
    src = events[0].source if events else "?"
    print(f"Wrote {n} events (source: {src}).")


if __name__ == "__main__":
    main()
