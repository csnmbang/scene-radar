# 📡 Scene Radar — Miami

Personal competitive-intelligence dashboard for electronic music: what's
trending on Beatport (demand) vs. who's actually booked in Miami (supply,
from Resident Advisor **plus** the Dice-only rooms — Club Space, Sable, M2).
**Gap = artists blowing up with no local dates.**

## Setup (5 steps)

1. Install [uv](https://docs.astral.sh/uv/) if you don't have it:
   `curl -LsSf https://astral.sh/uv/install.sh | sh`
2. (Optional) `cp .env.example .env` and set `APIFY_TOKEN` — without it the RA
   collector uses RA's public GraphQL endpoint directly, which works fine.
3. Collect + score + build: `uv run python run_all.py`
4. Open **`dashboard.html`** in your browser. That's it — no server.
5. Tests: `uv run pytest`

Re-running `run_all.py` on the same day reuses the on-disk cache and replaces
that day's snapshot (never duplicates). Run it weekly — momentum scoring gets
sharper once there are ≥ 2 snapshots to diff.

## What's inside

| Path | What |
|---|---|
| `collect_beatport.py` | Top 100 for 5 genres → `beatport_chart_entries` |
| `collect_ra.py` | RA Miami events next 90d → `ra_events`, `ra_event_artists` |
| `collect_dice.py` | Dice venue pages (Club Space / Sable / M2) → same tables, `source='dice'` |
| `compute_scores.py` | Demand scores + gap table (`artist_scores`, `gaps`) |
| `build_dashboard.py` | Renders `dashboard.html` (self-contained, dark, zero deps) |
| `run_all.py` | All of the above in order |
| `scene_radar/` | The actual logic (config, db, collectors, scoring, matching, template) |
| `data/` | `scene_radar.duckdb` + raw response cache per day (gitignored) |
| `tests/` | Parsing tests against saved real fixtures — break = source changed |

Dashboard views: **Gaps** (the product — filterable artist gap table),
**Genres** (demand share vs supply share), **Venues** (who books what),
**Supply** (raw event feed with RA/DICE source badges), **Matches** (join
confidence).

## How scoring works (tune in `scene_radar/config.py`)

```
chart_points = Σ (101 - rank) / 10            # height on the charts
raw = 10·charting_tracks + chart_points
    + 0.5·max(rank_velocity, 0)               # climbing vs previous snapshot
    + 8·new_entries                            # fresh entries weighted higher
demand_score = 100 · raw / max(raw)            # leader = 100
gap_score = demand_score / (1 + miami_bookings_90d)
```

First run has no previous snapshot → velocity/new-entries are 0, score is
purely rank-based (per spec). Artist names are normalized (lowercase,
diacritics stripped, `(BR)`/`(DJ set)` suffixes dropped) and joined
exact-first, then rapidfuzz `token_sort_ratio ≥ 90` **plus a per-token guard**
— without the guard, "chris lake" fuzzy-matches "chris clarke" at 90.9 and a
real booking gets credited to the wrong artist. Match confidence is visible in
the Matches tab.

## Implementation notes / deviations from spec

- **`curl-cffi` instead of `httpx`**: Beatport (Cloudflare) and RA (DataDome)
  both 403 plain httpx regardless of headers; curl-cffi's Chrome TLS
  impersonation gets normal 200s. Still 1 req/s, cached to disk, weekly volume.
- **Beatport needs no HTML parsing**: charts ship as embedded `__NEXT_DATA__`
  JSON. Parser validates hard and raises rather than writing partial data.
- **RA via GraphQL is the primary tested path** (`ra.co/graphql`, Miami area
  id 38). The Apify actor path exists (`APIFY_TOKEN` + optional
  `RA_APIFY_ACTOR` in `.env`) and falls back to GraphQL on any failure.
- **Dice was pulled forward from Phase 3** because Club Space Miami and Sable
  Miami list **zero** events on RA (verified against their RA venue pages,
  clubs 831/273769) — they ticket on Dice. The collector fetches just the
  three configured venue pages (`DICE_VENUES` in config). Dice event titles
  are the lineup ("Oliver Koletzki + Manumat"), so artists are parsed from
  titles; Dice cards carry no genre tags, so those events sit outside the
  genre supply-share view.
- **Dashboard is generated static HTML** (`scene_radar/dashboard_template.html`
  + embedded JSON). Streamlit was dropped by request.

## Roadmap (per spec)

- **Phase 2:** 1001Tracklists set-play counts, SoundCloud velocity, weighted
  blend into `demand_score`.
- **Phase 3 (remaining):** more Dice venues or full Dice-Miami browse,
  Ticketmaster for the bigger rooms, trend lines over weeks, threshold
  alerts, cross-source event dedupe.
