"""The Tonight page — what the two of you are most likely to both enjoy.

The pool is every film at least one of you has watchlisted, whether or not it's
on Plex yet; "On Plex now" narrows it to what can play tonight. Each film is
scored per person with ``taste.build_scorer(user)``, and ranked on
``taste.pair_fit()`` of those — or on one person's fit alone when only one of
you is watching.

The combined-taste ``match`` that ``picker`` sets is left untouched: it still
drives the detail card's breakdown and the JSON API.
"""
from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import db, picker, taste
from .config import get_settings
from .picker import Film

SCOPES = ("all", "plex")
SPIN_POOL = 12        # spins draw from the top this-many of the current ranking
SHORT_RUNTIME = 120   # the "Under 2 hrs" chip
FEW = 6               # under this many fits, the grid offers the best filter to drop
NIGHT_ENDS = 6        # "Not tonight" lasts until this hour, local time


@dataclass
class View:
    """What the Tonight page is showing — parsed from its query string."""
    scope: str = "all"                   # 'all' | 'plex'
    watching: list[str] = field(default_factory=list)  # users watching tonight
    genre: str | None = None
    max_runtime: int | None = None
    both_lists: bool = False

    @property
    def who(self) -> str:
        """The `who` query value: 'both', or the one user watching."""
        return "both" if len(self.watching) != 1 else self.watching[0]

    def params(self, **change) -> dict:
        """This view's query parameters, with ``change`` applied; blanks dropped."""
        p = {"scope": self.scope, "who": self.who, "genre": self.genre,
             "runtime": self.max_runtime, "both": 1 if self.both_lists else None}
        p.update(change)
        return {k: v for k, v in p.items()
                if v not in (None, "", 0) and not (k == "scope" and v == "all")
                and not (k == "who" and v == "both")}


def parse(scope: str | None = None, who: str | None = None, genre: str | None = None,
          runtime: str | int | None = None, both: str | None = None,
          *, mode: str | None = None) -> View:
    """Build a View from query values, ignoring anything unrecognised.

    ``mode`` is the old picker's scope ('both' = on both watchlists and on
    Plex, 'one' = on Plex) and still works, so old links and bookmarks land
    somewhere sensible.
    """
    users = get_settings().users
    v = View(scope=scope if scope in SCOPES else "all",
             watching=[who] if who in users else list(users),
             genre=genre or None,
             max_runtime=int(runtime) if runtime and str(runtime).isdigit() else None,
             both_lists=bool(both))
    if mode in ("both", "one"):
        v.scope = "plex"
        v.both_lists = v.both_lists or mode == "both"
    return v


def annotate(films: list[Film], watching: list[str]) -> list[Film]:
    """Set each film's per-user fits, pair score and tonight's score."""
    users = get_settings().users
    scorers = {u: taste.build_scorer(u) for u in users}
    for f in films:
        f.fits = {u: sc.match(f.genres, f.director) for u, sc in scorers.items()}
        both = len(f.wanted_by) == len(users)
        f.pair = taste.pair_fit(*(f.fits[u] for u in users[:2]), both_lists=both)
        f.score = f.pair if len(watching) != 1 else f.fits[watching[0]]
        # Many films reach the 100 ceiling, which swallows the both-lists
        # bonus; ranking ties on the uncapped pair gives it back.
        f.tiebreak = (taste.pair_fit(*(f.fits[u] for u in users[:2]), both_lists=both, cap=False)
                      if len(watching) != 1 else 0)
    return films


def night_start(now: datetime | None = None) -> str:
    """When tonight began — the last ``NIGHT_ENDS`` o'clock, local — in the
    UTC ``YYYY-MM-DD HH:MM:SS`` form SQLite's ``datetime('now')`` stamps, so
    it compares directly with ``last_veto``.
    """
    local = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(get_settings().local_tz))
    start = local.replace(hour=NIGHT_ENDS, minute=0, second=0, microsecond=0)
    if local < start:
        start -= timedelta(days=1)
    return start.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _pool(view: View, films: list[Film] | None = None, *,
          keep_vetoed: bool = False) -> list[Film]:
    """Wanted by someone, unseen by everyone watching, in scope — unfiltered.

    A film you said "Not tonight" to stays out until 6 a.m.; ``keep_vetoed``
    is for lists that aren't about tonight (the get-list). Pass ``films`` (one
    ``picker.load()``) to judge several views off one read.
    """
    users = get_settings().users
    tonight_began = night_start()
    out = [f for f in (picker.load(users) if films is None else films)
           if f.wanted_by and not set(f.seen_by) & set(view.watching)
           and (keep_vetoed or not (f.last_veto and f.last_veto >= tonight_began))
           and (view.scope == "all" or f.on_plex)]
    if view.both_lists:
        out = [f for f in out if len(f.wanted_by) == len(users)]
    return out


def _filtered(films: list[Film], view: View) -> list[Film]:
    return picker._apply_filters(
        films, picker.Filters(max_runtime=view.max_runtime, genre=view.genre))


def ranked(view: View, *, keep_vetoed: bool = False) -> list[Film]:
    """The pool, filtered and best-first for whoever's watching."""
    films = annotate(_filtered(_pool(view, keep_vetoed=keep_vetoed), view), view.watching)
    films.sort(key=lambda f: (f.score or 0, f.tiebreak, f.year or 0), reverse=True)
    return films


@dataclass
class Loosen:
    """One filter dropped: what the button says, and where it leads."""
    label: str
    count: int      # films the view shows with this filter dropped
    params: dict    # that view's query string


def loosen(view: View) -> tuple[int, list[Loosen]]:
    """How many films fit now, and each active filter that — dropped alone —
    would bring back more, biggest first.

    "On Plex now" counts as a filter here: leaving it is often the one way out
    that isn't a chip. Every view is judged off a single ``picker.load()``.
    """
    films = picker.load(get_settings().users)

    def fits(v: View) -> int:
        return len(_filtered(_pool(v, films), v))

    runtime = ("Under 2 hrs" if view.max_runtime == SHORT_RUNTIME
               else f"Under {view.max_runtime} min")
    drops = [(view.genre, f"Drop {view.genre}", replace(view, genre=None)),
             (view.max_runtime, f"Drop {runtime}", replace(view, max_runtime=None)),
             (view.both_lists, "Drop On both lists", replace(view, both_lists=False)),
             (view.scope == "plex", "Look beyond Plex", replace(view, scope="all"))]
    now = fits(view)
    out = [Loosen(label, n, v.params())
           for active, label, v in drops if active and (n := fits(v)) > now]
    out.sort(key=lambda o: o.count, reverse=True)  # stable: ties keep chip order
    return now, out


def genre_options(view: View, limit: int = 5) -> list[str]:
    """The genres most common in the current pool — the filter chips.

    A selected genre always stays in the list, so it can be switched off.
    """
    counts: Counter = Counter()
    for f in _pool(view):
        counts.update(g.strip() for g in (f.genres or "").split(",") if g.strip())
    top = [g for g, _ in counts.most_common(limit)]
    if view.genre and view.genre not in top:
        top.append(view.genre)
    return top


def spin(view: View, exclude: int | None = None) -> Film | None:
    """A weighted-random pick from the top of the ranking.

    Better scores weigh more; vetoes weigh less (never zero — a veto is "not
    tonight", not "never"). ``exclude`` keeps the current pick from coming
    straight back.
    """
    pool = [f for f in ranked(view) if f.tmdb_id != exclude][:SPIN_POOL]
    if not pool:
        return None
    weights = [max(f.score or 0, 1) / (1.0 + f.vetoes) for f in pool]
    return random.choices(pool, weights=weights, k=1)[0]


def by_id(tmdb_id: int, view: View) -> Film | None:
    """One film annotated for this view — the hero after a spin, or the card."""
    film = picker.by_id(tmdb_id)
    return annotate([film], view.watching)[0] if film else None


def explanations(film: Film) -> dict[str, taste.Explanation]:
    """Each user's own breakdown of the film — their fit, cited from their ratings."""
    return {u: taste.build_scorer(u).explain(film.genres, film.director, tmdb_id=film.tmdb_id)
            for u in get_settings().users}


def reason(ex: taste.Explanation) -> str:
    """The one line from a breakdown worth leading with.

    The director's line when they've landed before (the most specific claim
    there is), else the strongest genre's.
    """
    if not ex.has_signal:
        return "Nothing in their history points either way — a genuine unknown."
    if ex.director is not None and ex.director.affinity > 0:
        return ex.notes[-1]
    return ex.notes[0]


def people() -> list[dict]:
    """The two users as the templates draw them: name, initial, colour slot."""
    return [{"key": u, "name": u.capitalize(), "initial": u[:1].upper(), "cls": cls}
            for u, cls in zip(get_settings().users, ("a", "b"), strict=False)]


def on_plex_shelf(view: View, limit: int = 6, exclude: int | None = None) -> list[Film]:
    """The best few that can play right now — shown above the full ranking."""
    plex = View(scope="plex", watching=view.watching, genre=view.genre,
                max_runtime=view.max_runtime, both_lists=view.both_lists)
    return [f for f in ranked(plex) if f.tmdb_id != exclude][:limit]


def get_list(view: View) -> list[Film]:
    """Films you've marked to get, still not on Plex, best-first."""
    return [f for f in ranked(View(watching=view.watching), keep_vetoed=True)
            if f.on_get_list and not f.on_plex]


def worth_getting(view: View, limit: int = 30) -> list[Film]:
    """Wanted, not on Plex, not already on the get-list — best-first."""
    return [f for f in ranked(view, keep_vetoed=True)
            if not f.on_plex and not f.on_get_list][:limit]


def counts(view: View) -> dict:
    """Pool sizes for the scope switch: everything wanted, and the on-Plex part."""
    pool = _pool(View(watching=view.watching))
    return {"all": len(pool), "plex": sum(f.on_plex for f in pool)}


def sync_status(now: datetime | None = None) -> list[tuple[str, str]]:
    """(label, "3 h ago") for each sync that has ever stamped app_meta."""
    labels = {"plex_synced_at": "Plex", "letterboxd_rss_at": "Letterboxd diary",
              "letterboxd_watchlist_at": "watchlists"}
    with db.cursor() as cur:
        rows = dict(cur.execute("SELECT key, value FROM app_meta").fetchall())
    now = now or datetime.now(timezone.utc)
    out = []
    for key, label in labels.items():
        if rows.get(key):
            then = datetime.fromisoformat(rows[key]).replace(tzinfo=timezone.utc)
            out.append((label, _ago(now - then)))
    return out


def _ago(delta) -> str:
    mins = int(delta.total_seconds() // 60)
    if mins < 1:
        return "just now"
    if mins < 60:
        return f"{mins} min ago"
    if mins < 48 * 60:
        return f"{mins // 60} h ago"
    return f"{mins // (24 * 60)} days ago"
