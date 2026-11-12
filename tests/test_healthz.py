"""`/healthz` — the container healthcheck's probe.

The point of these is the *unhealthy* case. Until 2026-09-04 the Dockerfile's
HEALTHCHECK hit `/`, which renders the index template from settings alone and
never opens the database — so the one failure worth catching, a container that
has lost its `/app/data` bind mount, reported healthy while every page that
reads a film returned 500. A probe that cannot fail is not a probe, and
`test_healthz_reports_503_when_the_database_is_gone` is the test that would
have caught it.
"""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from app import db
from app.main import app

from .helpers import add_film


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_healthz_is_ok_on_a_live_database(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "database": "ok", "films": 0}


def test_healthz_counts_films(client):
    add_film(1, title="Whiplash", year=2014)
    add_film(2, title="Sicario", year=2015)
    assert client.get("/healthz").json()["films"] == 2


def test_healthz_reports_503_when_the_database_is_gone(client, monkeypatch):
    """A dropped bind mount, which is what the old `/` probe could not see."""
    def boom():
        raise OSError("unable to open database file")

    monkeypatch.setattr(db, "cursor", boom)
    r = client.get("/healthz")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "error"
    assert body["database"] == "unavailable"
    assert "unable to open database file" in body["detail"]


def test_healthz_reports_503_when_the_schema_is_missing(client):
    """Same answer for a file that opens but is not our database."""
    with db.cursor() as cur:
        cur.execute("DROP TABLE films")
    assert client.get("/healthz").status_code == 503
