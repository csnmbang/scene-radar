"""Retry behaviour for the Beatport fetch.

Regression: four daily runs (Oct 13, 14, 17, Nov 7 2026) died on a single
transient Cloudflare 403 and lost a whole day of snapshots each time.
"""

import pytest

from scene_radar import beatport
from scene_radar.beatport import BeatportParseError, fetch_chart_html


class FakeResp:
    def __init__(self, status: int, text: str = "<html>ok</html>"):
        self.status_code = status
        self.text = text


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(beatport.time, "sleep", lambda *_: None)


def _script(monkeypatch, outcomes):
    """Make cffi_requests.get return/raise the given outcomes in order."""
    seen = []

    def fake_get(url, impersonate=None, timeout=None):
        seen.append(impersonate)
        out = outcomes[len(seen) - 1]
        if isinstance(out, Exception):
            raise out
        return out

    monkeypatch.setattr(beatport.cffi_requests, "get", fake_get)
    return seen


def test_recovers_after_transient_403(tmp_path, monkeypatch):
    seen = _script(monkeypatch, [FakeResp(403), FakeResp(403), FakeResp(200, "<html>chart</html>")])
    html = fetch_chart_html("tech-house", 11, tmp_path)
    assert html == "<html>chart</html>"
    assert len(seen) == 3
    # a different browser fingerprint each attempt, not the same one hammered
    assert len(set(seen)) == 3
    assert (tmp_path / "beatport_tech-house.html").read_text() == "<html>chart</html>"


def test_recovers_after_connection_error(tmp_path, monkeypatch):
    _script(monkeypatch, [ConnectionError("reset by peer"), FakeResp(200)])
    assert fetch_chart_html("house", 5, tmp_path) == "<html>ok</html>"


def test_gives_up_loudly_and_writes_nothing(tmp_path, monkeypatch):
    seen = _script(monkeypatch, [FakeResp(403)] * len(beatport._IMPERSONATE_ROTATION))
    with pytest.raises(BeatportParseError, match="HTTP 403.*not writing"):
        fetch_chart_html("hard-techno", 2, tmp_path)
    assert len(seen) == len(beatport._IMPERSONATE_ROTATION)
    assert not (tmp_path / "beatport_hard-techno.html").exists()


def test_does_not_retry_a_404(tmp_path, monkeypatch):
    seen = _script(monkeypatch, [FakeResp(404)])
    with pytest.raises(BeatportParseError, match="HTTP 404"):
        fetch_chart_html("no-such-genre", 999, tmp_path)
    assert len(seen) == 1  # a missing page won't appear on retry


def test_cached_page_is_never_refetched(tmp_path, monkeypatch):
    (tmp_path / "beatport_house.html").write_text("<html>cached</html>")
    seen = _script(monkeypatch, [])
    assert fetch_chart_html("house", 5, tmp_path) == "<html>cached</html>"
    assert seen == []
