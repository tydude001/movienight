"""Taste scorer: the genre/director math (pure) and build_scorer (DB-backed)."""
from __future__ import annotations

import re
from collections import Counter

from app import taste
from app.taste import Scorer

from .helpers import add_film, add_rating, add_watched

# ── Scorer.match(): pure arithmetic, no DB ──────────────────────────

def test_match_pure_genre_affinity():
    # Drama at max weight, no director → 100 * 0.7 * 1.0 = 70
    sc = Scorer(Counter({"Drama": 2}), Counter(), g_max=2, d_max=1)
    assert sc.match("Drama", None) == 70


def test_match_genre_and_director_maxes_out():
    sc = Scorer(Counter({"Drama": 2}), Counter({"Nolan": 1}), g_max=2, d_max=1)
    # 100 * (0.7*1.0 + 0.3*1.0) = 100
    assert sc.match("Drama", "Nolan") == 100


def test_match_averages_across_multiple_genres():
    sc = Scorer(Counter({"Drama": 2, "Horror": 0}), Counter(), g_max=2, d_max=1)
    # g_aff = (2 + 0) / (2 genres * g_max 2) = 0.5 → 100 * 0.7 * 0.5 = 35
    assert sc.match("Drama, Horror", None) == 35


def test_match_clamps_negative_affinity_to_zero():
    sc = Scorer(Counter({"Horror": -3}), Counter(), g_max=1, d_max=1)
    assert sc.match("Horror", None) == 0


def test_match_zero_without_any_signal():
    sc = Scorer(Counter(), Counter(), g_max=1, d_max=1)
    assert sc.match("Unknown", "Nobody") == 0
    assert sc.match(None, None) == 0


# ── Scorer.why(): the human-readable reason ─────────────────────────

def test_why_names_top_two_genres_and_director():
    sc = Scorer(Counter({"Drama": 3, "Comedy": 1}), Counter({"Nolan": 2}), 3, 2)
    assert sc.why("Drama, Comedy", "Nolan") == \
        "You both lean Drama & Comedy, and have enjoyed Nolan."


def test_why_genre_only_when_director_unknown():
    sc = Scorer(Counter({"Drama": 3}), Counter(), 3, 1)
    assert sc.why("Drama", "Stranger") == "You both lean Drama."


def test_why_empty_string_without_signal():
    sc = Scorer(Counter(), Counter(), 1, 1)
    assert sc.why("X", "Y") == ""


# ── build_scorer(): tallies from the DB ─────────────────────────────

def test_build_scorer_counts_love_twice():
    add_film(1, genres="Drama", director="Nolan")
    add_watched("alice", 1)      # +1 (watched)
    add_rating("alice", 1, 4.5)  # +1 again (4★+ love)
    sc = taste.build_scorer()
    assert sc.genres["Drama"] == 2
    assert sc.directors["Nolan"] == 2


def test_build_scorer_dislike_subtracts():
    add_film(1, genres="Horror", director="Bay")
    add_watched("bob", 1)      # +1
    add_rating("bob", 1, 1.0)  # -1 (≤2★ dislike) → nets to 0
    sc = taste.build_scorer()
    assert sc.genres["Horror"] == 0
    assert sc.directors["Bay"] == 0


def test_build_scorer_collects_history_for_explanations():
    add_film(1, title="Whiplash", genres="Drama, Music", director="Chazelle")
    add_film(2, title="Babylon", genres="Drama", director="Chazelle")
    add_watched("alice", 1)
    add_rating("alice", 1, 5.0)
    add_watched("bob", 2)
    add_rating("bob", 2, 1.5)

    sc = taste.build_scorer()
    drama = sc.genre_history["Drama"]
    assert drama.count == 2
    assert [s.title for s in drama.best] == ["Whiplash", "Babylon"]
    assert [s.title for s in drama.loved] == ["Whiplash"]
    assert [s.title for s in drama.panned] == ["Babylon"]
    assert sc.director_history["Chazelle"].count == 2


def test_history_keeps_the_better_of_two_ratings():
    add_film(1, title="Heat", genres="Crime")
    add_rating("alice", 1, 2.0)
    add_rating("bob", 1, 4.5)
    assert taste.build_scorer().genre_history["Crime"].best[0].rating == 4.5


# ── Scorer.explain(): the long-form breakdown ───────────────────────

def test_explain_matches_the_badge_and_splits_the_points():
    sc = Scorer(Counter({"Drama": 2, "Horror": 0}), Counter({"Nolan": 1}), 2, 1)
    ex = sc.explain("Drama, Horror", "Nolan")
    assert ex.match == sc.match("Drama, Horror", "Nolan")
    # Genre terms sum to the genre half of the score; director takes its 30.
    assert round(ex.genre_points + ex.director_points) == ex.match
    assert ex.director_points == 30.0
    assert [f.label for f in ex.genres] == ["Drama", "Horror"]  # strongest first
    assert ex.genres[0].points == 35.0 and ex.genres[1].points == 0.0


def test_explain_orders_genres_by_affinity_not_film_order():
    sc = Scorer(Counter({"Drama": 4, "Comedy": 1}), Counter(), 4, 1)
    assert [f.label for f in sc.explain("Comedy, Drama", None).genres] == ["Drama", "Comedy"]


def test_explain_reports_no_signal_when_history_is_silent():
    sc = Scorer(Counter(), Counter(), 1, 1)
    ex = sc.explain("Western", "Nobody")
    assert ex.match == 0
    assert not ex.has_signal
    assert any("no track record" in n for n in ex.notes)
    assert any("Neither of you has logged" in n for n in ex.notes)


def test_explain_cites_the_films_behind_a_genre():
    add_film(1, title="Whiplash", genres="Drama", director="Chazelle")
    add_watched("alice", 1)
    add_rating("alice", 1, 5.0)
    ex = taste.build_scorer().explain("Drama", "Chazelle")
    assert any("Whiplash (5★)" in n for n in ex.notes)
    assert ex.has_signal


def test_explain_calls_out_a_genre_you_bounced_off():
    # Rated but never logged as watched, so the dislike isn't cancelled by the
    # +1 a watch would add (see test_build_scorer_dislike_subtracts).
    add_film(1, title="Cats", genres="Musical")
    add_rating("alice", 1, 0.5)
    ex = taste.build_scorer().explain("Musical", None)
    assert ex.match == 0
    assert ex.genres[0].affinity < 0
    assert any("didn't land" in n and "Cats (0.5★)" in n for n in ex.notes)


def test_explain_never_cites_the_film_as_evidence_for_itself():
    add_film(1, title="Heat", genres="Crime", director="Mann")
    add_film(2, title="Collateral", genres="Crime", director="Mann")
    for tid in (1, 2):
        add_watched("alice", tid)
        add_rating("alice", tid, 4.5)

    ex = taste.build_scorer().explain("Crime", "Mann", tmdb_id=1)
    joined = " ".join(ex.notes)
    assert "Collateral" in joined
    assert "Heat" not in joined
    assert "2 in your history" in joined  # still counted, just not quoted


def test_explain_owns_up_when_the_only_evidence_is_the_film_itself():
    # Nothing else in the genre, so excluding the film leaves nothing to cite —
    # and claiming "none has hit 4★" about a 5★ film would be a lie.
    add_film(1, title="Solaris", genres="Science Fiction", director="Tarkovsky")
    add_watched("alice", 1)
    add_rating("alice", 1, 5.0)

    notes = " ".join(taste.build_scorer().explain(
        "Science Fiction", "Tarkovsky", tmdb_id=1).notes)
    assert "Solaris" not in notes
    assert "none has hit 4★" not in notes
    assert "this one among them" in notes
    assert "the only one of theirs you've logged" in notes


# ── citation variety ────────────────────────────────────────────────

def _seed_library(n: int = 30) -> None:
    """A history where every film is both Comedy and Drama and rated 5★ — the
    worst case for repetition, since every line wants the same titles."""
    for i in range(1, n + 1):
        add_film(i, title=f"Film {i}", genres="Comedy, Drama", director=f"Dir {i}")
        add_watched("alice", i)
        add_rating("alice", i, 5.0)


def test_genre_lines_do_not_quote_the_same_films_as_each_other():
    _seed_library()
    ex = taste.build_scorer().explain("Comedy, Drama", None, tmdb_id=500)
    comedy, drama = ex.notes[0], ex.notes[1]
    quoted_in_comedy = set(re.findall(r"Film \d+", comedy))
    quoted_in_drama = set(re.findall(r"Film \d+", drama))
    assert quoted_in_comedy and quoted_in_drama
    assert not (quoted_in_comedy & quoted_in_drama)


def test_different_films_lead_with_different_examples():
    _seed_library()
    sc = taste.build_scorer()
    quoted = {sc.explain("Comedy", None, tmdb_id=tid).notes[0] for tid in range(500, 508)}
    assert len(quoted) > 1, "every card quoted the same examples"


def test_examples_are_stable_for_a_given_film():
    """Re-opening a card must not shuffle its reasons."""
    _seed_library()
    sc = taste.build_scorer()
    assert sc.explain("Comedy", None, tmdb_id=500).notes == \
        sc.explain("Comedy", None, tmdb_id=500).notes


def test_citations_read_best_first_despite_rotation():
    for i, rating in enumerate([3.0, 5.0, 4.0, 4.5, 3.5, 5.0, 4.0, 4.5], start=1):
        add_film(i, title=f"Film {i}", genres="Comedy")
        add_watched("alice", i)
        add_rating("alice", i, rating)
    note = taste.build_scorer().explain("Comedy", None, tmdb_id=77).notes[0]
    shown = [float(r) for r in re.findall(r"\((\d[\d.]*)★\)", note)]
    assert shown == sorted(shown, reverse=True), note


def test_a_purer_example_beats_a_multi_genre_one_at_equal_rating():
    add_film(1, title="Pure Comedy", genres="Comedy")
    add_film(2, title="Everything Film", genres="Comedy, Drama, Romance, Music")
    for tid in (1, 2):
        add_watched("alice", tid)
        add_rating("alice", tid, 5.0)
    notes = taste.build_scorer().explain("Comedy", None).notes[0]
    assert notes.index("Pure Comedy") < notes.index("Everything Film")


def test_the_director_line_keeps_its_evidence_when_a_genre_wants_it_too():
    """Only one film to point at, so de-duplication must not starve the
    director's claim — it would have to fall back on a false statement."""
    add_film(1, title="Solo Credit", genres="Comedy", director="Chazelle")
    add_watched("alice", 1)
    add_rating("alice", 1, 5.0)
    notes = taste.build_scorer().explain("Comedy", "Chazelle", tmdb_id=99).notes
    assert "Solo Credit (5★)" in notes[-1]
    assert "the only one of theirs" not in notes[-1]


def test_a_line_still_cites_rather_than_going_vague_when_choices_run_out():
    add_film(1, title="Only Film", genres="Comedy, Drama")
    add_watched("alice", 1)
    add_rating("alice", 1, 5.0)
    notes = taste.build_scorer().explain("Comedy, Drama", None, tmdb_id=99).notes
    assert all("Only Film (5★)" in n for n in notes[:2])
    assert not any("none has hit 4★" in n for n in notes)


# ── the director line matches the evidence it quotes ────────────────

def _director_note(rating: float, *, title: str = "Their Film") -> str:
    add_film(1, title=title, genres="Drama", director="Ti West")
    add_watched("alice", 1)
    add_rating("alice", 1, rating)
    return taste.build_scorer().explain("Drama", "Ti West", tmdb_id=99).notes[-1]


def test_director_line_claims_enjoyment_only_at_four_stars_plus():
    note = _director_note(4.5)
    assert "you've enjoyed" in note and "(4.5★)" in note


def test_director_line_does_not_claim_enjoyment_of_a_middling_rating():
    note = _director_note(3.0)
    assert "enjoyed" not in note
    assert "only ever been fine by you" in note and "(3★)" in note


def test_director_line_is_blunt_about_a_bad_record():
    # Two unrated watches carry the affinity positive, so this lands in the
    # positive branch while the only rating available to quote is a bad one.
    for i in (1, 2):
        add_film(i, title=f"Unrated {i}", genres="Drama", director="Ti West")
        add_watched("alice", i)
    add_film(3, title="The Bad One", genres="Drama", director="Ti West")
    add_watched("alice", 3)
    add_rating("alice", 3, 1.5)

    note = taste.build_scorer().explain("Drama", "Ti West", tmdb_id=99).notes[-1]
    assert "enjoyed" not in note and "fine by you" not in note
    assert "nothing of theirs has landed" in note and "(1.5★)" in note


def test_director_wording_follows_the_rating_it_actually_quotes():
    """Wording is chosen from the picked films, not the pool's best, so
    rotation can never pair 'you've enjoyed' with a 3★ citation."""
    for i, r in enumerate([5.0, 3.0, 3.0, 2.5, 3.5, 3.0, 4.0, 2.5], start=1):
        add_film(i, title=f"Film {i}", genres="Drama", director="Ti West")
        add_watched("alice", i)
        add_rating("alice", i, r)
    sc = taste.build_scorer()
    for tid in range(200, 216):
        note = sc.explain("Drama", "Ti West", tmdb_id=tid).notes[-1]
        shown = [float(x) for x in re.findall(r"\((\d[\d.]*)★\)", note)]
        if "you've enjoyed" in note:
            assert max(shown) >= taste.LOVE_THRESHOLD, note
        elif "fine by you" in note:
            assert taste.DISLIKE_THRESHOLD < max(shown) < taste.LOVE_THRESHOLD, note


def test_explain_has_a_note_per_genre_plus_the_director():
    sc = Scorer(Counter({"Drama": 1}), Counter(), 1, 1)
    assert len(sc.explain("Drama, Horror, Comedy", "Someone").notes) == 4


def test_explain_headline_scales_with_the_match():
    strong = Scorer(Counter({"Drama": 2}), Counter({"Nolan": 1}), 2, 1)
    assert "wheelhouse" in strong.explain("Drama", "Nolan").headline
    middling = Scorer(Counter({"Drama": 3}), Counter(), 10, 1)
    assert "Off your usual path" in middling.explain("Drama", None).headline
    blank = Scorer(Counter(), Counter(), 1, 1)
    assert "Nothing in your history" in blank.explain("Drama", None).headline


def test_build_scorer_is_cached_within_ttl():
    add_film(1, genres="Drama")
    add_watched("alice", 1)
    first = taste.build_scorer()

    add_film(2, genres="Comedy")
    add_watched("alice", 2)
    second = taste.build_scorer()  # inside the TTL → same cached object

    assert first is second
    assert "Comedy" not in second.genres
