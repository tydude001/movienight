"""Admin jobs run in the background and report through a polled fragment (app/jobs.py).

Before 2026-11-02 the Sync Plex POST blocked for the whole sync, and a busy
Plex timed it out at 30s with nothing on screen until it failed. Pinned here:
a slow job answers at once with a running state that polls; the buttons are
disabled and a second click starts nothing; the result replaces the spinner
and polling stops; and a Plex read timeout reads as "busy", not as a traceback.
"""
from __future__ import annotations

import html
import threading

import pytest
import requests
from starlette.testclient import TestClient

from app import jobs, letterboxd, plex_sync
from app.config import get_settings
from app.main import app

AUTH = ("alice", "pw")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "alice")
    monkeypatch.setenv("ADMIN_PASSWORD", "pw")
    get_settings.cache_clear()
    monkeypatch.setattr("app.main.JOB_GRACE_SECONDS", 0.05)
    with TestClient(app) as c:
        yield c
    get_settings.cache_clear()


@pytest.fixture
def gated_plex(monkeypatch):
    """A sync_plex that blocks until the test releases it."""
    gate, calls = threading.Event(), []

    def sync():
        calls.append(1)
        assert gate.wait(5)
        return {"matched": 770, "unmatched_count": 12}

    monkeypatch.setattr(plex_sync, "sync_plex", sync)
    yield gate, calls
    gate.set()


def _finish():
    assert jobs.current().wait(5)


def test_a_slow_job_answers_at_once_with_a_polling_running_state(client, gated_plex):
    r = client.post("/admin/sync-plex", auth=AUTH)
    assert r.status_code == 200
    assert "Syncing Plex" in r.text
    assert 'hx-get="/admin/job"' in r.text and "every 1s" in r.text
    assert r.text.count("disabled") == 3          # all three buttons
    gated_plex[0].set()
    _finish()


def test_a_second_click_while_running_starts_nothing(client, gated_plex, monkeypatch):
    gate, calls = gated_plex
    imports = []
    monkeypatch.setattr(letterboxd, "import_from_dir", lambda: imports.append(1) or [])
    client.post("/admin/sync-plex", auth=AUTH)
    client.post("/admin/sync-plex", auth=AUTH)
    r = client.post("/admin/import", auth=AUTH)
    assert "Syncing Plex" in r.text               # still reporting the running job
    gate.set()
    _finish()
    assert calls == [1] and imports == []


def test_the_result_replaces_the_spinner_and_polling_stops(client, gated_plex):
    client.post("/admin/sync-plex", auth=AUTH)
    gated_plex[0].set()
    _finish()
    r = client.get("/admin/job", auth=AUTH)
    assert "✓ Done" in r.text and "matched 770 films, 12 without a TMDb GUID" in r.text
    assert "hx-trigger" not in r.text and "disabled" not in r.text


def test_the_admin_page_picks_up_a_running_job(client, gated_plex):
    client.post("/admin/sync-plex", auth=AUTH)
    r = client.get("/admin", auth=AUTH)
    assert "Syncing Plex" in r.text and 'hx-get="/admin/job"' in r.text
    gated_plex[0].set()
    _finish()


def test_a_slow_plex_gets_the_busy_note(client, gated_plex, monkeypatch):
    monkeypatch.setattr(jobs, "SLOW_AFTER_SECONDS", 0)
    r = client.post("/admin/sync-plex", auth=AUTH)
    assert "Plex is slow to answer" in r.text and "Waiting up to 2:00" in r.text
    gated_plex[0].set()
    _finish()


def test_a_plex_read_timeout_reads_as_busy(client, monkeypatch):
    def timeout():
        raise requests.exceptions.ReadTimeout("HTTPConnectionPool(...): Read timed out.")

    monkeypatch.setattr(plex_sync, "sync_plex", timeout)
    client.post("/admin/sync-plex", auth=AUTH)
    _finish()
    text = html.unescape(client.get("/admin/job", auth=AUTH).text)
    assert "✗ Failed" in text
    assert "Plex didn't answer within 2 minutes" in text
    assert "HTTPConnectionPool" not in text


def test_idle_shows_when_each_sync_last_ran(client):
    r = client.get("/admin/job", auth=AUTH)
    assert "No syncs recorded yet" in r.text and "hx-trigger" not in r.text


def test_plex_timeout_outlasts_a_scanner_run():
    # 72.5s was measured listing the library during a scan (2026-11-02).
    assert plex_sync.PLEX_TIMEOUT_SECONDS >= 120
