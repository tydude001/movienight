"""Facts about the repository itself, not the app."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _tracked(*paths: str) -> list[str]:
    try:
        out = subprocess.run(["git", "ls-files", "--", *paths], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout
    except (FileNotFoundError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return out.split()


def test_data_dir_ships_empty():
    """data/ holds the database and people's Letterboxd exports. Only the
    placeholder that lets a fresh clone's first `docker compose up` bind-mount
    it may ever be tracked."""
    assert _tracked("data") == ["data/.gitkeep"]
