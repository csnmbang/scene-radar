from scene_radar.normalize import match_artists, norm_artist


def test_norm_basics():
    assert norm_artist("HUGEL") == "hugel"
    assert norm_artist("Beltrán (BR)") == "beltran"
    assert norm_artist("Mochakk (DJ set)") == "mochakk"
    assert norm_artist("Dom Dolla & John Summit") == "dom dolla and john summit"
    assert norm_artist("  Röyksopp ") == "royksopp"


def test_exact_match_wins():
    m = match_artists(["hugel"], ["hugel", "hugel gomez"])
    assert m == [("hugel", "hugel", 100.0, "exact")]


def test_fuzzy_match_catches_variants():
    m = match_artists(["vintage culture"], ["vintage kulture"])
    assert len(m) == 1
    assert m[0][3] == "fuzzy"
    assert m[0][2] >= 90


def test_fuzzy_rejects_different_artist_same_first_name():
    # regression: chris lake vs chris clarke scores 90.9 on token_sort_ratio
    # but they are different people — the token guard must reject it.
    m = match_artists(["chris lake"], ["chris clarke"])
    assert m == []


def test_no_match_below_threshold():
    m = match_artists(["amelie lens"], ["charlotte de witte"])
    assert m == []
