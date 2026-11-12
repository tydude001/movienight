"""FastAPI app — server-rendered (Jinja + HTMX) with a few JSON endpoints.

Routes:
  GET  /                      Tonight: best bet, on-Plex shelf, full ranking
  GET  /picker                HTMX partial: the ranked grid for a view
  POST /spin                  HTMX partial: a new best bet (weighted random)
  POST /skip                  veto the best bet and spin again
  POST /shortlist             HTMX partial: no-veto shortlist of N
  POST /watched               mark tonight's pick watched for both users
  POST /veto                  record a shortlist veto (down-weights spins)
  GET  /get                   the get-list: films to get onto Plex
  POST /get/{id}              HTMX partial: add/remove a film on the get-list
  GET  /film/{id}             HTMX partial: detail card (scores + why it matches)
  GET  /film/{id}/scores      HTMX partial: external scores, fetched on demand
  GET  /dashboard             taste dashboard
  GET  /wrapped[/{year}]      the year in story cards, from both diaries
  GET  /admin                 ingestion controls + unmatched review   (HTTP Basic, app/auth.py)
  POST /admin/sync-plex       start sync_plex() in the background      (HTTP Basic)
  POST /admin/import          start a Letterboxd ZIP import            (HTTP Basic)
  POST /admin/enrich          start enrich_tmdb()                      (HTTP Basic)
  GET  /admin/job             the running/last job's status, polled    (HTTP Basic)
  POST /admin/resolve         pin a manual TMDb id for an unmatched row (HTTP Basic)
  GET  /api/*                 JSON equivalents (for a future SPA / Project 1)
"""
from __future__ import annotations

import hashlib
import logging
import mimetypes
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import date
from pathlib import Path

import requests
from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import (
    dashboard,
    db,
    enrich,
    jobs,
    letterboxd,
    logs,
    picker,
    plex_sync,
    ratings,
    taste,
    tonight,
    wrapped,
)
from .auth import require_admin
from .config import get_settings
from .tmdb import TMDbClient, normalise_key, poster_size

BASE = Path(__file__).parent


def _is_asset(path: str) -> bool:
    """Paths whose request log would say nothing (see `request_log`)."""
    return path.startswith("/static/") or path == "/favicon.ico"


log = logging.getLogger("app.main")

# At import, not in the lifespan: uvicorn has already logged "Started server
# process" and "Waiting for application startup" by the time a lifespan runs,
# and those two lines came out unformatted while every line after them was
# JSON. Importing this module is the earliest hook the app has.
logs.configure(get_settings().log_level, get_settings().log_format)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    s = get_settings()
    # Re-read here as well as at import: a test (or an embedder) that changes
    # the environment between the two gets the settings it asked for.
    logs.configure(s.log_level, s.log_format)
    db.init_db()
    log.info("startup", extra={"database": str(s.db_path), "users": s.users})
    yield


app = FastAPI(title="Movie Night", lifespan=_lifespan)


@app.middleware("http")
async def request_log(request: Request, call_next):
    """One line per request, carrying the `request_id` every other line is tied by.

    The id is taken from an inbound `X-Request-ID` when there is one — nothing
    sits in front of this app today, but a reverse proxy added later would set
    one, and honouring it costs nothing — and echoed on the response so a
    browser's network tab and the log name the same string.

    A request that raises logs at `exception` and re-raises: the point of the
    line is that a 500 leaves something behind. `/static/*` is skipped, because
    one page load fans out to a dozen of them and none of them says anything,
    and `/favicon.ico` joins it for the same reason — a browser asks for it
    unprompted, so logging the hit only trades 404 noise for 200 noise.
    """
    rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    token = logs.request_id.set(rid)
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        log.exception("request failed", extra={
            "method": request.method, "path": request.url.path,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1)})
        raise
    else:
        if not _is_asset(request.url.path):
            log.info("request", extra={
                "method": request.method, "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1)})
        response.headers["X-Request-ID"] = rid
        return response
    finally:
        logs.request_id.reset(token)


# Python's mimetypes has no entry for woff2, so StaticFiles would serve the
# fonts as application/octet-stream.
mimetypes.add_type("font/woff2", ".woff2")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")
# Grid cards ask for a poster width that matches their box (see tmdb.poster_size).
templates.env.filters["poster_size"] = poster_size
# Every page's footer shows sync freshness; every page draws the two people.
templates.env.globals["sync_status"] = tonight.sync_status
templates.env.globals["people"] = tonight.people
templates.env.globals["plex_timeout"] = plex_sync.PLEX_TIMEOUT_SECONDS
# The stylesheet is linked as style.css?v=<hash>. StaticFiles sends no
# Cache-Control, so Safari guesses a lifetime and kept serving the old CSS to
# the new markup after a deploy; a URL that changes with the file can't go stale.
templates.env.globals["css_version"] = hashlib.sha256(
    (BASE / "static" / "style.css").read_bytes()).hexdigest()[:10]

GRID_PAGE = 60  # films per grid page on Tonight; "Show more" adds another


def _filters_from(runtime: str | None, genre: str | None,
                  decade: str | None, unseen: str | None) -> picker.Filters:
    return picker.Filters(
        max_runtime=int(runtime) if runtime else None,
        genre=genre or None,
        decade=int(decade) if decade else None,
        unseen_only=bool(unseen),
    )


# ── ops ─────────────────────────────────────────────────────────────

@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    """Browsers ask for /favicon.ico at the root whether or not a page links
    one, so without this every page load logs a 404 that means nothing. The
    file itself lives under /static/icons with the rest of them."""
    return FileResponse(BASE / "static" / "icons" / "favicon.ico",
                        media_type="image/x-icon")


@app.get("/healthz")
def healthz():
    """Liveness+readiness for the container healthcheck and an uptime monitor.

    Touches the database, because the failure this has to catch is not "is
    uvicorn up" — the Dockerfile's healthcheck probed `/`, which renders the
    index template and needs no DB at all, so a container whose `/app/data`
    bind mount had gone away stayed *healthy* while every page that reads a
    film 500'd. `SELECT count(*) FROM films` is the cheapest query that proves
    the file is open, readable and carries the schema `init_db` installed.

    Returns 503 rather than raising, so the body says which check failed
    instead of leaving a bare 500 for someone to guess at.
    """
    try:
        with db.cursor() as cur:
            cur.execute("SELECT count(*) FROM films")
            films = cur.fetchone()[0]
    except Exception as exc:  # noqa: BLE001 — any DB failure is the same answer
        log.exception("healthz: database unavailable")
        return JSONResponse(
            {"status": "error", "database": "unavailable", "detail": str(exc)},
            status_code=503,
        )
    return {"status": "ok", "database": "ok", "films": films}


# ── pages ───────────────────────────────────────────────────────────

def _view(scope: str | None = None, who: str | None = None, genre: str | None = None,
          runtime: str | None = None, both: str | None = None,
          mode: str | None = None) -> tonight.View:
    return tonight.parse(scope, who, genre, runtime, both, mode=mode)


def _who_toggles(view: tonight.View) -> list[dict]:
    """Per person: are they watching, and where does tapping their avatar go.

    Tapping someone who's watching leaves them out; tapping someone who's out
    brings them back. The last one watching can't be tapped out.
    """
    users = get_settings().users
    out = []
    for p in tonight.people():
        on = p["key"] in view.watching
        if on and len(view.watching) == 1:
            target = None
        elif on:
            target = view.params(who=next(u for u in users if u != p["key"]))
        else:
            target = view.params(who="both")
        out.append(p | {"on": on, "target": target})
    return out


def _hero_context(film: picker.Film | None, view: tonight.View) -> dict:
    # With no pick, say which filter to drop — but only when nothing fits at
    # all; a spin can come back empty while one film (the one it skipped) does.
    drops = []
    if film is None:
        now, drops = tonight.loosen(view)
        drops = drops if now == 0 else []
    return {"film": film, "view": view, "drops": drops,
            "reasons": ({u: tonight.reason(ex) for u, ex in tonight.explanations(film).items()}
                        if film else {})}


@app.get("/", response_class=HTMLResponse)
def home(request: Request, scope: str | None = None, who: str | None = None,
         genre: str | None = None, runtime: str | None = None, both: str | None = None):
    view = _view(scope, who, genre, runtime, both)
    ranked = tonight.ranked(view)
    hero = ranked[0] if ranked else None
    nudge = None
    if 0 < len(ranked) < tonight.FEW:
        nudge = next(iter(tonight.loosen(view)[1]), None)
    return templates.TemplateResponse(request, "index.html", {
        "nav": "tonight", "view": view, "counts": tonight.counts(view), "nudge": nudge,
        "toggles": _who_toggles(view), "genres": tonight.genre_options(view),
        "total": len(ranked), "short_runtime": tonight.SHORT_RUNTIME,
        "shelf": (tonight.on_plex_shelf(view, exclude=hero.tmdb_id if hero else None)
                  if view.scope == "all" else []),
        **_hero_context(hero, view),
    })


@app.get("/picker", response_class=HTMLResponse)
def picker_results(request: Request, scope: str | None = None, who: str | None = None,
                   genre: str | None = None, runtime: str | None = None,
                   both: str | None = None, mode: str | None = None,
                   exclude: int | None = None, limit: int = GRID_PAGE):
    """The ranked grid. ``mode`` is the old picker's scope and still works."""
    view = _view(scope, who, genre, runtime, both, mode)
    ranked = [f for f in tonight.ranked(view) if f.tmdb_id != exclude]
    empty_hint = None
    if not ranked:
        # The hero above already holds the one film that fits, or offers the
        # filters to drop; a second "nothing matches" box would only repeat it.
        now, drops = tonight.loosen(view)
        empty_hint = None if now or drops else _empty_hint(view)
    return templates.TemplateResponse(
        request, "_film_grid.html",
        {"films": ranked[:limit], "view": view, "total": len(ranked),
         "more": view.params(exclude=exclude, limit=limit + GRID_PAGE)
                 if len(ranked) > limit else None,
         "empty_hint": empty_hint},
    )


@app.post("/spin", response_class=HTMLResponse)
def spin(request: Request, scope: str | None = Form(None), who: str | None = Form(None),
         genre: str | None = Form(None), runtime: str | None = Form(None),
         both: str | None = Form(None), exclude: int | None = Form(None)):
    view = _view(scope, who, genre, runtime, both)
    return templates.TemplateResponse(
        request, "_hero.html", _hero_context(tonight.spin(view, exclude=exclude), view))


@app.post("/skip", response_class=HTMLResponse)
def skip(request: Request, tmdb_id: int = Form(...), scope: str | None = Form(None),
         who: str | None = Form(None), genre: str | None = Form(None),
         runtime: str | None = Form(None), both: str | None = Form(None)):
    """"Not tonight": hidden from Tonight until 6 a.m., then a fresh pick."""
    with db.cursor() as cur:
        db.add_veto(cur, tmdb_id)
    view = _view(scope, who, genre, runtime, both)
    return templates.TemplateResponse(
        request, "_hero.html", _hero_context(tonight.spin(view, exclude=tmdb_id), view))


@app.post("/shortlist", response_class=HTMLResponse)
def shortlist(request: Request, n: int = Form(3), mode: str = Form("one"),
              runtime: str | None = Form(None), genre: str | None = Form(None),
              decade: str | None = Form(None), unseen: str | None = Form(None)):
    f = _filters_from(runtime, genre, decade, unseen)
    films = picker.no_veto_shortlist(n, f, mode=mode)
    return templates.TemplateResponse(
        request, "_film_grid.html",
        {"films": films, "vetoable": True, "empty_hint": None if films else _empty_hint()},
    )


@app.post("/watched", response_class=HTMLResponse)
def mark_watched(request: Request, tmdb_id: int = Form(...),
                 scope: str | None = Form(None), who: str | None = Form(None),
                 genre: str | None = Form(None), runtime: str | None = Form(None),
                 both: str | None = Form(None)):
    """Log tonight's pick as watched for both users — closes the loop without
    waiting for the next Letterboxd sync."""
    today = date.today().isoformat()
    with db.cursor() as cur:
        row = cur.execute("SELECT title FROM films WHERE tmdb_id = ?",
                          (tmdb_id,)).fetchone()
        title = row["title"] if row else "that one"
        for user in get_settings().users:
            db.upsert_watched(cur, user, tmdb_id, today)
    return templates.TemplateResponse(
        request, "_watched_done.html",
        {"title": title, "view": _view(scope, who, genre, runtime, both)}
    )


@app.post("/veto", response_class=HTMLResponse)
def veto(tmdb_id: int = Form(...)):
    """Record a shortlist veto; future spins down-weight this film."""
    with db.cursor() as cur:
        db.add_veto(cur, tmdb_id)
    return HTMLResponse("")  # hx-swap outerHTML removes the card


@app.get("/film/{tmdb_id}", response_class=HTMLResponse)
def film_detail(request: Request, tmdb_id: int):
    """Modal for one film: external scores, each person's fit, and the long
    form of the shared match %.

    Renders immediately from cached scores; when they're stale or missing the
    template lazy-loads ``/film/{id}/scores`` so a slow third party can't hold
    up the card.
    """
    film = tonight.by_id(tmdb_id, _view())
    if film is None:
        return HTMLResponse("")
    personal = tonight.explanations(film)
    return templates.TemplateResponse(
        request, "_film_detail.html",
        {"film": film, "users": get_settings().users,
         "personal": personal,
         "reasons": {u: tonight.reason(ex) for u, ex in personal.items()},
         "why": taste.build_scorer().explain(film.genres, film.director, tmdb_id=tmdb_id),
         "scores": ratings.cached(tmdb_id)},
    )


@app.get("/film/{tmdb_id}/scores", response_class=HTMLResponse)
def film_scores(request: Request, tmdb_id: int):
    """The scores strip on its own — the network-bound half of the detail card.

    Only for films the app knows. The route needs no login, and every cache
    miss spends the OMDb and TMDb keys, so an arbitrary id must not reach the
    fetch: walking the id space would drain a free OMDb key in an afternoon.
    """
    with db.cursor() as cur:
        known = cur.execute("SELECT 1 FROM films WHERE tmdb_id = ?", (tmdb_id,)).fetchone()
    if known is None:
        return HTMLResponse("", status_code=404)
    return templates.TemplateResponse(
        request, "_film_scores.html", {"scores": ratings.get(tmdb_id)}
    )


@app.get("/get", response_class=HTMLResponse)
def get_page(request: Request, who: str | None = None, genre: str | None = None):
    """The get-list, then the best of what's wanted but not on Plex."""
    view = _view(None, who, genre)
    return templates.TemplateResponse(request, "get.html", {
        "nav": "get", "view": view, "marked": tonight.get_list(view),
        "worth": tonight.worth_getting(view), "genres": tonight.genre_options(view),
    })


@app.post("/get/{tmdb_id}", response_class=HTMLResponse)
def toggle_get(request: Request, tmdb_id: int):
    with db.cursor() as cur:
        on = db.toggle_get_list(cur, tmdb_id)
    return templates.TemplateResponse(
        request, "_get_button.html", {"tmdb_id": tmdb_id, "on": on})


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(request: Request):
    return templates.TemplateResponse(
        request, "dashboard.html", {"nav": "taste", "data": dashboard.summary()}
    )


@app.get("/wrapped", response_class=HTMLResponse)
def wrapped_latest(request: Request):
    """The newest year with a diary; an explanatory empty page when there's none."""
    have = wrapped.years()
    if have:
        return RedirectResponse(f"/wrapped/{have[0]}", status_code=307)
    return templates.TemplateResponse(
        request, "wrapped.html", {"nav": "wrapped", "w": None, "years": [], "year": date.today().year})


@app.get("/wrapped/{year}", response_class=HTMLResponse)
def wrapped_page(request: Request, year: int):
    return templates.TemplateResponse(
        request, "wrapped.html",
        {"nav": "wrapped", "w": wrapped.compute(year), "years": wrapped.years(), "year": year},
    )


# Every /admin route carries the guard as a route dependency: it runs before
# the body, and a new admin route that forgets it is what
# tests/test_admin_auth.py's route sweep exists to catch.
_ADMIN = {"dependencies": [Depends(require_admin)]}


@app.get("/admin", response_class=HTMLResponse, **_ADMIN)
def admin_page(request: Request):
    with db.cursor() as cur:
        cur.execute("SELECT * FROM unmatched ORDER BY seen_at DESC LIMIT 200")
        unmatched = [dict(r) for r in cur.fetchall()]
        counts = {t: cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ("films", "watchlist", "ratings", "watched", "plex_availability")}
    return templates.TemplateResponse(
        request, "admin.html",
        {"unmatched": unmatched, "counts": counts, "job": jobs.current(),
         "import_dir": str(get_settings().import_path)},
    )


# ── admin jobs (app/jobs.py runs them; the fragment polls until done) ──

# How long a POST waits for its job before answering with the running state.
# A job that finishes inside it (an import with no ZIPs, a sync on an idle
# Plex) shows its result at once instead of flashing a spinner for one poll.
JOB_GRACE_SECONDS = 1.5


def _jobs_fragment(request: Request):
    return templates.TemplateResponse(request, "_admin_jobs.html", {"job": jobs.current()})


def _failed(action: str, label: str):
    """An `on_error` for jobs.start(): log the traceback, return the status line."""
    def handle(e: Exception) -> str:
        log.exception(f"admin {action} failed")
        if isinstance(e, requests.exceptions.ReadTimeout):
            return (f"Plex didn't answer within {plex_sync.PLEX_TIMEOUT_SECONDS // 60} minutes. "
                    "It's running but busy, usually scanning new episodes. Try again in a few minutes.")
        return f"{label} failed: {e}"
    return handle


def _run_job(request: Request, name: str, label: str, fn, action: str, failed_label: str):
    job = jobs.start(name, label, fn, _failed(action, failed_label))
    job.wait(JOB_GRACE_SECONDS)
    return _jobs_fragment(request)


def _sync_plex() -> str:
    result = plex_sync.sync_plex()
    log.info("admin sync-plex", extra={"matched": result["matched"],
                                       "unmatched": result["unmatched_count"]})
    return f"Plex sync: matched {result['matched']} films, {result['unmatched_count']} without a TMDb GUID."


def _import() -> str:
    results = letterboxd.import_from_dir()
    if not results:
        return "No ZIPs found in the import folder."
    return " | ".join(
        f"{r['user']}: +{r['watchlist']} watchlist, {r['ratings']} ratings, "
        f"{r['watched']} watched, {r.get('diary', 0)} diary, {r['unmatched']} unmatched"
        for r in results
    )


def _enrich() -> str:
    result = enrich.enrich_tmdb()
    log.info("admin enrich", extra={"enriched": result["enriched"],
                                    "failed": result["failed"]})
    return (f"Enriched {result['enriched']} films ({result['failed']} failed); "
            f"cast for {result['cast']} diary films ({result['cast_failed']} failed).")


@app.post("/admin/sync-plex", response_class=HTMLResponse, **_ADMIN)
def admin_sync_plex(request: Request):
    return _run_job(request, "plex", "Syncing Plex", _sync_plex, "sync-plex", "Plex sync")


@app.post("/admin/import", response_class=HTMLResponse, **_ADMIN)
def admin_import(request: Request):
    return _run_job(request, "import", "Importing Letterboxd", _import, "import", "Import")


@app.post("/admin/enrich", response_class=HTMLResponse, **_ADMIN)
def admin_enrich(request: Request):
    return _run_job(request, "enrich", "Enriching from TMDb", _enrich, "enrich", "Enrich")


@app.get("/admin/job", response_class=HTMLResponse, **_ADMIN)
def admin_job(request: Request):
    """The buttons and status box, polled every second while a job runs."""
    return _jobs_fragment(request)


@app.post("/admin/resolve", response_class=HTMLResponse, **_ADMIN)
def admin_resolve(request: Request, title: str = Form(...),
                  year: str = Form(""), tmdb_id: int = Form(...)):
    s = get_settings()
    conn = db.connect()
    try:
        tmdb = TMDbClient(s.tmdb_api_key, conn=conn)
        yr = int(year) if year else None
        tmdb.set_manual(title, yr, tmdb_id)
        cur = conn.cursor()
        db.upsert_film(cur, tmdb_id, title, yr)
        cur.execute("DELETE FROM unmatched WHERE cache_key = ?", (normalise_key(title, yr),))
        conn.commit()
        tmdb.close()
        msg = f"Pinned “{title}” → TMDb {tmdb_id}. Re-run the relevant import to attach it."
        log.info("admin resolve", extra={"title": title, "tmdb_id": tmdb_id})
    except Exception as e:  # noqa: BLE001
        log.exception("admin resolve failed", extra={"title": title, "tmdb_id": tmdb_id})
        msg = f"Resolve failed: {e}"
    finally:
        conn.close()
    return _status(request, msg)


# ── JSON API (for a future SPA or Project 1) ───────────────────────

@app.get("/api/both-want")
def api_both_want():
    return JSONResponse([f.__dict__ for f in picker.both_want()])


@app.get("/api/one-wants")
def api_one_wants():
    return JSONResponse([f.__dict__ for f in picker.one_wants()])


@app.get("/api/spin")
def api_spin():
    film = picker.spin_the_wheel()
    return JSONResponse(film.__dict__ if film else None)


@app.get("/api/dashboard")
def api_dashboard():
    return JSONResponse(dashboard.summary())


@app.get("/api/wrapped/{year}")
def api_wrapped(year: int):
    return JSONResponse(wrapped.compute(year))


@app.get("/api/to-add")
def api_to_add():
    """Films on both watchlists not yet on Plex, ranked by shared-taste fit."""
    return JSONResponse(dashboard.to_add())


@app.get("/api/radarr")
def api_radarr():
    """The get-list as a Radarr "Custom Lists" import list, which polls this URL.

    Radarr's ``RadarrListParser`` reads a top-level JSON array and keeps each
    object's ``id`` as the TMDb id (``id > 0``), ignoring other fields — so
    ``title`` is only for a human reading the feed. Same films, same order as
    the get-list page: marked, still not on Plex. Radarr pulls; nothing pushes.
    """
    return JSONResponse([{"id": f.tmdb_id, "title": f.title}
                         for f in tonight.get_list(tonight.parse())])


@app.get("/api/film/{tmdb_id}")
def api_film(tmdb_id: int):
    """One film: metadata, external scores, and the full match breakdown.

    Unlike the HTML card this fetches scores inline — an API caller has nowhere
    to put a lazy-loading placeholder.
    """
    film = picker.by_id(tmdb_id)
    if film is None:
        return JSONResponse({"error": "unknown film"}, status_code=404)
    return JSONResponse({
        "film": film.__dict__,
        "scores": ratings.get(tmdb_id).as_dict(),
        "why": asdict(taste.build_scorer().explain(film.genres, film.director, tmdb_id=tmdb_id)),
    })


# ── helpers ─────────────────────────────────────────────────────────

def _status(request: Request, msg: str):
    return templates.TemplateResponse(request, "_status.html", {"msg": msg})


def _empty_hint(view: tonight.View | None = None) -> str:
    with db.cursor() as cur:
        films = cur.execute("SELECT COUNT(*) FROM films").fetchone()[0]
    if films == 0:
        return "No data yet — go to Admin and run Sync Plex + Import Letterboxd."
    if view is not None and view.scope == "plex":
        return "Nothing on Plex matches. Loosen a filter, or look across everything you want."
    return "Nothing matches these filters. Loosen one."
