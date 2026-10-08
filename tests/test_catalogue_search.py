"""
Offline tests for search-to-rate (tests/test_catalogue_search.py)

Throwaway SQLite DB in a tempdir; TMDB is never called.

    uv run python tests/test_catalogue_search.py
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import DatabaseManager  # noqa: E402
from services.catalogue_search import (  # noqa: E402
    CatalogueSearch, match_rank, normalise, tmdb_details_to_row,
)

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} — got: {detail}")
        failures.append(label)


TITLES = [
    # media_type, tmdb_id, title, original_title, year, votes
    ("movie", 1, "Amélie", "Le Fabuleux Destin d'Amélie Poulain", 2001, 11000),
    ("movie", 2, "Spider-Man", None, 2002, 18000),
    ("movie", 3, "Spider-Man: Into the Spider-Verse", None, 2018, 15000),
    ("movie", 4, "The Amazing Spider-Man", None, 2012, 17000),
    ("movie", 5, "Up", None, 2009, 20000),
    ("movie", 6, "Upgrade", None, 2018, 4000),
    ("movie", 7, "Shut Up and Dance", None, 2016, 50),
    ("tv", 8, "Up", None, 2020, 10),
    ("movie", 9, "Crouching Tiger, Hidden Dragon", "臥虎藏龍", 2000, 3000),
    ("movie", 10, "Fast & Furious", None, 2009, 9000),
]


def test_normalise():
    print("Normalise and rank")
    check("accents, case, punctuation", normalise("  Amélie: Le Fabuleux! ") == "amelie le fabuleux")
    check("& becomes and", normalise("Fast & Furious") == "fast and furious")
    check("apostrophes vanish", normalise("Schindler's List") == "schindlers list")
    check("exact", match_rank("up", "up") == 0)
    check("spaces don't matter: spiderman = spider man", match_rank("spiderman", "spider man") == 0)
    check("starts with", match_rank("spider", "spider man into the spider verse") == 1)
    check("a word starts with", match_rank("spider", "the amazing spider man") == 2)
    check("contains", match_rank("pgra", "upgrade") == 3)
    check("all words, any order", match_rank("dragon tiger", "crouching tiger hidden dragon") == 4)
    check("no match", match_rank("zzz", "up") is None)


def test_search(tmp):
    print("Search")
    db = DatabaseManager(os.path.join(tmp, "s.db"))
    with db.get_connection() as conn:
        for mt, tid, title, orig, year, votes in TITLES:
            conn.execute(
                "INSERT INTO media_items (media_type, tmdb_id, title, original_title, release_year, vote_count) "
                "VALUES (?, ?, ?, ?, ?, ?)", (mt, tid, title, orig, year, votes))
        conn.execute("INSERT INTO watch_history (media_item_id, user_rating, profile_id, status) VALUES (5, 9, 1, 'seen')")
        conn.execute("INSERT INTO watch_history (media_item_id, profile_id, status) VALUES (2, 1, 'not_seen')")
    cs = CatalogueSearch(db)

    def titles(q, media_type="all", limit=10):
        return [(r["title"], r["media_type"]) for r in cs.search(q, media_type, limit)]

    check("accent-insensitive", titles("amelie")[:1] == [("Amélie", "movie")], titles("amelie"))
    check("original title matches", titles("fabuleux") == [("Amélie", "movie")])
    check("exact first, then starts-with, then word, by votes",
          [t for t, _ in titles("spider-man")] == ["Spider-Man", "Spider-Man: Into the Spider-Verse",
                                                  "The Amazing Spider-Man"], titles("spider-man"))
    check("'up': exact matches first (most votes first), then starts-with, then word",
          titles("up") == [("Up", "movie"), ("Up", "tv"), ("Upgrade", "movie"), ("Shut Up and Dance", "movie")],
          titles("up"))
    check("media type filter", titles("up", "tv") == [("Up", "tv")])
    check("& matches 'and'", titles("fast and furious")[:1] == [("Fast & Furious", "movie")])
    check("1 character returns nothing", cs.search("u") == [])
    check("limit", len(cs.search("spider", limit=2)) == 2)

    mine = {r["id"]: r["my_verdict"] for r in cs.search("up", profile_id=1)}
    check("your own rating comes back", mine[5] == {"status": "seen", "user_rating": 9.0}, mine)
    nots = cs.search("spider-man", profile_id=1)[0]["my_verdict"]
    check("'haven't seen it' comes back too", nots == {"status": "not_seen", "user_rating": None}, nots)
    check("no profile, no verdict", cs.search("up")[0]["my_verdict"] is None)

    with db.get_connection() as conn:
        conn.execute("INSERT INTO media_items (media_type, tmdb_id, title, vote_count) VALUES ('movie', 11, 'Uptown Girls', 900)")
    check("index notices new titles", "Uptown Girls" in [t for t, _ in titles("uptown")])


def test_tmdb_mapping():
    print("TMDB mapping")
    movie = tmdb_details_to_row({
        "id": 7, "title": "Paddington 2", "original_title": "Paddington 2", "release_date": "2017-11-09",
        "genres": [{"id": 35, "name": "Comedy"}, {"id": 10751, "name": "Family"}], "runtime": 104,
        "vote_average": 7.567, "vote_count": 3100, "poster_path": "/p.jpg", "original_language": "en",
        "production_countries": [{"iso_3166_1": "GB"}, {"iso_3166_1": "FR"}],
    }, "movie")
    check("movie row", movie["genres"] == "Comedy, Family" and movie["release_year"] == 2017
          and movie["runtime"] == 104 and movie["origin_country"] == "GB,FR" and movie["rating"] == 7.6, movie)
    tv = tmdb_details_to_row({"id": 9, "name": "Fleabag", "first_air_date": "2016-07-21",
                              "episode_run_time": [27], "origin_country": ["GB"], "genres": []}, "tv")
    check("tv row", tv["title"] == "Fleabag" and tv["runtime"] == 27 and tv["origin_country"] == "GB"
          and tv["genres"] is None, tv)


if __name__ == "__main__":
    tmp = tempfile.mkdtemp()
    try:
        test_normalise()
        test_search(tmp)
        test_tmdb_mapping()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{'ALL PASSED' if not failures else f'{len(failures)} FAILED'}")
    sys.exit(1 if failures else 0)
