"""Demand scoring and gap computation.

demand_score formula (tune the weights in config.py):

    chart_points  = sum over charting tracks of (101 - rank) / 10
                    # rank 1 -> 10 pts, rank 100 -> 0.1 pts, per chart appearance
    raw_score     = W_TRACK_COUNT * charting_tracks     # breadth of presence
                  + W_CHART_POINTS * chart_points        # height on the charts
                  + W_VELOCITY * max(rank_velocity, 0)   # climbing since last run
                  + W_NEW_ENTRY * new_entries            # fresh entries score extra
    demand_score  = 100 * raw_score / max(raw_score)     # scaled so leader = 100

rank_velocity = sum over tracks present in BOTH snapshots of (prev_rank - rank);
positive means the artist's tracks are climbing. new_entries = tracks charting
now that weren't in the previous snapshot. On the very first run there is no
previous snapshot, so velocity and new_entries are 0 and the score is purely
rank-based — exactly what the spec asks for.

gap_score = demand_score * (1 / (1 + miami_bookings_90d)) * recency_factor

recency_factor = min(1, days_since_last_miami_show / RECENCY_WINDOW_DAYS):
an artist who played Miami last weekend isn't a gap even with zero future
dates — the market already has them. Never played (or played 90+ days ago)
= full gap.
"""

from collections import defaultdict
from datetime import date, datetime

import duckdb

from .config import (
    ARTIST_ALIASES,
    RECENCY_WINDOW_DAYS,
    W_CHART_POINTS,
    W_NEW_ENTRY,
    W_TRACK_COUNT,
    W_VELOCITY,
)
from .db import replace_snapshot
from .normalize import match_artists


def _snapshot_dates(con: duckdb.DuckDBPyConnection) -> tuple[date | None, date | None]:
    rows = con.execute(
        "SELECT DISTINCT snapshot_date FROM beatport_chart_entries ORDER BY snapshot_date DESC LIMIT 2"
    ).fetchall()
    latest = rows[0][0] if rows else None
    prev = rows[1][0] if len(rows) > 1 else None
    return latest, prev


def compute_scores(con: duckdb.DuckDBPyConnection) -> date:
    """Compute per-artist demand scores from the latest Beatport snapshot
    (with deltas vs the previous one) and write artist_scores."""
    latest, prev = _snapshot_dates(con)
    if latest is None:
        raise RuntimeError("No Beatport snapshots in DB — run collect_beatport.py first")

    cur = con.execute(
        """SELECT artist_norm, artist_raw, chart_genre, track_id, rank
           FROM beatport_chart_entries WHERE snapshot_date = ?""",
        [latest],
    ).fetchall()

    prev_ranks: dict[tuple[str, str, int], int] = {}
    prev_genres: set[str] = set()  # charts present last time; a chart we only
    # started tracking today has no baseline, so its tracks must not count as
    # "new entries" or velocity (they'd all get the W_NEW_ENTRY bonus at once)
    if prev is not None:
        for artist_norm, genre, track_id, rank in con.execute(
            """SELECT artist_norm, chart_genre, track_id, rank
               FROM beatport_chart_entries WHERE snapshot_date = ?""",
            [prev],
        ).fetchall():
            prev_ranks[(artist_norm, genre, track_id)] = rank
            prev_genres.add(genre)

    stats: dict[str, dict] = defaultdict(
        lambda: {"tracks": 0, "points": 0.0, "velocity": 0.0, "new": 0,
                 "genres": set(), "display": ""}
    )
    for artist_norm, artist_raw, genre, track_id, rank in cur:
        s = stats[artist_norm]
        s["tracks"] += 1
        s["points"] += (101 - rank) / 10.0
        s["genres"].add(genre)
        s["display"] = s["display"] or artist_raw
        if prev is not None and genre in prev_genres:
            key = (artist_norm, genre, track_id)
            if key in prev_ranks:
                s["velocity"] += prev_ranks[key] - rank
            else:
                s["new"] += 1

    raw_scores = {
        a: (
            W_TRACK_COUNT * s["tracks"]
            + W_CHART_POINTS * s["points"]
            + W_VELOCITY * max(s["velocity"], 0)
            + W_NEW_ENTRY * s["new"]
        )
        for a, s in stats.items()
    }
    top = max(raw_scores.values()) if raw_scores else 1.0

    now = datetime.now()
    replace_snapshot(con, "artist_scores", latest)
    con.executemany(
        """INSERT INTO artist_scores
           (computed_at, snapshot_date, artist_norm, artist_display, demand_score,
            charting_tracks, chart_points, rank_velocity, new_entries, genres)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (now, latest, a, s["display"], round(100 * raw_scores[a] / top, 2),
             s["tracks"], round(s["points"], 2), s["velocity"], s["new"],
             ", ".join(sorted(s["genres"])))
            for a, s in stats.items()
        ],
    )
    return latest


def compute_gaps(con: duckdb.DuckDBPyConnection) -> int:
    """Fuzzy-join Beatport artists to Miami lineups; write matches + gaps."""
    latest, _ = _snapshot_dates(con)
    if latest is None:
        raise RuntimeError("No scores to compute gaps from")

    ra_snapshot = con.execute(
        "SELECT max(snapshot_date) FROM ra_events"
    ).fetchone()[0]
    if ra_snapshot is None:
        raise RuntimeError("No RA snapshots in DB — run collect_ra.py first")

    bp_norms = [r[0] for r in con.execute(
        "SELECT DISTINCT artist_norm FROM artist_scores WHERE snapshot_date = ?", [latest]
    ).fetchall()]
    ra_norms = [r[0] for r in con.execute(
        "SELECT DISTINCT artist_norm FROM ra_event_artists WHERE snapshot_date = ?", [ra_snapshot]
    ).fetchall()]

    matches = match_artists(bp_norms, ra_norms)
    replace_snapshot(con, "artist_matches", latest)
    if matches:
        con.executemany(
            """INSERT INTO artist_matches (snapshot_date, bp_artist_norm, ra_artist_norm, confidence, method)
               VALUES (?, ?, ?, ?, ?)""",
            [(latest, bp, ra, conf, meth) for bp, ra, conf, meth in matches],
        )

    # bookings per RA artist key = distinct upcoming events they appear on
    booking_counts: dict[str, int] = dict(
        con.execute(
            """SELECT a.artist_norm, count(DISTINCT a.event_id)
               FROM ra_event_artists a
               JOIN ra_events e ON e.event_id = a.event_id AND e.snapshot_date = a.snapshot_date
               WHERE a.snapshot_date = ? AND e.event_date >= current_date
               GROUP BY a.artist_norm""",
            [ra_snapshot],
        ).fetchall()
    )
    match_map = {bp: ra for bp, ra, _, _ in matches}

    # most recent PAST Miami show per artist key, across ALL snapshots —
    # history accumulates, so shows collected weeks ago still count here
    last_played: dict[str, object] = dict(
        con.execute(
            """SELECT a.artist_norm, max(e.event_date)
               FROM ra_event_artists a
               JOIN ra_events e ON e.event_id = a.event_id AND e.snapshot_date = a.snapshot_date
               WHERE e.event_date < current_date
               GROUP BY a.artist_norm"""
        ).fetchall()
    )

    scores = con.execute(
        """SELECT artist_norm, artist_display, demand_score, genres
           FROM artist_scores WHERE snapshot_date = ?""",
        [latest],
    ).fetchall()

    now = datetime.now()
    today = date.today()
    replace_snapshot(con, "gaps", latest)
    rows = []
    for artist_norm, display, demand, genres in scores:
        # A collective's bookings live under its members' names, so check
        # every alias as well as the matched name.
        keys = {match_map.get(artist_norm, "")} | set(ARTIST_ALIASES.get(artist_norm, []))
        keys.discard("")
        bookings = sum(booking_counts.get(k, 0) for k in keys)
        played = [last_played[k] for k in keys if last_played.get(k)]
        lp = max(played) if played else None
        recency = min(1.0, (today - lp).days / RECENCY_WINDOW_DAYS) if lp else 1.0
        gap = round(demand * (1.0 / (1.0 + bookings)) * recency, 2)
        rows.append((now, latest, artist_norm, display, demand, bookings, gap, genres, lp))
    con.executemany(
        """INSERT INTO gaps
           (computed_at, snapshot_date, artist_norm, artist_display, demand_score,
            miami_bookings_90d, gap_score, genres, last_played)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    return len(rows)
