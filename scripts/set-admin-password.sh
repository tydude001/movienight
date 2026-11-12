#!/usr/bin/env bash
# Change the /admin login (username and/or password), recreate the app, and
# check the new login works.
#
#   scripts/set-admin-password.sh                                  # ./.env, local docker
#   scripts/set-admin-password.sh --remote user@host:/path/to/clone [--url http://host:8000]
#
# It asks two questions:
#   Username — Enter keeps the current one.
#   Password — Enter generates a random 24-character one; or type your own.
#
# The values travel over stdin, never as arguments, so they don't show up in
# `ps` on either end. Only the ADMIN_USER / ADMIN_PASSWORD lines of `.env` are
# rewritten (in place, so its owner and mode survive); the container is then
# recreated, because env_file is read at container start, not per request.
# The new login is printed at the end — put it in your password manager.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
USAGE="usage: $0 [--remote user@host:/path/to/clone] [--url http://host:8000]"
HOST=""
PROJECT="$REPO"
BASE=http://localhost:8000

while [[ $# -gt 0 ]]; do
    case "$1" in
        --remote)
            [[ "${2:-}" == ?*:?* ]] || { echo "$USAGE" >&2; exit 2; }
            HOST="${2%%:*}"; PROJECT="${2#*:}"; shift 2 ;;
        --url)
            [[ -n "${2:-}" ]] || { echo "$USAGE" >&2; exit 2; }
            BASE="${2%/}"; shift 2 ;;
        --prompt) shift ;;  # the old way to type a password; it's the default now
        *) echo "$USAGE" >&2; exit 2 ;;
    esac
done
URL="$BASE/admin"

# Run a shell snippet in the project dir: over ssh for --remote, else here.
# The snippet reads its own stdin, so the credentials can be piped to it.
in_project() {
    if [[ -n "$HOST" ]]; then
        # shellcheck disable=SC2029  # $PROJECT is meant to expand here
        ssh "$HOST" "export PATH=\$PATH:/usr/local/bin; cd '$PROJECT' && $1"
    else
        (cd "$PROJECT" && bash -c "$1")
    fi
}

in_project "test -f .env" < /dev/null || {
    echo "No .env in ${HOST:+$HOST:}$PROJECT — copy .env.example to .env first." >&2; exit 1; }
current=$(in_project "sed -n 's/^ADMIN_USER=//p' .env" < /dev/null | tr -d "'\"")

read -rp "Username [${current:-none set}]: " user
user=${user:-$current}
[[ -n "$user" ]] || { echo "Username can't be empty." >&2; exit 1; }
# A colon would split the HTTP Basic credential; keep it to plain characters.
[[ "$user" =~ ^[A-Za-z0-9._-]+$ ]] || {
    echo "Username can only use letters, digits, '.', '_' and '-'." >&2; exit 1; }

IFS= read -rsp "Password (Enter to generate one): " pw; echo
if [[ -n "$pw" ]]; then
    IFS= read -rsp "Again: " pw2; echo
    [[ "$pw" == "$pw2" ]] || { echo "Passwords don't match." >&2; exit 1; }
    # Written single-quoted below, which compose reads literally: $, #, spaces,
    # backslashes and double quotes all survive. Only ' itself would end it early.
    [[ "$pw" != *"'"* ]] || { echo "Password can't contain a single quote (')." >&2; exit 1; }
else
    # A finite read, not `tr </dev/urandom | head -c 24`: head's early exit
    # kills tr with SIGPIPE, pipefail makes that the assignment's status, and
    # set -e then quits with 141 before anything runs. 4096 bytes leave ~990
    # alphanumerics to cut 24 from.
    pw=$(head -c 4096 /dev/urandom | LC_ALL=C tr -dc 'A-Za-z0-9')
    pw=${pw:0:24}
fi

echo "Updating .env in ${HOST:+$HOST:}$PROJECT and recreating the app..."
# shellcheck disable=SC2016  # expanded where the snippet runs, not here
printf "ADMIN_USER=%s\nADMIN_PASSWORD='%s'\n" "$user" "$pw" | in_project '
    set -e
    IFS= read -r USERLINE; export USERLINE
    IFS= read -r PWLINE; export PWLINE
    tmp=$(mktemp .env.XXXXXX)
    awk '\''/^ADMIN_USER=/     { print ENVIRON["USERLINE"]; u = 1; next }
         /^ADMIN_PASSWORD=/ { print ENVIRON["PWLINE"];   p = 1; next }
         { print }
         END { if (!u) print ENVIRON["USERLINE"]; if (!p) print ENVIRON["PWLINE"] }'\'' .env > "$tmp"
    cat "$tmp" > .env && rm -f "$tmp"
    docker compose up -d --force-recreate --no-build >&2
'

# curl's config syntax treats \ and " as escapes inside a quoted value.
cpw=${pw//\\/\\\\}; cpw=${cpw//\"/\\\"}
echo -n "Waiting for the app to accept the new login"
for _ in $(seq 30); do
    code=$(printf 'user = "%s:%s"\n' "$user" "$cpw" |
           curl -s -o /dev/null -w '%{http_code}' --config - "$URL" || true)
    [[ "$code" == 200 ]] && break
    echo -n .; sleep 2
done
echo

if [[ "$code" == 200 ]]; then
    echo "Done. Log in at $URL with:"
    echo
    echo "    username: $user"
    echo "    password: $pw"
    echo
    echo "Put it in your password manager now — the .env holds the only other copy."
else
    echo "The .env was updated, but $URL answered $code instead of 200." >&2
    echo "Username: $user   Password: $pw" >&2
    echo "Check: docker compose logs --tail 50 (in ${HOST:+$HOST:}$PROJECT)" >&2
    exit 1
fi
