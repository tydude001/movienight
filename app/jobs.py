"""One admin job at a time, run off the request thread.

Until 2026-11-02 each admin button ran its job inside the POST, so the page sat
silent for as long as the job took, and a Plex busy scanning new episodes
pushed `sync_plex()` past its read timeout with nothing on screen in the
meantime. Now the POST starts the job in a thread and the status fragment polls
`GET /admin/job` until it finishes.

State is in memory on purpose: a restart mid-job loses only the status line,
and every job is idempotent, so the fix is to press the button again. One job
runs at a time across all three buttons, so a second click (or a second tab)
gets the running job's status instead of starting a duplicate against a Plex
that is already struggling.
"""
from __future__ import annotations

import contextvars
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

log = logging.getLogger("app.jobs")

# A job slower than this gets the "Plex is slow" note while it runs.
SLOW_AFTER_SECONDS = 30


@dataclass
class Job:
    name: str            # "plex", "import", "enrich"
    label: str           # "Syncing Plex" — what the running state says
    started: float = field(default_factory=time.monotonic)
    finished: float | None = None
    ok: bool | None = None
    msg: str = ""
    _done: threading.Event = field(default_factory=threading.Event, repr=False)

    @property
    def running(self) -> bool:
        return self.finished is None

    @property
    def elapsed(self) -> int:
        end = self.finished if self.finished is not None else time.monotonic()
        return int(end - self.started)

    @property
    def slow(self) -> bool:
        return self.running and self.elapsed >= SLOW_AFTER_SECONDS

    def wait(self, timeout: float) -> bool:
        return self._done.wait(timeout)


_lock = threading.Lock()
_job: Job | None = None  # the running job, or the last one to finish


def current() -> Job | None:
    return _job


def start(name: str, label: str, fn: Callable[[], str],
          on_error: Callable[[Exception], str]) -> Job:
    """Run ``fn`` in a thread unless a job is already running.

    ``fn`` returns the success message. ``on_error`` turns an exception into
    the failure message and does the logging, so a job's failure is logged by
    the code that knows what the job was. Returns the job now in charge: the
    new one, or the one that was already running.
    """
    global _job
    with _lock:
        if _job is not None and _job.running:
            return _job
        job = _job = Job(name, label)

    def run():
        try:
            job.msg, job.ok = fn(), True
        except Exception as e:  # noqa: BLE001 — every failure becomes a status line
            job.msg, job.ok = on_error(e), False
        finally:
            job.finished = time.monotonic()
            job._done.set()

    # copy_context() carries the request_id, so the job's log lines stay tied
    # to the click that started it.
    ctx = contextvars.copy_context()
    threading.Thread(target=ctx.run, args=(run,), name=f"admin-{name}", daemon=True).start()
    return job


def _reset() -> None:
    """Tests only: forget the current job."""
    global _job
    with _lock:
        _job = None
