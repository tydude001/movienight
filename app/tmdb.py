"""TMDb resolver — the shared component.

Two responsibilities:
  1. Resolve a Letterboxd entry (title + year, optionally a Letterboxd URI)
     to a stable TMDb id, caching every lookup so each film resolves once.
  2. Enrich a TMDb id with genre / director / runtime / poster.

Designed as a standalone module: Project 1 (Ambient Cinema Mode) can import
`TMDbClient` directly for its poster-colour feature without dragging in the
rest of the web app.
"""
from __future__ import annotations

import re
import sqlite3
import time
import unicodedata
from dataclasses import dataclass

import httpx

API_ROOT = "https://api.themoviedb.org/3"
IMAGE_BASE = "https://image.tmdb.org/t/p/w500"

# TMDb serves the same poster at several widths off the same path. Stored URLs
# are w500 (right for the detail card's 220px poster at 2x); the grid's ~150px
# cards want something far smaller — a w500 grid is ~7 MB for 119 films.
_SIZE_SEGMENT = re.compile(r"/t/p/w\d+/")

_MISSING = object()  # cache sentinel: "never looked up" (vs None = known miss)

# Flush cache writes every N lookups rather than per row; an interrupted run
# loses at most this many resolutions (they just re-resolve next time).
CACHE_COMMIT_EVERY = 25


def poster_size(url: str | None, width: int) -> str | None:
    """Re-point a stored TMDb poster URL at a different width.

    Anything that isn't a TMDb sized-image URL (a manual override, a bare path)
    comes back untouched — better a correctly-sized-by-CSS poster than a 404.
    """
    if not url:
        return url
    return _SIZE_SEGMENT.sub(f"/t/p/w{width}/", url, count=1)


CAST_KEEP = 6  # billing places kept per film: the leads, not the bit parts


def _top_cast(credits: dict) -> list[str]:
    people = sorted(credits.get("cast", []), key=lambda c: c.get("order", 999))
    return [c["name"] for c in people[:CAST_KEEP] if c.get("name")]


def normalise_key(title: str, year: int | None) -> str:
    """Stable cache key from a title+year, robust to punctuation/case/accents."""
    s = unicodedata.normalize("NFKD", title or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()
    return f"{s}|{year or ''}"


@dataclass
class FilmMeta:
    tmdb_id: int
    title: str
    year: int | None
    genres: str | None = None
    director: str | None = None
    runtime: int | None = None
    poster: str | None = None
    cast: list[str] | None = None   # top-billed, in billing order


class TMDbClient:
    """Thin TMDb v3 client with polite rate limiting and a SQLite-backed cache.

    The cache connection is optional: pass one to persist resolutions, or omit
    it to use the client statelessly (useful from Project 1's HA scripts).
    """

    def __init__(self, api_key: str, *, min_interval: float = 0.30,
                 conn: sqlite3.Connection | None = None,
                 client: httpx.Client | None = None) -> None:
        self.api_key = api_key
        self.min_interval = min_interval
        self.conn = conn
        self._last_call = 0.0
        self._uncommitted = 0
        self._http = client or httpx.Client(timeout=15.0)

    # ── low-level ───────────────────────────────────────────────────
    def _throttle(self) -> None:
        wait = self.min_interval - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def _get(self, path: str, **params) -> dict:
        params["api_key"] = self.api_key
        for _attempt in range(4):
            self._throttle()
            resp = self._http.get(f"{API_ROOT}{path}", params=params)
            if resp.status_code == 429:  # rate limited — back off and retry
                try:
                    retry_after = float(resp.headers.get("Retry-After", 1))
                except ValueError:  # spec also allows an HTTP-date here
                    retry_after = 1.0
                time.sleep(retry_after + 0.25)
                continue
            resp.raise_for_status()
            return resp.json()
        resp.raise_for_status()
        return {}

    # ── cache ───────────────────────────────────────────────────────
    def _cache_get(self, key: str) -> int | None | object:
        """Return tmdb_id, None (known-miss), or _MISSING (not cached)."""
        if self.conn is None:
            return _MISSING
        row = self.conn.execute(
            "SELECT tmdb_id FROM resolution_cache WHERE cache_key = ?", (key,)
        ).fetchone()
        if row is None:
            return _MISSING
        return row[0]  # may be None for a known miss

    def _cache_put(self, key: str, tmdb_id: int | None, source: str) -> None:
        if self.conn is None:
            return
        self.conn.execute(
            """
            INSERT INTO resolution_cache (cache_key, tmdb_id, source)
            VALUES (?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                tmdb_id = excluded.tmdb_id, source = excluded.source,
                resolved_at = datetime('now')
            """,
            (key, tmdb_id, source),
        )
        self._uncommitted += 1
        if self._uncommitted >= CACHE_COMMIT_EVERY:
            self.conn.commit()
            self._uncommitted = 0

    # ── resolution ──────────────────────────────────────────────────
    def resolve(self, title: str, year: int | None) -> int | None:
        """title + year -> tmdb_id (or None). Cached; each film resolved once."""
        key = normalise_key(title, year)
        cached = self._cache_get(key)
        if cached is not _MISSING:
            return cached  # int or None

        tmdb_id = self._search(title, year)
        self._cache_put(key, tmdb_id, "tmdb_search")
        return tmdb_id

    def _search(self, title: str, year: int | None) -> int | None:
        params = {"query": title, "include_adult": "false"}
        if year:
            params["primary_release_year"] = year
        data = self._get("/search/movie", **params)
        results = data.get("results") or []
        if not results and year:  # retry without the year constraint
            data = self._get("/search/movie", query=title, include_adult="false")
            results = data.get("results") or []
        if not results:
            return None
        # Prefer an exact title match in the right year; else most popular.
        norm = normalise_key(title, None).split("|")[0]
        exact = [r for r in results
                 if normalise_key(r.get("title", ""), None).split("|")[0] == norm]
        pool = exact or results
        pool.sort(key=lambda r: r.get("popularity", 0), reverse=True)
        return pool[0]["id"]

    def set_manual(self, title: str, year: int | None, tmdb_id: int) -> None:
        """Pin a hand-fix for the unmatched-review list."""
        self._cache_put(normalise_key(title, year), tmdb_id, "manual")

    def find_by_imdb(self, imdb_id: str) -> int | None:
        """IMDb id (tt…) -> TMDb id via /find. Cached like title lookups."""
        key = f"imdb:{imdb_id}"
        cached = self._cache_get(key)
        if cached is not _MISSING:
            return cached  # int or None
        data = self._get(f"/find/{imdb_id}", external_source="imdb_id")
        results = data.get("movie_results") or []
        tmdb_id = results[0]["id"] if results else None
        self._cache_put(key, tmdb_id, "tmdb_find")
        return tmdb_id

    # ── enrichment ──────────────────────────────────────────────────
    def cast(self, tmdb_id: int) -> list[str]:
        """Top-billed cast alone — the backfill for films enriched before cast was kept."""
        return _top_cast(self._get(f"/movie/{tmdb_id}/credits"))

    def enrich(self, tmdb_id: int) -> FilmMeta:
        data = self._get(f"/movie/{tmdb_id}", append_to_response="credits")
        genres = ", ".join(g["name"] for g in data.get("genres", []))
        director = next(
            (c["name"] for c in data.get("credits", {}).get("crew", [])
             if c.get("job") == "Director"),
            None,
        )
        poster_path = data.get("poster_path")
        poster = f"{IMAGE_BASE}{poster_path}" if poster_path else None
        year = None
        if data.get("release_date"):
            year = int(data["release_date"][:4])
        return FilmMeta(
            tmdb_id=tmdb_id,
            title=data.get("title", ""),
            year=year,
            genres=genres or None,
            director=director,
            runtime=data.get("runtime") or None,
            cast=_top_cast(data.get("credits", {})),
            poster=poster,
        )

    def scores(self, tmdb_id: int) -> dict:
        """TMDb's own audience average plus the IMDb id, for ``app.ratings``.

        Separate from ``enrich`` because it's a detail-card lookup, not part of
        ingestion: it must not touch the fields the picker joins on.
        """
        data = self._get(f"/movie/{tmdb_id}")
        return {
            "vote_average": data.get("vote_average"),
            "vote_count": data.get("vote_count"),
            "imdb_id": data.get("imdb_id") or None,
        }

    def close(self) -> None:
        if self.conn is not None and self._uncommitted:
            self.conn.commit()
            self._uncommitted = 0
        self._http.close()
