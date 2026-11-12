"""sync_plex() — pull the Plex library and map GUIDs -> tmdb_id.

Handles both modern Plex Movie agent GUIDs (tmdb://, imdb://) and the legacy
agent format (com.plexapp.agents.themoviedb://12345?lang=en).
"""
from __future__ import annotations

import re
import sqlite3

from plexapi.server import PlexServer

from . import db, notify, taste
from .config import get_settings
from .tmdb import TMDbClient

# plexapi's default is 30s. Listing the 770-film library took 1.4s at rest but
# 72.5s while the Media Scanner was analysing new episodes (2026-11-02), and
# the sync failed with a read timeout. 120s outlasts that; the admin page now
# shows a running job instead of hanging, so the longer wait costs nothing.
PLEX_TIMEOUT_SECONDS = 120

_TMDB_MODERN = re.compile(r"tmdb://(\d+)")
_TMDB_LEGACY = re.compile(r"themoviedb://(\d+)")
_IMDB = re.compile(r"imdb://(tt\d+)")  # same form in both agent styles


def _extract_ids(movie) -> tuple[int | None, str | None]:
    """Return (tmdb_id, imdb_id) from a plex movie's guids, both agent styles."""
    guid_strings = []
    # Modern agent: movie.guids is a list of Guid objects.
    for g in getattr(movie, "guids", []) or []:
        guid_strings.append(getattr(g, "id", "") or "")
    # Always include the primary guid (covers legacy agent).
    if getattr(movie, "guid", None):
        guid_strings.append(movie.guid)

    tmdb_id = imdb_id = None
    for gs in guid_strings:
        if tmdb_id is None:
            m = _TMDB_MODERN.search(gs) or _TMDB_LEGACY.search(gs)
            if m:
                tmdb_id = int(m.group(1))
        if imdb_id is None:
            m = _IMDB.search(gs)
            if m:
                imdb_id = m.group(1)
    return tmdb_id, imdb_id


def _both_want(cur: sqlite3.Cursor, tmdb_ids: set[int], n_users: int) -> list[dict]:
    """Of the given films, those on every user's watchlist (title/year/genres…)."""
    if not tmdb_ids:
        return []
    marks = ",".join("?" * len(tmdb_ids))
    cur.execute(
        f"""
        SELECT f.tmdb_id, f.title, f.year, f.genres, f.director
        FROM films f JOIN watchlist w ON w.tmdb_id = f.tmdb_id
        WHERE f.tmdb_id IN ({marks})
        GROUP BY f.tmdb_id
        HAVING COUNT(DISTINCT w.user) >= ?
        """,
        (*tmdb_ids, n_users),
    )
    return [dict(r) for r in cur.fetchall()]


def _announce_new_arrivals(films: list[dict]) -> bool:
    """Push 'new on Plex, you both want it' — best-effort, never raises."""
    try:
        sc = taste.build_scorer()
        lines = []
        for f in films:
            year = f" ({f['year']})" if f["year"] else ""
            pct = sc.match(f["genres"], f["director"])
            lines.append(f"🎬 {f['title']}{year} — {pct}% match, on both watchlists")
        return notify.send("\n".join(lines), title="New on Plex — you both want this")
    except Exception:  # noqa: BLE001 — notification must never break the sync
        return False


def sync_plex() -> dict:
    """Idempotent: re-flag availability for everything currently on Plex.

    Movies whose Plex GUIDs carry only an IMDb id are rescued through TMDb's
    /find endpoint (cached), instead of being dropped. Newly-available films
    that sit on both watchlists are pushed via notify.send() — except on the
    first-ever sync, where everything would count as "new".

    Returns a small summary dict for the admin UI.
    """
    s = get_settings()
    server = PlexServer(s.plex_baseurl, s.plex_token, timeout=PLEX_TIMEOUT_SECONDS)
    section = server.library.section(s.plex_movie_library)

    tmdb_client = None
    cache_conn = None
    if s.tmdb_api_key and s.tmdb_api_key != "changeme":
        cache_conn = db.connect()
        tmdb_client = TMDbClient(s.tmdb_api_key, min_interval=s.tmdb_min_interval,
                                 conn=cache_conn)

    matched = 0
    imdb_rescued = 0
    no_tmdb: list[str] = []
    now_available: set[int] = set()

    try:
        with db.cursor() as cur:
            cur.execute("SELECT tmdb_id FROM plex_availability WHERE available = 1")
            previously = {r[0] for r in cur.fetchall()}
            db.reset_plex_availability(cur)
            # includeGuids keeps `.guids` populated in the listing itself; without
            # it each access triggers a per-movie reload (one HTTP call per film).
            for movie in section.all(includeGuids=True):
                tmdb_id, imdb_id = _extract_ids(movie)
                if tmdb_id is None and imdb_id and tmdb_client is not None:
                    try:
                        tmdb_id = tmdb_client.find_by_imdb(imdb_id)
                    except Exception:  # noqa: BLE001 — fall through to unmatched
                        tmdb_id = None
                    if tmdb_id is not None:
                        imdb_rescued += 1
                if tmdb_id is None:
                    no_tmdb.append(f"{movie.title} ({getattr(movie, 'year', '?')})")
                    continue
                db.upsert_film(cur, tmdb_id, movie.title, getattr(movie, "year", None))
                added = movie.addedAt.date().isoformat() if getattr(movie, "addedAt", None) else None
                watched = 1 if getattr(movie, "isWatched", False) else 0
                db.upsert_availability(cur, tmdb_id, available=1, watched=watched, added=added)
                now_available.add(tmdb_id)
                matched += 1

            new_both_want = (_both_want(cur, now_available - previously, len(s.users))
                             if previously else [])  # skip on first-ever sync
            db.stamp(cur, "plex_synced_at")
    finally:
        if tmdb_client is not None:
            tmdb_client.close()
        if cache_conn is not None:
            cache_conn.close()

    # Notify only after the sync committed, so we never announce a failed run.
    notified = _announce_new_arrivals(new_both_want) if new_both_want else False

    return {"matched": matched, "without_tmdb_guid": no_tmdb,
            "unmatched_count": len(no_tmdb), "imdb_rescued": imdb_rescued,
            "new_both_want": [f["title"] for f in new_both_want],
            "notified": notified}
