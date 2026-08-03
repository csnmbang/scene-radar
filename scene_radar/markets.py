"""Comparable-market bookings — the signal that sees months out.

The ceiling on predicting Miami from Miami data is structural: a booking is
decided 3-6 months before the show and only announced 6-8 weeks out, so the
announcement we detect is old news by the time it exists. Watching Miami
harder cannot fix that.

Other cities break the ceiling. Tours are routed as a block, and each market
announces on its own schedule — an artist's LA and NYC dates are frequently
public months before the Miami date is. So a charting artist with confirmed
dates in comparable markets and nothing in Miami is a genuinely forward-
looking signal, available now rather than six weeks before the show.

Measured against the current gap list, ~20% of top zero-booking Miami gaps
already hold dates elsewhere, some up to six months out.

Same RA GraphQL endpoint as the Miami collector, just different area ids.
"""

import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from curl_cffi import requests as cffi_requests

from .config import (
    COMPARABLE_MARKETS,
    MARKETS_LOOKAHEAD_DAYS,
    MARKETS_MAX_PAGES,
    RA_PAGE_SIZE,
    RAW_DIR,
    REQUEST_DELAY_S,
)
from .normalize import norm_artist


class MarketsCollectError(Exception):
    """Raised when comparable-market data can't be collected. Fail loudly."""


_QUERY = """query($filters: FilterInputDtoInput, $page: Int, $pageSize: Int, $sort: SortInputDtoInput) {
  eventListings(filters: $filters, pageSize: $pageSize, page: $page, sort: $sort) {
    data { event { id title date venue { name } artists { name } } }
    totalResults
  }
}"""


def _page(area_id: int, page: int, start: date, end: date) -> dict:
    body = {
        "query": _QUERY,
        "variables": {
            "filters": {
                "areas": {"eq": area_id},
                "listingDate": {"gte": start.isoformat(), "lte": end.isoformat()},
            },
            "pageSize": RA_PAGE_SIZE * 2,
            "page": page,
            "sort": {"listingDate": {"order": "ASCENDING"}},
        },
    }
    # Shares RA's retry policy — six markets over 180 days is a lot of
    # pagination, so transient 5xx are expected and must not kill the run.
    from .ra import RACollectError, post_graphql

    try:
        data = post_graphql(body, f"area {area_id} p{page}")
    except RACollectError as e:
        raise MarketsCollectError(str(e)) from e
    return data["data"]["eventListings"]


def fetch_market(name: str, area_id: int, cache_dir: Path, force: bool = False) -> list[dict]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    slug = name.lower().replace(" ", "-")
    cache_file = cache_dir / f"market_{slug}.json"
    if cache_file.exists() and not force:
        return json.loads(cache_file.read_text())

    start = date.today()
    end = start + timedelta(days=MARKETS_LOOKAHEAD_DAYS)
    listings: list[dict] = []
    page = 1
    while page <= MARKETS_MAX_PAGES:
        chunk = _page(area_id, page, start, end)
        listings.extend(chunk["data"])
        if len(listings) >= chunk["totalResults"] or not chunk["data"]:
            break
        page += 1
        time.sleep(REQUEST_DELAY_S)
    cache_file.write_text(json.dumps(listings, indent=1))
    return listings


def parse(listings: list[dict], market: str) -> list[tuple[str, str, str, date, str]]:
    """-> (artist_raw, artist_norm, market, event_date, venue_name)"""
    rows = []
    seen: set[tuple[str, str]] = set()
    for row in listings:
        ev = row.get("event") or {}
        raw_date = ev.get("date")
        if not raw_date:
            continue
        ev_date = date.fromisoformat(raw_date[:10])
        venue = (ev.get("venue") or {}).get("name") or ""
        for a in ev.get("artists") or []:
            name = a.get("name")
            if not name:
                continue
            key = norm_artist(name)
            if not key or (key, str(ev.get("id"))) in seen:
                continue
            seen.add((key, str(ev.get("id"))))
            rows.append((name, key, market, ev_date, venue))
    return rows


def collect(snapshot_date: date | None = None, force: bool = False) -> list[tuple]:
    snapshot_date = snapshot_date or date.today()
    cache_dir = RAW_DIR / snapshot_date.isoformat()
    all_rows: list[tuple] = []
    for name, area_id in COMPARABLE_MARKETS.items():
        listings = fetch_market(name, area_id, cache_dir, force=force)
        rows = parse(listings, name)
        print(f"  {name}: {len(listings)} events, {len(rows)} artist slots")
        all_rows.extend(rows)
        time.sleep(REQUEST_DELAY_S)
    if not all_rows:
        raise MarketsCollectError("0 comparable-market rows — refusing to write an empty snapshot")
    return all_rows


def write_snapshot(con, rows: list[tuple], snapshot_date: date | None = None) -> int:
    snapshot_date = snapshot_date or date.today()
    now = datetime.now()
    con.execute("DELETE FROM market_bookings WHERE snapshot_date = ?", [snapshot_date])
    con.executemany(
        """INSERT INTO market_bookings
           (snapshot_date, collected_at, artist_raw, artist_norm, market, event_date, venue_name)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [(snapshot_date, now, r[0], r[1], r[2], r[3], r[4]) for r in rows],
    )
    return len(rows)
