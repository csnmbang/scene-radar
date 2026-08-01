"""Generate dashboard.html — a self-contained, zero-dependency dashboard.

Usage: uv run python build_dashboard.py   (run_all.py does it automatically)
Open dashboard.html in any browser. No server, no Streamlit.

All data is embedded as JSON at build time; charts are plain HTML/CSS/SVG.
Colors are the validated dark-mode categorical palette (see README).
"""

import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from scene_radar import calls as calls_mod
from scene_radar.config import (
    GENRE_LABELS,
    PROJECT_ROOT,
    RA_GENRE_TO_BUCKET,
    RA_LOOKBACK_DAYS,
    RECENCY_WINDOW_DAYS,
)
from scene_radar.db import connect

OUT = PROJECT_ROOT / "dashboard.html"

# A booking discovered less than a week after an artist starts charting tells
# you how fast we ingest a public announcement, not that we saw it coming.
# Only leads at or above this count as foresight.
MIN_CREDIBLE_LEAD_DAYS = 7


def build_payload() -> dict:
    con = connect(read_only=True)
    try:
        snap = con.execute("SELECT max(snapshot_date) FROM gaps").fetchone()[0]
        if snap is None:
            raise SystemExit("No data — run `uv run python run_all.py` first.")
        ra_snap = con.execute("SELECT max(snapshot_date) FROM ra_events").fetchone()[0]

        today = datetime.now().date()

        # Per-artist charting tracks — the "why is this artist here" hover.
        track_detail: dict[str, list[dict]] = {}
        for norm, rank, title, mix, label, chart in con.execute(
            """SELECT artist_norm, rank, track_title, mix_name, label, chart_genre
               FROM beatport_chart_entries WHERE snapshot_date = ?
               ORDER BY artist_norm, rank""",
            [snap],
        ).fetchall():
            track_detail.setdefault(norm, []).append(
                {"rank": rank, "title": title, "mix": mix, "label": label, "chart": chart}
            )

        gaps = [
            {"norm": r[9], "artist": r[0], "demand": r[1], "bookings": r[2], "gap": r[3],
             "genres": [g.strip() for g in (r[4] or "").split(",") if g.strip()],
             "tracks": r[5], "velocity": r[6], "new": r[7],
             "lastPlayed": (today - r[8]).days if r[8] else None,
             "trackList": track_detail.get(r[9], [])[:8]}
            for r in con.execute(
                """SELECT g.artist_display, g.demand_score, g.miami_bookings_90d, g.gap_score,
                          g.genres, s.charting_tracks, s.rank_velocity, s.new_entries,
                          g.last_played, g.artist_norm
                   FROM gaps g JOIN artist_scores s
                     ON s.artist_norm = g.artist_norm AND s.snapshot_date = g.snapshot_date
                   WHERE g.snapshot_date = ? ORDER BY g.gap_score DESC""",
                [snap],
            ).fetchall()
        ]

        # genre demand share: artist demand split across their chart buckets
        demand_mass = {b: 0.0 for b in GENRE_LABELS}
        for demand, genres in con.execute(
            "SELECT demand_score, genres FROM artist_scores WHERE snapshot_date = ?", [snap]
        ).fetchall():
            buckets = [b.strip() for b in genres.split(",") if b.strip() in demand_mass]
            for b in buckets:
                demand_mass[b] += demand / len(buckets)

        # supply share: RA tag mapping first; events with no mapped tag (all of
        # Dice, plus untagged RA events) fall back to the chart buckets of any
        # lineup artist we matched to Beatport ("inferred from lineup").
        artist_buckets: dict[str, set[str]] = {}
        for norm, genres in con.execute(
            "SELECT artist_norm, genres FROM artist_scores WHERE snapshot_date = ?", [snap]
        ).fetchall():
            artist_buckets[norm] = {b.strip() for b in genres.split(",") if b.strip() in GENRE_LABELS}
        ra_to_bp = {ra: bp for bp, ra in con.execute(
            "SELECT bp_artist_norm, ra_artist_norm FROM artist_matches WHERE snapshot_date = ?",
            [snap],
        ).fetchall()}

        supply_mass = {b: 0.0 for b in GENRE_LABELS}
        unmapped = 0
        inferred = 0
        # Upcoming events only: demand is "what's hot now", so supply has to be
        # "what's booked ahead". The 12-month lookback exists for the
        # last-played signal, not to dilute the forward-looking genre gap.
        for eid, gstr in con.execute(
            """SELECT event_id, genres FROM ra_events
               WHERE snapshot_date = ? AND event_date >= current_date""",
            [ra_snap],
        ).fetchall():
            tags = [t.strip().lower() for t in (gstr or "").split(",") if t.strip()]
            buckets = {RA_GENRE_TO_BUCKET[t] for t in tags if t in RA_GENRE_TO_BUCKET}
            if not buckets:  # infer from matched lineup artists' chart buckets
                lineup = [n for (n,) in con.execute(
                    "SELECT artist_norm FROM ra_event_artists WHERE event_id = ? AND snapshot_date = ?",
                    [eid, ra_snap]).fetchall()]
                for n in lineup:
                    buckets |= artist_buckets.get(ra_to_bp.get(n, n), set())
                if buckets:
                    inferred += 1
            if buckets:
                for b in buckets:
                    supply_mass[b] += 1
            else:
                unmapped += 1

        td = sum(demand_mass.values()) or 1.0
        ts = sum(supply_mass.values()) or 1.0
        genre_rows = sorted(
            [
                {"label": label,
                 "demand": round(100 * demand_mass[b] / td, 1),
                 "supply": round(100 * supply_mass[b] / ts, 1),
                 "delta": round(100 * demand_mass[b] / td - 100 * supply_mass[b] / ts, 1)}
                for b, label in GENRE_LABELS.items()
            ],
            key=lambda r: r["delta"],
            reverse=True,  # concrete order: most under-served first, everywhere
        )

        venues = [
            {"venue": r[0], "events": r[1], "artists": r[2],
             "cost": normalize_price(r[3])[0],
             "source": r[5], "topGenres": _top_tags(r[4])}
            for r in con.execute(
                """SELECT venue_name, count(DISTINCT e.event_id),
                          count(DISTINCT a.artist_norm),
                          mode(nullif(ticket_price, '')),
                          string_agg(DISTINCT nullif(e.genres,''), ' | '),
                          mode(e.source)
                   FROM ra_events e
                   LEFT JOIN ra_event_artists a
                     ON a.event_id = e.event_id AND a.snapshot_date = e.snapshot_date
                   WHERE e.snapshot_date = ? AND venue_name IS NOT NULL
                     AND e.event_date >= current_date
                   GROUP BY venue_name ORDER BY 2 DESC""",
                [ra_snap],
            ).fetchall()
        ]

        events = [
            {"date": r[0].isoformat(), "name": r[1], "venue": r[2] or "TBA",
             "price": normalize_price(r[3])[0],
             "priceValue": normalize_price(r[3])[1],
             "source": r[4],
             "artists": [a for (a,) in con.execute(
                 "SELECT artist_raw FROM ra_event_artists WHERE event_id = ? AND snapshot_date = ?",
                 [r[5], ra_snap]).fetchall()]}
            for r in con.execute(
                """SELECT event_date, event_name, venue_name, ticket_price, source, event_id
                   FROM ra_events WHERE snapshot_date = ? AND event_date >= current_date
                   ORDER BY event_date""",
                [ra_snap],
            ).fetchall()
        ]

        # Timeline: when each artist first hit the radar vs when a Miami
        # booking first showed up for them. Grows a row of receipts per day.
        entered = dict(con.execute(
            "SELECT artist_norm, min(snapshot_date) FROM artist_scores GROUP BY artist_norm"
        ).fetchall())
        first_booked = dict(con.execute(
            "SELECT artist_norm, min(snapshot_date) FROM gaps WHERE miami_bookings_90d > 0 GROUP BY artist_norm"
        ).fetchall())

        # When did each venue first enter the dataset? Adding a venue or a
        # promoter backfills its whole calendar, which looks like a burst of
        # "predictions" but is really just ingestion. Those are excluded below.
        venue_first_seen = dict(
            con.execute(
                """SELECT venue_name, min(snapshot_date) FROM ra_events
                   WHERE venue_name IS NOT NULL GROUP BY 1"""
            ).fetchall()
        )
        tracking_start = min(entered.values()) if entered else snap

        timeline = []
        for norm, display, demand in con.execute(
            "SELECT artist_norm, artist_display, demand_score FROM gaps WHERE snapshot_date = ?",
            [snap],
        ).fetchall():
            ent = entered.get(norm)
            bkd = first_booked.get(norm)
            where = ""
            booked_venues: list[str] = []
            if bkd is not None:
                ra_norm = con.execute(
                    "SELECT ra_artist_norm FROM artist_matches WHERE snapshot_date = ? AND bp_artist_norm = ?",
                    [bkd, norm]).fetchone()
                if ra_norm:
                    hits = con.execute(
                        """SELECT DISTINCT e.event_date, e.venue_name
                           FROM ra_events e JOIN ra_event_artists a
                             ON a.event_id = e.event_id AND a.snapshot_date = e.snapshot_date
                           WHERE e.snapshot_date = ? AND a.artist_norm = ?
                             AND e.event_date >= ?
                           ORDER BY e.event_date""",
                        [bkd, ra_norm[0], bkd]).fetchall()
                    where = " · ".join(f"{v or 'TBA'} ({d.strftime('%b %d')})" for d, v in hits)
                    booked_venues = [v for _, v in hits if v]
            lead = (bkd - ent).days if (bkd and ent) else None

            # Why a lead may not be real foresight:
            #   backfill  — the venue joined the dataset that same day, so its
            #               whole existing calendar arrived at once
            #   warm-up   — discovered while the artist set itself was still
            #               being established (first days of tracking)
            #   ingestion — under a week: dominated by how fast we notice an
            #               already-public announcement, not by predicting it
            quality = None
            if lead is not None and lead > 0:
                venue_is_new = any(
                    venue_first_seen.get(v) is not None and venue_first_seen[v] >= bkd
                    for v in booked_venues
                )
                if venue_is_new:
                    quality = "backfill"
                elif (ent - tracking_start).days < 1:
                    quality = "warmup"
                elif lead < MIN_CREDIBLE_LEAD_DAYS:
                    quality = "ingestion"
                else:
                    quality = "genuine"

            timeline.append({
                "artist": display, "demand": demand,
                "entered": ent.isoformat() if ent else None,
                "booked": bkd.isoformat() if bkd else None,
                "lead": lead,
                "quality": quality,
                "where": where,
            })
        timeline.sort(key=lambda r: (r["booked"] or "", r["demand"]), reverse=True)

        # Receipts. Only 'genuine' leads count toward the headline — an
        # earlier version counted every positive lead, which turned source
        # additions and matching lag into fake foresight.
        called = [t for t in timeline if t["quality"] == "genuine"]
        discounted = [t for t in timeline if t["quality"] in ("backfill", "warmup", "ingestion")]
        median_lead = None
        if called:
            leads = sorted(t["lead"] for t in called)
            median_lead = leads[len(leads) // 2]
        # 30-day hot-gap conversion: of artists first seen as a hot gap
        # (demand>=40, 0 bookings) at least 30 days ago, how many got booked
        # within 30 days of that sighting? Needs >=30 days of snapshots.
        conversion = None
        cohort = con.execute(
            """WITH first_hot AS (
                 SELECT artist_norm, min(snapshot_date) AS hot_date
                 FROM gaps WHERE demand_score >= 40 AND miami_bookings_90d = 0
                 GROUP BY artist_norm)
               SELECT count(*),
                      count(*) FILTER (WHERE b.booked_date IS NOT NULL
                                       AND b.booked_date <= f.hot_date + INTERVAL 30 DAY)
               FROM first_hot f
               LEFT JOIN (SELECT artist_norm, min(snapshot_date) AS booked_date
                          FROM gaps WHERE miami_bookings_90d > 0 GROUP BY artist_norm) b
                 USING (artist_norm)
               WHERE f.hot_date <= ? - INTERVAL 30 DAY""",
            [snap],
        ).fetchone()
        if cohort and cohort[0]:
            conversion = round(100 * cohort[1] / cohort[0], 1)
        receipts = {
            "called": len(called),
            "discounted": len(discounted),
            "medianLead": median_lead,
            "conversion30d": conversion,
            "trackingDays": (snap - min(entered.values())).days + 1 if entered else 1,
            "minCredibleLead": MIN_CREDIBLE_LEAD_DAYS,
        }

        n_events_ra = sum(1 for e in events if e["source"] != "dice")
        n_events_dice = sum(1 for e in events if e["source"] == "dice")
        n_matched = con.execute(
            "SELECT count(*) FROM artist_matches WHERE snapshot_date = ?", [snap]
        ).fetchone()[0]
        # RA often lists a past event with no lineup — bound how confident
        # "no show found" can be, and say so in the UI rather than implying
        # certainty.
        no_lineup, past_total = con.execute(
            """SELECT count(*) FILTER (WHERE a.event_id IS NULL), count(*)
               FROM ra_events e
               LEFT JOIN (SELECT DISTINCT event_id, snapshot_date FROM ra_event_artists) a
                 ON a.event_id = e.event_id AND a.snapshot_date = e.snapshot_date
               WHERE e.snapshot_date = ? AND e.event_date < current_date""",
            [ra_snap],
        ).fetchone()
        return {
            "snapshot": snap.isoformat(),
            "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "stats": {
                "artists": len(gaps),
                "events": len(events),
                "eventsRa": n_events_ra,
                "eventsDice": n_events_dice,
                "venues": len(venues),
                "matched": n_matched,
                "hotGaps": sum(1 for g in gaps if g["demand"] >= 40 and g["bookings"] == 0),
            },
            "genreLabels": list(GENRE_LABELS.values()),
            "genreKeys": list(GENRE_LABELS.keys()),
            "lookbackDays": RA_LOOKBACK_DAYS,
            "recencyWindow": RECENCY_WINDOW_DAYS,
            "history": {"pastEvents": past_total, "noLineup": no_lineup},
            "gaps": gaps, "genres": genre_rows,
            "unmapped": unmapped, "inferred": inferred,
            "venues": venues, "events": events, "timeline": timeline,
            "receipts": receipts,
            "calls": calls_mod.load(),
            "scoreboard": calls_mod.scoreboard(calls_mod.load()),
        }
    finally:
        con.close()


_PRICE_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def normalize_price(raw) -> tuple[str, float | None]:
    """Sources disagree wildly: RA sends None, a bare '$' tier, plain numbers
    ('20', '34.10'), a European decimal comma ('18,95'), '$20+' and
    '$219-$439'; Dice sends 'From $30' / 'From Free'. Render one consistent
    column and return a sortable value alongside it."""
    if raw is None or not str(raw).strip():
        return "—", None
    s = str(raw).strip()
    low = s.lower()
    if "free" in low:
        return "Free", 0.0
    nums = [float(n.replace(",", ".")) for n in _PRICE_NUM.findall(s)]
    if not nums:
        # bare '$' / '$$' is RA's relative tier, not an amount
        return (s, None) if set(s) == {"$"} else ("—", None)
    if max(nums) == 0:
        return "Free", 0.0
    lo, hi = min(nums), max(nums)
    if len(nums) > 1 and hi != lo:
        return f"${lo:,.0f}–{hi:,.0f}", lo
    prefix = "from " if low.startswith("from") else ""
    suffix = "+" if s.endswith("+") else ""
    return f"{prefix}${lo:,.0f}{suffix}", lo


def _top_tags(s, n: int = 3) -> str:
    if not isinstance(s, str):
        return "—"
    tags = [t.strip() for part in s.split("|") for t in part.split(",") if t.strip()]
    return ", ".join(t for t, _ in Counter(tags).most_common(n)) or "—"


def render(payload: dict) -> str:
    template = (Path(__file__).parent / "scene_radar" / "dashboard_template.html").read_text()
    return template.replace("/*__DATA__*/{}", json.dumps(payload, separators=(",", ":")))


def main() -> None:
    payload = build_payload()
    OUT.write_text(render(payload))
    print(f"Dashboard written to {OUT}  ({OUT.stat().st_size // 1024} KB)")
    print("Open it directly in a browser — no server needed.")


if __name__ == "__main__":
    main()
