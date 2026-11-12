# Deploying Movie Night

One container, port 8000, a `data/` directory for the SQLite database and your
Letterboxd exports. Run it on the same network as your Plex server.

## 1. Configure

```bash
cp .env.example .env
```

Every setting is explained in `.env.example`. The ones to fill in:

- **`PLEX_BASEURL`** — your Plex host's **LAN address**, e.g.
  `http://192.168.0.10:32400`. Not `localhost`: inside the container that is
  the container itself.
- **`PLEX_TOKEN`** — see below. **`PLEX_MOVIE_LIBRARY`** — the exact name of
  your movie library (`Movies`, `Films`, …).
- **`TMDB_API_KEY`** — a free v3 key from <https://www.themoviedb.org/settings/api>.
- **`USER_A` / `USER_B`** — your two names, as they'll appear in the app and in
  the export filenames.
- **`LOCAL_TZ`** — your timezone (`Europe/London`, …). *Not tonight* lasts until
  6 a.m. here.
- **`ADMIN_USER` / `ADMIN_PASSWORD`** — the login for `/admin`. Leave either
  blank and `/admin` answers 503: locked, never open.

### Your Plex token

On the Plex server's host, the token is in Plex's `Preferences.xml`:

```bash
sudo find / -name Preferences.xml -path '*Plex Media Server*' 2>/dev/null
sudo grep -oP 'PlexOnlineToken="\K[^"]+' "<the path from above>"
```

Or in Plex Web: open any movie → **⋯ → Get Info → View XML**, and copy the
value after `X-Plex-Token=` in the address bar.

### The admin login

`scripts/set-admin-password.sh` changes it later: it asks for a username
(Enter keeps the current one) and a password (Enter generates one), rewrites
only those two lines of `.env`, recreates the container and checks the new
login works. Add `--remote user@host:/path/to/movienight` to do it to an
install over ssh, and `--url http://host:8000` to say where to check.

## 2. Start it

```bash
docker compose up -d --build
docker compose ps                      # movienight should be "running (healthy)"
curl -s http://<host>:8000/healthz     # {"status":"ok","database":"ok",...}
```

`(healthy)` comes from the image's healthcheck, which probes `/healthz`. That
opens the database, so it goes red if the `data/` mount is lost; `/` doesn't,
and answers 200 through exactly that failure. **Point an uptime monitor at
`/healthz`, not `/`.**

`app/` and `scripts/` are bind-mounted over the image, so after a `git pull` a
code change needs only `docker compose restart`; a change to `Dockerfile` or
`requirements.txt` needs `docker compose up -d --build`.

## 3. First import

1. Each of you exports your Letterboxd data (Settings → Data → *Export Your
   Data*) and drops the ZIP in `data/imports/`, named `<USER_A>-anything.zip`
   / `<USER_B>-anything.zip` — the prefix routes it to that person.
2. On **Admin**, run in order: **Sync Plex** (your library, mapped to TMDb ids),
   **Import Letterboxd**, **Enrich** (posters, genres, directors, cast).

Each job runs in the background — leave the page and come back. If Plex is busy
scanning, a sync can take a minute or two. Or from the CLI:

```bash
docker compose exec movienight python -m scripts.run_ingest all
```

Re-import a fresh export any time; it overwrites cleanly. Films that don't
match land in Admin's review list: pin a TMDb id, then re-import.

## 4. Keep it fresh (optional)

`scripts/letterboxd-sync.sh` runs `scripts/sync_letterboxd.py` as a one-shot
container against the same `data/` and `.env`, logging to `data/sync.log`. Set
`LETTERBOXD_A` / `LETTERBOXD_B` to your Letterboxd usernames first.

- **`rss`** — your recent diary watches and ratings, from the feed Letterboxd
  publishes. The feed carries TMDb ids, so these match exactly. Only recent
  entries: older ones come from the export.
- **`watchlist`** — your full watchlist, read from your public watchlist pages.
  **Off by default** (`LETTERBOXD_WATCHLIST_SCRAPE=0`), because Letterboxd's
  terms prohibit automated access; off, it exits 1 without a request, and
  your watchlist comes from the export's `watchlist.csv`. Turning it on is your
  decision. A run that can't read every page changes nothing.
- **`all`** — both; RSS only while the watchlist scrape is off.

Schedule it from the Docker host's crontab, as a user that can run docker:

```
30 3 * * *  /path/to/movienight/scripts/letterboxd-sync.sh rss
0  4 * * 0  /path/to/movienight/scripts/letterboxd-sync.sh watchlist
```

`SYNC_NETWORK` in `.env` is passed to `docker run --network` (`host`, or
`container:<name>` to share another container's network); unset, the run uses
Docker's default network. `SYNC_IMAGE` overrides the image name, which defaults
to the one `docker compose build` gives it (`<checkout dir>-movienight`). The
wrapper never overlaps itself, and kills a run after 45 minutes.

## Logs

The app logs one JSON object per line. Every request line carries `path`,
`status`, `duration_ms` and a `request_id`, which is also sent to the browser as
`X-Request-ID`, so a failed page can be traced from the network tab.

```bash
docker compose logs --no-log-prefix --since 1h | jq -c 'select(.level=="ERROR")'
docker compose logs --no-log-prefix | jq -c 'select(.duration_ms > 500) | {path, duration_ms}'
```

`--no-log-prefix` matters: without it compose prepends the service name and
`jq` sees no JSON. `LOG_FORMAT=text` gives readable lines when running scripts
by hand. Keys never reach the logs: every line is redacted.

## Backups

Everything worth keeping is `data/watch.db`. It runs in WAL mode, so copy it
with SQLite's backup API, never `cp` while the app is running:

```bash
docker compose exec movienight python -c \
  "import sqlite3; sqlite3.connect('/app/data/watch.db').backup(sqlite3.connect('/app/data/watch-backup.db'))"
```

## Off the home network

Every page answers anyone who can reach the port, and HTTP Basic sends the
admin password in plain text. Keep it on your LAN or a private overlay network
(Tailscale, WireGuard), or put a reverse proxy with TLS — and its own auth, if
the reads matter to you — in front. [SECURITY.md](../SECURITY.md) has the
supported setups.
