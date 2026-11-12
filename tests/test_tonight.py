"""Tonight: the pair score, the pool it ranks, and the pages built on it."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient

from app import db, taste, tonight
from app.main import app

from .helpers import add_film, add_rating, add_watched, add_watchlist, set_plex


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


# ── pair_fit ────────────────────────────────────────────────────────

def test_pair_fit_leans_toward_the_less_keen():
    assert taste.pair_fit(90, 30) == 45          # harmonic, not the 60 average
    assert taste.pair_fit(70, 70) == 70


def test_pair_fit_bumps_a_film_on_both_lists():
    assert taste.pair_fit(70, 70, both_lists=True) == 70 + taste.BOTH_LISTS_BONUS


def test_pair_fit_is_zero_when_either_has_no_signal():
    assert taste.pair_fit(80, 0) == 0
    assert taste.pair_fit(0, 0) == 0


def test_pair_fit_never_passes_100():
    assert taste.pair_fit(100, 100, both_lists=True) == 100


# ── per-user scorers ────────────────────────────────────────────────

def test_a_user_scorer_reads_only_that_users_history():
    add_film(1, title="Hereditary", genres="Horror")
    add_watched("alice", 1); add_rating("alice", 1, 5.0)
    add_film(2, title="Paddington", genres="Comedy")
    add_watched("bob", 2); add_rating("bob", 2, 4.5)

    alice, bob = taste.build_scorer("alice"), taste.build_scorer("bob")
    assert "Horror" in alice.genres and "Comedy" not in alice.genres
    assert "Comedy" in bob.genres and "Horror" not in bob.genres
    assert alice.match("Horror", None) == 70 and bob.match("Horror", None) == 0


def test_a_user_scorer_cites_that_users_own_rating():
    add_film(1, title="Hereditary", genres="Horror")
    add_watched("alice", 1); add_rating("alice", 1, 4.0)
    add_watched("bob", 1); add_rating("bob", 1, 5.0)
    ex = taste.build_scorer("alice").explain("Horror", None)
    assert "Hereditary (4★)" in ex.notes[0]


# ── the pool ────────────────────────────────────────────────────────

def _horror_fan_and_comedy_fan():
    """alice loves horror, bob loves comedy — so their fits disagree."""
    add_film(90, genres="Horror"); add_watched("alice", 90); add_rating("alice", 90, 5.0)
    add_film(91, genres="Comedy"); add_watched("bob", 91); add_rating("bob", 91, 5.0)
    add_watched("alice", 91); add_watched("bob", 90)


def test_everything_wanted_counts_whether_or_not_its_on_plex():
    add_film(1); set_plex(1); add_watchlist("alice", 1)
    add_film(2); add_watchlist("bob", 2)                  # not on Plex
    add_film(3); set_plex(3)                              # nobody wants it
    view = tonight.parse()
    assert {f.tmdb_id for f in tonight.ranked(view)} == {1, 2}
    assert {f.tmdb_id for f in tonight.ranked(tonight.parse("plex"))} == {1}


def test_a_film_either_watcher_has_seen_drops_out():
    add_film(1); add_watchlist("alice", 1); add_watched("bob", 1)
    add_film(2); add_watchlist("alice", 2)
    assert {f.tmdb_id for f in tonight.ranked(tonight.parse())} == {2}
    # ...but on a night bob's out, alice can still pick it.
    assert {f.tmdb_id for f in tonight.ranked(tonight.parse(who="alice"))} == {1, 2}


def test_ranking_follows_whoever_is_watching():
    _horror_fan_and_comedy_fan()
    add_film(1, title="Scream", genres="Horror"); add_watchlist("alice", 1)
    add_film(2, title="Airplane!", genres="Comedy"); add_watchlist("bob", 2)
    add_film(3, title="Shaun of the Dead", genres="Horror, Comedy"); add_watchlist("bob", 3)

    assert tonight.ranked(tonight.parse())[0].title == "Shaun of the Dead"
    assert tonight.ranked(tonight.parse(who="alice"))[0].title == "Scream"
    assert tonight.ranked(tonight.parse(who="bob"))[0].title == "Airplane!"


def test_the_old_picker_modes_still_mean_what_they_did():
    v = tonight.parse(mode="both")
    assert (v.scope, v.both_lists) == ("plex", True)
    assert (tonight.parse(mode="one").scope, tonight.parse(mode="one").both_lists) == ("plex", False)


def test_unknown_query_values_fall_back_to_defaults():
    v = tonight.parse(scope="everything", who="carol", runtime="soon")
    assert (v.scope, v.watching, v.max_runtime) == ("all", ["alice", "bob"], None)


def test_genre_chips_come_from_the_pool_and_keep_a_selected_one():
    add_film(1, genres="Horror"); add_watchlist("alice", 1)
    add_film(2, genres="Horror, Comedy"); add_watchlist("alice", 2)
    assert tonight.genre_options(tonight.parse())[0] == "Horror"
    assert "Western" in tonight.genre_options(tonight.parse(genre="Western"))


def test_spin_never_hands_back_the_film_it_was_asked_to_replace():
    add_film(1); add_watchlist("alice", 1)
    add_film(2); add_watchlist("alice", 2)
    for _ in range(20):
        assert tonight.spin(tonight.parse(), exclude=1).tmdb_id == 2


def test_sync_status_reads_the_stamps():
    with db.cursor() as cur:
        db.stamp(cur, "plex_synced_at")
    later = datetime.now(timezone.utc) + timedelta(hours=3)
    assert tonight.sync_status(later) == [("Plex", "3 h ago")]


# ── pages ───────────────────────────────────────────────────────────

def test_tonight_leads_with_the_best_bet(client):
    _horror_fan_and_comedy_fan()
    add_film(1, title="Shaun of the Dead", genres="Horror, Comedy"); add_watchlist("bob", 1)
    add_film(2, title="Airplane!", genres="Comedy"); add_watchlist("bob", 2)
    body = client.get("/").text
    hero = body.split('id="hero"')[1].split("</section>")[0]
    assert "Shaun of the Dead" in hero and "Airplane!" not in hero
    assert "Not on Plex yet" in hero and "Add to the get-list" in hero


def test_the_grid_skips_the_film_already_in_the_hero(client):
    add_film(1, title="Hero Film"); add_watchlist("alice", 1)
    add_film(2, title="Other Film"); add_watchlist("alice", 2)
    grid = client.get("/picker", params={"exclude": 1}).text
    assert "Other Film" in grid and "Hero Film" not in grid


def test_skip_records_a_veto_and_picks_again(client):
    add_film(1, title="First"); add_watchlist("alice", 1)
    add_film(2, title="Second"); add_watchlist("alice", 2)
    body = client.post("/skip", data={"tmdb_id": 1}).text
    assert "Second" in body
    with db.cursor() as cur:
        assert cur.execute("SELECT count FROM vetoes WHERE tmdb_id = 1").fetchone()[0] == 1


def test_get_list_toggles_and_shows_on_the_get_page(client):
    add_film(1, title="Cursed"); add_watchlist("alice", 1)
    assert "On the get-list" in client.post("/get/1").text
    page = client.get("/get").text
    marked = page.split('id="marked-title"')[1].split('id="worth-title"')[0]
    assert "Cursed" in marked
    assert "Add to the get-list" in client.post("/get/1").text   # and off again


def test_a_film_that_reaches_plex_leaves_the_get_list(client):
    add_film(1, title="Cursed"); add_watchlist("alice", 1)
    client.post("/get/1")
    set_plex(1)
    marked = client.get("/get").text.split('id="marked-title"')[1].split('id="worth-title"')[0]
    assert "Cursed" not in marked


def test_the_detail_card_shows_each_persons_fit(client):
    _horror_fan_and_comedy_fan()
    add_film(1, title="Shaun of the Dead", genres="Horror, Comedy"); add_watchlist("bob", 1)
    body = client.get("/film/1").text
    assert 'class="person a"' in body and 'class="person b"' in body
    assert "Alice" in body and "Bob" in body


def test_the_stylesheet_url_changes_with_the_file(client):
    """Safari kept the old CSS after a deploy; a versioned URL can't go stale."""
    import hashlib

    from app.main import BASE
    want = hashlib.sha256((BASE / "static" / "style.css").read_bytes()).hexdigest()[:10]
    assert f'href="/static/style.css?v={want}"' in client.get("/").text


def test_fonts_are_served_as_woff2(client):
    r = client.get("/static/fonts/instrument-sans.woff2")
    assert r.status_code == 200 and r.headers["content-type"] == "font/woff2"


def test_a_tie_at_100_goes_to_the_film_on_both_lists():
    # Both love comedy and Brooks, so any Brooks comedy fits each of them 100
    # — the pair caps at 100 whether or not the +8 both-lists bonus applies.
    add_film(90, genres="Comedy", director="Brooks")
    add_watched("alice", 90); add_rating("alice", 90, 5.0)
    add_watched("bob", 90); add_rating("bob", 90, 5.0)
    add_film(1, title="Newer, one list", year=2020, genres="Comedy", director="Brooks")
    add_watchlist("alice", 1)
    add_film(2, title="Older, both lists", year=1980, genres="Comedy", director="Brooks")
    add_watchlist("alice", 2); add_watchlist("bob", 2)
    ranked = tonight.ranked(tonight.parse())
    assert [f.score for f in ranked] == [100, 100]
    assert ranked[0].title == "Older, both lists"


def test_pair_fit_uncapped_keeps_the_bonus():
    assert taste.pair_fit(100, 100, both_lists=True, cap=False) == 108
