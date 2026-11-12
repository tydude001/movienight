"""Tonight's "drop a filter" hint: which filters to offer, in what order, and when."""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from app import tonight
from app.main import app

from .helpers import add_film, add_watchlist, set_plex


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _film(tmdb_id, genres, runtime, *, plex, both, title=None):
    add_film(tmdb_id, title=title, genres=genres, runtime=runtime)
    if plex:
        set_plex(tmdb_id)
    add_watchlist("alice", tmdb_id)
    if both:
        add_watchlist("bob", tmdb_id)


@pytest.fixture
def library():
    _film(1, "Horror", 90, plex=True, both=True, title="Short Horror")
    _film(2, "Horror", 150, plex=True, both=True, title="Long Horror")
    _film(3, "Comedy", 90, plex=True, both=False)
    _film(7, "Comedy", 95, plex=True, both=False)
    _film(4, "Comedy", 90, plex=False, both=True)
    _film(8, "Comedy", 100, plex=False, both=True)
    _film(9, "Comedy", 85, plex=False, both=True)
    _film(5, "Horror", 90, plex=False, both=False)


NOTHING = {"scope": "plex", "genre": "Comedy", "runtime": "120", "both": "1"}


# ── loosen(): pure counting ─────────────────────────────────────────

def test_options_are_biggest_first_and_skip_filters_that_bring_nothing(library):
    now, drops = tonight.loosen(tonight.parse(**NOTHING))
    assert now == 0
    # Dropping the runtime brings back nothing (no comedy on Plex is on both
    # lists at any length), so it isn't offered.
    assert [(d.label, d.count) for d in drops] == [
        ("Look beyond Plex", 3), ("Drop On both lists", 2), ("Drop Comedy", 1)]


def test_each_option_links_to_the_view_without_that_filter(library):
    _, drops = tonight.loosen(tonight.parse(**NOTHING))
    by_label = {d.label: d.params for d in drops}
    assert by_label["Look beyond Plex"] == {"genre": "Comedy", "runtime": 120, "both": 1}
    assert by_label["Drop Comedy"] == {"scope": "plex", "runtime": 120, "both": 1}


def test_the_count_matches_what_the_dropped_view_ranks(library):
    _, drops = tonight.loosen(tonight.parse(**NOTHING))
    for d in drops:
        assert d.count == len(tonight.ranked(tonight.parse(**d.params)))


def test_no_options_without_filters(library):
    assert tonight.loosen(tonight.parse()) == (len(tonight.ranked(tonight.parse())), [])


def test_an_odd_runtime_is_named_in_minutes(library):
    _, drops = tonight.loosen(tonight.parse(scope="plex", genre="Horror", runtime="100"))
    assert "Drop Under 100 min" in [d.label for d in drops]


# ── the page ────────────────────────────────────────────────────────

def test_nothing_fits_offers_every_drop_in_the_hero(client, library):
    body = client.get("/", params=NOTHING).text
    hero = body.split('id="hero"')[1].split("</section>")[0]
    assert "Nothing on Plex fits all three filters" in hero
    assert hero.index("Look beyond Plex") < hero.index("Drop On both lists") < hero.index("Drop Comedy")
    assert "Drop Under 2 hrs" not in hero
    assert 'class="drop best"' in hero


def test_nothing_fits_leaves_the_grid_without_a_second_empty_box(client, library):
    assert 'class="empty"' not in client.get("/picker", params=NOTHING).text


def test_a_few_fits_nudges_toward_the_single_best_drop(client, library):
    body = client.get("/", params={"scope": "plex", "genre": "Horror"}).text
    nudge = body.split('class="nudge"')[1].split("</a>")[0]
    # 2 Horror on Plex now; dropping Horror shows 4 (2 more), beyond Plex 3.
    assert "Drop Horror for" in nudge and "2 more" in nudge
    assert body.count('class="nudge"') == 1


def test_enough_fits_means_no_nudge(client, library):
    for i in range(20, 20 + tonight.FEW):
        _film(i, "Horror", 90, plex=True, both=True)
    assert 'class="nudge"' not in client.get("/", params={"scope": "plex", "genre": "Horror"}).text


def test_a_spin_that_comes_back_empty_isnt_mistaken_for_nothing_fitting(client, library):
    # One film fits; spinning away from it leaves no pick, but there's no
    # filter to blame, so the old message stays.
    body = client.post("/spin", data={"scope": "plex", "genre": "Horror", "runtime": "120",
                                      "both": "1", "exclude": 1}).text
    assert "Drop one" not in body and "Nothing on Plex fits that" in body


def test_an_empty_library_still_says_so(client):
    assert "No data yet" in client.get("/picker").text
