"""Resident Advisor Miami event collection.

Strategy per spec: try the Apify RA scraper actor first (needs APIFY_TOKEN in
.env), fall back to RA's public GraphQL endpoint at ra.co/graphql. The
GraphQL path is read-only, cached to disk, paginated politely at 1 req/sec,
and uses the same query the RA events page itself issues.

RA's HTML pages are behind DataDome, but the GraphQL endpoint answers
normally to curl-cffi with Chrome impersonation.
"""

import json
import os
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from curl_cffi import requests as cffi_requests

from .config import (
    RA_APIFY_ACTOR_DEFAULT,
    RA_AREA_ID,
    RA_LOOKAHEAD_DAYS,
    RA_PAGE_SIZE,
    RAW_DIR,
    REQUEST_DELAY_S,
)
from .normalize import norm_artist


class RACollectError(Exception):
    """Raised when RA data can't be collected/validated. Fail loudly."""


@dataclass
class RAEvent:
    event_id: str
    event_date: date
    event_name: str
    venue_name: str | None
    ticket_price: str | None
    genres: list[str] = field(default_factory=list)
    artists: list[str] = field(default_factory=list)  # raw names
    source: str = "graphql"


_GQL_QUERY = """query($filters: FilterInputDtoInput, $page: Int, $pageSize: Int, $sort: SortInputDtoInput) {
  eventListings(filters: $filters, pageSize: $pageSize, page: $page, sort: $sort) {
    data { id listingDate event { id title date cost isTicketed
      venue { id name }
      artists { id name }
      genres { id name }
    } }
    totalResults
  }
}"""


def _gql_page(page: int, start: date, end: date) -> dict:
    body = {
        "query": _GQL_QUERY,
        "variables": {
            "filters": {
                "areas": {"eq": RA_AREA_ID},
                "listingDate": {"gte": start.isoformat(), "lte": end.isoformat()},
            },
            "pageSize": RA_PAGE_SIZE,
            "page": page,
            "sort": {"listingDate": {"order": "ASCENDING"}},
        },
    }
    resp = cffi_requests.post(
        "https://ra.co/graphql",
        json=body,
        impersonate="chrome",
        headers={"Content-Type": "application/json", "Referer": "https://ra.co/events/us/miami"},
        timeout=30,
    )
    if resp.status_code != 200:
        raise RACollectError(f"ra.co/graphql returned HTTP {resp.status_code} on page {page}")
    data = resp.json()
    if "errors" in data:
        raise RACollectError(f"GraphQL errors on page {page}: {data['errors']}")
    return data


def fetch_graphql(cache_dir: Path, force: bool = False) -> list[dict]:
    """All Miami listings for the next RA_LOOKAHEAD_DAYS, cached per snapshot day."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / "ra_miami_graphql.json"
    if cache_file.exists() and not force:
        return json.loads(cache_file.read_text())

    start = date.today()
    end = start + timedelta(days=RA_LOOKAHEAD_DAYS)
    listings: list[dict] = []
    page = 1
    total = None
    while True:
        data = _gql_page(page, start, end)
        chunk = data["data"]["eventListings"]["data"]
        total = data["data"]["eventListings"]["totalResults"]
        listings.extend(chunk)
        if len(listings) >= total or not chunk:
            break
        page += 1
        time.sleep(REQUEST_DELAY_S)

    if total is not None and len(listings) < total:
        raise RACollectError(f"expected {total} listings, got {len(listings)} — aborting")
    cache_file.write_text(json.dumps(listings, indent=1))
    return listings


def parse_graphql(listings: list[dict]) -> list[RAEvent]:
    events: list[RAEvent] = []
    seen: set[str] = set()
    for row in listings:
        ev = row.get("event")
        if not ev:
            raise RACollectError(f"listing {row.get('id')} has no event payload")
        eid = str(ev["id"])
        if eid in seen:  # same event can appear on multiple listing dates
            continue
        seen.add(eid)
        raw_date = ev.get("date") or row.get("listingDate")
        if not raw_date or not ev.get("title"):
            raise RACollectError(f"event {eid} missing date or title")
        events.append(
            RAEvent(
                event_id=eid,
                event_date=date.fromisoformat(raw_date[:10]),
                event_name=ev["title"],
                venue_name=(ev.get("venue") or {}).get("name"),
                ticket_price=ev.get("cost") or None,
                genres=[g["name"] for g in ev.get("genres") or []],
                artists=[a["name"] for a in ev.get("artists") or []],
                source="graphql",
            )
        )
    if not events:
        raise RACollectError("0 events parsed — refusing to write an empty snapshot")
    return events


def fetch_apify(cache_dir: Path, token: str, force: bool = False) -> list[RAEvent] | None:
    """Try the Apify RA actor. Returns None (caller falls back) on any failure —
    the actor ecosystem is flaky and the GraphQL path is the reliable one."""
    cache_file = cache_dir / "ra_miami_apify.json"
    try:
        if cache_file.exists() and not force:
            items = json.loads(cache_file.read_text())
        else:
            actor = os.environ.get("RA_APIFY_ACTOR", RA_APIFY_ACTOR_DEFAULT)
            url = f"https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items?token={token}"
            resp = cffi_requests.post(
                url,
                json={
                    "startUrls": [{"url": "https://ra.co/events/us/miami"}],
                    "maxItems": 500,
                },
                timeout=300,
            )
            if resp.status_code not in (200, 201):
                print(f"  Apify actor returned HTTP {resp.status_code}; falling back to GraphQL")
                return None
            items = resp.json()
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(items, indent=1))

        events: list[RAEvent] = []
        for it in items:
            eid = str(it.get("id") or it.get("eventId") or "")
            title = it.get("title") or it.get("name")
            raw_date = (it.get("date") or it.get("startTime") or "")[:10]
            if not eid or not title or not raw_date:
                continue
            events.append(
                RAEvent(
                    event_id=eid,
                    event_date=date.fromisoformat(raw_date),
                    event_name=title,
                    venue_name=(it.get("venue") or {}).get("name")
                    if isinstance(it.get("venue"), dict)
                    else it.get("venue"),
                    ticket_price=it.get("cost") or None,
                    genres=it.get("genres") or [],
                    artists=[
                        a["name"] if isinstance(a, dict) else a
                        for a in it.get("artists") or []
                    ],
                    source="apify",
                )
            )
        if not events:
            print("  Apify returned no usable events; falling back to GraphQL")
            return None
        return events
    except Exception as e:  # noqa: BLE001 — any actor weirdness means fall back
        print(f"  Apify path failed ({e}); falling back to GraphQL")
        return None


def collect(snapshot_date: date | None = None, force: bool = False) -> list[RAEvent]:
    snapshot_date = snapshot_date or date.today()
    cache_dir = RAW_DIR / snapshot_date.isoformat()
    token = os.environ.get("APIFY_TOKEN")
    if token:
        print("  APIFY_TOKEN found — trying Apify actor first")
        events = fetch_apify(cache_dir, token, force=force)
        if events:
            return events
    listings = fetch_graphql(cache_dir, force=force)
    return parse_graphql(listings)


def write_snapshot(con, events: list[RAEvent], snapshot_date: date | None = None) -> int:
    """Idempotent per day, scoped to RA-sourced rows so a Dice run
    (source='dice', ids prefixed 'dice:') is never clobbered."""
    snapshot_date = snapshot_date or date.today()
    now = datetime.now()
    con.execute(
        "DELETE FROM ra_events WHERE snapshot_date = ? AND source != 'dice'", [snapshot_date]
    )
    con.execute(
        "DELETE FROM ra_event_artists WHERE snapshot_date = ? AND event_id NOT LIKE 'dice:%'",
        [snapshot_date],
    )
    con.executemany(
        """INSERT INTO ra_events
           (snapshot_date, collected_at, event_id, event_date, event_name,
            venue_name, ticket_price, genres, source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (snapshot_date, now, e.event_id, e.event_date, e.event_name,
             e.venue_name, e.ticket_price, ", ".join(e.genres), e.source)
            for e in events
        ],
    )
    artist_rows = [
        (snapshot_date, e.event_id, a, norm_artist(a))
        for e in events
        for a in e.artists
        if a and norm_artist(a)
    ]
    if artist_rows:
        con.executemany(
            """INSERT INTO ra_event_artists (snapshot_date, event_id, artist_raw, artist_norm)
               VALUES (?, ?, ?, ?)""",
            artist_rows,
        )
    return len(events)
