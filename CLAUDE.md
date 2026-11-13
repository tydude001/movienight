# CLAUDE.md — movienight repo

**Movie Night** — a self-hosted FastAPI + HTMX app that ranks two people's combined Letterboxd watchlists by how likely *both* are to enjoy each film, narrows to what's *actually on Plex*, keeps a get-list of films to add, and has a shared taste dashboard. SQLite store, Docker on port 8000. See [README.md](README.md) and [docs/deploy.md](docs/deploy.md) for the full picture.

## Environment

- **Nothing about a particular machine or install enters this repo.** Host
  names, addresses, paths, who runs it and where, anyone's viewing data: not in
  the tree, not in a commit message. A pre-push hook refuses a push that carries
  one where it is installed, and `tests/test_denylist.py` runs the same scan.
  Where an install's own facts live is in the owner's notes, not here.
- App deps live only in Docker; tests run from a local `.venv` (below).
  `docker compose` bind-mounts `app/` and `scripts/` over the image, so a code
  change is a restart; only `Dockerfile` or `requirements.txt` needs a rebuild.
  Verify a deploy against the running service — `curl -s <app>/openapi.json`
  shows whether new routes actually exist, `/healthz` whether the database is
  reachable.

## Layout

- `app/` — FastAPI app. Key pieces: `tmdb.py` (Letterboxd → TMDb id resolver, cached) and `plex_sync.py` (Plex GUID extraction). The picker joins Letterboxd entries to Plex on TMDb id.
- `taste.py` is the **single taste model** (genre 0.7 / director 0.3 affinity, films rated 4★+ counted twice, films rated ≤2★ subtracted once). `build_scorer()` is the couple's pooled history; `build_scorer(user)` is one person's alone. Everything is cached per user with a 60s TTL (`SCORER_TTL_SECONDS`) — syncs run in another container, so TTL is the invalidation.
- `tonight.py` owns the **pair score** the UI ranks on: each film gets `fits` (one per user), `pair` = `taste.pair_fit()` (harmonic mean, +8 on both watchlists), and `score` (the pair, or one user's fit on a solo night). The pooled `match` from `picker._annotate()` is kept alongside — it drives the detail card's breakdown, the API's `why.match`, the Plex arrival notification, and `dashboard.to_add()`. Two numbers on purpose: the card shows the pair as its headline, the pooled match only inside "Your shared taste".
- `Scorer.explain()` is the film detail card's long-form "why": it reads `match()` rather than recomputing, so the card can never contradict the badge above it. Its citations come from a fourth query in `_build_scorer()` that's **evidence only and must never feed the score**. Citations rotate per film and de-duplicate across a card's lines, so a card often quotes a 4.5★ film while a 5★ one exists — deliberate, not a ranking bug: when a genre has only a handful of 5★ films, quoting the best would repeat the same titles on every card.
- `ratings.py` owns external scores (RT/IMDb/Metacritic via OMDb, Letterboxd by reading its film page, TMDb's own) and the `external_ratings` table outright — hence SQL there rather than in `db.py`. Every source is best-effort and independently optional; a failing one leaves `None`. Detail cards render from cache and lazy-load `/film/{id}/scores`, so **no third party is ever on a page-render path**. The Letterboxd reader **ships disabled** (`LETTERBOXD_SCORES=0`) because their ToS prohibits automated access — that default is a decision, not an oversight, and `test_both_scrapers_ship_disabled` guards it. Don't flip it. The same goes for the sync's watchlist scrape (`LETTERBOXD_WATCHLIST_SCRAPE=0`, same test): off, `watchlist` mode exits 1 without a request and `all` runs RSS only, and the watchlist comes from the export's `watchlist.csv`. An install that wants the weekly scrape sets `LETTERBOXD_WATCHLIST_SCRAPE=1` in its `.env`.
- `logs.py` is the **only place logging is configured** — a JSON formatter on the root logger, installed from the app's lifespan and from each script, plus the `request_id` ContextVar that `main.py`'s middleware sets per request. It removes only the handler it installed last, never every root handler, so `caplog` survives an app started inside a test. Pass whole result dicts through `logs.safe_extra()`: a key that shadows a `LogRecord` attribute (`module`, `name`, `args`) makes `logging` raise, which would kill an unattended sync at the moment it reported itself. Both formatters pass every line through `logs.redact()`, and `httpx` is held at WARNING: TMDb and OMDb take their keys in the URL, and httpx's request lines once put the TMDb key in `sync.log`.
- `wrapped.py` builds `/wrapped/{year}` from the **`diary` table only** — one row per watch, on the date set on Letterboxd. `watched` can't serve: one row per film, and its export date is when the entry was *logged* (UTC, so usually a day late — on real data it put most of a year's watches on the wrong date). Three counting rules, each deliberate: a film marked watched without a diary entry is a catch-up and never counts (a backfill of films seen years ago); 5+ entries by one person on one date are a bulk-dated catch-up and are dropped and listed on the page; "together" is the same film on the same date in both diaries, no slack. `diary` is filled from the export's `diary.csv` (which replaces everything up to its last date) and the RSS sync; cast (`film_cast`, top six billed) is backfilled by `enrich_tmdb()` for diary films only.
- `jobs.py` runs the three `/admin` ingestion buttons **off the request thread, one at a time**: the POST waits `JOB_GRACE_SECONDS` (1.5s) and then answers with `_admin_jobs.html`, which re-fetches itself from `GET /admin/job` every second until the job ends. State is in memory, so a restart loses only the status line, and every job is idempotent. A second click while a job runs gets that job's status instead of starting another. `plex_sync.PLEX_TIMEOUT_SECONDS` is 120 because a Media Scanner run once stretched the library listing from 1.4s to 72.5s, past plexapi's 30s default.
- `data/` — SQLite (`watch.db`) + `imports/` (Letterboxd export ZIPs). **Git-ignored.**
- `scripts/`, `Dockerfile`, `docker-compose.yml`, `requirements.txt`.
- `tests/` — `pytest` over the pure logic (taste, picker, tmdb resolver via mock transport, plex GUID extraction) plus `test_routes.py`, which renders the detail card through a `TestClient` because Jinja errors only surface at render time. Each test uses a throwaway temp SQLite DB (`conftest.py`); no network, no real `data/`. App deps live only in Docker, so tests run from a local `.venv` (git-ignored): `.venv/bin/pip install -r requirements.txt -r requirements-dev.txt`, then `.venv/bin/python -m pytest`. A Stop hook gates turn-end on that same suite (`.claude/test-command`), so a turn cannot end red locally and then fail in CI.
- Docs: `README.md`, `docs/deploy.md`; `docs/COMPETITORS.md` surveys the open-source alternatives, read from source.

## Conventions

- Python (FastAPI), server-rendered Jinja + HTMX. SQLite.
- htmx, Chart.js and the three web fonts (`app/static/fonts/`, OFL) are
  vendored — page loads stay CDN-free (same principle as ratings: no third party
  on a render path). Upgrade by replacing the files, don't reintroduce a CDN.
- The look is "Two Spotlights": first user blue `#6ca6ff`, second amber
  `#ffb36b`, white where they overlap. Tokens are at the top of `style.css`.
  `theme-color` stays `#0f1115` to match `site.webmanifest` (`test_icons.py`
  pins it).
- `get_list` (films marked to buy) and `app_meta` (sync timestamps — `plex_sync`
  and `scripts/sync_letterboxd.py` stamp them; the footer shows them) are plain
  tables in `db.py`, created by `init_db()` on the next start.
- Favicons and the iPhone homescreen icon live in `app/static/icons/`,
  regenerated from `favicon.svg` by `scripts/make-icons.sh` — edit the SVG, run
  it, commit the lot. The shortcut opens as a Safari tab on purpose:
  `apple-mobile-web-app-capable` is absent so the back gesture works and
  `/admin`'s HTTP Basic credentials keep sticking. `test_icons.py` guards that
  and iOS's homescreen rules: an opaque 180px icon, a label of ≤12 characters.
- Poster `<img>`s are the one third party left on a render path, so they're
  sized to their box via `tmdb.poster_size()`: grid cards take a w185/w342
  `srcset`, with w500 **deliberately absent** so a 2x screen can't pull it.
  Only the detail card's 220px poster gets w500 — serving w500 to the grid
  cost 7 MB per 119-film page.

## Admin auth

`/admin/*` is behind HTTP Basic (`app/auth.py`) and **fails closed**: with
`ADMIN_USER`/`ADMIN_PASSWORD` unset it answers 503, so a deploy that skips the
`.env` step locks the admin page rather than opening it. Every admin route
carries `_ADMIN`; `test_admin_auth.py` sweeps the route table for one that
doesn't. Change either with `scripts/set-admin-password.sh` (it prompts for
both, `--remote` for an install over ssh), which writes the password
single-quoted so compose reads it literally, and checks the new login works.

Open work is tracked outside the repo, never in this file.

CI: `.github/workflows/ci.yml` runs ruff + pytest on GitHub, on every push to
`main` and every pull request. It is the only CI. Keep `ruff check .` clean — CI lints the whole tree, not
just changed files like a pre-push hook does. The Pytest step sets
`TMPDIR=/dev/shm` (the suite makes a temp SQLite DB per test, and on a slow disk
that dominates the run); don't drop it, and give any new suite the same.

## Don't

- **Never commit** `.env` (Plex token, TMDb key) or `data/` (db + imports). They're git-ignored.
- The whole thing hinges on TMDb ids — don't change resolver/GUID logic without checking `tmdb.py` and `plex_sync.py` together.
