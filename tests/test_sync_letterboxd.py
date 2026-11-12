"""Watchlist sync: a truncated pull must never replace the stored watchlist.

No network — every request is served by an httpx.MockTransport handler.
"""
from __future__ import annotations

import httpx
import pytest

from app import db
from scripts import sync_letterboxd as sl


@pytest.fixture(autouse=True)
def no_delays(monkeypatch):
    monkeypatch.setattr(sl, "PAGE_DELAY", 0)
    monkeypatch.setattr(sl, "FILM_DELAY", 0)


def _page(*slugs: str) -> str:
    return "".join(f'<div data-film-slug="{s}"></div>' for s in slugs)


def _film(tmdb_id: int, title: str) -> str:
    return (f'<meta property="og:title" content="{title} (2000)">'
            f'<a href="https://www.themoviedb.org/movie/{tmdb_id}/">TMDb</a>')


FILMS = {"one": (1, "One"), "two": (2, "Two"), "three": (3, "Three")}


def _client(pages: dict[int, httpx.Response]) -> httpx.Client:
    """Watchlist page N answers ``pages[N]`` (default: empty, i.e. the end)."""
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "/watchlist/page/" in path:
            n = int(path.rstrip("/").rsplit("/", 1)[-1])
            return pages.get(n, httpx.Response(200, text=_page()))
        slug = path.rstrip("/").rsplit("/", 1)[-1]
        return httpx.Response(200, text=_film(*FILMS[slug]))
    return httpx.Client(transport=httpx.MockTransport(handler))


def _watchlist(user: str) -> set[int]:
    conn = db.connect()
    try:
        rows = conn.execute("SELECT tmdb_id FROM watchlist WHERE user = ?", (user,))
        return {r[0] for r in rows}
    finally:
        conn.close()


def _seed(user: str, *tmdb_ids: int) -> None:
    with db.cursor() as cur:
        for t in tmdb_ids:
            db.upsert_film(cur, t, f"Film {t}", 2000)
            db.upsert_watchlist(cur, user, t)


def test_complete_scan_replaces_watchlist():
    _seed("alice", 99)
    client = _client({1: httpx.Response(200, text=_page("one", "two")),
                      2: httpx.Response(200, text=_page("three"))})
    result = sl.sync_watchlist("alice", "alice_lb", client)
    assert result["scan_complete"] is True
    assert result["pages"] == 2
    assert _watchlist("alice") == {1, 2, 3}


@pytest.mark.parametrize("status", [429, 503, 500])
def test_failed_page_keeps_existing_watchlist(status):
    _seed("alice", 1, 2, 3, 99)
    client = _client({1: httpx.Response(200, text=_page("one")),
                      2: httpx.Response(status)})
    result = sl.sync_watchlist("alice", "alice_lb", client)
    assert result["scan_complete"] is False
    assert result["pages"] == 1
    assert _watchlist("alice") == {1, 2, 3, 99}


def test_failed_first_page_does_not_empty_watchlist():
    _seed("alice", 1, 2)
    client = _client({1: httpx.Response(429)})
    assert sl.sync_watchlist("alice", "alice_lb", client)["scan_complete"] is False
    assert _watchlist("alice") == {1, 2}


def test_hitting_page_limit_counts_as_incomplete(monkeypatch):
    # Every page full: the cap stops the loop before an empty page proves the end.
    monkeypatch.setattr(sl, "PAGE_LIMIT", 2)
    _seed("alice", 99)
    client = _client({1: httpx.Response(200, text=_page("one")),
                      2: httpx.Response(200, text=_page("two")),
                      3: httpx.Response(200, text=_page("three"))})
    assert sl.sync_watchlist("alice", "alice_lb", client)["scan_complete"] is False
    assert _watchlist("alice") == {99}


def _stamp(key: str):
    conn = db.connect()
    try:
        row = conn.execute("SELECT value FROM app_meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _run(monkeypatch, pages: dict[int, httpx.Response]) -> int:
    monkeypatch.setenv("LETTERBOXD_A", "alice_lb")
    monkeypatch.setenv("LETTERBOXD_B", "bob_lb")
    from app.config import get_settings
    get_settings.cache_clear()
    monkeypatch.setattr(sl, "_client", lambda: _client(pages))
    return sl.run("watchlist", do_enrich=False)


def test_run_stamps_and_succeeds_on_complete_scan(monkeypatch):
    rc = _run(monkeypatch, {1: httpx.Response(200, text=_page("one"))})
    assert rc == 0
    assert _stamp("letterboxd_watchlist_at") is not None


def test_run_does_not_stamp_a_truncated_scan(monkeypatch):
    # The footer's "watchlists synced" must not vouch for a run that kept stale data.
    rc = _run(monkeypatch, {1: httpx.Response(429)})
    assert rc == 1
    assert _stamp("letterboxd_watchlist_at") is None


# ── the scrape is opt-in ────────────────────────────────────────────

EMPTY_RSS = b'<rss><channel></channel></rss>'


@pytest.fixture
def scrape_off(monkeypatch):
    """A configured user, the scrape switched off, and a client
    that records every path it is asked for."""
    from app.config import get_settings
    monkeypatch.setenv("LETTERBOXD_A", "alice_lb")
    monkeypatch.setenv("LETTERBOXD_WATCHLIST_SCRAPE", "0")  # a real .env must not leak in
    get_settings.cache_clear()
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path.endswith("/rss/"):
            return httpx.Response(200, content=EMPTY_RSS)
        return httpx.Response(200, text=_page())
    monkeypatch.setattr(sl, "_client",
                        lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    yield seen
    get_settings.cache_clear()


def test_watchlist_mode_refuses_without_the_opt_in(scrape_off):
    assert sl.run("watchlist", do_enrich=False) == 1
    assert scrape_off == []


def test_all_mode_runs_rss_only_without_the_opt_in(scrape_off):
    _seed("alice", 1, 2)
    assert sl.run("all", do_enrich=False) == 0
    assert scrape_off == ["/alice_lb/rss/"]
    assert _watchlist("alice") == {1, 2}
