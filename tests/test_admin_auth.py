"""`/admin/*` behind HTTP Basic — hardening roadmap item 1 (app/auth.py).

Until 2026-09-05 every admin POST was open to the LAN. Three things are pinned:
the guard refuses (401 with a challenge, or 503 when unconfigured) before any
handler body runs; it fails *closed*, never open; and every route under
`/admin` carries it — the sweep at the bottom is what catches a new admin
route that forgets.
"""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from app import letterboxd, plex_sync
from app.config import get_settings
from app.main import app

ADMIN_ROUTES = [("GET", "/admin"), ("POST", "/admin/sync-plex"), ("POST", "/admin/import"),
                ("POST", "/admin/enrich"), ("POST", "/admin/resolve"), ("GET", "/admin/job")]


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "alice")
    monkeypatch.setenv("ADMIN_PASSWORD", "correct horse")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def unconfigured(monkeypatch):
    monkeypatch.delenv("ADMIN_USER", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    # a real .env could carry the pair; pin the test to "unset" regardless
    monkeypatch.setenv("ADMIN_USER", "")
    monkeypatch.setenv("ADMIN_PASSWORD", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def no_side_effects(monkeypatch):
    """If a guard ever lets a request through, the handler must not reach Plex."""
    def boom(*a, **k):
        raise AssertionError("handler body ran without credentials")
    monkeypatch.setattr(plex_sync, "sync_plex", boom)
    monkeypatch.setattr(letterboxd, "import_from_dir", boom)


def _call(client, method, path, **kw):
    if method == "POST" and path.endswith("/resolve"):
        kw.setdefault("data", {"title": "Whiplash", "year": "2014", "tmdb_id": "244786"})
    return client.request(method, path, **kw)


# ── refusals ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("method,path", ADMIN_ROUTES)
def test_no_credentials_is_401_with_a_challenge(client, configured, no_side_effects, method, path):
    r = _call(client, method, path)
    assert r.status_code == 401
    assert r.headers["www-authenticate"].startswith('Basic realm="')


@pytest.mark.parametrize("method,path", ADMIN_ROUTES)
def test_wrong_credentials_is_401(client, configured, no_side_effects, method, path):
    assert _call(client, method, path, auth=("alice", "wrong")).status_code == 401
    assert _call(client, method, path, auth=("mallory", "correct horse")).status_code == 401


@pytest.mark.parametrize("method,path", ADMIN_ROUTES)
def test_unconfigured_fails_closed_with_503(client, unconfigured, no_side_effects, method, path):
    """The one behaviour that matters most: blank settings lock the door, they
    do not reopen it."""
    r = _call(client, method, path)
    assert r.status_code == 503
    assert "ADMIN_USER" in r.text and "ADMIN_PASSWORD" in r.text


def test_a_rejected_login_is_logged_with_the_username(client, configured, no_side_effects, caplog):
    with caplog.at_level("WARNING", logger="app.auth"):
        client.get("/admin", auth=("mallory", "x"))
    rec = next(r for r in caplog.records if r.name == "app.auth")
    assert rec.username == "mallory" and rec.path == "/admin"


# ── the door opens for the right pair ───────────────────────────────────────

def test_right_credentials_render_the_admin_page(client, configured):
    r = client.get("/admin", auth=("alice", "correct horse"))
    assert r.status_code == 200
    assert "Sync Plex" in r.text


def test_right_credentials_reach_the_handler(client, configured, monkeypatch):
    monkeypatch.setattr(plex_sync, "sync_plex", lambda: {"matched": 3, "unmatched_count": 1})
    r = client.post("/admin/sync-plex", auth=("alice", "correct horse"))
    assert r.status_code == 200
    assert "matched 3 films" in r.text


# ── scope ────────────────────────────────────────────────────────────────────

def test_the_rest_of_the_app_needs_no_login(client, configured):
    for path in ("/", "/dashboard", "/healthz", "/api/both-want"):
        assert client.get(path).status_code == 200, path


def test_every_admin_route_carries_the_guard():
    """A new `/admin/...` route that forgets `**_ADMIN` is open to the LAN
    again, and nothing but this sweep would notice."""
    from app.auth import require_admin
    from app.main import app as the_app

    admin_paths = []
    for route in the_app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/admin"):
            continue
        admin_paths.append(path)
        deps = [d.call for d in route.dependant.dependencies]
        assert require_admin in deps, f"{path} has no admin guard"
    assert set(admin_paths) == {p for _, p in ADMIN_ROUTES}
