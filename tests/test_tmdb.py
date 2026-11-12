"""TMDb resolver: key normalisation + resolve/enrich against a mock transport.

No network — every HTTP call is served by an httpx.MockTransport handler.
"""
from __future__ import annotations

import httpx

from app import db
from app.tmdb import TMDbClient, normalise_key, poster_size


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# ── normalise_key: pure ─────────────────────────────────────────────

def test_normalise_key_strips_accents():
    assert normalise_key("Amélie", 2001) == "amelie|2001"


def test_normalise_key_collapses_punctuation():
    assert normalise_key("WALL·E", 2008) == "wall e|2008"


def test_normalise_key_keeps_inline_digits():
    assert normalise_key("Se7en", None) == "se7en|"


# ── poster_size: pure ───────────────────────────────────────────────

def test_poster_size_swaps_the_width_segment():
    url = "https://image.tmdb.org/t/p/w500/abc.jpg"
    assert poster_size(url, 185) == "https://image.tmdb.org/t/p/w185/abc.jpg"


def test_poster_size_leaves_non_tmdb_urls_alone():
    # A manually pinned poster shouldn't be rewritten into a 404.
    assert poster_size("https://example.com/poster.jpg", 185) == "https://example.com/poster.jpg"


def test_poster_size_passes_through_missing_posters():
    assert poster_size(None, 185) is None


# ── resolve ─────────────────────────────────────────────────────────

def test_resolve_prefers_exact_title_then_popularity():
    def handler(_request):
        return httpx.Response(200, json={"results": [
            {"id": 10, "title": "The Thing", "popularity": 5},
            {"id": 11, "title": "The Thing", "popularity": 50},
            {"id": 12, "title": "Some Other Thing", "popularity": 100},
        ]})
    client = TMDbClient("k", client=_client(handler))
    assert client.resolve("The Thing", 1982) == 11


def test_resolve_retries_without_year_when_first_search_empty():
    calls: list[dict] = []

    def handler(request):
        calls.append(dict(request.url.params))
        if "primary_release_year" in request.url.params:
            return httpx.Response(200, json={"results": []})
        return httpx.Response(200, json={"results": [
            {"id": 7, "title": "X", "popularity": 1}]})
    client = TMDbClient("k", client=_client(handler))
    assert client.resolve("X", 1999) == 7
    assert len(calls) == 2


def test_resolve_returns_none_when_nothing_found():
    client = TMDbClient("k", client=_client(
        lambda _r: httpx.Response(200, json={"results": []})))
    assert client.resolve("Nonexistent Film", None) is None


def test_resolve_hits_cache_and_skips_network_second_time():
    conn = db.connect()
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        return httpx.Response(200, json={"results": [
            {"id": 42, "title": "Cached", "popularity": 1}]})

    first = TMDbClient("k", conn=conn, client=_client(handler))
    assert first.resolve("Cached", 2000) == 42
    first.close()  # flushes the cache write

    def boom(_request):
        raise AssertionError("cache miss: should not hit the network")

    second = TMDbClient("k", conn=conn, client=_client(boom))
    assert second.resolve("Cached", 2000) == 42
    assert calls["n"] == 1
    conn.close()


def test_get_retries_on_429_then_succeeds():
    state = {"n": 0}

    def handler(_request):
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"results": [
            {"id": 9, "title": "Y", "popularity": 1}]})
    client = TMDbClient("k", client=_client(handler))
    assert client.resolve("Y", None) == 9
    assert state["n"] == 2


# ── find_by_imdb + enrich ───────────────────────────────────────────

def test_find_by_imdb():
    def handler(request):
        assert "/find/tt1375666" in str(request.url)
        return httpx.Response(200, json={"movie_results": [{"id": 27205}]})
    client = TMDbClient("k", client=_client(handler))
    assert client.find_by_imdb("tt1375666") == 27205


def test_find_by_imdb_none_when_no_match():
    client = TMDbClient("k", client=_client(
        lambda _r: httpx.Response(200, json={"movie_results": []})))
    assert client.find_by_imdb("tt0000000") is None


def test_enrich_extracts_director_genres_year_poster():
    def handler(_request):
        return httpx.Response(200, json={
            "title": "Inception",
            "release_date": "2010-07-16",
            "runtime": 148,
            "genres": [{"name": "Action"}, {"name": "Science Fiction"}],
            "credits": {"crew": [
                {"job": "Writer", "name": "Someone"},
                {"job": "Director", "name": "Christopher Nolan"},
            ]},
            "poster_path": "/abc.jpg",
        })
    meta = TMDbClient("k", client=_client(handler)).enrich(27205)
    assert meta.director == "Christopher Nolan"
    assert meta.genres == "Action, Science Fiction"
    assert meta.year == 2010
    assert meta.runtime == 148
    assert meta.poster.endswith("/abc.jpg")
