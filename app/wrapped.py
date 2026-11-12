"""Wrapped — one year of both diaries, as story cards.

Reads the ``diary`` table only: one row per watch, on the date the person set
on Letterboxd. ``watched`` is no use here — one row per film, and its import
date is when the entry was *logged*, a day late for most evening watches.

Three counting rules, each one a decision rather than an accident:

* **A watch is a diary entry.** A film ticked "watched" with no diary entry is
  a catch-up ("I've seen this"), not a viewing, so it never reaches the table.
* **A catch-up day is dropped.** ``CATCHUP_MIN`` or more entries by one person
  on one date is a backfill dated in bulk, not a day of viewing; those entries
  are left out and reported (``excluded``) so the page can say so.
* **Together means the same film on the same date in both diaries.** No
  slack: two days' tolerance added a handful of edge cases and no real nights.

Returns plain dicts for Jinja and ``/api/wrapped/{year}``.
"""
from __future__ import annotations

import calendar
import datetime as dt
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from . import db
from .config import get_settings

CATCHUP_MIN = 5          # entries by one person on one date that mark a backfill
ACTOR_MIN_NIGHTS = 3     # nights together before an actor can top a card...
ACTOR_FALLBACK_NIGHTS = 2  # ...relaxed when a thin (part-)year has none at 3
DIRECTOR_MIN_NIGHTS = 2
FAVOURITES = 6
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


@dataclass
class Entry:
    user: str
    tmdb_id: int
    date: str
    rating: float | None
    rewatch: bool
    title: str
    year: int | None
    runtime: int
    director: str | None
    poster: str | None
    genres: list[str] = field(default_factory=list)
    cast: list[str] = field(default_factory=list)


def years() -> list[int]:
    """Years with any diary entry, newest first."""
    with db.cursor() as cur:
        cur.execute("SELECT DISTINCT substr(watched_date, 1, 4) FROM diary ORDER BY 1 DESC")
        return [int(r[0]) for r in cur.fetchall() if r[0] and r[0].isdigit()]


def _load(year: int) -> list[Entry]:
    with db.cursor() as cur:
        cur.execute(
            """
            SELECT d.user, d.tmdb_id, d.watched_date, d.rating, d.rewatch,
                   f.title, f.year, f.runtime, f.director, f.poster, f.genres,
                   c.names AS cast_names
            FROM diary d
            JOIN films f ON f.tmdb_id = d.tmdb_id
            LEFT JOIN film_cast c ON c.tmdb_id = d.tmdb_id
            WHERE d.watched_date >= ? AND d.watched_date < ?
            ORDER BY d.watched_date, d.tmdb_id
            """,
            (f"{year}-01-01", f"{year + 1}-01-01"),
        )
        rows = cur.fetchall()
    return [Entry(
        user=r["user"], tmdb_id=r["tmdb_id"], date=r["watched_date"][:10],
        rating=r["rating"], rewatch=bool(r["rewatch"]), title=r["title"],
        year=r["year"], runtime=r["runtime"] or 0, director=r["director"],
        poster=r["poster"],
        genres=[g.strip() for g in (r["genres"] or "").split(",") if g.strip()],
        cast=json.loads(r["cast_names"]) if r["cast_names"] else [],
    ) for r in rows]


def _drop_catchups(entries: list[Entry]) -> tuple[list[Entry], list[dict]]:
    per_day = Counter((e.user, e.date) for e in entries)
    bulk = {k for k, n in per_day.items() if n >= CATCHUP_MIN}
    excluded = [{"user": u, "date": d, "entries": per_day[(u, d)]} for u, d in sorted(bulk)]
    return [e for e in entries if (e.user, e.date) not in bulk], excluded


def _avg(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 2) if xs else None


def _film(e: Entry) -> dict:
    return {"tmdb_id": e.tmdb_id, "title": e.title, "year": e.year,
            "date": e.date, "day": _short(e.date), "poster": e.poster}


def _top(counter: Counter, ratings: dict[str, list[float]], minimum: int = 1):
    """Most frequent key; ties go to the higher average rating, then the name."""
    best = sorted(((n, _avg(ratings.get(k, [])) or 0, k) for k, n in counter.items()
                   if n >= minimum), key=lambda t: (-t[0], -t[1], t[2]))
    return best


def _person(entries: list[Entry], together: set[tuple[int, str]]) -> dict:
    genres, directors, actors = Counter(), Counter(), Counter()
    g_r, d_r, a_r = defaultdict(list), defaultdict(list), defaultdict(list)
    actor_films = defaultdict(list)
    for e in entries:
        for g in e.genres:
            genres[g] += 1
            if e.rating is not None:
                g_r[g].append(e.rating)
        if e.director:
            directors[e.director] += 1
            if e.rating is not None:
                d_r[e.director].append(e.rating)
        for a in e.cast:
            actors[a] += 1
            actor_films[a].append(e.title)
            if e.rating is not None:
                a_r[a].append(e.rating)
    solo = [e for e in entries if (e.tmdb_id, e.date) not in together]
    rated = [e.rating for e in entries if e.rating is not None]
    face = _top(actors, a_r, minimum=2)
    face_name = face[0][2] if face else None
    director = _top(directors, d_r)
    return {
        "watches": len(entries),
        "films": len({e.tmdb_id for e in entries}),
        "minutes": sum(e.runtime for e in entries),
        "hours": round(sum(e.runtime for e in entries) / 60),
        "rewatches": sum(e.rewatch for e in entries),
        "avg_rating": _avg(rated),
        "solo": len(solo),
        "solo_hours": round(sum(e.runtime for e in solo) / 60),
        "solo_best": [_film(e) | {"rating": e.rating} for e in
                      sorted((e for e in solo if e.rating is not None),
                             key=lambda e: (-e.rating, e.date))[:3]],
        "top_genres": [{"name": k, "count": n} for n, _, k in _top(genres, g_r)[:4]],
        "top_director": ({"name": director[0][2], "count": director[0][0]}
                         if director else None),
        "face": ({"name": face_name, "count": face[0][0], "avg": _avg(a_r[face_name]),
                  "films": actor_films[face_name]} if face else None),
    }


def _pair_stats(pairs: list[tuple[Entry, Entry]]) -> dict:
    """Everything that needs both people's entry for the same night."""
    rated = [(a, b) for a, b in pairs if a.rating is not None and b.rating is not None]
    deltas = [a.rating - b.rating for a, b in rated]

    clashes = sorted(rated, key=lambda p: (-abs(p[0].rating - p[1].rating), p[0].date))
    clash = None
    if clashes and abs(clashes[0][0].rating - clashes[0][1].rating) >= 1:
        a, b = clashes[0]
        clash = _film(a) | {"a": a.rating, "b": b.rating,
                            "gap": abs(a.rating - b.rating),
                            "runners_up": [p[0].title for p in clashes[1:3]
                                           if abs(p[0].rating - p[1].rating) >= 1]}
        clash["others_within_one"] = all(abs(x.rating - y.rating) <= 1 for x, y in clashes[1:])

    favourites = [_film(a) | {"a": a.rating, "b": b.rating}
                  for a, b in sorted(rated, key=lambda p: (-(p[0].rating + p[1].rating), p[0].date))
                  [:FAVOURITES]]

    # Director and actor of the year: count nights together, rank by the two
    # ratings averaged. A night counts once even if both of you rated it.
    def people(key):
        nights, scores, films = Counter(), defaultdict(list), defaultdict(list)
        for a, b in pairs:
            for name in key(a):
                nights[name] += 1
                films[name].append(_film(a) | {"a": a.rating, "b": b.rating})
                if a.rating is not None and b.rating is not None:
                    scores[name].append((a.rating, b.rating))
        return nights, scores, films

    d_n, d_s, d_f = people(lambda e: [e.director] if e.director else [])
    director = None
    ranked = sorted((n, _avg([(x + y) / 2 for x, y in d_s[k]]) or 0, k)
                    for k, n in d_n.items() if n >= DIRECTOR_MIN_NIGHTS)
    if ranked:
        ranked.sort(key=lambda t: (-t[0], -t[1], t[2]))
        n, avg, name = ranked[0]
        director = {"name": name, "nights": n, "avg": avg, "films": d_f[name]}

    a_n, a_s, a_f = people(lambda e: e.cast)
    actor = None
    for minimum in (ACTOR_MIN_NIGHTS, ACTOR_FALLBACK_NIGHTS):
        cands = [(_avg([(x + y) / 2 for x, y in a_s[k]]),
                  _avg([abs(x - y) for x, y in a_s[k]]), k)
                 for k in a_n if len(a_s[k]) >= minimum]
        if cands:
            cands.sort(key=lambda t: (-t[0], t[1], t[2]))
            avg, gap, name = cands[0]
            actor = {"name": name, "avg": avg, "nights": len(a_s[name]),
                     "min_nights": minimum, "films": a_f[name],
                     "runner_up": cands[1][2] if len(cands) > 1 else None}
            break

    return {
        "rated": len(rated),
        "exact": sum(d == 0 for d in deltas),
        "within_half": sum(abs(d) <= 0.5 for d in deltas),
        "avg_gap": _avg([abs(d) for d in deltas]),
        "a_higher": sum(d > 0 for d in deltas),
        "b_higher": sum(d < 0 for d in deltas),
        "clash": clash,
        "favourites": favourites,
        "director": director,
        "actor": actor,
    }


def venn(a_hours: float, b_hours: float, both_hours: float,
         width: float = 200, height: float = 150) -> dict | None:
    """Two circles with areas to scale and an overlap of ``both_hours``.

    Solves for the centre distance by bisection; the area of a lens is
    monotonic in it. Returns SVG geometry for a ``width``×``height`` box.
    """
    if a_hours <= 0 or b_hours <= 0:
        return None
    ra, rb = math.sqrt(a_hours / math.pi), math.sqrt(b_hours / math.pi)
    both = max(0.0, min(both_hours, a_hours, b_hours))

    def lens(d: float) -> float:
        if d >= ra + rb:
            return 0.0
        if d <= abs(ra - rb):
            return math.pi * min(ra, rb) ** 2
        x = ra * ra * math.acos((d * d + ra * ra - rb * rb) / (2 * d * ra))
        y = rb * rb * math.acos((d * d + rb * rb - ra * ra) / (2 * d * rb))
        z = 0.5 * math.sqrt((-d + ra + rb) * (d + ra - rb) * (d - ra + rb) * (d + ra + rb))
        return x + y - z

    lo, hi = abs(ra - rb), ra + rb
    for _ in range(60):
        mid = (lo + hi) / 2
        if lens(mid) > both:
            lo = mid
        else:
            hi = mid
    d = (lo + hi) / 2
    scale = (height * 0.8 / 2) / max(ra, rb)        # the larger circle fills 80% of the height
    scale = min(scale, width * 0.92 / (ra + d + rb))  # and the pair fits the width
    ra_s, rb_s, d_s = ra * scale, rb * scale, d * scale
    left = (width - (ra_s + d_s + rb_s)) / 2
    ax = left + ra_s
    bx = ax + d_s
    # Label the overlap at the middle of the lens (or of the smaller circle,
    # when one sits inside the other).
    lo_edge, hi_edge = max(ax - ra_s, bx - rb_s), min(ax + ra_s, bx + rb_s)
    return {"w": width, "h": height, "cy": round(height / 2, 1),
            "ax": round(ax, 1), "ar": round(ra_s, 1),
            "bx": round(bx, 1), "br": round(rb_s, 1),
            "label_x": round((lo_edge + hi_edge) / 2, 1)}


def compute(year: int, today: dt.date | None = None) -> dict | None:
    """Every number a Wrapped card shows, or None when the year has no diary."""
    a, b = get_settings().users
    entries, excluded = _drop_catchups(_load(year))
    if not entries:
        return None
    today = today or dt.date.today()

    by_user = {u: [e for e in entries if e.user == u] for u in (a, b)}
    index_b = {(e.tmdb_id, e.date): e for e in by_user[b]}
    pairs = [(e, index_b[(e.tmdb_id, e.date)]) for e in by_user[a]
             if (e.tmdb_id, e.date) in index_b]
    together = {(e.tmdb_id, e.date) for e, _ in pairs}

    months = [0] * 12
    weekdays = [0] * 7
    genres = Counter()
    for e, _ in pairs:
        d = dt.date.fromisoformat(e.date)
        months[d.month - 1] += 1
        weekdays[d.weekday()] += 1
        genres.update(e.genres)
    best_month = max(range(12), key=lambda m: months[m]) if pairs else None
    best_day = max(range(7), key=lambda w: weekdays[w]) if pairs else None
    minutes = sum(e.runtime for e, _ in pairs)

    last = max(e.date for e in entries)
    complete = year < today.year
    people = {u: _person(by_user[u], together) for u in (a, b)}
    hours = {u: people[u]["minutes"] / 60 for u in (a, b)}
    # The best-rated night of the busiest month, for that card's one-liner.
    in_month = [(x, y) for x, y in pairs if best_month is not None
                and int(x.date[5:7]) == best_month + 1
                and x.rating is not None and y.rating is not None]
    month_peak = (_film(max(in_month, key=lambda p: (p[0].rating + p[1].rating))[0])
                  if in_month else None)

    return {
        "year": year,
        "user_a": a, "user_b": b,
        "complete": complete,
        "first_date": min(e.date for e in entries),
        "last_date": last,
        "span": f"Jan 1 – {_short(last)}" if not complete else "the whole year",
        "excluded": excluded,
        "people": people,
        "together": {
            "films": len(pairs),
            "nights": len({e.date for e, _ in pairs}),
            "minutes": minutes,
            "hours": round(minutes / 60),
            "months": months,
            "month_max": max(months) or 1,
            "best_month": calendar.month_name[best_month + 1] if best_month is not None else None,
            "best_month_count": months[best_month] if best_month is not None else 0,
            "best_month_pick": month_peak,
            "months_covered": 12 if complete else int(last[5:7]),
            "weekdays": weekdays,
            "weekday_max": max(weekdays) or 1,
            "best_day": WEEKDAYS[best_day] if best_day is not None else None,
            "best_day_name": calendar.day_name[best_day] if best_day is not None else None,
            "best_day_count": weekdays[best_day] if best_day is not None else 0,
            "genres": [{"name": k, "count": n} for k, n in genres.most_common(4)],
            "first": [_film(e) for e, _ in pairs[:2]],
            "last": [_film(e) for e, _ in pairs[-2:]],
            **_pair_stats(pairs),
        },
        "venn": venn(hours[a], hours[b], minutes / 60),
    }


def _short(iso: str) -> str:
    d = dt.date.fromisoformat(iso)
    return f"{calendar.month_abbr[d.month]} {d.day}"
