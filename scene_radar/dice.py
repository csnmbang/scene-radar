"""Dice.fm supply collection for venues that don't list on RA.

Club Space, Sable, and M2 ticket on Dice, so RA never sees them. Dice venue
pages are server-rendered; each event card carries link, title, date, venue,
and entry price. We fetch only the configured venue pages (3 requests/run),
cache them, and parse cards with selectolax.

Dice events go into the same supply tables as RA events with
source='dice' and event ids prefixed 'dice:', so scoring and the dashboard
treat all supply uniformly. Lineups aren't listed separately on venue pages —
on Dice the event title IS the lineup ("Oliver Koletzki + Manumat"), so
artists are extracted from the title.
"""

import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from curl_cffi import requests as cffi_requests
from selectolax.parser import HTMLParser

from .config import DICE_VENUES, RA_LOOKAHEAD_DAYS, RAW_DIR, REQUEST_DELAY_S
from .normalize import norm_artist
from .ra import RAEvent

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
)}
_DATE_RE = re.compile(r"\b(?:mon|tue|wed|thu|fri|sat|sun),\s+([a-z]{3})\s+(\d{1,2})", re.I)
_EVENT_HREF_RE = re.compile(r"/event/([a-z0-9]+)-")

# Titles that are branding, not artists.
_NON_ARTISTS = {"tba", "more tba", "nye", "friends", "guests", "special guest", "and more"}


class DiceParseError(Exception):
    """Raised when a Dice venue page doesn't look right. Fail loudly."""


def parse_event_date(text: str, today: date | None = None) -> date | None:
    """'Fri, Jul 31' -> date. Dice omits the year: assume the next occurrence."""
    today = today or date.today()
    m = _DATE_RE.search(text)
    if not m:
        return None
    month, day = _MONTHS.get(m.group(1).lower()), int(m.group(2))
    if not month:
        return None
    d = date(today.year, month, day)
    if d < today - timedelta(days=7):  # already passed -> it's next year's date
        d = date(today.year + 1, month, day)
    return d


def artists_from_title(title: str) -> list[str]:
    """Dice titles are lineups. 'After Midnight: Matroda x San Pacho' ->
    [Matroda, San Pacho]. Junk tokens (TBA, NYE...) are dropped; unmatched
    junk is harmless downstream since it won't join to Beatport anyway."""
    t = re.sub(r"^\([^)]*\)\s*", "", title.strip())  # drop leading "(A-Z) " style tags
    if ":" in t:  # brand prefix ("After Midnight: ...")
        t = t.split(":")[-1]
    if " presents " in t.lower():
        t = re.split(r"\s+presents\s+", t, flags=re.I)[0]
    parts = re.split(r"\s*(?:\+|,|&|\bx\b|\bb2b\b|\bvs\.?\b)\s*", t, flags=re.I)
    out = []
    for p in parts:
        p = p.strip(" -–—")
        if len(p) >= 2 and p.lower() not in _NON_ARTISTS:
            out.append(p)
    return out


def fetch_venue_html(slug: str, cache_dir: Path, force: bool = False) -> str:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"dice_{slug}.html"
    if cache_file.exists() and not force:
        return cache_file.read_text()
    resp = cffi_requests.get(f"https://dice.fm/venue/{slug}", impersonate="chrome", timeout=30)
    if resp.status_code != 200:
        raise DiceParseError(f"dice.fm returned HTTP {resp.status_code} for venue {slug}")
    cache_file.write_text(resp.text)
    time.sleep(REQUEST_DELAY_S)
    return resp.text


def parse_venue_page(html: str, venue_name: str, today: date | None = None) -> list[RAEvent]:
    tree = HTMLParser(html)
    blocks = tree.css('div[class*="EventParts__EventBlock"]')
    if not blocks:
        raise DiceParseError(
            f"{venue_name}: no event cards found — page structure changed, refusing to guess"
        )
    events: list[RAEvent] = []
    seen: set[str] = set()
    horizon = (today or date.today()) + timedelta(days=RA_LOOKAHEAD_DAYS)
    for block in blocks:
        a = block.css_first('a[href*="/event/"]')
        if a is None:
            continue
        m = _EVENT_HREF_RE.search(a.attributes.get("href", ""))
        if not m:
            continue
        eid = f"dice:{m.group(1)}"
        if eid in seen:
            continue
        texts = [t.strip() for t in block.text(separator="|").split("|") if t.strip()]
        if not texts:
            continue
        title = texts[0]
        ev_date = next((d for d in (parse_event_date(t, today) for t in texts[1:]) if d), None)
        if ev_date is None or ev_date > horizon:
            continue
        price = next((t for t in texts if t.startswith("$") or t.lower().startswith("from")), None)
        seen.add(eid)
        events.append(
            RAEvent(
                event_id=eid,
                event_date=ev_date,
                event_name=title,
                venue_name=venue_name,
                ticket_price=price,
                genres=[],  # Dice cards carry no genre tags
                artists=[] if title.lower() == "tba" else artists_from_title(title),
                source="dice",
            )
        )
    return events


def collect(snapshot_date: date | None = None, force: bool = False) -> list[RAEvent]:
    snapshot_date = snapshot_date or date.today()
    cache_dir = RAW_DIR / snapshot_date.isoformat()
    all_events: list[RAEvent] = []
    for slug, name in DICE_VENUES.items():
        html = fetch_venue_html(slug, cache_dir, force=force)
        events = parse_venue_page(html, name)
        print(f"  {name}: {len(events)} events")
        all_events.extend(events)
    return all_events


def write_snapshot(con, events: list[RAEvent], snapshot_date: date | None = None) -> int:
    """Idempotent per day, scoped to source='dice' so RA and Dice runs
    never clobber each other's rows."""
    snapshot_date = snapshot_date or date.today()
    now = datetime.now()
    con.execute(
        "DELETE FROM ra_events WHERE snapshot_date = ? AND source = 'dice'", [snapshot_date]
    )
    con.execute(
        "DELETE FROM ra_event_artists WHERE snapshot_date = ? AND event_id LIKE 'dice:%'",
        [snapshot_date],
    )
    con.executemany(
        """INSERT INTO ra_events
           (snapshot_date, collected_at, event_id, event_date, event_name,
            venue_name, ticket_price, genres, source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (snapshot_date, now, e.event_id, e.event_date, e.event_name,
             e.venue_name, e.ticket_price, "", e.source)
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
