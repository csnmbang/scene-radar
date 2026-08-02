"""The receipts ledger — timestamped calls with frozen evidence.

The Timeline tab answers "did artists we tracked get booked?" mechanically,
across every artist, with no cherry-picking. That's the unbiased baseline and
it stays. This module is the other half: the calls *you* make, recorded
before the outcome, with the metrics as they stood at that moment.

Why frozen evidence matters: a claim is only worth something if it can be
shown to predate the outcome. Each call stores the demand score, booking
count and chart position at the time it was made, and the file is committed
to git — so every entry carries a GitHub-side commit timestamp that can't be
back-dated. That's the difference between "I called Prospa" and proving it.

Artist calls resolve themselves when a Miami booking appears; genre and
venue calls resolve manually (`log_call.py --resolve`).
"""

import json
from datetime import date, datetime, timedelta

from .config import PROJECT_ROOT

CALLS_FILE = PROJECT_ROOT / "calls.json"

# A call that hasn't happened within its horizon is a miss. Being honest
# about misses is what makes the hit rate mean anything.
DEFAULT_HORIZON_DAYS = 120

KINDS = ("artist", "genre", "venue", "note")
STATUSES = ("open", "hit", "miss", "void")


def load() -> list[dict]:
    if not CALLS_FILE.exists():
        return []
    return json.loads(CALLS_FILE.read_text())


def save(calls: list[dict]) -> None:
    CALLS_FILE.write_text(json.dumps(calls, indent=1, ensure_ascii=False) + "\n")


def next_id(calls: list[dict]) -> str:
    n = 1 + max((int(c["id"].split("-")[1]) for c in calls if "-" in c["id"]), default=0)
    return f"call-{n:03d}"


def evidence_for_artist(con, artist_norm: str, snapshot_date) -> dict:
    """Freeze what we knew about this artist when the call was made."""
    row = con.execute(
        """SELECT g.artist_display, g.demand_score, g.miami_bookings_90d,
                  g.gap_score, g.last_played, s.charting_tracks, s.genres
           FROM gaps g JOIN artist_scores s
             ON s.artist_norm = g.artist_norm AND s.snapshot_date = g.snapshot_date
           WHERE g.snapshot_date = ? AND g.artist_norm = ?""",
        [snapshot_date, artist_norm],
    ).fetchone()
    if not row:
        return {}
    best = con.execute(
        """SELECT chart_genre, min(rank) FROM beatport_chart_entries
           WHERE snapshot_date = ? AND artist_norm = ? GROUP BY chart_genre
           ORDER BY 2 LIMIT 1""",
        [snapshot_date, artist_norm],
    ).fetchone()
    return {
        "display": row[0],
        "demandScore": row[1],
        "bookingsAtCall": row[2],
        "gapScore": row[3],
        "lastPlayedAtCall": row[4].isoformat() if row[4] else None,
        "chartingTracks": row[5],
        "genres": row[6],
        "bestChartRank": {"chart": best[0], "rank": best[1]} if best else None,
        "snapshotDate": str(snapshot_date),
    }


def evidence_for_genre(con, bucket: str, snapshot_date) -> dict:
    """Freeze the demand-vs-supply split for a genre bucket."""
    demand = con.execute(
        """SELECT sum(demand_score) FROM artist_scores
           WHERE snapshot_date = ? AND genres LIKE ?""",
        [snapshot_date, f"%{bucket}%"],
    ).fetchone()[0]
    total = con.execute(
        "SELECT sum(demand_score) FROM artist_scores WHERE snapshot_date = ?",
        [snapshot_date],
    ).fetchone()[0]
    return {
        "bucket": bucket,
        "demandSharePct": round(100 * (demand or 0) / (total or 1), 1),
        "snapshotDate": str(snapshot_date),
    }


def add(
    con,
    kind: str,
    subject: str,
    claim: str,
    rationale: str = "",
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    subject_norm: str | None = None,
    source_url: str = "",
) -> dict:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    calls = load()
    snapshot_date = con.execute("SELECT max(snapshot_date) FROM gaps").fetchone()[0]

    if kind == "artist":
        from .normalize import norm_artist

        subject_norm = subject_norm or norm_artist(subject)
        evidence = evidence_for_artist(con, subject_norm, snapshot_date)
        if not evidence:
            raise SystemExit(
                f"'{subject}' isn't in the current snapshot — check the spelling, "
                f"or use --kind note if it's not a charting artist."
            )
        if evidence["bookingsAtCall"] > 0:
            # Predicting something already true is how a track record gets
            # discredited. Block it at the source rather than logging a call
            # that can never be an honest hit.
            raise SystemExit(
                f"{evidence['display']} already has {evidence['bookingsAtCall']} upcoming "
                f"Miami booking(s) — there's nothing left to predict, so this can't be a "
                f"hit. Use --kind note to record the observation instead."
            )
        subject = evidence["display"]
    elif kind == "genre":
        subject_norm = subject_norm or subject
        evidence = evidence_for_genre(con, subject_norm, snapshot_date)
    else:
        subject_norm = subject_norm or subject.lower()
        evidence = {"snapshotDate": str(snapshot_date)}

    call = {
        "id": next_id(calls),
        "madeOn": date.today().isoformat(),
        "madeAt": datetime.now().isoformat(timespec="seconds"),
        "kind": kind,
        "subject": subject,
        "subjectNorm": subject_norm,
        "claim": claim,
        "rationale": rationale,
        "sourceUrl": source_url,
        "horizonDays": horizon_days,
        "status": "open",
        "resolvedOn": None,
        "resolution": None,
        "leadDays": None,
        "evidence": evidence,
    }
    calls.append(call)
    save(calls)
    return call


def auto_resolve(con) -> list[dict]:
    """Close out artist calls that came true, and mark expired ones as misses.

    A call is a hit when a Miami booking exists that was announced *after*
    the call was made — a booking already on the books at call time is not a
    prediction, and `bookingsAtCall` records that so it can't be claimed later.
    """
    calls = load()
    if not calls:
        return []

    first_booked = dict(
        con.execute(
            """SELECT artist_norm, min(snapshot_date) FROM gaps
               WHERE miami_bookings_90d > 0 GROUP BY artist_norm"""
        ).fetchall()
    )
    today = date.today()
    changed = []

    for c in calls:
        if c["status"] != "open":
            continue
        if c["kind"] == "note":
            continue  # an observation isn't a prediction; it can't hit or miss
        made = date.fromisoformat(c["madeOn"])

        if c["kind"] == "artist":
            booked_on = first_booked.get(c["subjectNorm"])
            # Only a hit if the artist had NO booking when the call was made
            # and one appeared afterwards. Without the first condition a call
            # on an already-booked artist resolves instantly at 0-day lead,
            # which is a fake receipt.
            already_booked = (c.get("evidence") or {}).get("bookingsAtCall", 0) > 0
            if booked_on and booked_on > made and not already_booked:
                c["status"] = "hit"
                c["resolvedOn"] = booked_on.isoformat()
                c["leadDays"] = (booked_on - made).days
                c["resolution"] = f"Miami booking first seen {booked_on.isoformat()}"
                changed.append(c)
                continue

        if (today - made).days > c["horizonDays"]:
            c["status"] = "miss"
            c["resolvedOn"] = today.isoformat()
            c["resolution"] = f"No outcome within {c['horizonDays']} days"
            changed.append(c)

    if changed:
        save(calls)
    return changed


def resolve_manual(call_id: str, status: str, note: str) -> dict:
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    calls = load()
    for c in calls:
        if c["id"] == call_id:
            c["status"] = status
            c["resolvedOn"] = date.today().isoformat()
            c["resolution"] = note
            if status == "hit":
                c["leadDays"] = (date.today() - date.fromisoformat(c["madeOn"])).days
            save(calls)
            return c
    raise SystemExit(f"No call with id {call_id}")


def scoreboard(calls: list[dict]) -> dict:
    """Hit rate and lead times — the numbers that go on the resume.

    Notes and voided calls are excluded: a note makes no prediction, and a
    voided call had a broken premise. Neither should move a hit rate in
    either direction.
    """
    predictions = [c for c in calls if c["kind"] != "note" and c["status"] != "void"]
    resolved = [c for c in predictions if c["status"] in ("hit", "miss")]
    hits = [c for c in resolved if c["status"] == "hit"]
    leads = sorted(c["leadDays"] for c in hits if c["leadDays"] is not None)
    open_calls = [c for c in predictions if c["status"] == "open"]
    return {
        "total": len(predictions),
        "notes": sum(1 for c in calls if c["kind"] == "note"),
        "voided": sum(1 for c in calls if c["status"] == "void"),
        "open": len(open_calls),
        "hits": len(hits),
        "resolved": len(resolved),
        "hitRate": round(100 * len(hits) / len(resolved), 1) if resolved else None,
        "medianLead": leads[len(leads) // 2] if leads else None,
        "bestLead": max(leads) if leads else None,
        "oldestOpen": min((c["madeOn"] for c in open_calls), default=None),
    }
