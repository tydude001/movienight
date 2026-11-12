"""Favicons and the iPhone homescreen icon.

The failures worth pinning are the silent ones. iOS fills any transparency in
an `apple-touch-icon` with black and masks the corners itself, so an icon with
an alpha channel or pre-rounded corners looks broken only on a phone — never
in a browser, never in a diff. And the head has to carry the links at all: a
missing `apple-touch-icon` doesn't error, it just makes the shortcut a grey
screenshot of the page.
"""
from __future__ import annotations

import struct

import pytest
from starlette.testclient import TestClient

from app.main import BASE, app

ICONS = BASE / "static" / "icons"


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def png_header(name: str) -> tuple[int, int, int]:
    """(width, height, colour_type) straight out of the IHDR chunk."""
    data = (ICONS / name).read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{name} is not a PNG"
    width, height, _depth, colour = struct.unpack(">IIBB", data[16:26])
    return width, height, colour


@pytest.mark.parametrize("name", [
    "favicon.svg", "favicon.ico", "apple-touch-icon.png",
    "icon-192.png", "icon-512.png", "site.webmanifest",
])
def test_every_icon_the_head_links_is_on_disk(name):
    assert (ICONS / name).is_file(), f"{name} missing from app/static/icons"


def test_apple_touch_icon_is_180_square_and_opaque():
    width, height, colour = png_header("apple-touch-icon.png")
    assert (width, height) == (180, 180)
    # Colour types 4 and 6 carry an alpha channel — but a palette PNG (type 3)
    # can be transparent too, via tRNS. iOS would fill either one black.
    assert colour not in (4, 6), "apple-touch-icon has an alpha channel"
    assert b"tRNS" not in (ICONS / "apple-touch-icon.png").read_bytes(), \
        "apple-touch-icon carries palette transparency"


def test_android_icons_are_the_sizes_the_manifest_claims():
    assert png_header("icon-192.png")[:2] == (192, 192)
    assert png_header("icon-512.png")[:2] == (512, 512)


def test_manifest_points_at_files_that_exist():
    import json
    manifest = json.loads((ICONS / "site.webmanifest").read_text())
    for icon in manifest["icons"]:
        served = icon["src"].removeprefix("/static/icons/")
        assert (ICONS / served).is_file(), f"manifest names a missing {served}"


def test_favicon_ico_is_served_from_the_root(client):
    """Browsers ask for /favicon.ico whether or not a page links one."""
    r = client.get("/favicon.ico")
    assert r.status_code == 200
    assert r.content[:4] == b"\x00\x00\x01\x00"  # ICO magic


def test_the_head_carries_the_icon_links(client):
    head = client.get("/").text.split("</head>")[0]
    assert 'rel="apple-touch-icon" href="/static/icons/apple-touch-icon.png"' in head
    assert 'rel="icon" href="/static/icons/favicon.svg"' in head
    assert 'rel="manifest" href="/static/icons/site.webmanifest"' in head
    assert 'name="theme-color" content="#0f1115"' in head


def test_the_homescreen_label_fits_ios(client):
    """iOS clips a homescreen label around 12 characters — which is the whole
    reason this isn't just the page title."""
    head = client.get("/").text.split("</head>")[0]
    marker = 'name="apple-mobile-web-app-title" content="'
    label = head.split(marker)[1].split('"')[0]
    assert len(label) <= 12, f"{label!r} will be clipped under the icon"


def test_the_shortcut_opens_in_safari_not_standalone(client):
    """Chrome-less mode loses the back gesture and has a history of re-asking
    for /admin's HTTP Basic credentials on every launch. Turning it on is a
    decision to make on a phone, not a tag to add in passing."""
    head = client.get("/").text.split("</head>")[0]
    assert "apple-mobile-web-app-capable" not in head
