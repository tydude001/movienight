""""Not tonight" holds until 6 a.m. local — on reload, and on the other phone."""
from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest
from starlette.testclient import TestClient

from app import db, tonight
from app.main import app

from .helpers import add_film, add_watchlist


@pytest.fixture(autouse=True)
def central_time(monkeypatch):
    """The cases below are written in US Central time, CDT and CST both; the
    app's own default zone is UTC."""
    from app.config import get_settings
    monkeypatch.setenv("LOCAL_TZ", "America/Chicago")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _hero(body: str) -> str:
    return re.search(r'id="hero-title">([^<]+)<', body).group(1)


def _two_films():
    add_film(1, title="Top Pick", genres="Drama"); add_watchlist("alice", 1); add_watchlist("bob", 1)
    add_film(2, title="Runner Up", genres="Drama"); add_watchlist("alice", 2)


def _set_last_veto(tmdb_id: int, stamp: str) -> None:
    with db.cursor() as cur:
        cur.execute("INSERT INTO vetoes (tmdb_id, count, last_veto) VALUES (?, 1, ?)",
                    (tmdb_id, stamp))


def test_not_tonight_survives_a_reload(client):
    _two_films()
    assert _hero(client.get("/").text) == "Top Pick"
    assert _hero(client.post("/skip", data={"tmdb_id": 1}).text) == "Runner Up"
    body = client.get("/").text
    assert _hero(body) == "Runner Up"
    assert "Top Pick" not in client.get("/picker").text


def test_a_veto_from_before_six_am_has_expired():
    _two_films()
    _set_last_veto(1, "2000-01-01 00:00:00")
    assert tonight.ranked(tonight.parse())[0].title == "Top Pick"


def test_the_get_list_ignores_tonights_veto(client):
    add_film(1, title="To Buy"); add_watchlist("alice", 1)
    with db.cursor() as cur:
        db.toggle_get_list(cur, 1)
        db.add_veto(cur, 1)
    assert [f.title for f in tonight.get_list(tonight.parse())] == ["To Buy"]
    assert tonight.ranked(tonight.parse()) == []


@pytest.mark.parametrize("now_utc, began_utc", [
    # 10:00 CDT (UTC-5): tonight began at 06:00 CDT today.
    ("2026-10-18 15:00", "2026-10-18 11:00:00"),
    # 03:00 CDT, still last night: it began at 06:00 CDT yesterday.
    ("2026-10-18 08:00", "2026-10-17 11:00:00"),
    # 23:30 CDT is 04:30 UTC the next day — same night, not a new one.
    ("2026-10-19 04:30", "2026-10-18 11:00:00"),
    # Winter, CST (UTC-6): 06:00 local is 12:00 UTC.
    ("2026-01-15 18:00", "2026-01-15 12:00:00"),
])
def test_night_starts_at_six_local(now_utc, began_utc):
    now = datetime.strptime(now_utc, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    assert tonight.night_start(now) == began_utc
