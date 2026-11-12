"""Central configuration, loaded from environment / .env."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Plex
    plex_baseurl: str = "http://localhost:32400"
    plex_token: str = "changeme"
    plex_movie_library: str = "Movies"

    # TMDb
    tmdb_api_key: str = "changeme"
    tmdb_min_interval: float = 0.30

    # External scores shown on the film detail card. OMDb (free key) is the
    # only practical source of a Rotten Tomatoes tomatometer; blank disables
    # RT/IMDb/Metacritic and the card just shows the rest.
    omdb_api_key: str = ""
    # Letterboxd has no public API, so its average can only come from reading
    # the public film page — which their terms of use prohibit ("you must not
    # employ any robot, spider, scraper... to access any portion of the
    # Service"), even though robots.txt permits /film/ and /tmdb/. Off by
    # default, and an explicit opt-in rather than something you inherit.
    letterboxd_scores: bool = False
    ratings_ttl_hours: float = 168.0  # a week; scores barely move

    # Logging. `json` is one object per line for `docker logs` and anything
    # reading it; `text` is the readable form for a terminal, which is where
    # the scripts in `scripts/` mostly run. See app/logs.py.
    log_level: str = "INFO"
    log_format: str = "json"

    # App
    # The household's clock. The container runs on UTC, so anything that means
    # "tonight" (a "Not tonight" lasts until 6 a.m.) reads the time here.
    local_tz: str = "UTC"
    database_path: str = "./data/watch.db"
    import_dir: str = "./data/imports"
    user_a: str = "alex"
    user_b: str = "sam"

    # Letterboxd usernames (for automated RSS + watchlist sync), mapped to the
    # app users above. Leave blank to skip a user in the sync jobs.
    letterboxd_a: str = ""
    letterboxd_b: str = ""
    # The watchlist sync reads the public watchlist pages and each new film's
    # page — the same automated access the terms prohibit (see letterboxd_scores
    # below), so it is opt-in too. Off, the watchlist comes from the export
    # ZIP's watchlist.csv, which is your own data and needs no scraping. RSS is
    # unaffected: it reads the feed Letterboxd publishes for feed readers.
    letterboxd_watchlist_scrape: bool = False

    # Optional push notifications: an ntfy topic URL (https://ntfy.sh/<topic>)
    # or a Discord webhook URL. Blank disables. Used by the Plex sync to
    # announce new arrivals that are on both watchlists.
    notify_url: str = ""

    # `/admin/*` login (HTTP Basic, app/auth.py). Both blank ships the admin
    # page *locked* — 503 with a pointer here — never open. Roadmap item 1.
    admin_user: str = ""
    admin_password: str = ""

    @property
    def users(self) -> list[str]:
        return [self.user_a, self.user_b]

    @property
    def letterboxd_map(self) -> dict[str, str]:
        """app username -> Letterboxd username, only for users that set one."""
        pairs = {self.user_a: self.letterboxd_a, self.user_b: self.letterboxd_b}
        return {u: lb for u, lb in pairs.items() if lb}

    # Directories are created once in db.init_db(), which every entry point
    # (app startup, scripts) runs first — not here, so property access stays
    # side-effect-free on hot paths.
    @property
    def db_path(self) -> Path:
        return Path(self.database_path)

    @property
    def import_path(self) -> Path:
        return Path(self.import_dir)


@lru_cache
def get_settings() -> Settings:
    return Settings()
