"""External scores: source parsing, graceful degradation, and the cache TTL.

No network — every source is served by one httpx.MockTransport handler that
dispatches on hostname, the way the three real APIs would.
"""
from __future__ import annotations

import httpx
import pytest

from app import db, ratings
from app.config import get_settings

from .helpers import add_film

# The shape of the JSON-LD block Letterboxd embeds on a film page.
LB_PAGE = """
<html><head><script type="application/ld+json">
/* <![CDATA[ */
{"@type":"Movie","name":"Whiplash",
 "aggregateRating":{"@type":"AggregateRating","ratingCount":912345,
 "bestRating":5,"worstRating":1,"ratingValue":4.28}}
/* ]]> */
</script></head><body></body></html>
"""

OMDB_JSON = {
    "Response": "True",
    "imdbRating": "8.5",
    "Metascore": "88",
    "Ratings": [
        {"Source": "Internet Movie Database", "Value": "8.5/10"},
        {"Source": "Rotten Tomatoes", "Value": "94%"},
        {"Source": "Metacritic", "Value": "88/100"},
    ],
}

TMDB_JSON = {"vote_average": 8.37, "vote_count": 14321, "imdb_id": "tt2582802"}


@pytest.fixture
def omdb_key(monkeypatch):
    """OMDb is off by default (no key); most tests want it on."""
    monkeypatch.setenv("OMDB_API_KEY", "omdbkey")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _all_ok(calls: list[str] | None = None):
    """Handler that answers all three sources successfully."""
    def handler(request):
        host = request.url.host
        if calls is not None:
            calls.append(host)
        if host == "api.themoviedb.org":
            return httpx.Response(200, json=TMDB_JSON)
        if host == "www.omdbapi.com":
            return httpx.Response(200, json=OMDB_JSON)
        if host == "letterboxd.com":
            return httpx.Response(200, text=LB_PAGE)
        raise AssertionError(f"unexpected host {host}")
    return handler


def _backdate(tmdb_id: int, hours: float) -> None:
    with db.cursor() as cur:
        cur.execute(
            "UPDATE external_ratings SET fetched_at = datetime('now', ?) WHERE tmdb_id = ?",
            (f"-{hours} hours", tmdb_id),
        )


# ── parsing each source ─────────────────────────────────────────────

def test_fetch_collects_every_source(omdb_key):
    r = ratings.fetch(1, _client(_all_ok()))
    assert r.rt_score == 94
    assert r.metacritic == 88
    assert r.imdb_score == 8.5
    assert r.imdb_id == "tt2582802"
    assert r.tmdb_score == 8.37
    assert r.tmdb_votes == 14321
    assert r.lb_score == 4.28
    assert r.lb_votes == 912345
    assert r.any_scores


def test_no_omdb_key_skips_rotten_tomatoes():
    hosts: list[str] = []
    r = ratings.fetch(1, _client(_all_ok(hosts)))
    assert r.rt_score is None and r.imdb_score is None
    assert "www.omdbapi.com" not in hosts
    assert r.lb_score == 4.28  # the other sources still render


def test_letterboxd_can_be_disabled(monkeypatch):
    monkeypatch.setenv("LETTERBOXD_SCORES", "0")
    get_settings.cache_clear()
    hosts: list[str] = []
    r = ratings.fetch(1, _client(_all_ok(hosts)))
    assert r.lb_score is None
    assert "letterboxd.com" not in hosts


def test_one_source_failing_leaves_the_others(omdb_key):
    def handler(request):
        if request.url.host == "letterboxd.com":
            return httpx.Response(503)
        return _all_ok()(request)
    r = ratings.fetch(1, _client(handler))
    assert r.lb_score is None
    assert r.rt_score == 94 and r.tmdb_score == 8.37


def test_unrated_letterboxd_page_is_not_an_error():
    r = ratings.fetch(1, _client(lambda req: httpx.Response(200, text="<html></html>")
                                if req.url.host == "letterboxd.com"
                                else httpx.Response(200, json=TMDB_JSON)))
    assert r.lb_score is None
    assert r.lb_url is not None  # the film page exists, just no ratings yet


def test_omdb_miss_is_not_an_error(omdb_key):
    def handler(request):
        if request.url.host == "www.omdbapi.com":
            return httpx.Response(200, json={"Response": "False", "Error": "not found"})
        return _all_ok()(request)
    r = ratings.fetch(1, _client(handler))
    assert r.rt_score is None
    assert r.tmdb_score == 8.37


def test_everything_failing_yields_no_scores(omdb_key):
    r = ratings.fetch(1, _client(lambda _req: httpx.Response(500)))
    assert not r.any_scores


# ── caching ─────────────────────────────────────────────────────────

def test_get_caches_and_skips_the_network_second_time(omdb_key):
    add_film(1)
    hosts: list[str] = []
    first = ratings.get(1, client=_client(_all_ok(hosts)))
    assert first.rt_score == 94
    n = len(hosts)

    def boom(_request):
        raise AssertionError("should have been served from cache")
    second = ratings.get(1, client=_client(boom))
    assert second.rt_score == 94
    assert second.lb_score == 4.28
    assert len(hosts) == n


def test_a_remembered_imdb_id_keeps_rotten_tomatoes_alive_without_tmdb(omdb_key):
    """TMDb supplies the IMDb id OMDb is keyed on, so a TMDb outage would
    otherwise silently take Rotten Tomatoes with it."""
    add_film(1)
    ratings.get(1, client=_client(_all_ok()))          # learns tt2582802
    _backdate(1, get_settings().ratings_ttl_hours + 1)

    def tmdb_down(request):
        if request.url.host == "api.themoviedb.org":
            return httpx.Response(503)
        return _all_ok()(request)

    again = ratings.get(1, client=_client(tmdb_down))
    assert again.tmdb_score is None      # the source that's actually down
    assert again.rt_score == 94          # the one that only needed the id
    assert again.imdb_id == "tt2582802"  # and the id survives for next time


def test_stale_cache_is_refetched(omdb_key):
    add_film(1)
    ratings.get(1, client=_client(_all_ok()))
    _backdate(1, get_settings().ratings_ttl_hours + 1)
    assert ratings.cached(1) is None

    def newer(request):
        if request.url.host == "www.omdbapi.com":
            return httpx.Response(200, json={**OMDB_JSON, "Ratings": [
                {"Source": "Rotten Tomatoes", "Value": "71%"}]})
        return _all_ok()(request)
    assert ratings.get(1, client=_client(newer)).rt_score == 71


def test_empty_result_is_retried_long_before_the_ttl(omdb_key):
    add_film(1)
    ratings.get(1, client=_client(lambda _r: httpx.Response(500)))
    _backdate(1, ratings.EMPTY_RETRY_HOURS + 0.5)
    # Well inside the week-long TTL, but an empty row is worth another try.
    assert get_settings().ratings_ttl_hours > ratings.EMPTY_RETRY_HOURS + 0.5
    assert ratings.cached(1) is None


def test_cached_returns_none_when_never_fetched():
    add_film(1)
    assert ratings.cached(1) is None


# ── shipped defaults ────────────────────────────────────────────────

def test_both_scrapers_ship_disabled():
    """Deliberate: Letterboxd's terms prohibit automated access, so reading its
    film pages is opt-in, never inherited. Asserted on the field default rather
    than the environment, which conftest pins."""
    from app.config import Settings
    assert Settings.model_fields["letterboxd_scores"].default is False
    assert Settings.model_fields["letterboxd_watchlist_scrape"].default is False
    assert Settings.model_fields["omdb_api_key"].default == ""
