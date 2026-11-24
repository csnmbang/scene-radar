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
import os
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


class BeatportBlocked(BeatportParseError):
    """Beatport refused this network address (Cloudflare challenge / persistent
    403). Distinct from a parse error because it says nothing about the
    page's structure — the only error that may fall back to the committed
    feed. A structural change must still fail loudly, never be masked by
    stale data."""


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


# Beatport sits behind Cloudflare, which intermittently 403s GitHub's runner
# IPs even with a valid browser fingerprint. Four of the last five failed
# daily runs (Oct 13, 14, 17, Nov 7) were exactly this, and each one cost a
# whole day of snapshot because the only retry was tomorrow's cron. The block
# is per-request and short-lived, so retrying with a different fingerprint
# after a pause usually gets through.
_RETRYABLE_STATUS = {403, 429, 500, 502, 503, 504}
_IMPERSONATE_ROTATION = ["chrome", "chrome136", "safari184", "firefox144", "chrome131"]
_RETRY_BACKOFF_S = [4, 12, 30, 60]


def fetch_chart_html(slug: str, genre_id: int, cache_dir: Path, force: bool = False) -> str:
    """Fetch one chart page, caching raw HTML so same-day re-runs are free.

    Retries transient blocks (403/429/5xx, connection errors) with backoff
    and a rotated browser fingerprint. Anything still failing after the last
    attempt raises — we never write a partial chart.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"beatport_{slug}.html"
    if cache_file.exists() and not force:
        return cache_file.read_text()

    last = "no attempt made"
    last_status = None
    for attempt, profile in enumerate(_IMPERSONATE_ROTATION):
        try:
            resp = cffi_requests.get(chart_url(slug, genre_id), impersonate=profile, timeout=30)
        except Exception as e:  # connection reset, timeout, TLS failure
            last = f"{type(e).__name__}: {e}"
        else:
            if resp.status_code == 200:
                if attempt:
                    print(f"  {slug}: recovered on attempt {attempt + 1} ({profile})")
                cache_file.write_text(resp.text)
                time.sleep(REQUEST_DELAY_S)
                return resp.text
            last = f"HTTP {resp.status_code}"
            last_status = resp.status_code
            if resp.status_code == 403 and resp.headers.get("cf-mitigated") == "challenge":
                # Cloudflare's managed challenge ("Just a moment..."), served
                # to this IP address regardless of browser fingerprint. On
                # Nov 24 2026 a diagnostic run showed all five fingerprints
                # get it from a GitHub runner (Azure, AS8075) while the same
                # request from a home connection returns the chart. Retrying
                # can't change the address, so don't burn two minutes on it.
                raise BeatportBlocked(
                    f"Beatport served a Cloudflare challenge for {slug} "
                    f"(this network address is blocked, not a transient error)."
                )
            if resp.status_code not in _RETRYABLE_STATUS:
                break  # a 404 etc. won't fix itself — fail now
        if attempt < len(_RETRY_BACKOFF_S):
            time.sleep(_RETRY_BACKOFF_S[attempt])

    msg = f"Beatport returned {last} for {slug} after {attempt + 1} attempts — not caching, not writing."
    # A 403 that survives every retry is the same address-level block, just
    # without the challenge header.
    raise (BeatportBlocked if last_status == 403 else BeatportParseError)(msg)


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


def collect_with_provenance(
    snapshot_date: date | None = None, force: bool = False, feed_path: Path | None = None
) -> tuple[list[ChartEntry], date, str]:
    """Collect the charts, falling back to the committed feed when blocked.

    Returns (entries, chart_date, mode) where mode is "live" or "feed".

    Beatport's Cloudflare challenges GitHub's datacenter addresses (see
    BeatportBlocked), so the daily cloud run usually can't fetch charts
    itself. A job on a home connection refreshes data/beatport_feed.json;
    this reads it when the live fetch is blocked. Only a *block* falls back:
    a parse error means Beatport changed its page and must stay loud.
    All-or-nothing — the feed is used for every genre, never mixed with live
    pages, so the nine charts always share one date.
    """
    from . import beatport_feed

    snapshot_date = snapshot_date or date.today()
    try:
        return collect(snapshot_date, force=force), snapshot_date, "live"
    except BeatportBlocked as e:
        print(f"  live fetch blocked: {e}")
        feed = beatport_feed.load_feed(today=snapshot_date, path=feed_path or beatport_feed.FEED_PATH)
        age = (snapshot_date - feed.chart_date).days
        print(
            f"  using committed Beatport feed: charts as of {feed.chart_date} "
            f"({'today' if age == 0 else f'{age} day(s) old'}, {len(feed.entries)} rows)"
        )
        return feed.entries, feed.chart_date, "feed"


def record_provenance(con, snapshot_date: date, chart_date: date, mode: str) -> None:
    """Remember which chart date a snapshot was built from, so the dashboard
    can say so when the charts are older than the rest of the data."""
    con.execute("DELETE FROM beatport_provenance WHERE snapshot_date = ?", [snapshot_date])
    con.execute(
        "INSERT INTO beatport_provenance (snapshot_date, chart_date, mode) VALUES (?, ?, ?)",
        [snapshot_date, chart_date, mode],
    )


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


def saber_configured() -> bool:
    return bool(os.environ.get("SABER_DATABASE_URL"))


def push_neon_chart_entries(entries: list[ChartEntry], snapshot_date: date | None = None) -> int:
    """Mirror today's chart entries into Saber's own Neon database, so
    saber.me can score a DJ's set against what's charting right now.

    No-op if SABER_DATABASE_URL isn't set — this pipeline's own DuckDB
    snapshot is written either way, same as manual.py's Supabase push is
    optional on top of the local file. Neon has no PostgREST layer the way
    Supabase does, so this goes through psycopg directly rather than
    reusing manual.py's REST-call pattern.

    Same day, same shape as write_snapshot: delete today's rows, then
    insert — a partial write from a failed run never sits half-applied
    next to yesterday's data with nothing to tell them apart.
    """
    if not saber_configured():
        return 0
    snapshot_date = snapshot_date or date.today()
    import psycopg

    with psycopg.connect(os.environ["SABER_DATABASE_URL"]) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM beatport_chart_entries WHERE snapshot_date = %s", [snapshot_date]
            )
            cur.executemany(
                """INSERT INTO beatport_chart_entries
                   (snapshot_date, chart_genre, rank, track_id, track_title,
                    mix_name, artist_raw, artist_norm)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                [
                    (snapshot_date, e.chart_genre, e.rank, e.track_id, e.track_title,
                     e.mix_name, e.artist_raw, e.artist_norm)
                    for e in entries
                ],
            )
        conn.commit()
    return len(entries)

