"""External scores for one film — TMDb, IMDb, Rotten Tomatoes, Metacritic, Letterboxd.

Only the film detail card uses this, so lookups are on demand and cached in
``external_ratings`` (this module owns that table outright, hence the SQL living
here rather than in ``db``). Nothing here is on the picker's hot path.

Sources, and why:
  * **TMDb** — already have a key; gives its own average *and* the IMDb id the
    other lookups need.
  * **OMDb** (``omdbapi.com``) — the only practical way to get a Rotten Tomatoes
    tomatometer without a commercial RT licence. Free key, 1000 calls/day, keyed
    by IMDb id. Blank key = those three scores are simply absent.
  * **Letterboxd** — no public API, so the weighted average is read from the
    JSON-LD block on the public film page. ``letterboxd.com/tmdb/<id>/``
    redirects to the right film, so no slug guessing.

Every source is best-effort: one failing leaves its fields ``None`` and the rest
still render. A fetch that returns nothing at all is cached only briefly, so a
transient outage doesn't blank the card for a week.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass

import httpx

from . import db
from .config import get_settings
from .tmdb import TMDbClient

log = logging.getLogger(__name__)

OMDB_ROOT = "https://www.omdbapi.com/"
LETTERBOXD_TMDB = "https://letterboxd.com/tmdb/{tmdb_id}/"

# Letterboxd blocks obvious bots; identify the app honestly instead.
USER_AGENT = "movienight/1.0 (self-hosted; +https://github.com/tydude001/movienight)"
HTTP_TIMEOUT = 8.0

# A fetch where every source came back empty is retried after this instead of
# waiting out the full TTL — it usually means a transient failure, not a film
# nobody has scored.
EMPTY_RETRY_HOURS = 1.0

_AGG_RATING = re.compile(r'"aggregateRating"\s*:\s*(\{.*?\})', re.DOTALL)


@dataclass
class ExternalRatings:
    """Whatever we managed to find. Any field may be ``None``."""
    tmdb_id: int
    imdb_id: str | None = None
    tmdb_score: float | None = None      # 0–10
    tmdb_votes: int | None = None
    imdb_score: float | None = None      # 0–10
    rt_score: int | None = None          # tomatometer, 0–100
    metacritic: int | None = None        # 0–100
    lb_score: float | None = None        # 0–5
    lb_votes: int | None = None
    lb_url: str | None = None
    age_hours: float | None = None       # None when never cached

    @property
    def any_scores(self) -> bool:
        return any(v is not None for v in
                   (self.tmdb_score, self.imdb_score, self.rt_score,
                    self.metacritic, self.lb_score))

    def as_dict(self) -> dict:
        return asdict(self)


# ── cache ───────────────────────────────────────────────────────────

def _read_cache(tmdb_id: int) -> ExternalRatings | None:
    with db.cursor() as cur:
        row = cur.execute(
            """
            SELECT *, (julianday('now') - julianday(fetched_at)) * 24.0 AS age_hours
            FROM external_ratings WHERE tmdb_id = ?
            """,
            (tmdb_id,),
        ).fetchone()
    if row is None:
        return None
    keep = {f for f in ExternalRatings.__dataclass_fields__ if f != "tmdb_id"}
    return ExternalRatings(tmdb_id=tmdb_id,
                           **{k: row[k] for k in row.keys() if k in keep})


def _write_cache(r: ExternalRatings) -> None:
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO external_ratings
                (tmdb_id, imdb_id, tmdb_score, tmdb_votes, imdb_score,
                 rt_score, metacritic, lb_score, lb_votes, lb_url, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(tmdb_id) DO UPDATE SET
                imdb_id = excluded.imdb_id,
                tmdb_score = excluded.tmdb_score, tmdb_votes = excluded.tmdb_votes,
                imdb_score = excluded.imdb_score, rt_score = excluded.rt_score,
                metacritic = excluded.metacritic,
                lb_score = excluded.lb_score, lb_votes = excluded.lb_votes,
                lb_url = excluded.lb_url, fetched_at = datetime('now')
            """,
            (r.tmdb_id, r.imdb_id, r.tmdb_score, r.tmdb_votes, r.imdb_score,
             r.rt_score, r.metacritic, r.lb_score, r.lb_votes, r.lb_url),
        )


def _is_fresh(cached: ExternalRatings) -> bool:
    age = cached.age_hours
    if age is None:
        return False
    ttl = get_settings().ratings_ttl_hours if cached.any_scores else EMPTY_RETRY_HOURS
    return age < ttl


def cached(tmdb_id: int) -> ExternalRatings | None:
    """Fresh cached scores, or None when a network fetch is needed.

    Lets a caller render instantly and defer the fetch to a second request.
    """
    row = _read_cache(tmdb_id)
    return row if row is not None and _is_fresh(row) else None


def get(tmdb_id: int, *, client: httpx.Client | None = None) -> ExternalRatings:
    """Scores for a film, from cache when fresh, else fetched and cached."""
    row = _read_cache(tmdb_id)
    if row is not None and _is_fresh(row):
        return row
    own = client is None
    http = client or httpx.Client(timeout=HTTP_TIMEOUT,
                                  headers={"User-Agent": USER_AGENT},
                                  follow_redirects=True)
    try:
        # An IMDb id never changes, so a stale row still carries a usable one —
        # which keeps Rotten Tomatoes alive on a refetch where TMDb is the thing
        # that's down.
        fresh = fetch(tmdb_id, http,
                      known_imdb_id=row.imdb_id if row is not None else None)
    finally:
        if own:
            http.close()
    _write_cache(fresh)
    fresh.age_hours = 0.0
    return fresh


# ── fetching ────────────────────────────────────────────────────────

def fetch(tmdb_id: int, client: httpx.Client, *,
          known_imdb_id: str | None = None) -> ExternalRatings:
    """Hit every configured source. Failures degrade to ``None``, never raise.

    ``known_imdb_id`` seeds the OMDb lookup from a previous fetch, so TMDb being
    unreachable doesn't take Rotten Tomatoes down with it.
    """
    s = get_settings()
    out = ExternalRatings(tmdb_id=tmdb_id, imdb_id=known_imdb_id)
    _tmdb_scores(out, s.tmdb_api_key, client)
    if s.omdb_api_key and out.imdb_id:
        _omdb_scores(out, s.omdb_api_key, client)
    if s.letterboxd_scores:
        _letterboxd_scores(out, client)
    return out


def _tmdb_scores(out: ExternalRatings, api_key: str, client: httpx.Client) -> None:
    """TMDb's own average, plus the IMDb id that OMDb is keyed on."""
    try:
        data = TMDbClient(api_key, client=client).scores(out.tmdb_id)
        out.tmdb_score = _as_float(data["vote_average"]) or None
        out.tmdb_votes = _as_int(data["vote_count"])
        # Never clobber a known id with a blank one.
        out.imdb_id = data["imdb_id"] or out.imdb_id
    except Exception as e:  # noqa: BLE001 — a missing score is not an error
        log.warning("TMDb scores failed for %s: %s", out.tmdb_id, e)


def _omdb_scores(out: ExternalRatings, api_key: str, client: httpx.Client) -> None:
    """Rotten Tomatoes + IMDb + Metacritic, keyed by IMDb id."""
    try:
        resp = client.get(OMDB_ROOT, params={"apikey": api_key, "i": out.imdb_id})
        resp.raise_for_status()
        data = resp.json()
        if data.get("Response") == "False":
            log.info("OMDb has nothing for %s: %s", out.imdb_id, data.get("Error"))
            return
        out.imdb_score = _as_float(data.get("imdbRating"))
        out.metacritic = _as_int(data.get("Metascore"))
        for entry in data.get("Ratings") or []:
            if entry.get("Source") == "Rotten Tomatoes":
                out.rt_score = _as_int((entry.get("Value") or "").rstrip("%"))
    except Exception as e:  # noqa: BLE001
        log.warning("OMDb lookup failed for %s: %s", out.imdb_id, e)


def _letterboxd_scores(out: ExternalRatings, client: httpx.Client) -> None:
    """Weighted average off the public film page's JSON-LD block.

    ``/tmdb/<id>/`` redirects to the canonical film page, so the final URL is
    also the link we show.
    """
    try:
        resp = client.get(LETTERBOXD_TMDB.format(tmdb_id=out.tmdb_id),
                          headers={"User-Agent": USER_AGENT},
                          follow_redirects=True)
        resp.raise_for_status()
        out.lb_url = str(resp.url)
        agg = _AGG_RATING.search(resp.text)
        if agg is None:
            return  # film exists but nobody has rated it yet
        data = json.loads(agg.group(1))
        out.lb_score = _as_float(data.get("ratingValue"))
        out.lb_votes = _as_int(data.get("ratingCount"))
    except Exception as e:  # noqa: BLE001
        log.warning("Letterboxd lookup failed for %s: %s", out.tmdb_id, e)


def _as_float(val) -> float | None:
    try:
        return round(float(val), 2)
    except (TypeError, ValueError):
        return None


def _as_int(val) -> int | None:
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return None
