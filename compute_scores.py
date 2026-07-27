"""Compute demand scores + gaps from the latest snapshots.

Usage: uv run python compute_scores.py
"""

from scene_radar.db import connect
from scene_radar.scoring import compute_gaps, compute_scores


def main() -> None:
    con = connect()
    snap = compute_scores(con)
    print(f"Scored artists for snapshot {snap}.")
    n = compute_gaps(con)
    print(f"Computed gaps for {n} artists.")
    con.close()


if __name__ == "__main__":
    main()
