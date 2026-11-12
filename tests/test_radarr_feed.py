"""/api/radarr: the get-list in the shape Radarr's "Custom Lists" import reads.

Radarr's RadarrListParser deserialises a top-level array and keeps each
object's integer ``id`` (> 0) as a TMDb id; anything else in the body breaks
the import, so the shape is what these pin.
"""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from app.main import app

from .helpers import add_film, add_watchlist, set_plex


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_an_empty_get_list_is_an_empty_array(client):
    r = client.get("/api/radarr")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert r.json() == []


def test_marked_films_come_out_as_tmdb_ids(client):
    add_film(603, title="The Matrix"); add_watchlist("alice", 603)
    add_film(78, title="Blade Runner"); add_watchlist("bob", 78)
    add_film(5, title="Not Marked"); add_watchlist("alice", 5)
    client.post("/get/603"); client.post("/get/78")
    feed = client.get("/api/radarr").json()
    assert sorted(m["id"] for m in feed) == [78, 603]
    assert all(isinstance(m["id"], int) and m["id"] > 0 for m in feed)
    assert {m["title"] for m in feed} == {"The Matrix", "Blade Runner"}


def test_a_film_already_on_plex_leaves_the_feed(client):
    add_film(603, title="The Matrix"); add_watchlist("alice", 603)
    client.post("/get/603")
    set_plex(603)
    assert client.get("/api/radarr").json() == []


def test_the_feed_needs_no_credentials(client):
    # Radarr's Custom Lists sends no auth; a 401/503 here would empty the list.
    assert client.get("/api/radarr", auth=None).status_code == 200
