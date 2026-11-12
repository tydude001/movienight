"""Picker: surface modes, filters, and the spin/shortlist weighting."""
from __future__ import annotations

from datetime import date, timedelta

from app import picker
from app.picker import Filters, _recency_boost

from .helpers import add_film, add_veto, add_watched, add_watchlist, set_plex

# ── surface modes ───────────────────────────────────────────────────

def test_both_want_requires_both_watchlists_and_plex():
    add_film(1); set_plex(1); add_watchlist("alice", 1); add_watchlist("bob", 1)
    add_film(2); set_plex(2); add_watchlist("alice", 2)            # only one wants
    add_film(3); add_watchlist("alice", 3); add_watchlist("bob", 3)  # both, not on Plex
    assert {f.tmdb_id for f in picker.both_want()} == {1}


def test_one_wants_includes_single_watchlist_on_plex():
    add_film(1); set_plex(1); add_watchlist("alice", 1); add_watchlist("bob", 1)
    add_film(2); set_plex(2); add_watchlist("alice", 2)
    add_film(3); add_watchlist("alice", 3)  # wanted but not on Plex
    assert {f.tmdb_id for f in picker.one_wants()} == {1, 2}


def test_wanted_by_and_seen_by_populated():
    add_film(1); set_plex(1)
    add_watchlist("alice", 1); add_watchlist("bob", 1)
    add_watched("bob", 1)
    film = picker.both_want()[0]
    assert sorted(film.wanted_by) == ["alice", "bob"]
    assert film.seen_by == ["bob"]


# ── filters ─────────────────────────────────────────────────────────

def test_filter_max_runtime():
    add_film(1, runtime=90); set_plex(1); add_watchlist("alice", 1)
    add_film(2, runtime=150); set_plex(2); add_watchlist("alice", 2)
    assert {f.tmdb_id for f in picker.one_wants(Filters(max_runtime=120))} == {1}


def test_filter_genre_substring_case_insensitive():
    add_film(1, genres="Science Fiction, Drama"); set_plex(1); add_watchlist("alice", 1)
    add_film(2, genres="Comedy"); set_plex(2); add_watchlist("alice", 2)
    assert {f.tmdb_id for f in picker.one_wants(Filters(genre="drama"))} == {1}


def test_filter_decade():
    add_film(1, year=1995); set_plex(1); add_watchlist("alice", 1)
    add_film(2, year=2005); set_plex(2); add_watchlist("alice", 2)
    assert {f.tmdb_id for f in picker.one_wants(Filters(decade=1990))} == {1}


def test_filter_unseen_only():
    add_film(1); set_plex(1); add_watchlist("alice", 1); add_watched("alice", 1)
    add_film(2); set_plex(2); add_watchlist("alice", 2)
    assert {f.tmdb_id for f in picker.one_wants(Filters(unseen_only=True))} == {2}


# ── spin_the_wheel weighting ────────────────────────────────────────

def test_spin_returns_none_on_empty_pool():
    assert picker.spin_the_wheel() is None


def _capture_weights(monkeypatch):
    """Patch random.choices to record the weights and pick deterministically."""
    captured: dict = {}

    def fake_choices(pool, weights, k):
        captured["by_id"] = {f.tmdb_id: w for f, w in zip(pool, weights, strict=True)}
        return [pool[0]]

    monkeypatch.setattr(picker.random, "choices", fake_choices)
    return captured


def test_spin_weights_both_want_three_times_higher(monkeypatch):
    add_film(1); set_plex(1); add_watchlist("alice", 1); add_watchlist("bob", 1)  # both
    add_film(2); set_plex(2); add_watchlist("alice", 2)                            # one
    weights = _capture_weights(monkeypatch)
    picker.spin_the_wheel(mode="one", weight_recency=False)
    assert weights["by_id"][1] == 3.0
    assert weights["by_id"][2] == 1.0


def test_spin_veto_downweights(monkeypatch):
    add_film(1); set_plex(1); add_watchlist("alice", 1)
    add_veto(1, times=1)
    weights = _capture_weights(monkeypatch)
    picker.spin_the_wheel(mode="one", weight_recency=False)
    assert weights["by_id"][1] == 0.5  # 1.0 / (1 + 1 veto)


def test_recency_boost_tiers():
    today = date.today()
    assert _recency_boost(today.isoformat()) == 2.0
    assert _recency_boost((today - timedelta(days=60)).isoformat()) == 1.5
    assert _recency_boost((today - timedelta(days=200)).isoformat()) == 1.0
    assert _recency_boost(None) == 1.0
    assert _recency_boost("not-a-date") == 1.0


# ── no_veto_shortlist ordering ──────────────────────────────────────

def test_shortlist_puts_both_want_first():
    add_film(1); set_plex(1); add_watchlist("alice", 1); add_watchlist("bob", 1)
    add_film(2); set_plex(2); add_watchlist("alice", 2)
    add_film(3); set_plex(3); add_watchlist("alice", 3)
    picks = picker.no_veto_shortlist(2, mode="one")
    assert picks[0].tmdb_id == 1
    assert len(picks) == 2


def test_shortlist_sinks_vetoed_films_within_group():
    add_film(1); set_plex(1); add_watchlist("alice", 1)
    add_film(2); set_plex(2); add_watchlist("alice", 2); add_veto(2, times=2)
    picks = picker.no_veto_shortlist(2, mode="one")
    assert [p.tmdb_id for p in picks] == [1, 2]
