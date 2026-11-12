"""Picker logic — what to actually watch tonight.

Everything is an intersection of the combined Letterboxd watchlists with the
Plex library (joined on tmdb_id). Surface modes and filters per the build plan.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date

from . import db, taste
from .config import get_settings


@dataclass
class Film:
    tmdb_id: int
    title: str
    year: int | None
    genres: str | None
    director: str | None
    runtime: int | None
    poster: str | None
    wanted_by: list[str]      # which users have it on their watchlist
    seen_by: list[str]        # which users have watched it
    on_plex: bool
    added: str | None = None  # ISO date it landed on Plex
    vetoes: int = 0           # times vetoed on a shortlist; down-weights spins
    last_veto: str | None = None  # UTC 'YYYY-MM-DD HH:MM:SS'; hides it from Tonight till 6 a.m.
    match: int | None = None  # taste-fit %, set by _annotate()
    why: str | None = None    # one-line reason, set by _annotate()
    on_get_list: bool = False  # marked to get onto Plex
    # Set by tonight.annotate(): each user's own fit, the pair score, and the
    # score for whoever's watching tonight (the pair, or one user's fit).
    fits: dict[str, int] | None = None
    pair: int | None = None
    score: int | None = None
    tiebreak: int = 0  # the pair before its 100 cap; ranks films tied on score


@dataclass
class Filters:
    max_runtime: int | None = None      # minutes; school-night vs weekend
    genre: str | None = None            # substring match against genres
    decade: int | None = None           # e.g. 1990
    unseen_only: bool = False           # neither user has watched it


def _row_to_film(row, users: list[str]) -> Film:
    # Fixed a/b aliases in the SQL (usernames aren't valid SQL identifiers);
    # zip maps them back to the configured users, in order.
    wanted = [u for u, flag in zip(users, (row["want_a"], row["want_b"]), strict=True) if flag]
    seen = [u for u, flag in zip(users, (row["seen_a"], row["seen_b"]), strict=True) if flag]
    return Film(
        tmdb_id=row["tmdb_id"], title=row["title"], year=row["year"],
        genres=row["genres"], director=row["director"], runtime=row["runtime"],
        poster=row["poster"], wanted_by=wanted, seen_by=seen,
        on_plex=bool(row["on_plex"]), added=row["added"], vetoes=row["vetoes"],
        last_veto=row["last_veto"],
        on_get_list=bool(row["on_get_list"]),
    )


def _eligible_query(users: list[str], tmdb_id: int | None = None) -> tuple[str, list]:
    """Build a query returning every film with per-user want/seen flags.

    Pass ``tmdb_id`` to narrow it to one film (the detail card) — SQLite binds
    parameters in the order they appear in the statement, so it goes last.
    """
    a, b = users[0], users[1]
    sql = f"""
        SELECT f.tmdb_id, f.title, f.year, f.genres, f.director, f.runtime, f.poster,
               COALESCE(pa.available, 0) AS on_plex, pa.added AS added,
               COALESCE(v.count, 0) AS vetoes, v.last_veto AS last_veto,
               (gl.tmdb_id IS NOT NULL) AS on_get_list,
               MAX(CASE WHEN w.user = ? THEN 1 ELSE 0 END) AS want_a,
               MAX(CASE WHEN w.user = ? THEN 1 ELSE 0 END) AS want_b,
               MAX(CASE WHEN wd.user = ? THEN 1 ELSE 0 END) AS seen_a,
               MAX(CASE WHEN wd.user = ? THEN 1 ELSE 0 END) AS seen_b
        FROM films f
        LEFT JOIN plex_availability pa ON pa.tmdb_id = f.tmdb_id
        LEFT JOIN vetoes v ON v.tmdb_id = f.tmdb_id
        LEFT JOIN get_list gl ON gl.tmdb_id = f.tmdb_id
        LEFT JOIN watchlist w  ON w.tmdb_id  = f.tmdb_id
        LEFT JOIN watched   wd ON wd.tmdb_id = f.tmdb_id
        {"WHERE f.tmdb_id = ?" if tmdb_id is not None else ""}
        GROUP BY f.tmdb_id
    """
    params = [a, b, a, b]
    if tmdb_id is not None:
        params.append(tmdb_id)
    return sql, params


def _apply_filters(films: list[Film], f: Filters) -> list[Film]:
    out = films
    if f.max_runtime:
        out = [x for x in out if x.runtime and x.runtime <= f.max_runtime]
    if f.genre:
        g = f.genre.lower()
        out = [x for x in out if x.genres and g in x.genres.lower()]
    if f.decade:
        out = [x for x in out if x.year and f.decade <= x.year < f.decade + 10]
    if f.unseen_only:
        out = [x for x in out if not x.seen_by]
    return out


def load(users: list[str]) -> list[Film]:
    sql, params = _eligible_query(users)
    with db.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [_row_to_film(r, users) for r in rows]


def _annotate(films: list[Film]) -> list[Film]:
    """Tag each film with a taste match % and reason, best matches first.

    Same scorer as the dashboard's "worth adding" feed, so a film shows the
    same percentage and reason on the picker as it would there.
    """
    if not films:
        return films
    sc = taste.build_scorer()
    for f in films:
        f.match = sc.match(f.genres, f.director)
        f.why = sc.why(f.genres, f.director) or None
    films.sort(key=lambda f: f.match or 0, reverse=True)
    return films


def by_id(tmdb_id: int) -> Film | None:
    """One film with the same annotations the grid shows — for the detail card.

    Not restricted to the Plex intersection: the dashboard's "worth adding"
    entries aren't on Plex yet and are still worth opening.
    """
    users = get_settings().users
    sql, params = _eligible_query(users, tmdb_id)
    with db.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    if row is None:
        return None
    return _annotate([_row_to_film(row, users)])[0]


# ── surface modes ───────────────────────────────────────────────────

def both_want(filters: Filters | None = None) -> list[Film]:
    """In both watchlists AND on Plex — the sweet spot."""
    users = get_settings().users
    films = [f for f in load(users) if f.on_plex and len(f.wanted_by) == len(users)]
    return _annotate(_apply_filters(films, filters or Filters()))


def one_wants(filters: Filters | None = None) -> list[Film]:
    """In at least one watchlist AND on Plex."""
    users = get_settings().users
    films = [f for f in load(users) if f.on_plex and len(f.wanted_by) >= 1]
    return _annotate(_apply_filters(films, filters or Filters()))


def eligible_set(filters: Filters | None = None) -> list[Film]:
    """Anything on Plex that at least one person wants — the random pool."""
    return one_wants(filters)


def _recency_boost(added: str | None) -> float:
    """Weight multiplier for films recently added to Plex (fresh acquisitions)."""
    if not added:
        return 1.0
    try:
        days = (date.today() - date.fromisoformat(added)).days
    except ValueError:
        return 1.0
    if days <= 30:
        return 2.0
    if days <= 90:
        return 1.5
    return 1.0


def spin_the_wheel(filters: Filters | None = None, mode: str = "one",
                   *, weight_recency: bool = True) -> Film | None:
    """Weighted random pick from the chosen scope ('both' or 'one').

    Films both want, and films recently added to Plex, weigh more; films
    that have been vetoed on shortlists weigh less (never zero — a veto is
    "not tonight", not "never").
    """
    pool = both_want(filters) if mode == "both" else one_wants(filters)
    if not pool:
        return None
    n_users = len(get_settings().users)
    weights = []
    for f in pool:
        w = 1.0
        if len(f.wanted_by) == n_users:
            w *= 3.0           # both want it -> 3x more likely
        if weight_recency:
            w *= _recency_boost(f.added)
        w /= 1.0 + f.vetoes    # each veto halves-ish the odds
        weights.append(w)
    return random.choices(pool, weights=weights, k=1)[0]


def no_veto_shortlist(n: int = 3, filters: Filters | None = None,
                      mode: str = "one") -> list[Film]:
    """Pick n distinct films from the chosen scope; each person can veto one."""
    pool = both_want(filters) if mode == "both" else one_wants(filters)
    random.shuffle(pool)  # deliberately discards the match ordering
    # Prefer 'both want it' first, then fill; previously-vetoed films sink
    # within each group (stable sort keeps the shuffle among equals).
    n_users = len(get_settings().users)
    both, rest = [], []
    for f in pool:
        (both if len(f.wanted_by) == n_users else rest).append(f)
    both.sort(key=lambda f: f.vetoes)
    rest.sort(key=lambda f: f.vetoes)
    return (both + rest)[:n]
