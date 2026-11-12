"""Taste dashboard — agreement, per-person stats, blind spots, trends.

All computed from the merged ratings / watched tables. Returns plain dicts /
lists ready for Jinja or a JSON endpoint.
"""
from __future__ import annotations

from collections import Counter

from . import db, taste
from .config import get_settings


def agreement() -> dict:
    """Films both users rated: scatter points + highlights."""
    a, b = get_settings().users
    sql = """
        SELECT f.tmdb_id, f.title, f.year, ra.rating AS ra, rb.rating AS rb
        FROM ratings ra
        JOIN ratings rb ON rb.tmdb_id = ra.tmdb_id AND rb.user = ?
        JOIN films f ON f.tmdb_id = ra.tmdb_id
        WHERE ra.user = ? AND ra.rating IS NOT NULL AND rb.rating IS NOT NULL
    """
    with db.cursor() as cur:
        cur.execute(sql, (b, a))
        rows = cur.fetchall()

    points = [{"tmdb_id": r["tmdb_id"], "title": r["title"], "year": r["year"],
               a: r["ra"], b: r["rb"], "delta": round(r["ra"] - r["rb"], 1)}
              for r in rows]
    perfect = [p for p in points if p["delta"] == 0]
    clashes = sorted((p for p in points if abs(p["delta"]) >= 2),
                     key=lambda p: abs(p["delta"]), reverse=True)
    return {
        "user_a": a, "user_b": b,
        "count": len(points),
        "points": points,
        "perfect_agreements": perfect,
        "biggest_clashes": clashes[:15],
        "avg_delta": round(sum(abs(p["delta"]) for p in points) / len(points), 2) if points else 0.0,
    }


def per_person(user: str) -> dict:
    """Rating distribution + top genres/decades/directors for one user.

    Top genres/directors are taste-weighted, not raw watch counts: each watched
    film counts once, films this user rated 4 stars or higher count a second
    time, and films rated 2 stars or lower subtract one. This mirrors
    ``taste.Scorer`` so the dashboard and the recommendations agree on what a
    "favourite" is. Decades stay a plain count — that's a distribution of when
    films were made, not a taste ranking.
    """
    with db.cursor() as cur:
        cur.execute(
            "SELECT rating FROM ratings WHERE user = ? AND rating IS NOT NULL", (user,)
        )
        ratings = [r[0] for r in cur.fetchall()]
        cur.execute(
            """
            SELECT f.genres, f.director, f.year, r.rating
            FROM watched wd
            JOIN films f ON f.tmdb_id = wd.tmdb_id
            LEFT JOIN ratings r ON r.tmdb_id = wd.tmdb_id AND r.user = wd.user
            WHERE wd.user = ?
            """,
            (user,),
        )
        rows = cur.fetchall()

    dist = Counter(round(r * 2) / 2 for r in ratings)  # half-star buckets
    genres: Counter = Counter()
    directors: Counter = Counter()
    decades: Counter = Counter()
    for r in rows:
        if r["rating"] is not None and r["rating"] >= taste.LOVE_THRESHOLD:
            weight = 2
        elif r["rating"] is not None and r["rating"] <= taste.DISLIKE_THRESHOLD:
            weight = -1
        else:
            weight = 1
        if r["genres"]:
            for g in (g.strip() for g in r["genres"].split(",") if g.strip()):
                genres[g] += weight
        if r["director"]:
            directors[r["director"]] += weight
        if r["year"]:
            decades.update([f"{(r['year'] // 10) * 10}s"])

    return {
        "user": user,
        "rated_count": len(ratings),
        "watched_count": len(rows),
        "avg_rating": round(sum(ratings) / len(ratings), 2) if ratings else None,
        "rating_distribution": dict(sorted(dist.items())),
        # Only positive-affinity entries: dislike penalties can push a genre
        # negative, and "Horror (-3)" isn't a *top* anything.
        "top_genres": [(g, n) for g, n in genres.most_common() if n > 0][:10],
        "top_directors": [(d, n) for d, n in directors.most_common() if n > 0][:10],
        "top_decades": dict(sorted(decades.items())),
    }


def shared_blind_spots(limit: int = 25) -> list[dict]:
    """Films on Plex that neither user has watched or rated — easy wins to log.

    A lightweight stand-in for the 'canon' comparison: surfaces owned films
    nobody has touched, ordered by newest. Swap in a TMDb top-rated list later.
    """
    a, b = get_settings().users
    sql = """
        SELECT f.tmdb_id, f.title, f.year, f.genres
        FROM films f
        JOIN plex_availability pa ON pa.tmdb_id = f.tmdb_id AND pa.available = 1
        WHERE f.tmdb_id NOT IN (SELECT tmdb_id FROM watched)
          AND f.tmdb_id NOT IN (SELECT tmdb_id FROM ratings)
        ORDER BY f.year DESC
        LIMIT ?
    """
    with db.cursor() as cur:
        cur.execute(sql, (limit,))
        return [dict(r) for r in cur.fetchall()]


def to_add(limit: int = 18) -> list[dict]:
    """The shopping list: films on BOTH watchlists but not on Plex yet.

    Ranked by how well each fits your shared taste (genre + director affinity),
    so the ones you'd most likely love sit at the top. Films either of you has
    already watched are excluded. Scoring is shared with the picker via
    ``taste.Scorer`` so the percentages and reasons match everywhere.
    """
    a, b = get_settings().users
    sc = taste.build_scorer()

    sql = """
        SELECT f.tmdb_id, f.title, f.year, f.genres, f.director, f.runtime, f.poster
        FROM films f
        JOIN watchlist wa ON wa.tmdb_id = f.tmdb_id AND wa.user = ?
        JOIN watchlist wb ON wb.tmdb_id = f.tmdb_id AND wb.user = ?
        LEFT JOIN plex_availability pa ON pa.tmdb_id = f.tmdb_id
        WHERE COALESCE(pa.available, 0) = 0
          AND f.tmdb_id NOT IN (SELECT tmdb_id FROM watched)
    """
    with db.cursor() as cur:
        cur.execute(sql, (a, b))
        rows = cur.fetchall()

    recs = []
    for r in rows:
        recs.append({
            "tmdb_id": r["tmdb_id"], "title": r["title"], "year": r["year"],
            "genres": r["genres"], "director": r["director"],
            "runtime": r["runtime"], "poster": r["poster"],
            "match": sc.match(r["genres"], r["director"]),
            "why": sc.why(r["genres"], r["director"]) or "On both your watchlists.",
        })
    recs.sort(key=lambda x: (x["match"], x["year"] or 0), reverse=True)
    return recs[:limit]


BULK_DAY_THRESHOLD = 20  # >this many watches on one day = a backlog dump, not viewing
TIMELINE_START_MONTH = "2022-01"  # before this, only one user was on Letterboxd


def over_time(
    bulk_day_threshold: int = BULK_DAY_THRESHOLD,
    start_month: str = TIMELINE_START_MONTH,
) -> dict:
    """Watches per month per user, from the watched dates.

    Days where a user logged more than ``bulk_day_threshold`` films at once
    (e.g. marking a whole backlog 'watched' when they joined Letterboxd) are
    excluded, so one import day doesn't dwarf the whole chart. Months before
    ``start_month`` (YYYY-MM) are dropped, since only one user was on
    Letterboxd back then and the comparison isn't meaningful.
    """
    a, b = get_settings().users
    day_counts: dict[str, Counter] = {a: Counter(), b: Counter()}
    month_direct: dict[str, Counter] = {a: Counter(), b: Counter()}
    with db.cursor() as cur:
        cur.execute("SELECT user, date FROM watched WHERE date IS NOT NULL")
        for user, date in cur.fetchall():
            if user not in day_counts or not date:
                continue
            if len(date) >= 10:
                day_counts[user][date[:10]] += 1          # YYYY-MM-DD
            elif len(date) >= 7:
                month_direct[user][date[:7]] += 1         # no day precision

    series: dict[str, Counter] = {a: Counter(), b: Counter()}
    excluded: dict[str, int] = {}
    for user, days in day_counts.items():
        for day, n in days.items():
            if n > bulk_day_threshold:
                excluded[user] = excluded.get(user, 0) + n
                continue
            series[user][day[:7]] += n
        series[user].update(month_direct[user])

    months = sorted({m for c in series.values() for m in c if m >= start_month})
    return {
        "months": months,
        "series": {u: [c.get(m, 0) for m in months] for u, c in series.items()},
        "excluded": excluded,  # {user: watches hidden as a bulk-import day}
    }


def summary() -> dict:
    """One call for the dashboard landing view."""
    a, b = get_settings().users
    return {
        "agreement": agreement(),
        "per_person": {a: per_person(a), b: per_person(b)},
        "to_add": to_add(),
        "blind_spots": shared_blind_spots(),
        "over_time": over_time(),
    }
