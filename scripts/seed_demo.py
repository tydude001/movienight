#!/usr/bin/env python3
"""Seed the DB with a made-up couple so you can click around the UI without
real Plex/Letterboxd/TMDb access. NOT for production — just a sandbox, and the
source of every README screenshot.

    python -m scripts.seed_demo

Deterministic: the same two people every run. The first user leans to sci-fi
and Nolan, the second to drama, romance and animation, so Tonight's pair score,
the Taste page and Wrapped all have something to say. No posters: the grid
shows its placeholders, and no studio art ends up in a screenshot.
"""
from __future__ import annotations

from app import db
from app.config import get_settings

# (tmdb_id, title, year, genres, director, runtime)
HISTORY = [
    (27205, "Inception", 2010, "Action, Sci-Fi", "Christopher Nolan", 148),
    (155, "The Dark Knight", 2008, "Action, Crime, Drama", "Christopher Nolan", 152),
    (1124, "The Prestige", 2006, "Drama, Mystery, Sci-Fi", "Christopher Nolan", 130),
    (680, "Pulp Fiction", 1994, "Thriller, Crime", "Quentin Tarantino", 154),
    (807, "Se7en", 1995, "Crime, Mystery, Thriller", "David Fincher", 127),
    (496243, "Parasite", 2019, "Comedy, Thriller, Drama", "Bong Joon-ho", 132),
    (419430, "Get Out", 2017, "Mystery, Thriller, Horror", "Jordan Peele", 104),
    (244786, "Whiplash", 2014, "Drama, Music", "Damien Chazelle", 107),
    (152601, "Her", 2013, "Romance, Sci-Fi, Drama", "Spike Jonze", 126),
    (550, "Fight Club", 1999, "Drama", "David Fincher", 139),
    (603, "The Matrix", 1999, "Action, Sci-Fi", "Lana Wachowski", 136),
    (76341, "Mad Max: Fury Road", 2015, "Action, Adventure, Sci-Fi", "George Miller", 121),
    (13, "Forrest Gump", 1994, "Comedy, Drama, Romance", "Robert Zemeckis", 142),
    (129, "Spirited Away", 2001, "Animation, Fantasy, Family", "Hayao Miyazaki", 125),
    (194, "Amélie", 2001, "Comedy, Romance", "Jean-Pierre Jeunet", 122),
]

UNSEEN = [
    (438631, "Dune", 2021, "Sci-Fi, Adventure", "Denis Villeneuve", 155),
    (278, "The Shawshank Redemption", 1994, "Drama, Crime", "Frank Darabont", 142),
    (637, "Life Is Beautiful", 1997, "Comedy, Drama", "Roberto Benigni", 116),
    (105, "Back to the Future", 1985, "Adventure, Comedy, Sci-Fi", "Robert Zemeckis", 116),
    (348, "Alien", 1979, "Horror, Sci-Fi", "Ridley Scott", 117),
    (78, "Blade Runner", 1982, "Sci-Fi, Drama", "Ridley Scott", 117),
    (238, "The Godfather", 1972, "Crime, Drama", "Francis Ford Coppola", 175),
    (274, "The Silence of the Lambs", 1991, "Crime, Drama, Thriller", "Jonathan Demme", 119),
    (914, "The Great Dictator", 1940, "Comedy, Drama, War", "Charlie Chaplin", 125),
    (4935, "Howl's Moving Castle", 2004, "Animation, Fantasy, Romance", "Hayao Miyazaki", 119),
    (12477, "Grave of the Fireflies", 1988, "Animation, Drama, War", "Isao Takahata", 89),
    (489, "Good Will Hunting", 1997, "Drama, Romance", "Gus Van Sant", 126),
    (530385, "Midsommar", 2019, "Drama, Horror, Mystery", "Ari Aster", 148),
    (497, "The Green Mile", 1999, "Fantasy, Drama, Crime", "Frank Darabont", 189),
]

# Who watched what, when, and how each of you rated it. The same date for both
# is a night together, which is what Wrapped counts.
#   tmdb_id: (date, rating_a, date_b, rating_b) — None where one didn't watch.
WATCHES = {
    27205: ("2026-01-17", 5.0, "2026-01-17", 4.0),
    155: ("2026-02-07", 5.0, "2026-02-07", 3.5),
    1124: ("2026-02-21", 4.5, "2026-02-21", 4.5),
    680: ("2026-03-14", 4.0, "2026-03-14", 3.0),
    807: ("2026-04-04", 4.0, "2026-04-04", 3.5),
    496243: ("2026-05-02", 4.5, "2026-05-02", 5.0),
    419430: ("2026-06-13", 3.5, "2026-06-13", 4.0),
    244786: ("2026-08-08", 4.0, "2026-08-08", 4.5),
    152601: ("2026-09-19", 3.0, "2026-09-19", 5.0),
    550: ("2026-03-03", 4.5, None, None),
    603: ("2026-05-20", 5.0, None, None),
    76341: ("2026-07-11", 4.5, None, None),
    13: (None, None, "2026-04-22", 4.5),
    129: (None, None, "2026-06-28", 5.0),
    194: (None, None, "2026-10-03", 4.5),
}

CAST = {
    27205: ["Leonardo DiCaprio", "Joseph Gordon-Levitt", "Elliot Page", "Tom Hardy", "Michael Caine"],
    155: ["Christian Bale", "Heath Ledger", "Aaron Eckhart", "Michael Caine", "Gary Oldman"],
    1124: ["Hugh Jackman", "Christian Bale", "Michael Caine", "Scarlett Johansson"],
    680: ["John Travolta", "Samuel L. Jackson", "Uma Thurman", "Bruce Willis"],
    807: ["Brad Pitt", "Morgan Freeman", "Gwyneth Paltrow", "Kevin Spacey"],
    496243: ["Song Kang-ho", "Lee Sun-kyun", "Cho Yeo-jeong", "Choi Woo-shik"],
    419430: ["Daniel Kaluuya", "Allison Williams", "Catherine Keener", "Bradley Whitford"],
    244786: ["Miles Teller", "J.K. Simmons", "Paul Reiser", "Melissa Benoist"],
    152601: ["Joaquin Phoenix", "Scarlett Johansson", "Amy Adams", "Rooney Mara"],
}

# Watchlists: the first user's, the second's; both lists overlap on four.
WATCHLIST_A = [438631, 278, 637, 105, 348, 78, 238, 274, 914]
WATCHLIST_B = [438631, 278, 637, 105, 4935, 12477, 489, 530385, 497]
# Not on Plex yet — these fill the Get-list.
OFF_PLEX = {238, 4935, 497, 12477}


def main() -> None:
    a, b = get_settings().users
    db.init_db()
    with db.cursor() as cur:
        for tid, title, year, genres, director, runtime in HISTORY + UNSEEN:
            db.upsert_film(cur, tid, title, year)
            db.set_enrichment(cur, tid, genres=genres, director=director,
                              runtime=runtime, poster=None)
            db.upsert_availability(cur, tid, available=int(tid not in OFF_PLEX),
                                   watched=0, added="2026-01-15")
        for tid, (date_a, rating_a, date_b, rating_b) in WATCHES.items():
            for user, date, rating in ((a, date_a, rating_a), (b, date_b, rating_b)):
                if date is None:
                    continue
                db.upsert_watched(cur, user, tid, date)
                db.upsert_rating(cur, user, tid, rating, date)
                db.upsert_diary(cur, user, tid, date, rating, False)
        for tid, names in CAST.items():
            db.set_cast(cur, tid, names)
        for tid in WATCHLIST_A:
            db.upsert_watchlist(cur, a, tid)
        for tid in WATCHLIST_B:
            db.upsert_watchlist(cur, b, tid)
    print(f"Seeded {len(HISTORY) + len(UNSEEN)} demo films for {a} & {b}.")


if __name__ == "__main__":
    main()
