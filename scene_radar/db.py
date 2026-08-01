"""DuckDB storage. Snapshots are append-only, keyed by snapshot_date.

Idempotency model: every collector run stamps its rows with snapshot_date
(the calendar date of the run). Re-running on the same day deletes that
date's rows first, then inserts — so re-runs never duplicate.
"""

import duckdb

from .config import DATA_DIR, DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS beatport_chart_entries (
    snapshot_date DATE NOT NULL,
    collected_at TIMESTAMP NOT NULL,
    chart_genre VARCHAR NOT NULL,      -- slug from config.BEATPORT_GENRES
    rank INTEGER NOT NULL,
    track_id BIGINT,
    track_title VARCHAR NOT NULL,
    mix_name VARCHAR,
    artist_raw VARCHAR NOT NULL,       -- one row per (track, artist)
    artist_norm VARCHAR NOT NULL,
    remixer VARCHAR,
    label VARCHAR
);

CREATE TABLE IF NOT EXISTS ra_events (
    snapshot_date DATE NOT NULL,
    collected_at TIMESTAMP NOT NULL,
    event_id VARCHAR NOT NULL,
    event_date DATE NOT NULL,
    event_name VARCHAR NOT NULL,
    venue_name VARCHAR,
    ticket_price VARCHAR,              -- RA gives '$'/'$$' style or blank
    genres VARCHAR,                    -- comma-joined RA genre tags
    source VARCHAR NOT NULL            -- 'apify' or 'graphql'
);

CREATE TABLE IF NOT EXISTS ra_event_artists (
    snapshot_date DATE NOT NULL,
    event_id VARCHAR NOT NULL,
    artist_raw VARCHAR NOT NULL,
    artist_norm VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS artist_scores (
    computed_at TIMESTAMP NOT NULL,
    snapshot_date DATE NOT NULL,
    artist_norm VARCHAR NOT NULL,
    artist_display VARCHAR NOT NULL,
    demand_score DOUBLE NOT NULL,
    charting_tracks INTEGER NOT NULL,
    chart_points DOUBLE NOT NULL,
    rank_velocity DOUBLE NOT NULL,
    new_entries INTEGER NOT NULL,
    genres VARCHAR                     -- charts the artist appears on
);

CREATE TABLE IF NOT EXISTS artist_matches (
    snapshot_date DATE NOT NULL,
    bp_artist_norm VARCHAR NOT NULL,
    ra_artist_norm VARCHAR NOT NULL,
    confidence DOUBLE NOT NULL,        -- 100 = exact, else rapidfuzz score
    method VARCHAR NOT NULL            -- 'exact' or 'fuzzy'
);

CREATE TABLE IF NOT EXISTS gaps (
    computed_at TIMESTAMP NOT NULL,
    snapshot_date DATE NOT NULL,
    artist_norm VARCHAR NOT NULL,
    artist_display VARCHAR NOT NULL,
    demand_score DOUBLE NOT NULL,
    miami_bookings_90d INTEGER NOT NULL,
    gap_score DOUBLE NOT NULL,
    genres VARCHAR
);

-- Confirmed dates in comparable markets (NYC, LA, Mexico City...). The
-- long-horizon signal: these are frequently public months before Miami's
-- own announcement for the same tour.
CREATE TABLE IF NOT EXISTS market_bookings (
    snapshot_date DATE NOT NULL,
    collected_at TIMESTAMP NOT NULL,
    artist_raw VARCHAR NOT NULL,
    artist_norm VARCHAR NOT NULL,
    market VARCHAR NOT NULL,
    event_date DATE NOT NULL,
    venue_name VARCHAR
);

-- columns added after first release (no-ops when already present)
ALTER TABLE gaps ADD COLUMN IF NOT EXISTS last_played DATE;
"""


def connect(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH), read_only=read_only)
    if not read_only:
        con.execute(SCHEMA)
    return con


def replace_snapshot(con: duckdb.DuckDBPyConnection, table: str, snapshot_date) -> None:
    """Delete any rows for this snapshot_date so a re-run is idempotent."""
    con.execute(f"DELETE FROM {table} WHERE snapshot_date = ?", [snapshot_date])
