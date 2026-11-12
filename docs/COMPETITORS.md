# Competitors

Open-source projects on GitHub that also answer "what do we watch", read from
their source. This file is the evidence. Anything it suggests building is tracked
as open work, not here. Star counts, versions and heads are
snapshots dated where they appear, and go stale. Re-run the survey before a
major scope change. § Method records how it searched.

This file was named `PRIOR_ART.md` until 2026-10-18, when the survey widened
past its one entry, Nostalgex (read 2026-10-12).

## How to read an entry

Each project was cloned shallow (`--depth 50`) and its source was read before
its README. Nothing was built or run. **[claim]** marks a project's own
statement that was not confirmed in its source. "Read" is the commit that was
read. A verdict is one of three: *competitor* (same job, at least partly),
*adjacent* (shares a piece of the pipeline) and *not a competitor*.

## Where movienight stands (2026-10-18)

No project found does what movienight does. movienight gives each film a
content-affinity score per person from their rated history, fuses the two into
one pair score, and ranks both Letterboxd watchlists on that score, narrowed to
what's on Plex. The field splits four ways, and each part covers one piece:

- **Group pickers over a media server** pick at random or by swipe agreement.
  None of them uses history. The closest is Movie-Roulette, which intersects
  two Plex watchlists and then picks at random.
- **Letterboxd compare tools** intersect watchlists. They rank by how many
  people listed a film, by TMDb vote average, or not at all.
- **Recommenders** score for one person, or for a pooled household. curatarr
  is the nearest mechanism: genre and director affinity weighted by rating,
  scored against the Plex library. It never fuses two people into a pair.
- **Trackers and bridges** import, sync and count. They do not rank.

None of them reads the Letterboxd CSV export for its watchlist. Every
Letterboxd-centred project here gets its data from letterboxd.com by automated
means. So does movienight's own weekly watchlist job: `scripts/sync_letterboxd.py`
scrapes the public watchlist pages and each film's page for its TMDb id. Since
2026-11-12 that job ships off like the scores reader (`LETTERBOXD_WATCHLIST_SCRAPE=0`),
for the same terms-of-use reason; off, the watchlist comes from the export.

## At a glance

| Project | ★ (2026-10-18) | Picks by | Two-person? | Letterboxd | Media server | Verdict |
|---|---|---|---|---|---|---|
| [Movie-Roulette](#movie-roulette) | 434 | random | shared Plex watchlist | — | Plex / Jellyfin / Emby | competitor (partial) |
| [curatarr](#curatarr) | 36 | content affinity | pooled household | — | Plex | competitor (closest mechanism) |
| [twinboxd](#twinboxd) | 10 | intersection, unranked | yes | scrapes | — | competitor (shallow) |
| [BoxdBuddies](#boxdbuddies) | 5 | intersection, TMDb vote | N friends | scrapes | — | competitor (shallow) |
| [Letterboxd-Watchlist-Matcher](#letterboxd-watchlist-matcher) | 0 | k-of-n intersection | N users | scrapes (letterboxdpy) | — | competitor (shallow) |
| [swiparr](#swiparr) | 799 | session swipe | group | — | Jellyfin / Emby / Plex | adjacent |
| [kino-swipe](#kino-swipe) | 29 | session swipe | yes | — | Plex / Jellyfin | adjacent |
| [decide](#decide) | 3 | unanimous swipe | 2–4 | — | Plex | adjacent |
| [moviematch](#moviematch) | 513 | session swipe | group | — | Plex | adjacent, dormant |
| [letterboxd_recommendations](#letterboxd_recommendations) | 407 | SVD (collaborative) | no | scrapes | — | adjacent |
| [Kometa](#kometa) | 3,454 | — (collections) | no | scrapes | Plex | adjacent |
| [letterboxd-plex-sync](#letterboxd-plex-sync) | 60 | — (sync) | no | logs in, scrapes | Plex (+ Radarr) | adjacent |
| [letterboxd-jellyfin](#letterboxd-jellyfin) | 42 | — (sync) | multi-user | scrapes via proxies | Jellyfin (+ *arr) | adjacent |
| [Movary](#movary) | 784 | — (tracker) | separate accounts | ratings/diary CSV; scrapes for TMDb id | Plex scrobble | adjacent |
| [Ryot](#ryot) | 3,641 | TMDb suggestions (Pro) | collaborators (Pro) | — | Plex | adjacent |
| [Yamtrack](#yamtrack) | 3,708 | TMDb per-title | collaborative lists | link-out only | Plex webhook | adjacent |
| [SuggestArr](#suggestarr) | 1,343 | TMDb similar + LLM | strictly per user | — | Plex / Jellyfin / Emby | adjacent |
| [Nostalgex](#nostalgex) | 117 (2026-10-12) | schedule rules | no | — | Plex / Jellyfin / Emby | not a competitor |
| [voterr](#voterr) | 0 | group votes | group | — | Plex (via Overseerr) | not a competitor |
| [Blendboxd](#blendboxd) | 1 | overlap % | yes | scrapes | — | not a competitor |
| [Seerr](#seerr) | 12,931 | — (requests) | per user | — | Plex / Jellyfin | not a competitor (get-list analogue) |

## Group pickers over a media server

### Movie-Roulette

[sahara101/Movie-Roulette](https://github.com/sahara101/Movie-Roulette). MIT,
434★, created 2024-07-07, last commit 2026-09-23, tag v5.5.6, read `15975a4`.
It is listed as a fork of the dead Akasiek/Random-Plex-Movie and has been almost
entirely rewritten: `movie_selector.py` alone is 4,030 lines.

**What it is.** A random picker over Plex, Jellyfin or Emby. `/random_movie`
(`movie_selector.py:1445`) calls `random.choice()` over cached unwatched films,
minus a per-session seen-set that resets when the pool runs out. `filter_movies()`
adds genre, year and certificate filters. Its couple mode, "Watch With", is Plex
only (`routes/watchlist_routes.py`, `/api/shared_watchlist_movie`):

- *Watchlist mode.* `PlexService.get_shared_watchlist_pool()`
  (`utils/plex_service.py:927`) intersects your plex.tv watchlist with a
  friend's, fetched over GraphQL. It maps the result to library films by Plex
  GUID, falling back to TMDb id, caches it for 5 minutes and picks at random.
- *Library mode.* `get_library_intersection_pool()` (`:981`) keeps films that
  both users have left unwatched.

It has a Seerr/Ombi request action (`routes/request_routes.py`) and a TMDb
collection-gap service with request status (`utils/collection_service.py`).
**[claim]** Apple TV and LG webOS power-on, a cinema poster mode, passkeys.

**Stack.** Flask with gunicorn and eventlet (Socket.IO), port 4000, state in
JSON caches. Docker images at ghcr.io.

**Compared with movienight.** It is movienight's two-watchlist ∩ library join
without the ranking. It has no taste model, no rating history, no Letterboxd,
no dashboard, and no get-list of its own: the nearest thing is "request via
Seerr".

**Borrowable.** Collection gaps ("you own 2 of the 3 in this TMDb
collection") as get-list suggestions. Its spin-with-seen-set is already covered
by movienight's weighted spin with vetoes.

**Verdict.** Competitor, partial.

### swiparr

[m3sserstudi0s/swiparr](https://github.com/m3sserstudi0s/swiparr). MIT, 799★,
created 2025-12-26, last commit 2026-07-22, tag v1.5.1, read `8697e1f`. The
strongest product in the session-swipe genre.

**What it is.** A group swipe app over Jellyfin, Emby, Plex, or TMDb with no
media server at all. `MediaService` (`src/lib/services/media-service.ts`
~L283–345) fetches the filtered library and shuffles it with
`shuffleWithSeed(items, "${session.randomSeed}:${filtersHash}")`, so every
member sees the same order. `SessionService.addSwipe()`
(`src/lib/services/session-service.ts` L265–434) decides a match by a
`matchStrategy` of `atLeastTwo` or `allMembers`, with optional caps on likes and
nopes. `reEvaluateMatch()` undoes a match when a like is retracted or a member
leaves. Its sorts come from the server (random, community rating, newest) and
are not personal. **[claim]** Watchlist sync for every provider; Emby and Plex
support are called "experimental".

**Stack.** Next.js 16 and Drizzle on libsql (SQLite or Turso). Docker, Vercel,
or the hosted swiparr.com.

**Compared with movienight.** Agreement exists only inside one session. It has
no history, no Letterboxd, no get-list and no dashboard.

**Borrowable.** A seeded shuffle keyed on session and filter hash, for ties or
for a "surprise me" order that both phones agree on without syncing.

**Verdict.** Adjacent.

### kino-swipe

[Bergasha/kino-swipe](https://github.com/Bergasha/kino-swipe). MIT, 29★, created
2026-02-11, last commit 2026-10-11, tag V1.2, read `dc15204`. Built explicitly for
couples.

**What it is.** Tinder for two over Plex or Jellyfin. `join_room()`
(`app/routes.py` ~L455–486) intersects the joiner's visible films with the
host's, so restricted users see only what both can watch, then shuffles them.
`swipe()` (L490) records a match on a right swipe from any other user. Matches go
to SQLite, and `app/plex_homescreen.py` pushes them into a per-user Plex
collection, promotes it to Home with `collection.visibility().promoteHome()`,
and hides it from other users with label exclusions.

**Stack.** Flask, SQLite, vanilla JS, a PWA. Docker or an Unraid template.

**Compared with movienight.** A two-person picker with no scoring at all.

**Borrowable.** Promote Tonight's top pair picks, or get-list films that have
arrived, to a Plex Home collection, so they show on the TV with no phone.
`plex_homescreen.py` has the plexapi calls.

**Verdict.** Adjacent.

### decide

[hk21x/decide](https://github.com/hk21x/decide). MIT, 3★, created 2026-07-24,
last commit 2026-07-26, tag v0.1.2, read `5153a72`. Toy-scale but the best
engineered, and the same stack family as movienight (FastAPI, SQLite, Docker,
pytest).

**What it is.** Swipe-to-match for 2–4 people over Plex. `build_deck()`
(`backend/src/decide/services/deck.py`) filters, dedupes by imdb → tmdb → guid,
shuffles with a sha256(session) seed, and truncates. Below `MIN_DECK = 10` it
raises `DeckTooSmall`, naming the one filter whose removal recovers the most
films ("Removing the decade filter would give 47"). A match requires every
member who has joined. `GET /{session}/stats` computes a pairwise "taste
agreement %" in SQL. `FinalRound.tsx` runs a head-to-head bracket over the
matches. **[claim]** Push alerts on a match; playing the winner on a Plex
client.

**Compared with movienight.** Its agreement number covers one session and
persists nothing. No Letterboxd, no get-list.

**Borrowable.** The `DeckTooSmall` hint. When the Tonight filters (on-Plex,
genre, decade, runtime, unseen) leave little, say which single filter to drop
and how many films that would bring back. Taken up 2026-10-18 as
`tonight.loosen()`: every drop when nothing fits, the best one under 6.

**Verdict.** Adjacent.

### moviematch

[LukeChannings/moviematch](https://github.com/LukeChannings/moviematch).
Apache-2.0, 513★, created 2020-12-06, last commit 2023-02-26, tag v2.0.0-beta.3,
read `bd5d1fd`. The original of the genre, now dormant.

**What it is.** Rooms are kept in memory (`internal/app/moviematch/room.ts:208`).
The deck is shuffled with `sort(() => 0.5 - Math.random())`, which is biased.
`likes.length > 1` counts as a match. A stored `sort` option is never read on
the server. Deno and React, in Docker.

**Verdict.** Adjacent and dormant. Nothing to borrow.

### voterr

[Tmirage/voterr](https://github.com/Tmirage/voterr). No license, 0★, created
2026-01-16, last commit 2026-02-03, read `8030ee0`. A movie-night planner for a
group: films are nominated through Overseerr or TMDb, each user gets
`max_votes_per_user` stackable votes, and the host has a veto. The host-decide
winner query counts vote rows rather than stacked votes (`server/routes/votes.ts`
~L705–730). It is a social scheduler, not a taste ranker.

**Verdict.** Not a competitor.

## Letterboxd-centred tools

### twinboxd

[juliobsz/twinboxd](https://github.com/juliobsz/twinboxd), with its backend
[juliobsz/letterboxd-watchlist-api](https://github.com/juliobsz/letterboxd-watchlist-api).
GPL-3.0, 10★ and 0★, created 2026-04, last commit 2026-06-01, read `3a3693a` and
`9506c0c`. Its tagline is "Compare 2 Letterboxd watchlists", the closest in
intent to movienight.

**What it is.** The Express backend (`src/services/letterboxd.ts`) scrapes
`/{user}/watchlist/page/N/` with a spoofed Chrome user agent, 6 pages at a time.
It **stops silently at `MAX_RESULTS` = 800** (`scraper.ts`). The frontend's
`mergeMatches()` (`src/lib/match.ts`) is a plain intersection on Letterboxd URL,
in no order. Its own to-do list says "Rate sorting" and "Shuffle" are not done.

**Borrowable.** Nothing. The silent cap is the kind of data-source truncation
that tests missed here before.

**Verdict.** Competitor, shallow.

### BoxdBuddies

[Wootehfook/BoxdBuddies](https://github.com/Wootehfook/BoxdBuddies). AGPL-3.0,
5★, created 2025-08-02, last commit 2026-10-18, tag v2.1.6, read `74b2231`.

**What it is.** Compares your watchlist with selected Letterboxd friends.
`functions/api/watchlist-comparison/index.ts` scrapes the friends list and the
watchlists and keeps films listed by at least two people. Its final sort is by
TMDb `vote_average` alone (~L957). **[claim]** "sorted to show movies that the
most friends have": that sort is only in an older endpoint that the frontend no
longer calls. TMDb matching is by title and year ±1 against a D1 copy of TMDb.
React with Cloudflare Pages Functions, D1 and KV.

**Verdict.** Competitor, shallow.

### Letterboxd-Watchlist-Matcher

[Styleflo/Letterboxd-Watchlist-Matcher](https://github.com/Styleflo/Letterboxd-Watchlist-Matcher).
MIT, 0★, created 2026-01-22, last commit 2026-10-09, read `f94125c`. N-user
watchlist intersection through letterboxdpy (`backend/services/letterboxd.py`).
When no film is shared by all of more than two users, `routers/movies.py` steps
down to ceil(n/2) ("Shared by k/n users"). No scoring. **[claim]** genre and
runtime previews: the backend returns neither. FastAPI and React on Vercel.

**Verdict.** Competitor, shallow. Its k-of-n fallback only matters beyond two
people.

### letterboxd_recommendations

[sdl60660/letterboxd_recommendations](https://github.com/sdl60660/letterboxd_recommendations).
GPL-3.0, 407★, created 2020-12-01, last commit 2026-08-15, tag v4.0.0, read
`71e6588`. Hosted at letterboxd.samlearner.com.

**What it is.** A one-person recommender. It scrapes the user's ratings and
watchlist (`data_processing/get_user_ratings.py`, `get_user_watchlist.py`) and
folds them into an SVD model trained offline on a crawl of popular members'
ratings (`Model._fold_in_from_pairs`, `data_processing/model.py`). Liked but
unrated films get a synthetic rating (`attach_synthetic_ratings`). The README
says the model is "completely blind to genres, themes, directors", the opposite
of `taste.py`. **[claim]** a dataset of over 50 million ratings. FastAPI, RQ,
Redis, MongoDB and React.

**Compared with movienight.** It needs a site-wide corpus that it scrapes, so
the approach can't transfer. It has no notion of two people and no Plex.

**Verdict.** Adjacent.

### Kometa

[Kometa-Team/Kometa](https://github.com/Kometa-Team/Kometa). MIT, 3,454★,
created 2020-11-30, last commit 2026-10-12, tag v2.5.3, read `e1327dc`.

**What it is.** A Plex metadata and collection manager.
`modules/letterboxd.py` turns Letterboxd lists, watchlists and genre or
similar-film pages into Plex collections. It reads through letterboxdpy, falls
back to cloudscraper, and retries Cloudflare challenges with curl_cffi. It reads
each film's TMDb id from the Letterboxd page and caches it in `letterboxd_map`
(`modules/cache.py`) with an expiry. It is a scheduled batch run with no
ranking.

**Compared with movienight.** It is the Plex half of movienight's join, with
none of the scoring.

**Verdict.** Adjacent.

### letterboxd-plex-sync

[treysu/letterboxd-plex-sync](https://github.com/treysu/letterboxd-plex-sync).
BSD-3-Clause, 60★, created 2024-03-13, last commit 2026-04-27, tag v1.0.2, read
`6b12255`.

**What it is.** A one-way sync of Letterboxd ratings, watched status and
watchlist into Plex, with an optional push to Radarr (`python/sync_lb_to_plex.py`).
It logs into Letterboxd to download the export, and scrapes each film page for
its TMDb id. It joins on Plex `tmdb://` GUIDs. For films that aren't in the
library it queries Plex Discover (`/library/metadata/matches?guid=tmdb://…`) and
adds them to the Plex watchlist. `add_to_radarr` POSTs to `/api/v3/movie` with a
quality profile, root folder and tags, and skips ids Radarr already has. Docker
with cron.

**Borrowable.** Both handoffs, as get-list actions keyed on the TMDb id
movienight already holds: a Radarr add with dedupe, and a Plex-watchlist add
through Discover. Neither touches Letterboxd.

**Verdict.** Adjacent.

### letterboxd-jellyfin

[MathisVerstrepen/letterboxd-jellyfin](https://github.com/MathisVerstrepen/letterboxd-jellyfin).
MIT per the README (no LICENSE file), 42★, created 2024-01-09, last commit
2026-07-20, read `13801a2`.

**What it is.** A multi-user watchlist sync into Radarr, Sonarr and a Jellyfin
collection. `src/letterboxd.py` scrapes the watchlists, optionally through
rotating proxies (`src/proxies.py`). Every scan returns
`WatchlistResult(outcome=success|partial|failed, scan_complete, failed_items)`,
and items that fail stay pending in SQLite (`state_manager.py`) for the next
run. A film is removed from the collection once its user has played it. JSON
logs, a health check, Prometheus metrics.

**Borrowable.** An explicit `scan_complete` flag and partial outcome on every
sync, so that a truncated pull says so instead of passing as a short
watchlist. On 2026-10-18 movienight's watchlist job reported `watchlist_slugs`,
`matched` and `unmatched`, but not whether pagination reached the end, and a
failed page wiped the rest of the stored watchlist. Taken up the same day:
the job now reports `scan_complete` and keeps the stored watchlist when it is false.

**Verdict.** Adjacent.

### Blendboxd

[lumiboi/Blendboxd](https://github.com/lumiboi/Blendboxd). No license, 1★, last
commit 2025-11-23, read `fb985be`. It stands in for the "letterboxd blend" toys.
`calculate_compatibility()` in `app.py` is overlap of watched titles,
`2c/total*100` plus a flat +50 once more than 5 are shared, capped at 100. It
ignores ratings. It scrapes through cloudscraper.

**Verdict.** Not a competitor. It names the "how compatible are we" idea that
movienight's taste dashboard answers properly.

## Trackers and recommenders

### curatarr

[OrchestratedChaos/curatarr](https://github.com/OrchestratedChaos/curatarr).
AGPL-3.0, 36★, created 2026-01-01, last commit 2026-08-30, tag v2.22.0, read
`16f83ac`. The closest mechanism to `taste.py`.

**What it is.** It scores the unwatched films already in the Plex library for
each Plex user, writes per-user Plex collections, and exports acquire lists to
Trakt, MDBList, Radarr and Sonarr. `utils/scoring.py` is content-based:

- Default weights: genre 0.20, director 0.15, actor 0.15, language 0.05,
  keyword 0.45 (`_redistribute_weights`). An empty component's weight is
  **redistributed** to the others.
- **IDF damping** on genres and keywords, so ubiquitous ones count for less.
- A recency multiplier and a rewatch multiplier.
- Rating weights 5★ 1.0, 4★ 0.75, 3★ 0.5, 2★ 0.25, unrated 0.6
  (`utils/config.py`). Low ratings become penalties, and films left unwatched
  in the library for a long time count against the user.
- A run without `single_user` pools every managed user's history into one
  profile (`recommenders/base.py` ~L827–860), the same idea as movienight's
  pooled `build_scorer()`. Its `user_mode: combined` is only a deduplicated
  union of each user's list. There is no pair fusion.
- Per-user `exclude_genres` and `max_rating` hard filters.

**[claim]** Streaming availability per user; Tautulli and Simkl history merge.
Python CLI on cron, with a local Flask UI, PyInstaller binaries and Docker. It
does not use Letterboxd.

**Compared with movienight.** It has the per-user and pooled affinity model but
not the pair score, the watchlist pool, Letterboxd, or a shared dashboard.

**Borrowable.**

- **Redistribute the director weight.** `Scorer.match()` (`app/taste.py:133`)
  scores a film with no known director as `0.7 × genre affinity`, so it can
  never pass 70. Redistributing would make the score genre-only rather than
  capped. Note that `explain()` reads `match()`, so the card moves with it.
  Tried 2026-10-18 for a director with no history (291 of 598 watchlist
  films; only 6 had none at all) and reverted the same day: single-genre
  comedies by unseen directors, indistinguishable to the model, went from a
  tie at 70 to a six-way tie at 100 above Rushmore (81, a director both like).
  Scoring an unseen director at the user's average director instead is worth
  only 3–6 points (Spearman 0.95–0.99), and ties five films at the top of
  On Plex, so it wasn't built either.
- **IDF on genres.** Drama-heavy histories inflate every drama's match today.
  Measured 2026-10-18 on the 10-17 snapshot and not taken up. Comedy, not
  Drama, is both people's top genre. curatarr's own form (`utils/corpus_idf.py`:
  log(N/(1+df))/log N, floor 0.05, over the library, no renormalising) gives
  every genre 0.14–0.27, since ~20 broad genres each cover 10–34% of films.
  It deflated the top pick to 36–39 (below every headline band) and replaced
  18–20 of the top 20 (Spearman 0.36–0.57).
- Recency decay on old ratings. This is weaker for two people with long
  Letterboxd histories, and the dashboard would need to say so.

**Verdict.** Competitor: the closest mechanism, but household-pooled rather
than pair-fused.

### Movary

[leepeuker/movary](https://github.com/leepeuker/movary). MIT, 784★, created
2021-01-24, last commit 2026-10-17, release 0.80.0, read `3e3699a`.

**What it is.** A watch-history and ratings tracker with statistics. It imports
the Letterboxd ratings and diary CSVs (`src/Service/Letterboxd/`) but not the
watchlist, and gets TMDb ids by scraping `boxd.it/<id>`
(`LetterboxdWebScrapper::scrapeTmdbIdByLetterboxdId`). Its stats are plain
counts (`MovieRepository::fetchMostWatchedGenres`), not weighted by rating.
There is no recommender. Users are separate accounts with a privacy level and
no shared view. It has a Plex scrobbler and a Plex watchlist importer.
`RadarrController::renderRadarrFeed` serves a user's watchlist as a
Radarr-importable JSON list under an unguessable feed id. PHP and Twig on
SQLite or MySQL, in Docker. **[claim]** Trakt and Netflix import;
Jellyfin, Emby and Kodi scrobbling.

**Borrowable.** The Radarr list feed. Radarr polls a URL and movienight
serves the get-list as JSON, so there's no push, no credentials and no
Radarr client code.

**Verdict.** Adjacent.

### Ryot

[IgnisDa/ryot](https://github.com/IgnisDa/ryot). GPL-3.0, 3,641★, last commit
2026-10-12, release v10.5.0, read `a96986e`. A tracker for all media plus
fitness. Its recommendations (`recommendation_operations.rs`) sample 10 random
items the user has touched and pool TMDb's suggestions for them. Recommendations
and collaborator collections both sit behind the Pro key. A Plex integration
fills an "Owned" collection from GUIDs. No Letterboxd import. Rust, GraphQL and
Postgres.

**Verdict.** Adjacent.

### Yamtrack

[FuzzyGrim/Yamtrack](https://github.com/FuzzyGrim/Yamtrack). AGPL-3.0, 3,708★,
last commit 2026-10-18, release v0.26.3, read `2d82621` (branch `dev`). A
multi-media tracker. Its recommendations are TMDb's own per title
(`src/app/providers/tmdb.py` ~L162–240). `CustomList` has collaborators, and
there are per-user stats charts and a Plex webhook. It links to Letterboxd
through `letterboxd.com/tmdb/{id}`, the same no-lookup link movienight's
`ratings.py` uses, and imports nothing from it. Django, Celery and Redis, with
server-rendered htmx templates, the closest stack to movienight's.

**Verdict.** Adjacent.

### SuggestArr

[giuseppe99barchetta/SuggestArr](https://github.com/giuseppe99barchetta/SuggestArr).
MIT, 1,343★, last commit 2026-10-08, release v2.16.1, read `50f350b`.

**What it is.** It seeds from recent Plex, Jellyfin, Emby or Trakt watches,
pulls TMDb `/recommendations` for each seed (`tmdb_client.py:232`), drops what's
owned or watched, and requests the rest through Seerr. An optional LLM mode
gets a taste profile of loved, liked, disliked and uninterested buckets
(`suggestion_feedback_mixin.py`). Feedback values have explicit meanings:
`seen_disliked` and `too_similar` suppress a film, while `interested` adds +2.
Each user is kept strictly separate (`_process_seed_groups`,
`_feedback_owner_for`). Flask and Vue on SQLite or Postgres.

**Compared with movienight.** The candidates come from TMDb's similar-titles
graph, not a watchlist, and it has no pair. movienight's "not tonight" veto
already covers its suppression for a single night. A durable "not for us"
dismissal would be the new part.

**Verdict.** Adjacent.

### Seerr

[seerr-team/seerr](https://github.com/seerr-team/seerr), the merger of Overseerr
and Jellyseerr. MIT, 12,931★, release v3.5.0, read `63705622`. Included only as
the industrial version of the get-list. `server/entity/Watchlist.ts` is unique
on `(tmdbId, mediaType, requestedBy)`. `Media.status` runs UNKNOWN → PENDING →
PROCESSING → (PARTIALLY_)AVAILABLE. movienight's get-list is a bare row with
`added_at`, and the Plex arrival notice already detects AVAILABLE.

**Verdict.** Not a competitor.

## Ambient channels

### Nostalgex

[chadMueller/Nostalgex](https://github.com/chadMueller/Nostalgex). MIT, 117★,
the repo created 2026-09-28. It has 145 commits, and the latest tag is v1.0.24
(2026-10-09). It is a one-person project. The README says the app has been on
the App Store since 2026-04-19 **[claim]**. The commit read was `c2f32e6`,
checked 2026-10-12.

**What it is.** It turns a Plex, Jellyfin or Emby library into **131 retro
"cable" channels in 14 bundles** with a channel guide. Each channel is a rule
over library metadata: type, genres (include/exclude/requireAll), studio, year
range, content rating, duration range, watched/unwatched/rewatched, and a
time-of-day maturity gate. Rules live in a shared `channels.json`. Playback runs
on a fixed schedule computed from the clock, so tuning in joins mid-programme
like live TV. There are two clients:

- **A web tuner** (`plex-tuner.html`, vanilla JS, hosted on Vercel at
  nostalgex.app). It is served over HTTPS, so it reaches Plex through plex.direct
  certificates. A plain-`http://` LAN Jellyfin or Emby server is out of reach.
- **A tvOS app** (SwiftUI/AVPlayer, tvOS 18+). The App Store build sends
  TelemetryDeck usage counts.

It is a client only, with no server, so there is nothing to deploy
alongside movienight. A Roku has no client; it would mean the web tuner in a
browser or an Apple TV.

**Schedule logic, as read.** The README says each channel's pool is "shuffled
once per day (seeded by date + channel ID)" **[claim]**. In the web tuner's
`shuffleArray()` the seed is `Math.floor(Date.now() / 86400000)` alone. That is
the **UTC** day with no channel component, so the lineup turns over at UTC
midnight, which is 18:00 or 19:00 local, mid-evening. The README itself says the
tvOS schedule has diverged (premieres, sequel adjacency, a 6-hour refresh).

**Compared with movienight.** These are different answers to the same question.
movienight is two-person and intent-driven. It joins both Letterboxd watchlists
to Plex on TMDb id, scores films with `taste.py` on a pair score, and leads with
the best bet, with a spin to reroll. Nostalgex is ambient and choice-free: it
pays no attention to who wants what, only to library metadata and watch state.
Neither one covers the other.

**Borrowable.**

- **Rule-defined collections.** A small rule object (genres, year range, max
  runtime, unwatched) is a superset of `picker.Filters`. Named presets like
  "short comedy, unwatched, pre-2000" would be cheap on top of
  `_apply_filters`.
- **A stable "tonight's pick".** Nostalgex seeds its daily pick by UTC day.
  movienight gets the same effect without a seed: the Tonight hero is the
  top-ranked film, so reloads and both phones agree until a sync changes the
  data, and only an explicit spin rerolls.

**Verdict.** Not a competitor, and it has nothing to run.

## Borrowable, in short

These were checked against movienight's code on 2026-10-18. This is evidence,
not a plan: anything taken up is tracked as open work.

| Idea | From | Touches |
|---|---|---|
| Redistribute the director weight when a film has none (now capped at 70) | curatarr | `taste.py` `match()`, and `explain()` with it |
| IDF damping on genre affinity | curatarr | `taste.py` |
| Get-list as a Radarr list feed (Radarr polls it) | Movary | new route, `get_list` |
| Get-list → Radarr add / Plex watchlist add, on TMDb id | letterboxd-plex-sync | new admin action |
| Top pair picks as a Plex Home collection, for the TV | kino-swipe | `plex_sync.py` |
| "Drop the decade filter for 47 more" when filters leave few | decide | `picker._apply_filters`, Tonight template |
| `scan_complete` on the watchlist sync, so truncation is visible | letterboxd-jellyfin | `scripts/sync_letterboxd.py`, `app_meta` |
| TMDb collection gaps as get-list suggestions | Movie-Roulette | `dashboard.to_add()` |
| Named filter presets | Nostalgex | `picker.Filters` |

Already in movienight, so not borrowable: weighted spin, the "not tonight" veto,
a stable top pick, `letterboxd.com/tmdb/<id>` links, and the Plex arrival
notice.

## Method

Swept 2026-10-18 by three parallel readers, one each for group pickers,
Letterboxd tools, and trackers and recommenders. Each used `gh search repos`
with several queries per slice ("plex movie match", "plex swipe", "movie
roulette plex", "letterboxd watchlist compare", "letterboxd plex",
"letterboxd recommendations", "self-hosted movie tracker", "plex
recommendations") and web search after GitHub's search API rate-limited mid-sweep.
Each pick's metadata came from `gh api`.

Looked at and skipped:

- **Dead or upstream:** Akasiek/Random-Plex-Movie (2021, Movie-Roulette's
  upstream); Slgoetz/plex-letterboxd-sync (2023, scrapes page 1 only, exact-title
  match); fingerthief/recommendarr (404, owner has no public repos); sct/overseerr
  (archived into Seerr).
- **Toys or class projects:** rndchris/okies; the various MovieMatch and couple-
  recommendation repos (0–4★); other letterboxd-blend repos; Hawxkz,
  darianzhang0, samirsaliba, yongssh and ferkrodriguez98 watchlist pickers;
  Vanguarr; plex-recommendations-ai, PlexMind and other single-user LLM
  wrappers.
- **Off-topic:** GoodbyteCo/Letterboxd-Watchlist-Picker (random pick from one
  list); Choff3/letterboxd-sync and kristianfreeman/letterboxd-plex-watched-sync
  (covered by the two sync entries); CrossWatch (watch-state sync);
  Jellystat and Tracearr (server stats); navilg/media-stack (an *arr compose
  bundle); stremio-letterboxd-addon, letterboxd-rss and the JustWatch tools.
- **No source:** watchlistmatch.com (hosted, ranks by average rating).
