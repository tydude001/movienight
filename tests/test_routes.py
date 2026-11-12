"""The film detail card end to end: routes + templates.

Unit tests cover the scoring; these exist because a Jinja slip only shows up
when a page is actually rendered. Scores are pre-seeded into the cache so no
test can reach the network.
"""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from app import db
from app.main import app

from .helpers import add_film, add_rating, add_watched, add_watchlist, set_plex


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def seed_scores(tmdb_id: int, **fields) -> None:
    """A fresh row in the scores cache, so rendering never hits the network."""
    cols = {"rt_score": 94, "lb_score": 4.28, "lb_votes": 912345,
            "imdb_score": 8.5, "metacritic": 88, "tmdb_score": 8.4,
            "tmdb_votes": 1200, "lb_url": "https://letterboxd.com/film/whiplash/"}
    cols.update(fields)
    names = ", ".join(cols)
    with db.cursor() as cur:
        cur.execute(
            f"INSERT INTO external_ratings (tmdb_id, {names}) "
            f"VALUES (?{', ?' * len(cols)})",
            [tmdb_id, *cols.values()],
        )


def seed_film() -> int:
    """A film both users want, on Plex, with a little shared history behind it."""
    add_film(1, title="Whiplash", year=2014, genres="Drama, Music",
             director="Damien Chazelle", runtime=106)
    set_plex(1, added="2026-07-01")
    add_watchlist("alice", 1)
    add_watchlist("bob", 1)
    add_film(2, title="La La Land", genres="Drama, Music", director="Damien Chazelle")
    add_watched("alice", 2)
    add_rating("alice", 2, 4.5)
    return 1


# ── the detail card ─────────────────────────────────────────────────

def test_detail_card_renders_scores_and_reasons(client):
    tmdb_id = seed_film()
    seed_scores(tmdb_id)
    body = client.get(f"/film/{tmdb_id}").text

    assert "Whiplash" in body
    assert "Damien Chazelle" in body
    assert "94%" in body and "Rotten Tomatoes" in body   # cached scores, inline
    assert "4.28" in body and "Letterboxd" in body
    assert "Why it's a match for you two" in body
    assert "La La Land (4.5★)" in body                   # the evidence, cited
    assert "/scores" not in body                        # nothing left to fetch


def test_detail_card_defers_the_fetch_when_scores_are_missing(client):
    tmdb_id = seed_film()
    body = client.get(f"/film/{tmdb_id}").text
    assert f'hx-get="/film/{tmdb_id}/scores"' in body
    assert "Fetching scores…" in body


def test_detail_card_match_agrees_with_the_grid(client):
    tmdb_id = seed_film()
    grid = client.get("/picker", params={"mode": "both"}).text
    detail = client.get(f"/film/{tmdb_id}").text
    import re
    pct = re.search(r"(\d+)% match", grid).group(1)
    assert f">{pct}<" in detail.replace("\n", "").replace(" ", "")


def test_detail_card_is_empty_for_an_unknown_film(client):
    assert client.get("/film/999999").text == ""


def test_scores_partial_renders_from_cache(client):
    tmdb_id = seed_film()
    seed_scores(tmdb_id, rt_score=71)
    body = client.get(f"/film/{tmdb_id}/scores").text
    assert "71%" in body


def test_scores_partial_says_so_when_there_is_nothing(client):
    tmdb_id = seed_film()
    seed_scores(tmdb_id, rt_score=None, lb_score=None, imdb_score=None,
                metacritic=None, tmdb_score=None)
    assert "No external scores" in client.get(f"/film/{tmdb_id}/scores").text


# ── the pages that open it ──────────────────────────────────────────

def test_grid_cards_open_the_detail_card(client):
    tmdb_id = seed_film()
    body = client.get("/picker", params={"mode": "both"}).text
    assert f'hx-get="/film/{tmdb_id}"' in body
    assert "data-film-open" in body


def test_dashboard_renders_with_openable_cards(client):
    seed_film()
    assert client.get("/dashboard").status_code == 200


# ── JSON ────────────────────────────────────────────────────────────

def test_api_film_returns_film_scores_and_breakdown(client):
    tmdb_id = seed_film()
    seed_scores(tmdb_id)
    data = client.get(f"/api/film/{tmdb_id}").json()

    assert data["film"]["title"] == "Whiplash"
    assert data["scores"]["rt_score"] == 94
    assert data["why"]["match"] == data["film"]["match"]
    assert [f["label"] for f in data["why"]["genres"]]
    assert data["why"]["notes"]


def test_api_film_404s_for_an_unknown_id(client):
    assert client.get("/api/film/999999").status_code == 404


# ── the picker shell + slim grid cards ──────────────────────────────

def test_index_renders_with_a_loading_skeleton(client):
    body = client.get("/").text
    assert 'class="card skel"' in body
    assert "/static/htmx.min.js" in body


def test_grid_cards_stay_slim(client):
    """Genres/director/why belong to the detail card, not the grid (UI refresh
    2026-07: cards are poster + title + runtime only)."""
    seed_film()
    grid = client.get("/picker", params={"mode": "both"}).text
    assert "Whiplash" in grid and "106 min" in grid
    assert "Damien Chazelle" not in grid
    assert "Drama" not in grid


def test_every_page_credits_tmdb(client):
    """TMDb's API terms require this notice, with their logo, wherever the
    data is used. The footer is in base.html, so one page proves them all."""
    body = client.get("/").text
    assert "This product uses the TMDB API but is not endorsed or certified by TMDB." in body
    assert 'src="/static/tmdb-logo.svg"' in body
    assert client.get("/static/tmdb-logo.svg").status_code == 200


def test_scores_fragment_never_fetches_for_an_unknown_film(client, monkeypatch):
    """No login guards /film/{id}/scores, and a cache miss spends the API keys,
    so only a film already in the database may reach the fetch."""
    from app import ratings
    calls = []
    monkeypatch.setattr(ratings, "fetch", lambda *a, **k: calls.append(a) or None)
    assert client.get("/film/999999/scores").status_code == 404
    assert calls == []
