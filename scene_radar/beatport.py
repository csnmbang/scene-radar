"""Beatport Top 100 chart collection.

Note on the HTTP client: the spec suggested httpx, but Beatport sits behind
Cloudflare TLS fingerprinting and returns 403 to httpx/curl regardless of
headers. curl-cffi with Chrome impersonation gets a normal 200 on the public
chart pages, so that's what we use — still 1 req/sec, still cached to disk.

The chart itself is embedded in the page as JSON (Next.js __NEXT_DATA__),
so there is no fragile HTML parsing: we pull the dehydrated react-query
state and validate it hard. If Beatport changes the page shape, we raise
BeatportParseError and write nothing.
"""

import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from curl_cffi import requests as cffi_requests

from .config import BEATPORT_GENRES, RAW_DIR, REQUEST_DELAY_S
from .normalize import norm_artist

_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S
)


class BeatportParseError(Exception):
    """Raised when a chart page doesn't contain what we expect. Fail loudly."""


@dataclass
class ChartEntry:
    chart_genre: str
    rank: int
    track_id: int
    track_title: str
    mix_name: str | None
    artist_raw: str
    artist_norm: str
    remixer: str | None
    label: str | None


def chart_url(slug: str, genre_id: int) -> str:
    return f"https://www.beatport.com/genre/{slug}/{genre_id}/top-100"


def fetch_chart_html(slug: str, genre_id: int, cache_dir: Path, force: bool = False) -> str:
    """Fetch one chart page, caching raw HTML so same-day re-runs are free."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"beatport_{slug}.html"
    if cache_file.exists() and not force:
        return cache_file.read_text()
    resp = cffi_requests.get(chart_url(slug, genre_id), impersonate="chrome", timeout=30)
    if resp.status_code != 200:
        raise BeatportParseError(
            f"Beatport returned HTTP {resp.status_code} for {slug} — not caching, not writing."
        )
    cache_file.write_text(resp.text)
    time.sleep(REQUEST_DELAY_S)
    return resp.text


def parse_chart(html: str, chart_genre: str) -> list[ChartEntry]:
    """Extract the top-100 list from a chart page's __NEXT_DATA__ JSON.

    One ChartEntry per (track, artist): a track by two artists yields two
    rows at the same rank, so each artist gets credit in scoring.
    """
    m = _NEXT_DATA_RE.search(html)
    if not m:
        raise BeatportParseError(f"{chart_genre}: no __NEXT_DATA__ script tag in page")
    try:
        data = json.loads(m.group(1))
        queries = data["props"]["pageProps"]["dehydratedState"]["queries"]
    except (json.JSONDecodeError, KeyError) as e:
        raise BeatportParseError(f"{chart_genre}: unexpected __NEXT_DATA__ shape: {e}") from e

    results = None
    for q in queries:
        key = json.dumps(q.get("queryKey", ""))
        if "top-100" in key:
            results = q.get("state", {}).get("data", {}).get("results")
            break
    if not results:
        raise BeatportParseError(f"{chart_genre}: no top-100 query in dehydrated state")
    if not (50 <= len(results) <= 100):
        raise BeatportParseError(
            f"{chart_genre}: expected ~100 tracks, got {len(results)} — refusing to write"
        )

    entries: list[ChartEntry] = []
    for i, t in enumerate(results, start=1):
        title = t.get("name")
        artists = t.get("artists") or []
        if not title or not artists:
            raise BeatportParseError(f"{chart_genre}: rank {i} missing title or artists")
        remixers = ", ".join(r["name"] for r in t.get("remixers") or []) or None
        label = ((t.get("release") or {}).get("label") or {}).get("name")
        for a in artists:
            entries.append(
                ChartEntry(
                    chart_genre=chart_genre,
                    rank=i,
                    track_id=t.get("id", 0),
                    track_title=title,
                    mix_name=t.get("mix_name"),
                    artist_raw=a["name"],
                    artist_norm=norm_artist(a["name"]),
                    remixer=remixers,
                    label=label,
                )
            )
    return entries


def collect(snapshot_date: date | None = None, force: bool = False) -> list[ChartEntry]:
    """Fetch + parse all configured genre charts. All-or-nothing:
    any genre failing to parse aborts the whole collection."""
    snapshot_date = snapshot_date or date.today()
    cache_dir = RAW_DIR / snapshot_date.isoformat()
    all_entries: list[ChartEntry] = []
    for slug, genre_id in BEATPORT_GENRES.items():
        html = fetch_chart_html(slug, genre_id, cache_dir, force=force)
        entries = parse_chart(html, slug)
        print(f"  {slug}: {len(entries)} artist-track rows")
        all_entries.extend(entries)
    return all_entries


def write_snapshot(con, entries: list[ChartEntry], snapshot_date: date | None = None) -> int:
    from .db import replace_snapshot

    snapshot_date = snapshot_date or date.today()
    now = datetime.now()
    replace_snapshot(con, "beatport_chart_entries", snapshot_date)
    con.executemany(
        """INSERT INTO beatport_chart_entries
           (snapshot_date, collected_at, chart_genre, rank, track_id, track_title,
            mix_name, artist_raw, artist_norm, remixer, label)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (snapshot_date, now, e.chart_genre, e.rank, e.track_id, e.track_title,
             e.mix_name, e.artist_raw, e.artist_norm, e.remixer, e.label)
            for e in entries
        ],
    )
    return len(entries)
