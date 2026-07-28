"""Central config for Scene Radar. Tune weights and mappings here."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
DB_PATH = DATA_DIR / "scene_radar.duckdb"

# --- Beatport ---------------------------------------------------------------
# Genre slug -> Beatport numeric genre id (both appear in the chart URL).
# URL shape: https://www.beatport.com/genre/<slug>/<id>/top-100
BEATPORT_GENRES = {
    "tech-house": 11,
    "techno-peak-time-driving": 6,
    "hard-techno": 2,
    "minimal-deep-tech": 14,
    "house": 5,
    "melodic-house-techno": 90,
    "progressive-house": 15,
    "afro-house": 89,
    "indie-dance": 37,
}

# Human-readable labels for charts/genre buckets.
GENRE_LABELS = {
    "tech-house": "Tech House",
    "techno-peak-time-driving": "Techno (Peak Time)",
    "hard-techno": "Hard Techno",
    "minimal-deep-tech": "Minimal / Deep Tech",
    "house": "House",
    "melodic-house-techno": "Melodic H&T",
    "progressive-house": "Progressive House",
    "afro-house": "Afro House",
    "indie-dance": "Indie Dance",
}

# Seconds to sleep between outbound requests (politeness).
REQUEST_DELAY_S = 1.0

# --- Resident Advisor -------------------------------------------------------
RA_AREA_ID = 38  # Miami (resolved via ra.co/graphql areas(searchTerm:"Miami"))
RA_LOOKAHEAD_DAYS = 90
# Trailing window: who has played Miami recently. 12 months so "last played"
# is genuinely informative (catches Miami Music Week, annual bookings) rather
# than reporting "never" for anyone outside a short window.
RA_LOOKBACK_DAYS = 365
RA_PAGE_SIZE = 50
# Apify actor to try first when APIFY_TOKEN is set. Override via env RA_APIFY_ACTOR.
RA_APIFY_ACTOR_DEFAULT = "lhotanova~resident-advisor-scraper"

# --- Dice (supply for venues that left RA) ----------------------------------
# Venue slug on dice.fm -> display name. These rooms ticket on Dice, not RA.
DICE_VENUES = {
    "club-space-miami-wlav": "Club Space Miami",
    "sable-miami-l8qmp": "Sable Miami",
    "m2-miami-ya3v": "M2 Miami",
}

# Promoter pages on dice.fm. Same card markup as venue pages, but each card
# names its own venue (a promoter moves between rooms), so these catch
# events at venues we don't track directly — La Otra, MAD LIVE, Boho.
DICE_PROMOTERS = {
    "pitch-park-9n6d": "Pitch Park",
    "apex-presents-pkd6k": "Apex Presents",
}

# Promoter pages list events nationally; keep only this city.
DICE_CITY = "Miami"

# --- Matching ---------------------------------------------------------------
# rapidfuzz score (0-100) required to accept a fuzzy artist-name match.
FUZZY_MATCH_THRESHOLD = 90

# --- Scoring weights (see scoring.py for the formula) -----------------------
W_CHART_POINTS = 1.0   # weight on summed per-track position points
W_TRACK_COUNT = 10.0   # flat points per charting track
W_VELOCITY = 0.5       # points per rank climbed since previous snapshot
W_NEW_ENTRY = 8.0      # bonus per brand-new chart entry (weighted higher, per spec)

# Gap view default filter: demand_score above this AND bookings <= 1.
GAP_MIN_DEMAND = 20.0
GAP_MAX_BOOKINGS = 1

# An artist who played Miami recently isn't a real gap even with no future
# dates. gap_score is scaled by min(1, days_since_last_played / this) —
# played yesterday ≈ 0, played 90+ days ago (or not within RA_LOOKBACK_DAYS)
# = full gap. Independent of the lookback window: we look back a year to
# *report* last-played, but only discount the gap for the last 90 days.
RECENCY_WINDOW_DAYS = 90

# --- RA genre tag -> Beatport genre bucket (for the heat-vs-supply view) ----
# RA tags are free-ish text; map the common ones onto our five chart buckets.
RA_GENRE_TO_BUCKET = {
    "tech house": "tech-house",
    "techno": "techno-peak-time-driving",
    "dub techno": "techno-peak-time-driving",
    "acid": "techno-peak-time-driving",
    "hard techno": "hard-techno",
    "industrial techno": "hard-techno",
    "industrial": "hard-techno",
    "hardcore": "hard-techno",
    "hard dance": "hard-techno",
    "minimal": "minimal-deep-tech",
    "minimal techno": "minimal-deep-tech",
    "deep tech": "minimal-deep-tech",
    "microhouse": "minimal-deep-tech",
    "house": "house",
    "deep house": "house",
    "soulful house": "house",
    "funky house": "house",
    # Afro house was previously folded into 'house', which made Miami's
    # afro-house bookings read as generic house oversupply.
    "afro house": "afro-house",
    "amapiano": "afro-house",
    "afro tech": "afro-house",
    "afrobeat": "afro-house",
    "indie dance": "indie-dance",
    "nu disco": "indie-dance",
    "nu disco / indie dance": "indie-dance",
    "disco": "indie-dance",
    "new wave": "indie-dance",
    "electronica": "indie-dance",
    "melodic house & techno": "melodic-house-techno",
    "melodic techno": "melodic-house-techno",
    "melodic house": "melodic-house-techno",
    "organic house": "melodic-house-techno",
    "progressive house": "progressive-house",
    "progressive": "progressive-house",
    "trance": "progressive-house",
}
