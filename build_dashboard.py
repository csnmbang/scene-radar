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

        gaps = [
            {"artist": r[0], "demand": r[1], "bookings": r[2], "gap": r[3],
             "genres": [g.strip() for g in (r[4] or "").split(",") if g.strip()],
             "tracks": r[5], "velocity": r[6], "new": r[7]}
            for r in con.execute(
                """SELECT g.artist_display, g.demand_score, g.miami_bookings_90d, g.gap_score,
                          g.genres, s.charting_tracks, s.rank_velocity, s.new_entries
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

        supply_mass = {b: 0.0 for b in GENRE_LABELS}
        unmapped = 0
        for (gstr,) in con.execute(
            "SELECT genres FROM ra_events WHERE snapshot_date = ? AND source != 'dice'", [ra_snap]
        ).fetchall():
            tags = [t.strip().lower() for t in (gstr or "").split(",") if t.strip()]
            hit = False
            for t in tags:
                b = RA_GENRE_TO_BUCKET.get(t)
                if b:
                    supply_mass[b] += 1
                    hit = True
            if tags and not hit:
                unmapped += 1

        td = sum(demand_mass.values()) or 1.0
        ts = sum(supply_mass.values()) or 1.0
        genre_rows = [
            {"label": label,
             "demand": round(100 * demand_mass[b] / td, 1),
             "supply": round(100 * supply_mass[b] / ts, 1),
             "delta": round(100 * demand_mass[b] / td - 100 * supply_mass[b] / ts, 1)}
            for b, label in GENRE_LABELS.items()
        ]

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

        matches = [
            {"bp": r[0], "ra": r[1], "conf": r[2], "method": r[3], "demand": r[4] or 0}
            for r in con.execute(
                """SELECT m.bp_artist_norm, m.ra_artist_norm, m.confidence, m.method,
                          s.demand_score
                   FROM artist_matches m
                   LEFT JOIN artist_scores s
                     ON s.artist_norm = m.bp_artist_norm AND s.snapshot_date = m.snapshot_date
                   WHERE m.snapshot_date = ? ORDER BY s.demand_score DESC NULLS LAST""",
                [snap],
            ).fetchall()
        ]

        n_events_ra = sum(1 for e in events if e["source"] != "dice")
        n_events_dice = sum(1 for e in events if e["source"] == "dice")
        return {
            "snapshot": snap.isoformat(),
            "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "stats": {
                "artists": len(gaps),
                "events": len(events),
                "eventsRa": n_events_ra,
                "eventsDice": n_events_dice,
                "venues": len(venues),
                "matched": len(matches),
                "hotGaps": sum(1 for g in gaps if g["demand"] >= 40 and g["bookings"] == 0),
            },
            "genreLabels": list(GENRE_LABELS.values()),
            "genreKeys": list(GENRE_LABELS.keys()),
            "gaps": gaps, "genres": genre_rows, "unmapped": unmapped,
            "venues": venues, "events": events, "matches": matches,
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
