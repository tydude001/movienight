"""HTTP Basic auth for `/admin/*` — hardening roadmap item 1.

Every admin route writes the database or calls a third party (Plex, TMDb), and
until 2026-09-05 anyone on the LAN could trigger all of them with a POST: no
login, no token, nothing in the logs to say who. The guard is deliberately the
smallest thing that closes that: one username/password pair from the
environment, checked in constant time, over HTTP Basic — the browser prompts
once on `/admin` and re-sends the credentials on every HTMX call from that
page, so the templates needed no change.

**It fails closed.** With `ADMIN_USER` / `ADMIN_PASSWORD` unset, `/admin/*`
answers 503 and says which two variables to set, rather than reverting to the
open door. That means the first deploy after this lands *does* lock the admin
page until `.env` carries the pair — which is the intended step, not a
regression — and the rest of the app (picker, dashboard, `/api/*`, which only
read) is untouched.

Basic auth is cleartext on the wire; this app is LAN-only and unencrypted
already, so the guard raises the bar from "anyone" to "anyone with the
password", not to TLS-grade. That is the agreed scope.
"""
from __future__ import annotations

import logging
import secrets

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from .config import get_settings

log = logging.getLogger("app.auth")

REALM = "movienight admin"
_CHALLENGE = {"WWW-Authenticate": f'Basic realm="{REALM}"'}

# auto_error=False so a missing header reaches us: the unconfigured case must
# come back as 503, not as the 401 the default would raise first.
_basic = HTTPBasic(auto_error=False)


def _matches(given: str, expected: str) -> bool:
    return secrets.compare_digest(given.encode("utf-8"), expected.encode("utf-8"))


def require_admin(request: Request,
                  credentials: HTTPBasicCredentials | None = Depends(_basic)) -> str:
    """FastAPI dependency: the admin username, or a 401/503 response.

    Both the username and the password are compared in constant time, and both
    are always compared — short-circuiting on the username would leak which
    half was wrong through timing.
    """
    s = get_settings()
    if not s.admin_user or not s.admin_password:
        log.error("admin auth is not configured", extra={"path": request.url.path})
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "admin is disabled until ADMIN_USER and ADMIN_PASSWORD are set in .env",
        )
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "admin login required",
                            headers=_CHALLENGE)
    user_ok = _matches(credentials.username, s.admin_user)
    pass_ok = _matches(credentials.password, s.admin_password)
    if not (user_ok and pass_ok):
        log.warning("admin login rejected",
                    extra={"path": request.url.path, "username": credentials.username,
                           "client": request.client.host if request.client else None})
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad admin credentials",
                            headers=_CHALLENGE)
    return credentials.username
