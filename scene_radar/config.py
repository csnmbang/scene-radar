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
}

# Human-readable labels for charts/genre buckets.
GENRE_LABELS = {
    "tech-house": "Tech House",
    "techno-peak-time-driving": "Techno (Peak Time)",
    "hard-techno": "Hard Techno",
    "minimal-deep-tech": "Minimal / Deep Tech",
    "house": "House",
}

# Seconds to sleep between outbound requests (politeness).
REQUEST_DELAY_S = 1.0

# --- Resident Advisor -------------------------------------------------------
RA_AREA_ID = 38  # Miami (resolved via ra.co/graphql areas(searchTerm:"Miami"))
RA_LOOKAHEAD_DAYS = 90
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

# --- RA genre tag -> Beatport genre bucket (for the heat-vs-supply view) ----
# RA tags are free-ish text; map the common ones onto our five chart buckets.
RA_GENRE_TO_BUCKET = {
    "tech house": "tech-house",
    "techno": "techno-peak-time-driving",
    "hard techno": "hard-techno",
    "industrial techno": "hard-techno",
    "hard dance": "hard-techno",
    "minimal": "minimal-deep-tech",
    "deep tech": "minimal-deep-tech",
    "microhouse": "minimal-deep-tech",
    "house": "house",
    "deep house": "house",
    "afro house": "house",
    "soulful house": "house",
    "funky house": "house",
    "disco": "house",
}
