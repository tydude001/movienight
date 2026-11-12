"""Shared test fixtures.

Every test runs against a throwaway SQLite database in a temp dir. The app's
settings are ``lru_cache``d and read from the environment, so the fixture points
``DATABASE_PATH`` at the temp file and clears the cache before each test. Users
are fixed to alice/bob so the two-user picker queries are deterministic.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def temp_env(tmp_path, monkeypatch):
    """Fresh temp DB + known settings for every test."""
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "watch.db"))
    monkeypatch.setenv("IMPORT_DIR", str(tmp_path / "imports"))
    monkeypatch.setenv("USER_A", "alice")
    monkeypatch.setenv("USER_B", "bob")
    monkeypatch.setenv("TMDB_API_KEY", "changeme")
    monkeypatch.setenv("PLEX_TOKEN", "changeme")
    monkeypatch.setenv("NOTIFY_URL", "")
    # Pinned so a real .env can never change what a test does. OMDb off (no
    # key); the Letterboxd scraper forced ON despite shipping off by default,
    # so its code path stays covered — every request is a mock transport.
    monkeypatch.setenv("OMDB_API_KEY", "")
    monkeypatch.setenv("LETTERBOXD_SCORES", "1")
    monkeypatch.setenv("LETTERBOXD_WATCHLIST_SCRAPE", "1")
    monkeypatch.setenv("LETTERBOXD_A", "")
    monkeypatch.setenv("LETTERBOXD_B", "")

    from app import db, jobs, taste
    from app.config import get_settings

    get_settings.cache_clear()   # re-read the temp env, not any real .env
    taste._cache = None          # drop the module-level scorer cache
    jobs._reset()                # no admin job carried over from another test
    db.init_db()
    yield
    get_settings.cache_clear()
    taste._cache = None
