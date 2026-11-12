"""Shared taste scoring.

How well a film fits the couple's *combined* taste, as a 0–100 match plus a
one-line reason. Built once from both users' watched history (films rated 4★+
count double), then applied to any film by genre + director.

Used by both the dashboard's "worth adding" feed and the picker's match badges,
so the percentage and reason are computed identically wherever they appear.

``Scorer.explain()`` unpacks the same number for the film detail card: which
genres earned which share of the score, and the films in your history that put
them there. It reads the score rather than recomputing it, so a card can never
disagree with the badge above it.

``build_scorer(user)`` builds the same model from one person's history alone,
and ``pair_fit()`` folds two of those into the number the Tonight page ranks
on: "how likely are we *both* to enjoy this", which a blended taste can't say —
a film one of you would love and the other would hate averages out to "fine".
"""
from __future__ import annotations

import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from . import db

GENRE_WEIGHT = 0.7
DIRECTOR_WEIGHT = 0.3
LOVE_THRESHOLD = 4.0     # rated this high -> counts a second time
DISLIKE_THRESHOLD = 2.0  # rated this low  -> subtracts one

# How many example films to retain per genre / director. The score uses the
# whole history; these are only the ones a card may quote. Kept deep enough
# that de-duplicating across a card's lines never runs out of material.
EXAMPLES_KEPT = 24
# ...of which the top slice is rotated per film, so two cards sharing a genre
# don't quote the same three favourites. Small enough that everything in it is
# still something you rated highly.
CITE_POOL = 8

# The taste data only changes when a sync/import runs (possibly in another
# container), so a short TTL is the right invalidation: fresh enough, and one
# rebuild per window instead of one per request.
SCORER_TTL_SECONDS = 60.0

# A film on both watchlists has already been chosen by both of you — that's
# evidence the taste model can't see, so it earns a flat bump on the pair score.
BOTH_LISTS_BONUS = 8


def _split(genres: str | None) -> list[str]:
    return [g.strip() for g in (genres or "").split(",") if g.strip()]


# ── evidence: the films behind a score ──────────────────────────────

@dataclass
class Seen:
    """A film in your combined history, with the better of your two ratings."""
    tmdb_id: int
    title: str
    year: int | None
    rating: float | None  # None = watched but unrated by either of you
    genre_count: int = 1  # how many genres it spans; fewer = a purer example


@dataclass
class History:
    """What you've logged in one genre, or from one director."""
    count: int = 0                              # films logged
    rated_count: int = 0
    best: list[Seen] = field(default_factory=list)   # highest rated first
    worst: list[Seen] = field(default_factory=list)  # lowest rated first

    @property
    def loved(self) -> list[Seen]:
        return [s for s in self.best if s.rating is not None and s.rating >= LOVE_THRESHOLD]

    @property
    def panned(self) -> list[Seen]:
        return [s for s in self.worst
                if s.rating is not None and s.rating <= DISLIKE_THRESHOLD]


@dataclass
class Fit:
    """One genre's or one director's share of a film's match score."""
    label: str
    affinity: int      # raw tally from your history; can be negative
    share: float       # affinity relative to your strongest, -1.0…1.0
    points: float      # of the 100 available, how many this contributed
    history: History = field(default_factory=History)


@dataclass
class Explanation:
    """The long-form 'why this is a match', for the film detail card."""
    match: int
    genre_points: float
    director_points: float
    genres: list[Fit]           # every genre on the film, strongest first
    director: Fit | None        # None when the film has no known director
    headline: str
    notes: list[str]

    @property
    def has_signal(self) -> bool:
        """False when nothing in your history speaks to this film either way."""
        return any(f.affinity for f in self.genres) or bool(
            self.director and self.director.affinity)


@dataclass
class Scorer:
    """Combined-taste model. Build once per request via ``build_scorer()``."""
    genres: Counter
    directors: Counter
    g_max: int
    d_max: int
    genre_history: dict[str, History] = field(default_factory=dict)
    director_history: dict[str, History] = field(default_factory=dict)

    def match(self, genres: str | None, director: str | None) -> int:
        """0–100 fit score for a film's genres + director.

        Dislike penalties can push raw affinity negative; clamp at 0.
        """
        fg = _split(genres)
        g_aff = (sum(self.genres.get(g, 0) for g in fg)
                 / (len(fg) * self.g_max)) if fg else 0.0
        d_aff = (self.directors.get(director, 0) / self.d_max) if director else 0.0
        return max(0, round(100 * (GENRE_WEIGHT * g_aff + DIRECTOR_WEIGHT * d_aff)))

    def why(self, genres: str | None, director: str | None) -> str:
        """Human reason, or '' when there's no taste signal (caller supplies a fallback)."""
        fg = _split(genres)
        fav = sorted((g for g in fg if self.genres.get(g, 0) > 0),
                     key=lambda g: self.genres[g], reverse=True)[:2]
        likes_dir = bool(director) and self.directors.get(director, 0) > 0
        if fav and likes_dir:
            return f"You both lean {' & '.join(fav)}, and have enjoyed {director}."
        if fav:
            return f"You both lean {' & '.join(fav)}."
        if likes_dir:
            return f"You've enjoyed {director} before."
        return ""

    def explain(self, genres: str | None, director: str | None,
                *, tmdb_id: int | None = None) -> Explanation:
        """The long version of ``match()`` — where the score came from.

        The percentage is taken from ``match()`` rather than recomputed, so the
        breakdown can never contradict the badge. Per-genre points are the
        unrounded terms of that same sum, which is why they can end up a
        fraction off the total once rounded for display.

        Pass the film's own ``tmdb_id`` when it might be in your history (you've
        seen it, and are looking it up again) so it isn't cited as evidence for
        itself. It still counts toward the tallies — it *is* part of your taste.
        """
        fg = _split(genres)
        fits = []
        for g in fg:
            aff = self.genres.get(g, 0)
            fits.append(Fit(
                label=g, affinity=aff, share=aff / self.g_max,
                points=100 * GENRE_WEIGHT * aff / (len(fg) * self.g_max),
                history=self.genre_history.get(g, History()),
            ))
        fits.sort(key=lambda f: f.affinity, reverse=True)

        dfit = None
        if director:
            aff = self.directors.get(director, 0)
            dfit = Fit(
                label=director, affinity=aff, share=aff / self.d_max,
                points=100 * DIRECTOR_WEIGHT * aff / self.d_max,
                history=self.director_history.get(director, History()),
            )

        return Explanation(
            match=self.match(genres, director),
            genre_points=round(sum(f.points for f in fits), 1),
            director_points=round(dfit.points, 1) if dfit else 0.0,
            genres=fits, director=dfit,
            headline=_headline(self.match(genres, director), fits, dfit),
            notes=_notes(fits, dfit, film_id=tmdb_id),
        )


# ── prose for the detail card ───────────────────────────────────────

def _stars(rating: float) -> str:
    return f"{rating:g}★"


def _rotate(films: list[Seen], seed: int) -> list[Seen]:
    """Rotate the top slice so different films lead with different examples.

    Deterministic in the film's id: a card quotes the same examples every time
    it's opened, but the card next to it quotes different ones.
    """
    head = films[:CITE_POOL]
    if len(head) < 2 or not seed:
        return films
    k = seed % len(head)
    return head[k:] + head[:k] + films[CITE_POOL:]


def _pick(films: list[Seen], limit: int = 3, *, exclude: int | None = None,
          used: set[int] | None = None, seed: int = 0) -> list[Seen]:
    """Choose which films a line quotes, in the order they should read.

    ``used`` collects what the rest of the card has already quoted; anything in
    it sinks to the back rather than being dropped, so a line always gets its
    evidence — repeating a title beats making a vaguer claim.
    """
    pool = _rotate([s for s in films if s.tmdb_id != exclude], seed)
    if used is not None:
        pool = ([s for s in pool if s.tmdb_id not in used]
                + [s for s in pool if s.tmdb_id in used])
    keep = pool[:limit]
    if used is not None:
        used.update(s.tmdb_id for s in keep)
    # Rotation and de-duplication choose *which* films; the reader still expects
    # the strongest first, so restore rating order for display.
    keep.sort(key=lambda s: (-(s.rating or 0), s.genre_count, s.title))
    return keep


def _cite(films: list[Seen]) -> str:
    """'Whiplash (5★), Parasite (4.5★)' — the receipts, already chosen."""
    return ", ".join(
        f"{s.title}{f' ({_stars(s.rating)})' if s.rating is not None else ''}"
        for s in films
    )


def _top_rating(films: list[Seen]) -> float | None:
    """The strongest rating among the films actually being quoted."""
    rated = [s.rating for s in films if s.rating is not None]
    return max(rated) if rated else None


def _evidence(films: list[Seen], film_id: int | None,
              used: set[int] | None = None) -> tuple[str, bool]:
    """Citation text, and whether the only thing to cite was the film itself.

    That second case needs its own wording: saying "none has hit 4★ yet" about a
    genre whose one 5★ entry is the film on screen would be a flat lie. Note it
    keys on ``film_id`` alone — ``used`` only reorders, never empties, so
    de-duplication can't push a line into the wrong sentence.
    """
    cited = _cite(_pick(films, exclude=film_id, used=used, seed=film_id or 0))
    return cited, (not cited and bool(films))


def _headline(match: int, fits: list[Fit], dfit: Fit | None) -> str:
    """One line setting expectations, before the breakdown."""
    top = fits[0] if fits and fits[0].affinity > 0 else None
    dir_known = dfit is not None and dfit.affinity > 0
    if match >= 75:
        lead = "Right in your wheelhouse"
    elif match >= 50:
        lead = "A solid fit"
    elif match >= 25:
        lead = "A bit of a stretch"
    elif match > 0:
        lead = "Off your usual path"
    else:
        lead = "Nothing in your history backs this one"
    if top and dir_known:
        return f"{lead} — {top.label} is your strongest suit, and {dfit.label} has landed before."
    if top:
        return f"{lead}, mostly on the strength of your {top.label} history."
    if dir_known:
        return f"{lead} — you've got form with {dfit.label}."
    return f"{lead}."


def _notes(fits: list[Fit], dfit: Fit | None, *, film_id: int | None = None) -> list[str]:
    """A sentence per genre, then the director — each with films to back it.

    ``film_id`` does double duty: it keeps a film you've already seen from being
    cited as evidence for itself, and seeds the per-film rotation of examples.

    The director's line is built *first* even though it prints last. Its claim
    is the least substitutable — there may be only one film of theirs to point
    at — so it gets first pick, and the genre lines steer around it.
    """
    used: set[int] = set()
    director_note = _director_note(dfit, film_id, used) if dfit is not None else None

    notes = []
    for f in fits:
        h = f.history
        if f.affinity > 0:
            loved, only_this = _evidence(h.loved, film_id, used)
            if loved:
                tail = f", including {loved}."
            elif only_this:
                tail = " — this one among them."
            else:
                tail = ", though none has hit 4★ yet."
            notes.append(f"{f.label}: {h.count} in your history{tail}")
        elif f.affinity < 0:
            panned, only_this = _evidence(h.panned, film_id, used)
            if panned:
                tail = f" ({panned})."
            elif only_this:
                tail = " — this one included."
            else:
                tail = "."
            notes.append(f"{f.label}: the ones you've tried didn't land{tail}")
        elif h.count:
            notes.append(f"{f.label}: {h.count} logged, but the highs and lows cancel out.")
        else:
            notes.append(f"{f.label}: no track record either way — this would be a first.")

    if director_note:
        notes.append(director_note)
    return notes


def _director_note(dfit: Fit, film_id: int | None, used: set[int]) -> str:
    """The director sentence, and the first claim on the card's citations.

    Unlike a genre line — which only quotes films you rated 4★+ — this one
    quotes whatever exists, because a director may have just one credit in your
    history. So the wording has to follow the evidence: "you've enjoyed" is a
    claim three-star ratings don't support.
    """
    h = dfit.history
    if dfit.affinity > 0:
        # ``best`` only ever holds rated films, so an empty pick means the one
        # rated credit was this film itself.
        chosen = _pick(h.best, exclude=film_id, used=used, seed=film_id or 0)
        cited, top = _cite(chosen), _top_rating(chosen)
        if top is None:
            if h.best:
                return f"Directed by {dfit.label} — this is the only one of theirs you've logged."
            return f"Directed by {dfit.label} — {h.count} of theirs logged, none of them rated."
        if top >= LOVE_THRESHOLD:
            return f"Directed by {dfit.label}, and you've enjoyed {cited}."
        if top > DISLIKE_THRESHOLD:
            return f"Directed by {dfit.label}, though they've only ever been fine by you ({cited})."
        return f"Directed by {dfit.label}, and nothing of theirs has landed yet ({cited})."
    if dfit.affinity < 0:
        panned, only_this = _evidence(h.panned, film_id, used)
        if panned:
            return f"{dfit.label} hasn't worked for you before ({panned})."
        if only_this:
            return f"{dfit.label} hasn't worked for you before — this film is the evidence."
        return f"{dfit.label} hasn't worked for you before."
    if h.count:
        return (f"{dfit.label} nets out neutral for you — {h.count} logged, "
                "the highs and lows cancelling.")
    return f"Neither of you has logged a {dfit.label} film before."


# (built_at, scorer) per user; the key None is the combined couple's model.
# Tests reset it to None, which reads as empty.
_cache: dict[str | None, tuple[float, Scorer]] | None = None


def build_scorer(user: str | None = None) -> Scorer:
    """Cached Scorer (TTL ``SCORER_TTL_SECONDS``) — both users' demonstrated
    taste by default, or one user's alone when ``user`` is given.

    A stale-check race between threads just rebuilds twice — harmless, so no lock.
    """
    global _cache
    if _cache is None:
        _cache = {}
    hit = _cache.get(user)
    if hit is not None and time.monotonic() - hit[0] < SCORER_TTL_SECONDS:
        return hit[1]
    scorer = _build_scorer(user)
    _cache[user] = (time.monotonic(), scorer)
    return scorer


def pair_fit(fit_a: int, fit_b: int, *, both_lists: bool = False, cap: bool = True) -> int:
    """0–100: how likely a film lands for *both* of you, from your two fits.

    The harmonic mean leans toward whoever's less keen — 90 and 30 gives 45,
    not the 60 an average would — because a pick that one of you sits through
    isn't a good pick. Being on both watchlists adds ``BOTH_LISTS_BONUS``.
    ``cap=False`` keeps what the 100 ceiling would cut off, for breaking ties.
    """
    hm = 2 * fit_a * fit_b / (fit_a + fit_b) if fit_a + fit_b else 0.0
    raw = round(hm) + (BOTH_LISTS_BONUS if both_lists else 0)
    return min(100, raw) if cap else raw


def _build_scorer(user: str | None = None) -> Scorer:
    """Construct a Scorer from both users' demonstrated taste, or one user's.

    Genres/directors are tallied from every film either user has watched, with
    films rated 4★+ counted a second time as a stronger "we love this" signal,
    and films rated 2★ or lower subtracting once ("we tried this and didn't
    like it"). A film loved by one and hated by the other nets out to +1.

    A fourth pass collects the films behind each tally so ``explain()`` can name
    them. It's evidence only — it never feeds the score.

    With ``user`` set, every pass reads that person's rows only, so the
    citations are their own ratings too.
    """
    # The same four queries either way; one user just narrows each of them.
    and_wd = " AND wd.user = ?" if user else ""
    and_r = " AND r.user = ?" if user else ""
    where_user = " WHERE user = ?" if user else ""
    args: tuple = (user,) if user else ()
    genres: Counter = Counter()
    directors: Counter = Counter()
    with db.cursor() as cur:
        cur.execute(
            f"""
            SELECT DISTINCT f.tmdb_id, f.genres, f.director
            FROM films f JOIN watched wd ON wd.tmdb_id = f.tmdb_id{and_wd}
            """,
            args,
        )
        watched_rows = cur.fetchall()
        cur.execute(
            f"""
            SELECT DISTINCT f.tmdb_id, f.genres, f.director
            FROM films f JOIN ratings r ON r.tmdb_id = f.tmdb_id{and_r}
            WHERE r.rating >= ?
            """,
            (*args, LOVE_THRESHOLD),
        )
        loved_rows = cur.fetchall()
        cur.execute(
            f"""
            SELECT DISTINCT f.tmdb_id, f.genres, f.director
            FROM films f JOIN ratings r ON r.tmdb_id = f.tmdb_id{and_r}
            WHERE r.rating IS NOT NULL AND r.rating <= ?
            """,
            (*args, DISLIKE_THRESHOLD),
        )
        disliked_rows = cur.fetchall()
        # Evidence: every film either of you has logged, carrying the better of
        # your two ratings (MAX ignores NULLs, so a film one of you rated and
        # the other only watched keeps the rating). One user: their own rating.
        cur.execute(
            f"""
            SELECT f.tmdb_id, f.title, f.year, f.genres, f.director,
                   MAX(r.rating) AS best
            FROM films f
            LEFT JOIN ratings r ON r.tmdb_id = f.tmdb_id{and_r}
            WHERE f.tmdb_id IN (SELECT tmdb_id FROM watched{where_user}
                                UNION SELECT tmdb_id FROM ratings{where_user})
            GROUP BY f.tmdb_id
            """,
            args * 3,
        )
        history_rows = cur.fetchall()
    for rows, sign in ((watched_rows, 1), (loved_rows, 1), (disliked_rows, -1)):
        for r in rows:
            if r["genres"]:
                for g in (g.strip() for g in r["genres"].split(",") if g.strip()):
                    genres[g] += sign
            if r["director"]:
                directors[r["director"]] += sign

    by_genre: dict[str, list[Seen]] = defaultdict(list)
    by_director: dict[str, list[Seen]] = defaultdict(list)
    for r in history_rows:
        gs = _split(r["genres"])
        seen = Seen(tmdb_id=r["tmdb_id"], title=r["title"], year=r["year"],
                    rating=r["best"], genre_count=len(gs) or 1)
        for g in gs:
            by_genre[g].append(seen)
        if r["director"]:
            by_director[r["director"]].append(seen)

    return Scorer(genres, directors,
                  max(max(genres.values(), default=1), 1),
                  max(max(directors.values(), default=1), 1),
                  genre_history={g: _history(v) for g, v in by_genre.items()},
                  director_history={d: _history(v) for d, v in by_director.items()})


def _history(films: list[Seen]) -> History:
    """Roll a genre's / director's logged films into a citable summary.

    Ties on rating are broken by how few genres a film spans, so the Comedy line
    reaches for an actual comedy rather than the same beloved five-genre dramedy
    every other line is also reaching for.
    """
    rated = [f for f in films if f.rating is not None]
    best = sorted(rated, key=lambda s: (-s.rating, s.genre_count, s.title))
    worst = sorted(rated, key=lambda s: (s.rating, s.genre_count, s.title))
    return History(
        count=len(films), rated_count=len(rated),
        best=best[:EXAMPLES_KEPT],
        worst=worst[:EXAMPLES_KEPT],
    )
