"""Best-effort push notifications — ntfy topic or Discord webhook.

One function, fire-and-forget: a failed or unconfigured notification never
breaks the job that triggered it.
"""
from __future__ import annotations

import httpx

from .config import get_settings


def send(message: str, *, title: str = "Movie Night") -> bool:
    """POST ``message`` to NOTIFY_URL. Returns True only on a 2xx response."""
    url = get_settings().notify_url
    if not url:
        return False
    try:
        if "discord.com/api/webhooks" in url:
            resp = httpx.post(url, json={"content": f"**{title}**\n{message}"},
                              timeout=10.0)
        else:  # ntfy-style: plain-text body, title in a header
            resp = httpx.post(url, content=message.encode("utf-8"),
                              headers={"Title": title}, timeout=10.0)
        return resp.status_code < 300
    except httpx.HTTPError:
        return False
