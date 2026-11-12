#!/usr/bin/env bash
# Letterboxd sync as a one-shot container, for a cron line on the Docker host.
#
#   letterboxd-sync.sh rss        # nightly: recent watches + ratings
#   letterboxd-sync.sh watchlist  # weekly, only with LETTERBOXD_WATCHLIST_SCRAPE=1
#   letterboxd-sync.sh all
#
# Runs a fresh container from the app's image against the same data/ and .env,
# so it writes to the same SQLite DB as the running app. Run it as a user that
# can use docker (root's crontab, typically).
#
# Settings, from the environment or else from this checkout's .env:
#   SYNC_NETWORK  passed to `docker run --network` when set (e.g. `host`, or
#                 `container:<name>` to share another container's network).
#                 Unset, the container gets Docker's default bridge network.
#   SYNC_IMAGE    the image to run; default `<checkout dir>-movienight`, the
#                 name `docker compose build` gives it.
#
# Safeguards:
#   * flock  — never overlaps a previous run that's still going (skips instead).
#   * timeout — a hung run is killed after MAX_MINUTES; it can't run forever.
#   * named container + pre-clean — no orphaned containers pile up.
set -uo pipefail

PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
NAME=movienight-sync
MAX_MINUTES=45
MODE="${1:-all}"
LOG="$PROJECT/data/sync.log"

# A value from the environment wins; otherwise the .env line, unquoted.
from_env_file() {
  sed -n "s/^$1=//p" "$PROJECT/.env" 2>/dev/null | tail -n 1 | tr -d "'\""
}
SYNC_NETWORK="${SYNC_NETWORK:-$(from_env_file SYNC_NETWORK)}"
SYNC_IMAGE="${SYNC_IMAGE:-$(from_env_file SYNC_IMAGE)}"
SYNC_IMAGE="${SYNC_IMAGE:-$(basename "$PROJECT" | tr '[:upper:]' '[:lower:]')-movienight}"

mkdir -p "$PROJECT/data"

# Don't let two syncs run at once (e.g. a slow watchlist still going at the
# next nightly RSS). If locked, skip this run cleanly.
exec 9>"$PROJECT/data/.sync.lock"
if ! flock -n 9; then
  echo "[$(date '+%F %T')] another sync still running — skipping: $MODE" >> "$LOG"
  exit 0
fi

# Clear any orphan from a previously killed run.
docker rm -f "$NAME" >/dev/null 2>&1 || true

echo "[$(date '+%F %T')] sync start: $MODE" >> "$LOG"

network=()
[ -n "$SYNC_NETWORK" ] && network=(--network "$SYNC_NETWORK")

timeout "${MAX_MINUTES}m" docker run --rm --name "$NAME" \
  "${network[@]}" \
  --env-file "$PROJECT/.env" \
  -v "$PROJECT/data:/app/data:z" \
  -v "$PROJECT/app:/app/app:ro,z" \
  -v "$PROJECT/scripts:/app/scripts:ro,z" \
  "$SYNC_IMAGE" \
  python -m scripts.sync_letterboxd "$MODE" >> "$LOG" 2>&1
rc=$?

if [ "$rc" -eq 124 ]; then
  echo "[$(date '+%F %T')] TIMEOUT: $MODE killed after ${MAX_MINUTES}m" >> "$LOG"
  docker rm -f "$NAME" >/dev/null 2>&1 || true
else
  echo "[$(date '+%F %T')] sync done ($rc): $MODE" >> "$LOG"
fi
exit "$rc"
