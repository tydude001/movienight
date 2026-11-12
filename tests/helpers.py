"""Tiny seed helpers so tests read as data setup, not SQL.

Each writes through the app's own upsert helpers (the code under test), so the
fixtures exercise the real schema and constraints.
"""
from __future__ import annotations

from app import db


def add_film(tmdb_id: int, *, title: str | None = None, year: int | None = 2000,
             genres: str | None = None, director: str | None = None,
             runtime: int | None = None, poster: str | None = None,
             enriched: bool = True) -> None:
    with db.cursor() as cur:
        db.upsert_film(cur, tmdb_id, title or f"Film {tmdb_id}", year)
        if enriched:
            db.set_enrichment(cur, tmdb_id, genres=genres, director=director,
                              runtime=runtime, poster=poster)


def set_plex(tmdb_id: int, *, available: int = 1, watched: int = 0,
             added: str | None = None) -> None:
    with db.cursor() as cur:
        db.upsert_availability(cur, tmdb_id, available=available,
                               watched=watched, added=added)


def add_watchlist(user: str, tmdb_id: int) -> None:
    with db.cursor() as cur:
        db.upsert_watchlist(cur, user, tmdb_id)


def add_watched(user: str, tmdb_id: int, date: str = "2020-01-01") -> None:
    with db.cursor() as cur:
        db.upsert_watched(cur, user, tmdb_id, date)


def add_rating(user: str, tmdb_id: int, rating: float, date: str = "2020-01-01") -> None:
    with db.cursor() as cur:
        db.upsert_rating(cur, user, tmdb_id, rating, date)


def add_veto(tmdb_id: int, times: int = 1) -> None:
    with db.cursor() as cur:
        for _ in range(times):
            db.add_veto(cur, tmdb_id)
