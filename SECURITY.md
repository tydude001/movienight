# Security

## Reporting a vulnerability

Please report security problems privately, through GitHub's private
vulnerability reporting: the repository's **Security** tab, then **Report a
vulnerability**. Don't open a public issue for them.

## The supported setup

Movie Night is built for a home network. Run it on a LAN, on a private
overlay network (Tailscale, WireGuard), or behind a reverse proxy that adds
TLS. **Most of the app is open by design**, because it is for the people in
the house:

- every page, the JSON API, the Radarr feed and Wrapped answer anyone who can
  reach the port;
- the household's own small writes are open too: *Not tonight*, marking a film
  watched, adding to the get-list;
- opening a film's detail card fetches its external scores (OMDb, TMDb) on a
  cache miss: only for films already in the database, at most once per film
  per `RATINGS_TTL_HOURS` (an hour when the last lookup came back empty).

Only `/admin` — the Plex sync, imports, enrichment and match fixes — needs the
login, and HTTP Basic sends it in plain text unless something in front of the
app adds TLS. Putting the bare port on the internet is outside what the app
defends against.

## What counts

- **An admin action without the login.** Every `/admin` route needs it and
  fails closed with 503 while none is set; `test_admin_auth.py` sweeps the
  route table. A way around that is a vulnerability.
- **Spending a key past the cache.** A way to make the open routes call OMDb
  or TMDb for a film that isn't in the database, or more often than the cache
  allows.
- **A path outside `data/`.** Imports read only ZIPs from `IMPORT_DIR`; a
  request or an export file that makes the app read or write elsewhere is a
  vulnerability.
- **A ZIP that does more than unzip** — runs code, or exhausts the host.
- **A key leaking.** A way for the app to log, render or send an API key or
  the Plex token anywhere but the service it belongs to.

Not in scope: anything that needs an attacker to already run code as you, a
deployment that exposes the port to the internet without TLS, the open
reads and household writes above, and the third-party services
themselves.
