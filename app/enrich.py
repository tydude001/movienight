"""enrich_tmdb() — fill genre / director / runtime / poster / cast for films missing them.

Idempotent and incremental: only touches rows where enriched = 0, so re-running
is cheap and resumable after a rate-limit interruption.
"""
from __future__ import annotations

from . import db
from .config import get_settings
from .tmdb import TMDbClient


def enrich_tmdb(limit: int | None = None) -> dict:
    s = get_settings()
    conn = db.connect()
    tmdb = TMDbClient(s.tmdb_api_key, min_interval=s.tmdb_min_interval, conn=conn)
    done = failed = 0

    try:
        q = "SELECT tmdb_id FROM films WHERE enriched = 0"
        if limit:
            q += f" LIMIT {int(limit)}"
        ids = [r[0] for r in conn.execute(q).fetchall()]

        cur = conn.cursor()
        for tmdb_id in ids:
            try:
                meta = tmdb.enrich(tmdb_id)
            except Exception:  # noqa: BLE001 — leave enriched=0 to retry next run
                failed += 1
                continue
            db.set_enrichment(
                cur, tmdb_id,
                genres=meta.genres, director=meta.director,
                runtime=meta.runtime, poster=meta.poster,
            )
            if meta.cast is not None:
                db.set_cast(cur, tmdb_id, meta.cast)
            if meta.year:
                cur.execute(
                    "UPDATE films SET year = COALESCE(year, ?) WHERE tmdb_id = ?",
                    (meta.year, tmdb_id),
                )
            done += 1
            if done % 25 == 0:
                conn.commit()
        conn.commit()
        cast = _backfill_cast(conn, tmdb, limit)
    finally:
        tmdb.close()
        conn.close()

    return {"enriched": done, "failed": failed, **cast}


def _backfill_cast(conn, tmdb: TMDbClient, limit: int | None) -> dict:
    """Cast for diary films enriched before cast was kept.

    Only films someone has a diary entry for — Wrapped is the one reader, and
    the whole watchlist would be thousands of calls for nothing.
    """
    q = ("SELECT DISTINCT d.tmdb_id FROM diary d "
         "LEFT JOIN film_cast c ON c.tmdb_id = d.tmdb_id WHERE c.tmdb_id IS NULL")
    if limit:
        q += f" LIMIT {int(limit)}"
    ids = [r[0] for r in conn.execute(q).fetchall()]
    done = failed = 0
    cur = conn.cursor()
    for tmdb_id in ids:
        try:
            names = tmdb.cast(tmdb_id)
        except Exception:  # noqa: BLE001 — no row, so the next run retries
            failed += 1
            continue
        db.set_cast(cur, tmdb_id, names)
        done += 1
        if done % 25 == 0:
            conn.commit()
    conn.commit()
    return {"cast": done, "cast_failed": failed}
