"""Structured logging — one JSON object per line, on stdout.

**Named `logs.py`, not `logging.py`**, so `import logging` inside this package
still reaches the standard library. Absolute imports make the clash harmless in
principle; the confusion it causes a reader is the reason to avoid it anyway.

Three things live here and nothing else does:

- **`JsonFormatter`** — renders a record as a single JSON object. Anything
  passed through `extra=` rides along as a top-level key, which is the whole
  point of structured logging: `log.info("plex sync", extra={"matched": 12})`
  is greppable as `.matched`, a formatted sentence is not.
- **`configure()`** — installs it on the root logger, once, from
  `LOG_LEVEL`/`LOG_FORMAT`. Called from the app's lifespan and from the
  scripts; safe to call twice.
- **`request_id`** — a `ContextVar` the middleware sets per request, so a line
  logged deep inside `ratings.py` can be tied back to the request that caused
  it without threading an argument through four call sites.
- **`redact()`** — scrubs credentials from every rendered line. TMDb and OMDb
  take their keys as query parameters, and a URL reaches the log two ways:
  httpx's own request line (silenced in `configure`) and the message of an
  `HTTPStatusError`, which `ratings.py` logs at WARNING.

**`LOG_FORMAT=text` exists for a terminal**, where a wall of JSON is worse than
a sentence — the scripts run interactively far more often than the app does.
The container leaves the default (`json`), which is what `docker logs` and
anything reading it want.

Deliberately no log file and no rotation: the app runs under Docker, where
stdout *is* the log and the daemon owns retention. A file here would be a
second copy nobody rotates.
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import re
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

#: Set per request by the middleware in `main.py`, read by `JsonFormatter`.
#: Empty outside a request — a script, a startup line — and omitted from the
#: output entirely rather than logged as null.
request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")

#: Everything `logging` puts on a record itself. Any key not in here came from
#: a caller's `extra=` and belongs in the JSON object.
#: `color_message` is uvicorn's own doing — it attaches the ANSI-coloured
#: version of every line as an extra, which in JSON is the same sentence twice
#: with escape codes in it.
_RESERVED = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
) | {"message", "asctime", "taskName", "color_message"}

#: Query parameters that carry a credential, case-insensitive: TMDb's
#: `api_key`, OMDb's `apikey`, Plex's `X-Plex-Token`.
_SECRET_PARAM = re.compile(r"(?i)\b(api_key|apikey|x-plex-token|token)=[^&\s\"'<>\\]+")

#: The handler the last `configure` installed, so the next one replaces it
#: instead of stacking a second copy of every line. See `configure`.
_handler: logging.Handler | None = None


def redact(text: str) -> str:
    """`text` with the value of every credential query parameter replaced."""
    return _SECRET_PARAM.sub(r"\1=REDACTED", text)


def safe_extra(fields: Mapping[str, Any]) -> dict[str, Any]:
    """An `extra=` dict that cannot kill the caller.

    `logging` raises `KeyError` when an `extra` key shadows a `LogRecord`
    attribute (`module`, `name`, `args`, `filename`…). The sync script passes
    whole result dicts straight through, so a key added to one of those returns
    a year from now would otherwise take down an unattended job at the moment
    it tried to report itself. A clashing key is suffixed with `_` rather than
    dropped — a renamed field is still visible, a missing one is not.
    """
    return {(k + "_" if k in _RESERVED else k): v for k, v in fields.items()}


class JsonFormatter(logging.Formatter):
    """One JSON object per record: fixed keys first, then the caller's `extra`.

    An exception is rendered into a `traceback` string rather than dropped, so
    `log.exception(...)` in an admin route keeps the stack that a swallowed
    `except Exception` used to lose.

    Values that JSON cannot represent are stringified (`default=str`) rather
    than raising — a formatter that can fail is a formatter that turns a logged
    error into a lost one.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        rid = request_id.get()
        if rid:
            payload["request_id"] = rid
        for key, value in record.__dict__.items():
            if key not in _RESERVED:
                payload[key] = value
        if record.exc_info:
            payload["traceback"] = self.formatException(record.exc_info)
        return redact(json.dumps(payload, default=str))


class TextFormatter(logging.Formatter):
    """`LOG_FORMAT=text`: a readable line, with the `extra` keys appended.

    The extras are still shown — dropping them would make the text mode lie
    about what was logged — just as `key=value` pairs rather than JSON.
    """

    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)-7s %(name)s | %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = {k: v for k, v in record.__dict__.items() if k not in _RESERVED}
        rid = request_id.get()
        if rid:
            extras = {"request_id": rid, **extras}
        if extras:
            base += " " + " ".join(f"{k}={v}" for k, v in extras.items())
        return redact(base)


def configure(level: str | None = None, fmt: str | None = None) -> None:
    """Install the formatter on the root logger. Idempotent.

    **Removes only the handler a previous `configure` installed**, never every
    root handler. Stripping the root wholesale was the first shape of this and
    it is wrong twice over: uvicorn attaches its handlers to its own loggers
    rather than to the root, so there is nothing there to clean up — and the
    one thing that *does* attach to the root in practice is pytest's `caplog`,
    which the app's own lifespan would then silently unhook mid-test.

    uvicorn's loggers are pointed at this handler by clearing theirs and
    letting them propagate — except `uvicorn.access`, which is silenced
    (`propagate=False`, level WARNING) because `main.py`'s middleware already
    logs a richer line for the same request, and two access lines per request
    is the same duplication in a different place.

    `httpx` is held at WARNING: at INFO it logs every request URL, query string
    and all, which put the TMDb key in `sync.log` once per lookup. `redact`
    would scrub it anyway; this also drops a line per request nobody reads.
    """
    global _handler
    level = (level or os.getenv("LOG_LEVEL") or "INFO").upper()
    fmt = (fmt or os.getenv("LOG_FORMAT") or "json").lower()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if fmt == "json" else TextFormatter())

    root = logging.getLogger()
    if _handler is not None:
        root.removeHandler(_handler)
    root.addHandler(handler)
    root.setLevel(level)
    _handler = handler

    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True
    access = logging.getLogger("uvicorn.access")
    access.handlers = []
    access.propagate = False
    access.setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
