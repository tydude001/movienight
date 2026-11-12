"""Structured logging — the formatter, and the request line the app emits.

The value being guarded here is that a failure leaves something behind. Before
this existed the admin routes caught every exception into an HTML fragment and
logged nothing, so a Plex sync that failed at 03:00 was gone by morning —
`test_a_failing_admin_action_logs_the_traceback` is the test for that, and the
JSON shape tests are what make those lines machine-readable rather than prose.
"""
from __future__ import annotations

import json
import logging
import sys

import pytest
from starlette.testclient import TestClient

from app import logs
from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _record(**extra):
    record = logging.LogRecord("app.test", logging.INFO, __file__, 1, "hello", (), None)
    record.__dict__.update(extra)
    return record


def test_json_formatter_emits_one_object_with_the_fixed_keys():
    out = json.loads(logs.JsonFormatter().format(_record()))
    assert out["level"] == "INFO"
    assert out["logger"] == "app.test"
    assert out["msg"] == "hello"
    assert out["ts"].endswith("+00:00")


def test_extra_fields_become_top_level_keys():
    out = json.loads(logs.JsonFormatter().format(_record(matched=12, path="/admin")))
    assert out["matched"] == 12
    assert out["path"] == "/admin"


def test_a_traceback_is_carried_rather_than_dropped():
    try:
        raise ValueError("plex is down")
    except ValueError:
        import sys
        record = _record()
        record.exc_info = sys.exc_info()
    out = json.loads(logs.JsonFormatter().format(record))
    assert "ValueError: plex is down" in out["traceback"]


def test_an_unserialisable_value_is_stringified_not_raised():
    out = json.loads(logs.JsonFormatter().format(_record(when=object())))
    assert isinstance(out["when"], str)


def test_request_id_rides_along_when_one_is_set():
    token = logs.request_id.set("abc123")
    try:
        out = json.loads(logs.JsonFormatter().format(_record()))
    finally:
        logs.request_id.reset(token)
    assert out["request_id"] == "abc123"
    # ...and is absent, not null, outside a request.
    assert "request_id" not in json.loads(logs.JsonFormatter().format(_record()))


def test_safe_extra_renames_a_key_that_would_raise():
    # `module` is a LogRecord attribute: passed raw, `logging` raises KeyError.
    fields = logs.safe_extra({"module": "plex", "matched": 3})
    assert fields == {"module_": "plex", "matched": 3}
    logging.getLogger("app.test").info("fine", extra=fields)  # would raise unrenamed


def test_text_format_still_shows_the_extras():
    line = logs.TextFormatter().format(_record(matched=12))
    assert "app.test | hello" in line
    assert "matched=12" in line


def test_a_request_is_logged_with_its_status_and_duration(client, caplog):
    with caplog.at_level(logging.INFO, logger="app.main"):
        r = client.get("/healthz")
    line = next(rec for rec in caplog.records if rec.message == "request")
    assert (line.method, line.path, line.status) == ("GET", "/healthz", 200)
    assert line.duration_ms >= 0
    assert r.headers["X-Request-ID"]


def test_an_inbound_request_id_is_honoured_and_echoed(client):
    r = client.get("/healthz", headers={"X-Request-ID": "from-the-proxy"})
    assert r.headers["X-Request-ID"] == "from-the-proxy"


def test_a_failing_admin_action_logs_the_traceback(client, caplog, monkeypatch):
    def boom():
        raise RuntimeError("plex refused the connection")

    # /admin/* is behind HTTP Basic since 2026-09-05 (app/auth.py); the guard
    # runs before the handler, so the failure this pins is only reachable
    # logged in. The refusals themselves are tests/test_admin_auth.py's.
    from app.config import get_settings
    monkeypatch.setenv("ADMIN_USER", "alice")
    monkeypatch.setenv("ADMIN_PASSWORD", "pw")
    get_settings.cache_clear()
    monkeypatch.setattr("app.main.plex_sync.sync_plex", boom)
    with caplog.at_level(logging.ERROR, logger="app.main"):
        r = client.post("/admin/sync-plex", auth=("alice", "pw"))
    assert r.status_code == 200  # the fragment still renders; the failure is logged
    failure = next(rec for rec in caplog.records if rec.message == "admin sync-plex failed")
    assert failure.exc_info is not None
    assert "plex refused the connection" in logs.JsonFormatter().format(failure)


# ── credentials never reach the log ─────────────────────────────────

URL = "https://api.themoviedb.org/3/movie/1?append_to_response=credits&api_key=sekrit123"


@pytest.mark.parametrize("formatter", [logs.JsonFormatter(), logs.TextFormatter()])
def test_a_key_in_a_url_is_redacted(formatter):
    record = logging.LogRecord("httpx", logging.INFO, __file__, 1, "HTTP Request: GET %s", (URL,), None)
    out = formatter.format(record)
    assert "sekrit123" not in out and "api_key=REDACTED" in out
    assert "append_to_response=credits" in out  # only the credential goes


def test_a_key_in_an_exception_message_is_redacted():
    # httpx.HTTPStatusError repeats the URL; ratings.py logs it at WARNING.
    try:
        raise RuntimeError(f"Client error '401 Unauthorized' for url '{URL}&apikey=omdb456'")
    except RuntimeError as e:
        record = logging.LogRecord("app.ratings", logging.WARNING, __file__, 1,
                                   "TMDb scores failed for %s: %s", (1, e), None)
        record.exc_info = sys.exc_info()
    out = logs.JsonFormatter().format(record)
    assert "sekrit123" not in out and "omdb456" not in out


def test_plex_tokens_are_redacted_whatever_the_case():
    assert logs.redact("http://plex:32400/library?X-Plex-Token=abc") == \
        "http://plex:32400/library?X-Plex-Token=REDACTED"


def test_httpx_request_lines_are_not_logged():
    logs.configure("INFO", "json")
    assert not logging.getLogger("httpx").isEnabledFor(logging.INFO)
