"""Wrapped: the diary is the only source, and its three counting rules hold.

No network: the export import resolves through a stubbed TMDb client, and the
cast backfill gets its credits from a stub too.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import zipfile

import pytest
from starlette.testclient import TestClient

from app import db, enrich, letterboxd, tmdb, wrapped
from app.main import app
from scripts import sync_letterboxd as sl

from .helpers import add_film

TODAY = dt.date(2026, 11, 5)


def add_diary(user: str, tmdb_id: int, date: str, rating: float | None = None,
              rewatch: bool = False) -> None:
    with db.cursor() as cur:
        db.upsert_diary(cur, user, tmdb_id, date, rating, rewatch)


def add_cast(tmdb_id: int, *names: str) -> None:
    with db.cursor() as cur:
        db.set_cast(cur, tmdb_id, list(names))


def rows(sql: str, *args) -> list[tuple]:
    conn = db.connect()
    try:
        return [tuple(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


# ── export import ───────────────────────────────────────────────────

IDS = {"Alpha": 1, "Beta": 2, "Gamma": 3}


@pytest.fixture
def stub_resolver(monkeypatch):
    monkeypatch.setattr(tmdb.TMDbClient, "resolve", lambda self, title, year: IDS.get(title))


def _zip(path, diary: list[str], watched: list[str] | None = None):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("diary.csv", "Date,Name,Year,Letterboxd URI,Rating,Rewatch,Tags,Watched Date\n"
                    + "\n".join(diary) + "\n")
        if watched is not None:
            zf.writestr("watched.csv", "Date,Name,Year,Letterboxd URI\n" + "\n".join(watched) + "\n")
    return path


def test_diary_import_uses_the_watched_date_not_the_logged_date(tmp_path, stub_resolver):
    zp = _zip(tmp_path / "alice-x.zip",
              ["2026-01-03,Alpha,2000,u,4,,,2026-01-02",
               "2026-01-03,Beta,2000,u,,Yes,,2026-01-02"],
              # Gamma is ticked watched with no diary entry: a catch-up, not a watch.
              watched=["2026-01-30,Gamma,2000,u", "2026-01-03,Alpha,2000,u"])
    summary = letterboxd.import_letterboxd("alice", zp)
    assert summary["diary"] == 2
    assert rows("SELECT tmdb_id, watched_date, rating, rewatch FROM diary ORDER BY tmdb_id") == [
        (1, "2026-01-02", 4.0, 0), (2, "2026-01-02", None, 1)]


def test_reimport_replaces_everything_up_to_its_last_date_and_keeps_newer_rss_rows(tmp_path, stub_resolver):
    letterboxd.import_letterboxd("alice", _zip(tmp_path / "a1.zip", [
        "2026-01-03,Alpha,2000,u,4,,,2026-01-02", "2026-02-02,Beta,2000,u,3,,,2026-02-01"]))
    add_film(3)
    add_diary("alice", 3, "2026-07-01")          # from RSS, after the export ends
    # Alpha was re-dated on Letterboxd; the old date must not linger as a second watch.
    letterboxd.import_letterboxd("alice", _zip(tmp_path / "a2.zip", [
        "2026-01-06,Alpha,2000,u,4,,,2026-01-05", "2026-02-02,Beta,2000,u,3,,,2026-02-01"]))
    assert rows("SELECT tmdb_id, watched_date FROM diary ORDER BY watched_date") == [
        (1, "2026-01-05"), (2, "2026-02-01"), (3, "2026-07-01")]


def test_rss_item_records_a_diary_watch_with_its_rewatch_flag():
    xml = b"""<rss xmlns:letterboxd="https://letterboxd.com" xmlns:tmdb="https://themoviedb.org">
      <channel><item>
        <letterboxd:watchedDate>2026-03-01</letterboxd:watchedDate>
        <letterboxd:rewatch>Yes</letterboxd:rewatch>
        <letterboxd:filmTitle>Alpha</letterboxd:filmTitle>
        <letterboxd:filmYear>2000</letterboxd:filmYear>
        <letterboxd:memberRating>3.5</letterboxd:memberRating>
        <tmdb:movieId>1</tmdb:movieId>
      </item></channel></rss>"""
    [item] = sl.parse_rss(xml)
    assert item["rewatch"] is True and item["watched_date"] == "2026-03-01"


# ── counting rules ──────────────────────────────────────────────────

def test_together_is_same_film_same_date_only():
    for t in (1, 2):
        add_film(t, runtime=100)
    add_diary("alice", 1, "2026-03-01", 4)
    add_diary("bob", 1, "2026-03-01", 3)
    add_diary("alice", 2, "2026-03-02", 4)
    add_diary("bob", 2, "2026-03-03", 4)          # a day apart: two solo watches
    w = wrapped.compute(2026, today=TODAY)
    assert w["together"]["films"] == 1
    assert w["people"]["alice"]["solo"] == 1 and w["people"]["bob"]["solo"] == 1
    assert w["together"]["hours"] == round(100 / 60)


def test_catchup_day_is_dropped_and_reported():
    for t in range(1, 8):
        add_film(t, runtime=90)
    for t in range(1, 1 + wrapped.CATCHUP_MIN):
        add_diary("bob", t, "2026-01-30")
    add_diary("bob", 7, "2026-02-01")
    w = wrapped.compute(2026, today=TODAY)
    assert w["people"]["bob"]["watches"] == 1
    assert w["excluded"] == [{"user": "bob", "date": "2026-01-30", "entries": wrapped.CATCHUP_MIN}]


def test_one_short_of_a_catchup_still_counts():
    for t in range(1, wrapped.CATCHUP_MIN):
        add_film(t)
        add_diary("bob", t, "2026-01-30")
    assert wrapped.compute(2026, today=TODAY)["people"]["bob"]["watches"] == wrapped.CATCHUP_MIN - 1


def test_a_rewatch_counts_every_time():
    add_film(1, runtime=60)
    add_diary("alice", 1, "2026-01-01")
    add_diary("alice", 1, "2026-05-01", rewatch=True)
    p = wrapped.compute(2026, today=TODAY)["people"]["alice"]
    assert (p["watches"], p["films"], p["rewatches"], p["hours"]) == (2, 1, 1, 2)


def test_other_years_stay_out():
    add_film(1)
    add_diary("alice", 1, "2025-12-31")
    assert wrapped.compute(2026, today=TODAY) is None
    assert wrapped.years() == [2025]


def test_clash_and_agreement():
    for t, (a, b) in enumerate([(4.5, 2.5), (3, 3), (4, 3.5)], start=1):
        add_film(t, title=f"F{t}")
        add_diary("alice", t, f"2026-04-0{t}", a)
        add_diary("bob", t, f"2026-04-0{t}", b)
    t = wrapped.compute(2026, today=TODAY)["together"]
    assert (t["rated"], t["exact"], t["within_half"]) == (3, 1, 2)
    assert t["clash"]["title"] == "F1" and t["clash"]["gap"] == 2
    assert t["clash"]["others_within_one"] is True
    assert [f["title"] for f in t["favourites"]] == ["F3", "F1", "F2"]


def test_actor_needs_enough_nights_and_relaxes_only_when_none_qualify():
    for t in range(1, 5):
        add_film(t)
        add_diary("alice", t, f"2026-05-0{t}", 4)
        add_diary("bob", t, f"2026-05-0{t}", 4)
    add_cast(1, "Often", "Twice")
    add_cast(2, "Often", "Twice")
    add_cast(3, "Often")
    add_cast(4, "Once")
    # Twice averages the same as Often, but only Often reaches three nights.
    actor = wrapped.compute(2026, today=TODAY)["together"]["actor"]
    assert actor["name"] == "Often" and actor["min_nights"] == wrapped.ACTOR_MIN_NIGHTS


def test_venn_overlap_is_drawn_to_scale():
    v = wrapped.venn(130, 217, 122)
    ra, rb, d = v["ar"], v["br"], v["bx"] - v["ax"]
    # Recompute the lens area from the geometry and compare in the same units.
    lens = (ra * ra * math.acos((d * d + ra * ra - rb * rb) / (2 * d * ra))
            + rb * rb * math.acos((d * d + rb * rb - ra * ra) / (2 * d * rb))
            - 0.5 * math.sqrt((-d + ra + rb) * (d + ra - rb) * (d - ra + rb) * (d + ra + rb)))
    assert lens / (math.pi * rb * rb) == pytest.approx(122 / 217, abs=0.02)
    assert ra / rb == pytest.approx(math.sqrt(130 / 217), abs=0.01)
    apart = wrapped.venn(10, 10, 0)
    assert apart["bx"] - apart["ax"] >= apart["ar"] + apart["br"] - 0.2
    assert wrapped.venn(0, 10, 0) is None


# ── cast ────────────────────────────────────────────────────────────

def test_top_cast_keeps_billing_order():
    credits = {"cast": [{"name": f"P{i}", "order": i} for i in (7, 0, 3, 1, 2, 4, 5, 6)]}
    assert tmdb._top_cast(credits) == ["P0", "P1", "P2", "P3", "P4", "P5"]


def test_cast_backfill_only_touches_diary_films(monkeypatch):
    add_film(1)
    add_film(2)                                  # on a watchlist, never in a diary
    add_diary("alice", 1, "2026-01-01")
    asked = []
    monkeypatch.setattr(tmdb.TMDbClient, "cast", lambda self, i: asked.append(i) or ["Lead"])
    result = enrich.enrich_tmdb()
    assert asked == [1] and result["cast"] == 1
    assert rows("SELECT tmdb_id, names FROM film_cast") == [(1, json.dumps(["Lead"]))]
    enrich.enrich_tmdb()
    assert asked == [1]                          # fetched once, never again


# ── page ────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_page_renders_every_card(client):
    for t, (a, b) in enumerate([(4.5, 2.5), (4, 4), (3, 3.5)], start=1):
        add_film(t, title=f"F{t}", genres="Comedy, Horror", director="Dee", runtime=95)
        add_cast(t, "Lead", "Second")
        add_diary("alice", t, f"2026-03-0{t}", a)
        add_diary("bob", t, f"2026-03-0{t}", b)
    add_film(9, title="Solo", runtime=80)
    add_diary("bob", 9, "2026-06-01", 5)
    assert client.get("/wrapped", follow_redirects=False).headers["location"] == "/wrapped/2026"
    r = client.get("/wrapped/2026")
    assert r.status_code == 200
    names = {"Opener", "Together", "Hours", "Busiest month", "Movie night", "In sync",
             "Biggest clash", "You both loved", "Genre", "Director", "Actor", "Faces",
             "On your own", "Bookends"}
    assert all(f'data-name="{n}"' in r.text for n in names)
    assert client.get("/api/wrapped/2026").json()["together"]["films"] == 3


def test_empty_year_explains_itself(client):
    r = client.get("/wrapped")
    assert r.status_code == 200 and "No Letterboxd diary entries" in r.text
    assert "No Letterboxd diary entries" in client.get("/wrapped/1999").text
