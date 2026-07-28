"""Record a call — a claim you're making now, judged later.

  # artist call (auto-resolves when a Miami booking appears)
  uv run python log_call.py "Vlace" "Will get a Miami booking" \
      --why "72 demand, 6 hard techno tracks, never played Miami"

  # genre / venue / free-form calls (resolve by hand)
  uv run python log_call.py "melodic-house-techno" "Someone launches a melodic night" \
      --kind genre --why "16% of demand, 2% of supply"

  uv run python log_call.py --list
  uv run python log_call.py --resolve call-003 hit --note "Booked at Space Sep 5"

Calls live in calls.json and are committed to git, so each one carries a
GitHub commit timestamp that proves it predates the outcome.
"""

import argparse
import sys

from scene_radar import calls as calls_mod
from scene_radar.db import connect

STATUS_MARK = {"open": "○", "hit": "✓", "miss": "✗", "void": "–"}


def print_calls(calls: list[dict]) -> None:
    if not calls:
        print("No calls logged yet.")
        return
    for c in sorted(calls, key=lambda x: x["madeOn"], reverse=True):
        lead = f"  lead {c['leadDays']}d" if c.get("leadDays") is not None else ""
        print(f"{STATUS_MARK[c['status']]} {c['id']}  {c['madeOn']}  "
              f"[{c['kind']}] {c['subject']}{lead}")
        print(f"    {c['claim']}")
        if c.get("rationale"):
            print(f"    why: {c['rationale']}")
        if c.get("resolution"):
            print(f"    → {c['resolution']}")
    s = calls_mod.scoreboard(calls)
    rate = f"{s['hitRate']}%" if s["hitRate"] is not None else "n/a"
    print(f"\n{s['total']} calls · {s['open']} open · {s['hits']}/{s['resolved']} "
          f"resolved as hits ({rate})"
          + (f" · median lead {s['medianLead']}d" if s["medianLead"] else ""))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("subject", nargs="?", help="Artist name, genre bucket, or venue")
    p.add_argument("claim", nargs="?", help="What you're predicting")
    p.add_argument("--kind", default="artist", choices=calls_mod.KINDS)
    p.add_argument("--why", default="", help="Rationale — the reasoning behind the call")
    p.add_argument("--horizon", type=int, default=calls_mod.DEFAULT_HORIZON_DAYS,
                   help="Days before an unresolved call counts as a miss")
    p.add_argument("--list", action="store_true", help="Show all calls and the scoreboard")
    p.add_argument("--resolve", nargs=2, metavar=("CALL_ID", "STATUS"),
                   help="Manually resolve: --resolve call-003 hit")
    p.add_argument("--note", default="", help="Resolution note")
    args = p.parse_args()

    if args.list:
        print_calls(calls_mod.load())
        return

    if args.resolve:
        call_id, status = args.resolve
        c = calls_mod.resolve_manual(call_id, status, args.note or f"Marked {status}")
        print(f"{STATUS_MARK[c['status']]} {c['id']} → {c['status']}"
              + (f" (lead {c['leadDays']}d)" if c.get("leadDays") else ""))
        return

    if not args.subject or not args.claim:
        p.print_help()
        sys.exit(1)

    con = connect()
    try:
        c = calls_mod.add(con, args.kind, args.subject, args.claim,
                          rationale=args.why, horizon_days=args.horizon)
    finally:
        con.close()

    print(f"Logged {c['id']} — {c['subject']}: {c['claim']}")
    if c["kind"] == "artist":
        e = c["evidence"]
        print(f"  Evidence frozen: demand {e['demandScore']}, {e['chartingTracks']} tracks, "
              f"{e['bookingsAtCall']} bookings at call time"
              + (f", best rank #{e['bestChartRank']['rank']}" if e.get("bestChartRank") else ""))
    print(f"  Resolves automatically, or counts as a miss after {c['horizonDays']} days.")
    print("  Commit calls.json to timestamp it: git add calls.json && git commit")


if __name__ == "__main__":
    main()
