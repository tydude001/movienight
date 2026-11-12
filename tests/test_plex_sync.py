"""Plex sync: GUID extraction (both agent styles) and the both-want query."""
from __future__ import annotations

from types import SimpleNamespace

from app import db, plex_sync
from app.plex_sync import _extract_ids

from .helpers import add_film, add_watchlist


def _guid(value: str) -> SimpleNamespace:
    return SimpleNamespace(id=value)


# ── _extract_ids ────────────────────────────────────────────────────

def test_extract_modern_tmdb_and_imdb():
    movie = SimpleNamespace(
        guids=[_guid("tmdb://27205"), _guid("imdb://tt1375666")],
        guid="plex://movie/xyz",
    )
    assert _extract_ids(movie) == (27205, "tt1375666")


def test_extract_legacy_agent_guid():
    movie = SimpleNamespace(
        guids=[],
        guid="com.plexapp.agents.themoviedb://603?lang=en",
    )
    assert _extract_ids(movie) == (603, None)


def test_extract_imdb_only():
    movie = SimpleNamespace(guids=[_guid("imdb://tt0111161")], guid="")
    assert _extract_ids(movie) == (None, "tt0111161")


def test_extract_none_when_no_recognised_ids():
    movie = SimpleNamespace(guids=[], guid="local://deadbeef")
    assert _extract_ids(movie) == (None, None)


# ── _both_want query ────────────────────────────────────────────────

def test_both_want_returns_only_films_on_every_watchlist():
    add_film(1); add_watchlist("alice", 1); add_watchlist("bob", 1)
    add_film(2); add_watchlist("alice", 2)  # only one wants it
    with db.cursor() as cur:
        rows = plex_sync._both_want(cur, {1, 2}, n_users=2)
    assert [r["tmdb_id"] for r in rows] == [1]


def test_both_want_empty_for_no_ids():
    with db.cursor() as cur:
        assert plex_sync._both_want(cur, set(), n_users=2) == []
