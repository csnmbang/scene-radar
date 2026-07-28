"""Generate dashboard.html — a self-contained, zero-dependency dashboard.

Usage: uv run python build_dashboard.py   (run_all.py does it automatically)
Open dashboard.html in any browser. No server, no Streamlit.

All data is embedded as JSON at build time; charts are plain HTML/CSS/SVG.
Colors are the validated dark-mode categorical palette (see README).
"""

import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from scene_radar.config import GENRE_LABELS, PROJECT_ROOT, RA_GENRE_TO_BUCKET
from scene_radar.db import connect

OUT = PROJECT_ROOT / "dashboard.html"


def build_payload() -> dict:
    con = connect(read_only=True)
    try:
        snap = con.execute("SELECT max(snapshot_date) FROM gaps").fetchone()[0]
        if snap is None:
            raise SystemExit("No data — run `uv run python run_all.py` first.")
        ra_snap = con.execute("SELECT max(snapshot_date) FROM ra_events").fetchone()[0]

        today = datetime.now().date()
        gaps = [
            {"artist": r[0], "demand": r[1], "bookings": r[2], "gap": r[3],
             "genres": [g.strip() for g in (r[4] or "").split(",") if g.strip()],
             "tracks": r[5], "velocity": r[6], "new": r[7],
             "lastPlayed": (today - r[8]).days if r[8] else None}
            for r in con.execute(
                """SELECT g.artist_display, g.demand_score, g.miami_bookings_90d, g.gap_score,
                          g.genres, s.charting_tracks, s.rank_velocity, s.new_entries,
                          g.last_played
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
        for eid, gstr in con.execute(
            "SELECT event_id, genres FROM ra_events WHERE snapshot_date = ?", [ra_snap]
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
            {"venue": r[0], "events": r[1], "artists": r[2], "cost": r[3] or "—",
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
             "price": r[3] or "—", "source": r[4],
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

        timeline = []
        for norm, display, demand in con.execute(
            "SELECT artist_norm, artist_display, demand_score FROM gaps WHERE snapshot_date = ?",
            [snap],
        ).fetchall():
            ent = entered.get(norm)
            bkd = first_booked.get(norm)
            where = ""
            if bkd is not None:
                ra_norm = con.execute(
                    "SELECT ra_artist_norm FROM artist_matches WHERE snapshot_date = ? AND bp_artist_norm = ?",
                    [bkd, norm]).fetchone()
                if ra_norm:
                    where = " · ".join(
                        f"{v or 'TBA'} ({d.strftime('%b %d')})"
                        for d, v in con.execute(
                            """SELECT DISTINCT e.event_date, e.venue_name
                               FROM ra_events e JOIN ra_event_artists a
                                 ON a.event_id = e.event_id AND a.snapshot_date = e.snapshot_date
                               WHERE e.snapshot_date = ? AND a.artist_norm = ?
                                 AND e.event_date >= ?
                               ORDER BY e.event_date""",
                            [bkd, ra_norm[0], bkd]).fetchall())
            timeline.append({
                "artist": display, "demand": demand,
                "entered": ent.isoformat() if ent else None,
                "booked": bkd.isoformat() if bkd else None,
                "lead": (bkd - ent).days if (bkd and ent) else None,
                "where": where,
            })
        timeline.sort(key=lambda r: (r["booked"] or "", r["demand"]), reverse=True)

        # Receipts: did the radar see artists before Miami booked them?
        called = [t for t in timeline if t["lead"] is not None and t["lead"] > 0]
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
            "medianLead": median_lead,
            "conversion30d": conversion,
            "trackingDays": (snap - min(entered.values())).days + 1 if entered else 1,
        }

        n_events_ra = sum(1 for e in events if e["source"] != "dice")
        n_events_dice = sum(1 for e in events if e["source"] == "dice")
        n_matched = con.execute(
            "SELECT count(*) FROM artist_matches WHERE snapshot_date = ?", [snap]
        ).fetchone()[0]
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
            "gaps": gaps, "genres": genre_rows,
            "unmapped": unmapped, "inferred": inferred,
            "venues": venues, "events": events, "timeline": timeline,
            "receipts": receipts,
        }
    finally:
        con.close()


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
