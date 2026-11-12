"""Automated Letterboxd sync — RSS (recent activity) + watchlist scrape.

Run on a schedule by ``scripts/letterboxd-sync.sh`` as a one-shot container on
the network ``SYNC_NETWORK`` names. It writes straight to the shared SQLite DB —
no need to reach Plex.

Two jobs:
  * rss        — recent diary watches + ratings (and the `diary` table). Letterboxd RSS embeds the
                 TMDb id (<tmdb:movieId>), so no resolution guesswork. Nightly.
  * watchlist  — the full watchlist, which RSS does NOT carry. Scrapes the
                 public watchlist pages for film slugs, then resolves each slug
                 to a TMDb id via that film's page (exact), cached by slug so
                 subsequent runs only fetch newly-added films. Weekly.

Usage (inside the image):
    python -m scripts.sync_letterboxd rss
    python -m scripts.sync_letterboxd watchlist
    python -m scripts.sync_letterboxd all
    python -m scripts.sync_letterboxd all --no-enrich
"""
from __future__ import annotations

import logging
import re
import sys
import time
import xml.etree.ElementTree as ET

import httpx

from app import db, enrich, logs
from app.config import get_settings

log = logging.getLogger("scripts.sync_letterboxd")

UA = "Mozilla/5.0 (compatible; MovieNight/1.0; +self-hosted personal use)"
TMDB_LINK_RE = re.compile(r"themoviedb\.org/movie/(\d+)")
SLUG_RE = re.compile(r'data-(?:film|item)-slug="([^"]+)"')
OG_TITLE_RE = re.compile(r'<meta property="og:title" content="([^"]*?)"')
PAGE_LIMIT = 60          # hard stop on watchlist pagination
FILM_DELAY = 0.6         # polite delay between film-page fetches
PAGE_DELAY = 0.8         # polite delay between watchlist pages


# ── pure parsers (unit-testable, no network) ────────────────────────

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(item, name: str):
    for ch in item:
        if _local(ch.tag) == name:
            return (ch.text or "").strip() or None
    return None


def parse_rss(xml_bytes: bytes) -> list[dict]:
    """RSS XML -> list of {tmdb_id, title, year, rating, watched_date, rewatch}."""
    root = ET.fromstring(xml_bytes)
    out = []
    for el in root.iter():
        if _local(el.tag) != "item":
            continue
        tmdb = _child_text(el, "movieId")
        title = _child_text(el, "filmTitle")
        if not tmdb or not title or not tmdb.isdigit():
            continue
        year = _child_text(el, "filmYear")
        rating = _child_text(el, "memberRating")
        try:
            rating_val = float(rating) if rating else None
        except ValueError:
            rating_val = None
        out.append({
            "tmdb_id": int(tmdb),
            "title": title,
            "year": int(year) if year and year.isdigit() else None,
            "rating": rating_val,
            "watched_date": _child_text(el, "watchedDate"),
            "rewatch": (_child_text(el, "rewatch") or "").lower() == "yes",
        })
    return out


def parse_watchlist_slugs(html: str) -> list[str]:
    """Extract ordered, de-duplicated film slugs from a watchlist page."""
    seen, slugs = set(), []
    for s in SLUG_RE.findall(html):
        if s not in seen:
            seen.add(s)
            slugs.append(s)
    return slugs


def parse_film_page(html: str) -> dict | None:
    """Film page HTML -> {tmdb_id, title, year} via the embedded TMDb link."""
    m = TMDB_LINK_RE.search(html)
    if not m:
        return None
    tmdb_id = int(m.group(1))
    title, year = None, None
    om = OG_TITLE_RE.search(html)
    if om:
        raw = om.group(1)
        ym = re.search(r"\((\d{4})\)\s*$", raw)
        if ym:
            year = int(ym.group(1))
            title = raw[: ym.start()].strip()
        else:
            title = raw.strip()
    return {"tmdb_id": tmdb_id, "title": title, "year": year}


# ── network jobs ────────────────────────────────────────────────────

def _client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": UA}, timeout=30.0,
                        follow_redirects=True)


def sync_rss(user: str, username: str, client: httpx.Client) -> dict:
    url = f"https://letterboxd.com/{username}/rss/"
    resp = client.get(url)
    resp.raise_for_status()
    records = parse_rss(resp.content)

    rated = watched = 0
    with db.cursor() as cur:
        for r in records:
            db.upsert_film(cur, r["tmdb_id"], r["title"], r["year"])
            if r["watched_date"]:
                db.upsert_watched(cur, user, r["tmdb_id"], r["watched_date"])
                # Every RSS item with a watch date is a diary entry, so it is
                # also one watch for Wrapped (app/wrapped.py).
                db.upsert_diary(cur, user, r["tmdb_id"], r["watched_date"],
                                r["rating"], r["rewatch"])
                watched += 1
            if r["rating"] is not None:
                db.upsert_rating(cur, user, r["tmdb_id"], r["rating"], r["watched_date"])
                rated += 1
    return {"user": user, "rss_items": len(records), "watched": watched, "rated": rated}


def _slug_to_tmdb(slug: str, client: httpx.Client, conn) -> dict | None:
    """Resolve a film slug to {tmdb_id,title,year}, cached by slug."""
    key = f"slug:{slug}"
    row = conn.execute(
        "SELECT tmdb_id FROM resolution_cache WHERE cache_key = ?", (key,)
    ).fetchone()
    if row is not None:
        if row[0] is None:
            # Known miss (page had no TMDb link) — don't re-scrape every run;
            # hand-fix via the admin resolve form if it ever matters.
            return None
        # Cached hit; we still need a title/year for upsert — pull from films.
        f = conn.execute(
            "SELECT title, year FROM films WHERE tmdb_id = ?", (row[0],)
        ).fetchone()
        return {"tmdb_id": row[0],
                "title": f["title"] if f else slug, "year": f["year"] if f else None}

    resp = client.get(f"https://letterboxd.com/film/{slug}/")
    if resp.status_code != 200:
        return None
    meta = parse_film_page(resp.text)
    conn.execute(
        """
        INSERT INTO resolution_cache (cache_key, tmdb_id, source)
        VALUES (?, ?, 'letterboxd_scrape')
        ON CONFLICT(cache_key) DO UPDATE SET tmdb_id = excluded.tmdb_id,
            source = excluded.source, resolved_at = datetime('now')
        """,
        (key, meta["tmdb_id"] if meta else None),
    )
    conn.commit()
    time.sleep(FILM_DELAY)
    return meta


def sync_watchlist(user: str, username: str, client: httpx.Client) -> dict:
    # 1. Collect every slug across the paginated watchlist. Only an empty page
    # proves we reached the end: a non-200 (rate limit, Cloudflare) or running
    # out of PAGE_LIMIT means the pull is truncated, and step 2 replaces the
    # user's whole watchlist — so a truncated pull must not reach it.
    slugs: list[str] = []
    scan_complete = False
    pages = 0
    for page in range(1, PAGE_LIMIT + 1):
        url = f"https://letterboxd.com/{username}/watchlist/page/{page}/"
        resp = client.get(url)
        if resp.status_code != 200:
            log.warning("watchlist page failed", extra={
                "user": user, "page": page, "status": resp.status_code})
            break
        page_slugs = parse_watchlist_slugs(resp.text)
        if not page_slugs:
            scan_complete = True
            break
        pages = page
        slugs.extend(page_slugs)
        time.sleep(PAGE_DELAY)
    # De-dupe preserving order.
    slugs = list(dict.fromkeys(slugs))
    if not scan_complete:
        return {"user": user, "scan_complete": False, "pages": pages,
                "watchlist_slugs": len(slugs), "matched": 0, "unmatched": 0}

    # 2. Resolve slugs -> tmdb ids (cached), then replace the user's watchlist.
    conn = db.connect()
    matched = unmatched = 0
    try:
        resolved: list[dict] = []
        for slug in slugs:
            meta = _slug_to_tmdb(slug, client, conn)
            if meta and meta.get("tmdb_id"):
                resolved.append(meta)
            else:
                unmatched += 1

        cur = conn.cursor()
        db.clear_user_watchlist(cur, user)
        for meta in resolved:
            db.upsert_film(cur, meta["tmdb_id"], meta.get("title") or "", meta.get("year"))
            db.upsert_watchlist(cur, user, meta["tmdb_id"])
            matched += 1
        conn.commit()
    finally:
        conn.close()
    return {"user": user, "scan_complete": True, "pages": pages,
            "watchlist_slugs": len(slugs), "matched": matched, "unmatched": unmatched}


# ── driver ──────────────────────────────────────────────────────────

def run(mode: str, do_enrich: bool = True) -> int:
    s = get_settings()
    # This job runs unattended on a schedule, so its whole account of itself is
    # the log — the per-user results below are `extra` fields rather than a
    # printed dict, and a user that fails keeps its traceback.
    logs.configure(s.log_level, s.log_format)
    lb = s.letterboxd_map
    if not lb:
        log.error("no Letterboxd usernames configured (LETTERBOXD_A/B)")
        return 1

    parts = [p for p in ("rss", "watchlist") if mode in (p, "all")]
    if "watchlist" in parts and not s.letterboxd_watchlist_scrape:
        # Opt-in (app/config.py). Asked for by name, refusing is an error the
        # cron log shows; as part of `all`, the RSS half still runs.
        if mode == "watchlist":
            log.error("watchlist scrape is off (LETTERBOXD_WATCHLIST_SCRAPE=0); "
                      "import the export ZIP's watchlist.csv instead")
            return 1
        log.info("watchlist scrape is off (LETTERBOXD_WATCHLIST_SCRAPE=0); rss only")
        parts.remove("watchlist")

    db.init_db()
    jobs = {"rss": sync_rss, "watchlist": sync_watchlist}
    # A part is stamped only when it succeeded for every user: the footer's
    # "synced at" must not vouch for a run that failed or was truncated.
    ok = dict.fromkeys(parts, True)
    client = _client()
    try:
        for user, username in lb.items():
            for part in parts:
                try:  # one user or part failing shouldn't abort the others
                    result = jobs[part](user, username, client)
                except Exception:
                    log.exception("sync failed", extra={
                        "user": user, "letterboxd": username, "part": part})
                    ok[part] = False
                    continue
                if result.get("scan_complete", True):
                    log.info(f"{part} sync", extra=logs.safe_extra(result))
                else:
                    log.warning(f"{part} sync incomplete, existing watchlist kept",
                                extra=logs.safe_extra(result))
                    ok[part] = False
        with db.cursor() as cur:
            for part in parts:
                if ok[part]:
                    db.stamp(cur, f"letterboxd_{part}_at")
    finally:
        client.close()

    if do_enrich:
        # Fill genre/director/runtime/poster for anything newly added.
        log.info("enrich", extra=logs.safe_extra(enrich.enrich_tmdb()))
    return 0 if all(ok.values()) else 1


def main(argv: list[str]) -> int:
    mode = argv[0] if argv else "all"
    if mode not in ("rss", "watchlist", "all"):
        print(__doc__)
        return 1
    do_enrich = "--no-enrich" not in argv
    return run(mode, do_enrich=do_enrich)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
