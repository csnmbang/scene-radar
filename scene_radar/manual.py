"""Manually-added supply events.

Venues announce lineups on Instagram days before tickets hit RA or Dice —
this module lets those announcements enter the radar immediately. Events live
in manual_events.json (committed, so the GitHub Actions pipeline sees them)
and optionally in a Supabase table (set SUPABASE_URL + SUPABASE_SERVICE_KEY
to enable; lets you add events from anywhere without touching git).

Use add_events.py to add events from a screenshot or JSON.

Manual events merge into the same supply tables with source='manual' and
event ids 'manual:<hash>'. An event that later appears on RA/Dice is deduped
(same date + fuzzy venue + fuzzy title) in favor of the ticketing source.
"""

import hashlib
import json
import os
from datetime import date, datetime
from pathlib import Path

from rapidfuzz import fuzz

from .config import PROJECT_ROOT
from .normalize import norm_artist
from .ra import RAEvent

MANUAL_FILE = PROJECT_ROOT / "manual_events.json"

# Fuzzy thresholds for "this manual event is the same as a ticketed one".
_VENUE_MATCH = 85
_TITLE_MATCH = 80


def _event_id(e: dict) -> str:
    key = f"{e['date']}|{e.get('venue', '')}|{e['name']}".lower()
    return "manual:" + hashlib.sha1(key.encode()).hexdigest()[:10]


def load_file_events() -> list[dict]:
    if not MANUAL_FILE.exists():
        return []
    return json.loads(MANUAL_FILE.read_text())


def save_file_events(events: list[dict]) -> None:
    MANUAL_FILE.write_text(json.dumps(events, indent=1, ensure_ascii=False) + "\n")


def supabase_configured() -> bool:
    return bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_KEY"))


def _supabase_headers() -> dict:
    key = os.environ["SUPABASE_SERVICE_KEY"]
    return {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def load_supabase_events() -> list[dict]:
    """Rows from the Supabase manual_events table (empty if not configured)."""
    if not supabase_configured():
        return []
    from curl_cffi import requests as cffi_requests

    url = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/manual_events?select=*"
    resp = cffi_requests.get(url, headers=_supabase_headers(), timeout=30)
    if resp.status_code != 200:
        print(f"  Supabase read failed (HTTP {resp.status_code}) — using file events only")
        return []
    return resp.json()


def push_supabase_events(events: list[dict]) -> bool:
    """Upsert events into Supabase (no-op if not configured)."""
    if not supabase_configured():
        return False
    from curl_cffi import requests as cffi_requests

    url = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/manual_events?on_conflict=event_key"
    rows = [
        {"event_key": _event_id(e), "date": e["date"], "name": e["name"],
         "venue": e.get("venue"), "artists": e.get("artists", []),
         "price": e.get("price"), "genres": e.get("genres", [])}
        for e in events
    ]
    resp = cffi_requests.post(
        url,
        headers={**_supabase_headers(), "Prefer": "resolution=merge-duplicates"},
        json=rows,
        timeout=30,
    )
    return resp.status_code in (200, 201, 204)


def collect() -> list[RAEvent]:
    """All manual events (file + Supabase), future-dated only, deduped."""
    raw = load_file_events() + load_supabase_events()
    events: list[RAEvent] = []
    seen: set[str] = set()
    for e in raw:
        try:
            ev_date = date.fromisoformat(str(e["date"])[:10])
        except (KeyError, ValueError):
            print(f"  skipping manual event with bad date: {e}")
            continue
        if ev_date < date.today():
            continue
        eid = _event_id(e)
        if eid in seen:
            continue
        seen.add(eid)
        events.append(
            RAEvent(
                event_id=eid,
                event_date=ev_date,
                event_name=e["name"],
                venue_name=e.get("venue"),
                ticket_price=e.get("price"),
                genres=e.get("genres") or [],
                artists=e.get("artists") or [],
                source="manual",
            )
        )
    return events


def dedupe_against_db(con, events: list[RAEvent], snapshot_date: date) -> list[RAEvent]:
    """Drop manual events that already exist as RA/Dice events in this
    snapshot (same date, similar venue, similar title) — ticketing sources win."""
    existing = con.execute(
        """SELECT event_date, coalesce(venue_name, ''), event_name FROM ra_events
           WHERE snapshot_date = ? AND source != 'manual'""",
        [snapshot_date],
    ).fetchall()
    by_date: dict[date, list[tuple[str, str]]] = {}
    for d, venue, name in existing:
        by_date.setdefault(d, []).append((venue.lower(), name.lower()))

    kept = []
    for e in events:
        dupe = any(
            fuzz.token_sort_ratio((e.venue_name or "").lower(), venue) >= _VENUE_MATCH
            and fuzz.token_sort_ratio(e.event_name.lower(), name) >= _TITLE_MATCH
            for venue, name in by_date.get(e.event_date, [])
        )
        if dupe:
            print(f"  manual event now on a ticketing source, skipping: {e.event_name} ({e.event_date})")
        else:
            kept.append(e)
    return kept


def write_snapshot(con, events: list[RAEvent], snapshot_date: date | None = None) -> int:
    snapshot_date = snapshot_date or date.today()
    now = datetime.now()
    con.execute(
        "DELETE FROM ra_events WHERE snapshot_date = ? AND source = 'manual'", [snapshot_date]
    )
    con.execute(
        "DELETE FROM ra_event_artists WHERE snapshot_date = ? AND event_id LIKE 'manual:%'",
        [snapshot_date],
    )
    events = dedupe_against_db(con, events, snapshot_date)
    if not events:
        return 0
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
    rows = [
        (snapshot_date, e.event_id, a, norm_artist(a))
        for e in events
        for a in e.artists
        if a and norm_artist(a)
    ]
    if rows:
        con.executemany(
            """INSERT INTO ra_event_artists (snapshot_date, event_id, artist_raw, artist_norm)
               VALUES (?, ?, ?, ?)""",
            rows,
        )
    return len(events)
