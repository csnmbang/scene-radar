"""A committed copy of the Beatport charts, fetched from a home connection.

Why this exists: Beatport sits behind a Cloudflare managed challenge that
GitHub's runners (Azure datacenter addresses) can't pass — every browser
fingerprint gets "Just a moment..." — while the same request from a normal
residential connection returns the chart. The daily cloud run therefore
can't fetch charts itself. `refresh_beatport_feed.sh` runs on a laptop,
fetches the nine charts, and commits this one small file; the cloud run
reads it when its own fetch is blocked (beatport.collect_with_provenance).

Charts move slowly, so a copy a day or two old is a fair stand-in, but it is
never presented as fresh: the snapshot records the chart date and the
dashboard shows it whenever it is older than the rest of the data. Past
MAX_FEED_AGE_DAYS the pipeline refuses to run on it at all — at that point
the laptop job has stopped and the failure email is the right signal.
"""

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path

from .beatport import BeatportParseError, ChartEntry
from .config import BEATPORT_GENRES, DATA_DIR

FEED_PATH = DATA_DIR / "beatport_feed.json"
MAX_FEED_AGE_DAYS = 7


@dataclass
class Feed:
    chart_date: date
    entries: list[ChartEntry]


def write_feed(entries: list[ChartEntry], chart_date: date, path: Path = FEED_PATH) -> None:
    missing = set(BEATPORT_GENRES) - {e.chart_genre for e in entries}
    if missing:
        raise ValueError(f"refusing to write a feed missing charts: {sorted(missing)}")
    payload = {
        "chart_date": chart_date.isoformat(),
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "entries": [asdict(e) for e in entries],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    # compact, key-sorted: one small file whose daily diffs stay readable
    path.write_text(json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n")


def load_feed(today: date, path: Path = FEED_PATH, max_age_days: int = MAX_FEED_AGE_DAYS) -> Feed:
    if not path.exists():
        raise BeatportParseError(
            f"Beatport blocked this network and there is no committed feed at {path}. "
            f"Run refresh_beatport_feed.sh from a home connection."
        )
    try:
        raw = json.loads(path.read_text())
        chart_date = date.fromisoformat(raw["chart_date"])
        entries = [ChartEntry(**row) for row in raw["entries"]]
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        raise BeatportParseError(f"Beatport feed at {path} is unreadable: {e}") from e

    age = (today - chart_date).days
    if age > max_age_days:
        raise BeatportParseError(
            f"Beatport blocked this network and the committed feed is {age} days old "
            f"(charts as of {chart_date}, limit {max_age_days}) — the home-connection "
            f"refresh job has stopped running."
        )
    missing = set(BEATPORT_GENRES) - {e.chart_genre for e in entries}
    if missing:
        raise BeatportParseError(
            f"Beatport feed is missing charts {sorted(missing)} — it predates a config "
            f"change; run refresh_beatport_feed.sh."
        )
    return Feed(chart_date=chart_date, entries=entries)
