#!/usr/bin/env python3
"""Command-line ingestion — handy for cron or a first manual run.

Usage:
    python -m scripts.run_ingest init                 # create tables
    python -m scripts.run_ingest plex                 # sync Plex library
    python -m scripts.run_ingest letterboxd <user> <zip>
    python -m scripts.run_ingest import-dir           # all ZIPs in IMPORT_DIR
    python -m scripts.run_ingest enrich [limit]
    python -m scripts.run_ingest all                  # plex + import-dir + enrich
"""
from __future__ import annotations

import sys

from app import db, enrich, letterboxd, logs, plex_sync
from app.config import get_settings


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    cmd, *rest = argv
    # Each command's own result is `print`ed below — this is a terminal tool and
    # that is its output. `configure` is here for the *other* stream: the
    # warnings `app.*` modules log (a TMDb lookup that failed, an OMDb miss),
    # which otherwise reach an unconfigured root logger and are lost.
    # `LOG_FORMAT=text` is the setting to use when running this by hand.
    s = get_settings()
    logs.configure(s.log_level, s.log_format)
    db.init_db()

    if cmd == "init":
        print("Database initialised.")
    elif cmd == "plex":
        print(plex_sync.sync_plex())
    elif cmd == "letterboxd":
        user, zip_path = rest
        print(letterboxd.import_letterboxd(user, zip_path))
    elif cmd == "import-dir":
        for r in letterboxd.import_from_dir():
            print(r)
    elif cmd == "enrich":
        limit = int(rest[0]) if rest else None
        print(enrich.enrich_tmdb(limit))
    elif cmd == "all":
        print(plex_sync.sync_plex())
        for r in letterboxd.import_from_dir():
            print(r)
        print(enrich.enrich_tmdb())
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
