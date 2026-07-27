"""Artist-name normalization and fuzzy matching.

Beatport spells artists one way, RA lineups another ("HUGEL" vs "Hugel",
"Joris Voorn" vs "JORIS VOORN (extended set)"). We normalize both sides to a
join key, then fuzzy-match the leftovers with rapidfuzz.
"""

import re
import unicodedata

from rapidfuzz import fuzz, process

from .config import FUZZY_MATCH_THRESHOLD

# Junk RA promoters append to lineup names — stripped before matching.
_PARENS_NOISE = re.compile(
    r"\s*[\(\[](?:live|dj set|extended set|hybrid set|b2b|all night long|"
    r"[a-z]{2})[\)\]]\s*$",
    re.IGNORECASE,
)
_WS = re.compile(r"\s+")


def norm_artist(name: str) -> str:
    """lowercase, strip diacritics/possessive noise, collapse whitespace."""
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.strip().lower()
    s = _PARENS_NOISE.sub("", s)
    s = s.replace("&", "and")
    s = re.sub(r"[^\w\s]", "", s)  # drop punctuation, keep letters/digits
    s = _WS.sub(" ", s).strip()
    return s


def match_artists(
    bp_norms: list[str], ra_norms: list[str], threshold: float = FUZZY_MATCH_THRESHOLD
) -> list[tuple[str, str, float, str]]:
    """Match Beatport artist keys to RA artist keys.

    Returns (bp_norm, ra_norm, confidence 0-100, method) tuples.
    Exact key equality wins at confidence 100; the rest go through
    rapidfuzz token_sort_ratio and are kept only at >= threshold.
    """
    ra_set = set(ra_norms)
    out: list[tuple[str, str, float, str]] = []
    unmatched: list[str] = []
    for bp in bp_norms:
        if bp in ra_set:
            out.append((bp, bp, 100.0, "exact"))
        else:
            unmatched.append(bp)

    ra_list = list(ra_set)
    if ra_list:
        for bp in unmatched:
            hit = process.extractOne(
                bp, ra_list, scorer=fuzz.token_sort_ratio, score_cutoff=threshold
            )
            if hit is not None:
                ra_name, score, _ = hit
                if _tokens_agree(bp, ra_name):
                    out.append((bp, ra_name, float(score), "fuzzy"))
    return out


# Guard against near-miss different artists ("chris lake" vs "chris clarke"
# scores 90.9 on token_sort_ratio). When both names have the same number of
# words, every aligned word must itself be close — a whole surname swap fails.
_TOKEN_AGREE_MIN = 85.0


def _tokens_agree(a: str, b: str) -> bool:
    ta, tb = sorted(a.split()), sorted(b.split())
    if len(ta) != len(tb):
        return True  # different shapes; trust the overall score
    return all(fuzz.ratio(x, y) >= _TOKEN_AGREE_MIN for x, y in zip(ta, tb))
