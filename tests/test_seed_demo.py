"""The demo is what a newcomer sees first and what every README screenshot
shows, so each page it seeds must have something on it."""
from __future__ import annotations

from app import tonight, wrapped
from scripts import seed_demo


def test_demo_fills_tonight_and_the_get_list():
    seed_demo.main()
    both = tonight.ranked(tonight.parse())
    on_plex = tonight.ranked(tonight.parse(scope="plex"))
    assert len(both) >= 10
    assert 0 < len(on_plex) < len(both)
    assert tonight.worth_getting(tonight.parse())


def test_demo_fills_wrapped_with_nights_together():
    seed_demo.main()
    year = wrapped.compute(2026, today=wrapped.dt.date(2026, 11, 12))
    assert year is not None
    assert year["together"]["nights"] >= 5
    assert year["excluded"] == []          # no day trips the bulk catch-up rule
    assert all(year["people"][u]["minutes"] for u in (year["user_a"], year["user_b"]))


def test_demo_is_the_same_every_run():
    seed_demo.main()
    first = [f.tmdb_id for f in tonight.ranked(tonight.parse())]
    seed_demo.main()
    assert [f.tmdb_id for f in tonight.ranked(tonight.parse())] == first
