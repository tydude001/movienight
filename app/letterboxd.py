"""import_letterboxd(user, zip) — parse a Letterboxd export and resolve to TMDb.

The export ZIP contains: watchlist.csv, ratings.csv, watched.csv, diary.csv,
reviews.csv, profile.csv. We use watchlist / ratings / watched / diary. Each row has
Name, Year, and a Letterboxd URI but no TMDb id — so every entry is resolved
through the cached TMDb resolver. Unresolved rows are parked in `unmatched`
for hand-fixing. Idempotent: re-importing overwrites cleanly.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pandas as pd

from . import db
from .config import get_settings
from .tmdb import TMDbClient, normalise_key


def _read_csv(zf: zipfile.ZipFile, name: str) -> pd.DataFrame | None:
    match = next((n for n in zf.namelist() if n.lower().endswith(name)), None)
    if match is None:
        return None
    with zf.open(match) as fh:
        return pd.read_csv(io.BytesIO(fh.read()))


def _year(val) -> int | None:
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _date(val) -> str | None:
    """CSV date cell -> string, mapping pandas NaN to None (str(nan) == 'nan')."""
    if val is None or pd.isna(val):
        return None
    return str(val).strip() or None


def _record_unmatched(cur, user: str, row, kind: str) -> None:
    title = str(row.get("Name", "")).strip()
    year = _year(row.get("Year"))
    cur.execute(
        """
        INSERT INTO unmatched (cache_key, user, title, year, uri, kind)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(cache_key) DO UPDATE SET
            user = excluded.user, title = excluded.title,
            year = excluded.year, uri = excluded.uri, kind = excluded.kind
        """,
        (normalise_key(title, year), user, title, year,
         str(row.get("Letterboxd URI", "")), kind),
    )


def import_letterboxd(user: str, zip_path: str | Path) -> dict:
    s = get_settings()
    conn = db.connect()
    # The resolver cache gets its own connection: _cache_put commits as it
    # goes, and sharing `conn` would commit the half-rebuilt watchlist
    # mid-import. This way the data writes stay one atomic transaction.
    cache_conn = db.connect()
    tmdb = TMDbClient(s.tmdb_api_key, min_interval=s.tmdb_min_interval, conn=cache_conn)
    summary = {"user": user, "watchlist": 0, "ratings": 0, "watched": 0, "unmatched": 0}

    try:
        with zipfile.ZipFile(zip_path) as zf:
            watchlist = _read_csv(zf, "watchlist.csv")
            ratings = _read_csv(zf, "ratings.csv")
            watched = _read_csv(zf, "watched.csv")
            diary = _read_csv(zf, "diary.csv")

        cur = conn.cursor()

        # Watchlist replaces wholesale (it shrinks as well as grows).
        if watchlist is not None:
            db.clear_user_watchlist(cur, user)
            for _, row in watchlist.iterrows():
                tid = _resolve_row(tmdb, row)
                if tid is None:
                    _record_unmatched(cur, user, row, "watchlist")
                    summary["unmatched"] += 1
                    continue
                db.upsert_film(cur, tid, str(row["Name"]).strip(), _year(row.get("Year")))
                db.upsert_watchlist(cur, user, tid)
                summary["watchlist"] += 1

        if ratings is not None:
            for _, row in ratings.iterrows():
                tid = _resolve_row(tmdb, row)
                if tid is None:
                    _record_unmatched(cur, user, row, "ratings")
                    summary["unmatched"] += 1
                    continue
                db.upsert_film(cur, tid, str(row["Name"]).strip(), _year(row.get("Year")))
                rating = row.get("Rating")
                db.upsert_rating(cur, user, tid,
                                 float(rating) if pd.notna(rating) else None,
                                 _date(row.get("Date")))
                summary["ratings"] += 1

        if watched is not None:
            for _, row in watched.iterrows():
                tid = _resolve_row(tmdb, row)
                if tid is None:
                    _record_unmatched(cur, user, row, "watched")
                    summary["unmatched"] += 1
                    continue
                db.upsert_film(cur, tid, str(row["Name"]).strip(), _year(row.get("Year")))
                db.upsert_watched(cur, user, tid, _date(row.get("Date")))
                summary["watched"] += 1

        if diary is not None:
            summary["diary"] = _import_diary(cur, tmdb, user, diary, summary)

        conn.commit()
    finally:
        tmdb.close()
        cache_conn.close()
        conn.close()

    return summary


def _import_diary(cur, tmdb: TMDbClient, user: str, diary: pd.DataFrame,
                  summary: dict) -> int:
    """diary.csv -> the `diary` table, one row per watch on its *Watched Date*.

    The export's ``Date`` column is when the entry was logged (in UTC, so an
    evening watch usually lands a day late); ``Watched Date`` is the date the
    person set, and it's the only one used. The export is the whole diary, so
    everything up to its last date is replaced: an entry deleted or re-dated on Letterboxd
    disappears here too. Rows after its last date (the RSS sync's, newer than
    the export) are left alone. Everything before that date goes, not only the
    span the export's own entries cover: an entry re-dated later would
    otherwise leave its old date behind as a second watch.
    """
    rows = []
    for _, row in diary.iterrows():
        watched_date = _date(row.get("Watched Date"))
        if not watched_date:
            continue
        tid = _resolve_row(tmdb, row)
        if tid is None:
            _record_unmatched(cur, user, row, "diary")
            summary["unmatched"] += 1
            continue
        rating = row.get("Rating")
        rows.append((tid, str(row["Name"]).strip(), _year(row.get("Year")), watched_date,
                     float(rating) if pd.notna(rating) else None,
                     str(row.get("Rewatch", "")).strip().lower() == "yes"))
    if not rows:
        return 0
    cur.execute(
        "DELETE FROM diary WHERE user = ? AND watched_date <= ?",
        (user, max(r[3] for r in rows)),
    )
    for tid, title, year, watched_date, rating, rewatch in rows:
        db.upsert_film(cur, tid, title, year)
        db.upsert_diary(cur, user, tid, watched_date, rating, rewatch)
    return len(rows)


def _resolve_row(tmdb: TMDbClient, row) -> int | None:
    title = str(row.get("Name", "")).strip()
    if not title:
        return None
    return tmdb.resolve(title, _year(row.get("Year")))


def import_from_dir() -> list[dict]:
    """Convenience: import every *.zip in the watched IMPORT_DIR.

    Filename convention: ``<user>-*.zip`` (e.g. alex-2026-06-25.zip) routes
    the export to that user. Falls back to USER_A if no prefix matches.
    """
    s = get_settings()
    results = []
    for zp in sorted(s.import_path.glob("*.zip")):
        prefix = zp.stem.split("-", 1)[0].lower()
        user = next((u for u in s.users if u.lower() == prefix), s.user_a)
        results.append(import_letterboxd(user, zp))
    return results
