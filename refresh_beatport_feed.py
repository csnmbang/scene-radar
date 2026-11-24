"""Fetch the Beatport charts from THIS machine and write data/beatport_feed.json.

Usage: uv run python refresh_beatport_feed.py

Run it from a normal home connection. Beatport's Cloudflare challenges
GitHub's runners, so the daily cloud run falls back to the file this writes
(see scene_radar/beatport_feed.py). Normally invoked by
scripts/refresh_beatport_feed.sh, which also commits and pushes the result.

Exits non-zero, writing nothing, if any chart can't be fetched or parsed —
a partial feed would be worse than the previous complete one.
"""

import sys
from datetime import date

from scene_radar import beatport, beatport_feed


def main() -> int:
    today = date.today()
    print(f"Fetching Beatport charts for {today}…")
    try:
        entries = beatport.collect(today, force=True)
    except beatport.BeatportBlocked as e:
        print(f"Blocked even from here: {e}", file=sys.stderr)
        return 2
    beatport_feed.write_feed(entries, today)
    print(f"Wrote {beatport_feed.FEED_PATH} ({len(entries)} rows, charts as of {today}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
