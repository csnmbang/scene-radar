"""Run the whole pipeline: Beatport -> RA -> scores -> gaps.

Usage: uv run python run_all.py [--force]
Then:  uv run streamlit run app.py
"""

import sys
import traceback

from dotenv import load_dotenv

from scene_radar import beatport, dice, manual, ra
from scene_radar.db import connect
from scene_radar.scoring import compute_gaps, compute_scores


def main() -> int:
    load_dotenv()
    force = "--force" in sys.argv

    print("[1/6] Beatport charts")
    try:
        bp_entries = beatport.collect(force=force)
    except Exception:
        traceback.print_exc()
        print("Beatport collection FAILED — nothing written.")
        return 1

    print("[2/6] RA Miami events")
    try:
        ra_events = ra.collect(force=force)
    except Exception:
        traceback.print_exc()
        print("RA collection FAILED — nothing written.")
        return 1

    print("[3/6] Dice venues (Club Space / Sable / M2)")
    try:
        dice_events = dice.collect(force=force)
    except Exception:
        traceback.print_exc()
        print("Dice collection FAILED — nothing written.")
        return 1

    print("[4/6] Manual events")
    manual_events = manual.collect()

    con = connect()
    try:
        n_bp = beatport.write_snapshot(con, bp_entries)
        n_ra = ra.write_snapshot(con, ra_events)
        n_dice = dice.write_snapshot(con, dice_events)
        n_manual = manual.write_snapshot(con, manual_events)
        print(f"  wrote {n_bp} chart rows, {n_ra} RA + {n_dice} Dice + {n_manual} manual events")

        print("[5/6] Scores + gaps")
        snap = compute_scores(con)
        n_gaps = compute_gaps(con)
        print(f"  snapshot {snap}: {n_gaps} artists scored")
    finally:
        con.close()

    print("[6/6] Dashboard")
    import build_dashboard

    build_dashboard.main()

    print("\nDone. Open dashboard.html in your browser.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
