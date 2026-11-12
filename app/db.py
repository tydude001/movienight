"""SQLite layer: connection, schema, and small helpers.

All writes are upserts so ingestion jobs are idempotent (re-running
overwrites cleanly), per the build plan.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from .config import get_settings

SCHEMA = """
-- Canonical film record, keyed by TMDb ID.
CREATE TABLE IF NOT EXISTS films (
    tmdb_id    INTEGER PRIMARY KEY,
    title      TEXT NOT NULL,
    year       INTEGER,
    genres     TEXT,          -- comma-separated
    director   TEXT,
    runtime    INTEGER,       -- minutes
    poster     TEXT,          -- TMDb poster path or full URL
    enriched   INTEGER NOT NULL DEFAULT 0  -- 1 once genre/director/runtime filled
);

-- What's on the Plex server.
CREATE TABLE IF NOT EXISTS plex_availability (
    tmdb_id    INTEGER PRIMARY KEY,
    available  INTEGER NOT NULL DEFAULT 1,
    watched    INTEGER NOT NULL DEFAULT 0,
    added      TEXT,          -- ISO date the item was added to Plex
    FOREIGN KEY (tmdb_id) REFERENCES films(tmdb_id)
);

-- Per-user Letterboxd watchlist.
CREATE TABLE IF NOT EXISTS watchlist (
    user     TEXT NOT NULL,
    tmdb_id  INTEGER NOT NULL,
    PRIMARY KEY (user, tmdb_id),
    FOREIGN KEY (tmdb_id) REFERENCES films(tmdb_id)
);

-- Per-user ratings (Letterboxd 0.5–5.0 scale).
CREATE TABLE IF NOT EXISTS ratings (
    user     TEXT NOT NULL,
    tmdb_id  INTEGER NOT NULL,
    rating   REAL,
    date     TEXT,
    PRIMARY KEY (user, tmdb_id),
    FOREIGN KEY (tmdb_id) REFERENCES films(tmdb_id)
);

-- Per-user watched history.
CREATE TABLE IF NOT EXISTS watched (
    user     TEXT NOT NULL,
    tmdb_id  INTEGER NOT NULL,
    date     TEXT,
    PRIMARY KEY (user, tmdb_id),
    FOREIGN KEY (tmdb_id) REFERENCES films(tmdb_id)
);

-- Per-user Letterboxd diary: one row per watch, on the date it was watched.
-- `watched` above keeps one row per film and its import date is when the
-- entry was *logged*; Wrapped needs every watch on its real date, so it reads
-- this table only. Filled from diary.csv in the export and from the RSS sync.
-- A film watched twice on one day collapses to one row (rare, and harmless).
CREATE TABLE IF NOT EXISTS diary (
    user          TEXT NOT NULL,
    tmdb_id       INTEGER NOT NULL,
    watched_date  TEXT NOT NULL,      -- YYYY-MM-DD, as entered on Letterboxd
    rating        REAL,
    rewatch       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user, tmdb_id, watched_date),
    FOREIGN KEY (tmdb_id) REFERENCES films(tmdb_id)
);

-- Top-billed cast per film (TMDb credits, billing order). A row means "fetched";
-- an empty list is a real answer, not a reason to fetch again.
CREATE TABLE IF NOT EXISTS film_cast (
    tmdb_id    INTEGER PRIMARY KEY,
    names      TEXT NOT NULL,      -- JSON list of names
    FOREIGN KEY (tmdb_id) REFERENCES films(tmdb_id)
);

-- Cache: Letterboxd entry -> TMDb id. Resolve each film once, never again.
CREATE TABLE IF NOT EXISTS resolution_cache (
    cache_key  TEXT PRIMARY KEY,   -- normalised "title|year" or letterboxd uri
    tmdb_id    INTEGER,            -- NULL means "looked up, found nothing"
    source     TEXT,               -- 'tmdb_search' | 'letterboxd_scrape' | 'manual'
    resolved_at TEXT DEFAULT (datetime('now'))
);

-- Films vetoed ("Not tonight"). Tonight hides one until 6 a.m. local after
-- its last_veto; after that it's back, and spins still down-weight by count.
CREATE TABLE IF NOT EXISTS vetoes (
    tmdb_id    INTEGER PRIMARY KEY,
    count      INTEGER NOT NULL DEFAULT 0,
    last_veto  TEXT,
    FOREIGN KEY (tmdb_id) REFERENCES films(tmdb_id)
);

-- Cached external scores (TMDb / IMDb / Rotten Tomatoes / Metacritic /
-- Letterboxd) for the film detail card. Refetched when older than
-- RATINGS_TTL_HOURS; see app/ratings.py.
CREATE TABLE IF NOT EXISTS external_ratings (
    tmdb_id     INTEGER PRIMARY KEY,
    imdb_id     TEXT,
    tmdb_score  REAL,      -- 0–10
    tmdb_votes  INTEGER,
    imdb_score  REAL,      -- 0–10
    rt_score    INTEGER,   -- tomatometer, 0–100
    metacritic  INTEGER,   -- 0–100
    lb_score    REAL,      -- 0–5
    lb_votes    INTEGER,
    lb_url      TEXT,      -- resolved letterboxd.com/film/... page
    fetched_at  TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (tmdb_id) REFERENCES films(tmdb_id)
);

-- Films you've marked to get onto Plex. Order of marking is the list's order.
CREATE TABLE IF NOT EXISTS get_list (
    tmdb_id    INTEGER PRIMARY KEY,
    added_at   TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (tmdb_id) REFERENCES films(tmdb_id)
);

-- Small key/value facts about the data itself, e.g. when each sync last ran
-- ('plex_synced_at', 'letterboxd_rss_at'). Written by the jobs, shown in the
-- footer so stale data is visible instead of silent.
CREATE TABLE IF NOT EXISTS app_meta (
    key        TEXT PRIMARY KEY,
    value      TEXT
);

-- Rows that couldn't be auto-resolved — hand-fix list.
CREATE TABLE IF NOT EXISTS unmatched (
    cache_key  TEXT PRIMARY KEY,
    user       TEXT,
    title      TEXT,
    year       INTEGER,
    uri        TEXT,
    kind       TEXT,               -- 'watchlist' | 'ratings' | 'watched'
    seen_at    TEXT DEFAULT (datetime('now'))
);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(get_settings().db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    # The sync container and the web app may write concurrently; wait for a
    # lock rather than failing immediately.
    conn.execute("PRAGMA busy_timeout = 8000")
    return conn


def init_db() -> None:
    s = get_settings()
    s.db_path.parent.mkdir(parents=True, exist_ok=True)
    s.import_path.mkdir(parents=True, exist_ok=True)
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


@contextmanager
def cursor() -> Iterator[sqlite3.Cursor]:
    conn = connect()
    try:
        yield conn.cursor()
        conn.commit()
    finally:
        conn.close()


# ── upsert helpers ──────────────────────────────────────────────────

def upsert_film(cur: sqlite3.Cursor, tmdb_id: int, title: str, year: int | None) -> None:
    """Insert a bare film if absent; never clobber enrichment already present."""
    cur.execute(
        """
        INSERT INTO films (tmdb_id, title, year) VALUES (?, ?, ?)
        ON CONFLICT(tmdb_id) DO UPDATE SET
            title = excluded.title,
            year  = COALESCE(excluded.year, films.year)
        """,
        (tmdb_id, title, year),
    )


def set_enrichment(cur: sqlite3.Cursor, tmdb_id: int, *, genres: str | None,
                   director: str | None, runtime: int | None, poster: str | None) -> None:
    cur.execute(
        """
        UPDATE films SET genres = ?, director = ?, runtime = ?, poster = ?, enriched = 1
        WHERE tmdb_id = ?
        """,
        (genres, director, runtime, poster, tmdb_id),
    )


def upsert_availability(cur: sqlite3.Cursor, tmdb_id: int, *, available: int,
                        watched: int, added: str | None) -> None:
    cur.execute(
        """
        INSERT INTO plex_availability (tmdb_id, available, watched, added)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(tmdb_id) DO UPDATE SET
            available = excluded.available,
            watched   = excluded.watched,
            added     = excluded.added
        """,
        (tmdb_id, available, watched, added),
    )


def upsert_watchlist(cur: sqlite3.Cursor, user: str, tmdb_id: int) -> None:
    cur.execute(
        "INSERT OR IGNORE INTO watchlist (user, tmdb_id) VALUES (?, ?)",
        (user, tmdb_id),
    )


def upsert_rating(cur: sqlite3.Cursor, user: str, tmdb_id: int,
                  rating: float | None, date: str | None) -> None:
    cur.execute(
        """
        INSERT INTO ratings (user, tmdb_id, rating, date) VALUES (?, ?, ?, ?)
        ON CONFLICT(user, tmdb_id) DO UPDATE SET
            rating = excluded.rating, date = excluded.date
        """,
        (user, tmdb_id, rating, date),
    )


def upsert_watched(cur: sqlite3.Cursor, user: str, tmdb_id: int, date: str | None) -> None:
    cur.execute(
        """
        INSERT INTO watched (user, tmdb_id, date) VALUES (?, ?, ?)
        ON CONFLICT(user, tmdb_id) DO UPDATE SET date = excluded.date
        """,
        (user, tmdb_id, date),
    )


def upsert_diary(cur: sqlite3.Cursor, user: str, tmdb_id: int, watched_date: str,
                 rating: float | None, rewatch: bool) -> None:
    cur.execute(
        """
        INSERT INTO diary (user, tmdb_id, watched_date, rating, rewatch)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user, tmdb_id, watched_date) DO UPDATE SET
            rating = excluded.rating, rewatch = excluded.rewatch
        """,
        (user, tmdb_id, watched_date, rating, int(rewatch)),
    )


def set_cast(cur: sqlite3.Cursor, tmdb_id: int, names: list[str]) -> None:
    cur.execute(
        "INSERT INTO film_cast (tmdb_id, names) VALUES (?, ?) "
        "ON CONFLICT(tmdb_id) DO UPDATE SET names = excluded.names",
        (tmdb_id, json.dumps(names)),
    )


def add_veto(cur: sqlite3.Cursor, tmdb_id: int) -> None:
    cur.execute(
        """
        INSERT INTO vetoes (tmdb_id, count, last_veto) VALUES (?, 1, datetime('now'))
        ON CONFLICT(tmdb_id) DO UPDATE SET
            count = vetoes.count + 1, last_veto = datetime('now')
        """,
        (tmdb_id,),
    )


def toggle_get_list(cur: sqlite3.Cursor, tmdb_id: int) -> bool:
    """Add a film to the get-list, or take it off. Returns True if now on it."""
    cur.execute("DELETE FROM get_list WHERE tmdb_id = ?", (tmdb_id,))
    if cur.rowcount:
        return False
    cur.execute("INSERT INTO get_list (tmdb_id) VALUES (?)", (tmdb_id,))
    return True


def stamp(cur: sqlite3.Cursor, key: str) -> None:
    """Record now (UTC, ISO) under ``key`` in app_meta."""
    cur.execute(
        """
        INSERT INTO app_meta (key, value) VALUES (?, datetime('now'))
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key,),
    )


def clear_user_watchlist(cur: sqlite3.Cursor, user: str) -> None:
    """Watchlists shrink as well as grow, so a re-import replaces wholesale."""
    cur.execute("DELETE FROM watchlist WHERE user = ?", (user,))


def reset_plex_availability(cur: sqlite3.Cursor) -> None:
    """Mark everything unavailable before a sync; the sync re-flags what's present."""
    cur.execute("UPDATE plex_availability SET available = 0")
