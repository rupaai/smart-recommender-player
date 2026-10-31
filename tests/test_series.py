"""
Offline tests for series order + watched candidates (tests/test_series.py)

Throwaway SQLite DB in a tempdir — no network, real catalogues untouched.

    uv run python tests/test_series.py
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import DatabaseManager  # noqa: E402
from services.recommender_service import apply_series_order  # noqa: E402

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} — got: {detail}")
        failures.append(label)


FILMS = [
    # title, year, genres, tmdb_id, collection_id, collection_order, checked
    ("Harry Potter and the Philosopher's Stone", 2001, "Adventure, Fantasy", 671, 1241, 1, 1),
    ("Harry Potter and the Chamber of Secrets", 2002, "Adventure, Fantasy", 672, 1241, 2, 1),
    ("Harry Potter and the Prisoner of Azkaban", 2004, "Adventure, Fantasy", 673, 1241, 3, 1),
    ("Harry Potter and the Deathly Hallows: Part 1", 2010, "Adventure, Fantasy", 12444, 1241, 7, 1),
    ("Toy Story", 1995, "Animation, Comedy, Family", 862, None, None, 0),
    ("Toy Story 2", 1999, "Animation, Comedy, Family", 863, None, None, 0),
    ("Toy Story 3", 2010, "Animation, Comedy, Family", 10193, None, None, 0),
    ("Super", 2010, "Comedy, Drama, Action", 1001, None, None, 0),
    ("Super 8", 2011, "Thriller, Science Fiction, Mystery", 1002, None, None, 0),
    ("Inception", 2010, "Action, Science Fiction", 27205, None, None, 1),
    ("Shrek", 2001, "Animation, Comedy", 808, None, None, 1),     # TMDB-checked standalone
    ("Shrek 2", 2004, "Animation, Comedy", 809, None, None, 1),   # -> fallback must NOT apply
]


def build(path):
    db = DatabaseManager(path)
    with db.get_connection() as conn:
        for title, year, genres, tmdb, cid, order, checked in FILMS:
            conn.execute(
                "INSERT INTO media_items (title, release_year, media_type, rating, vote_count, "
                "genres, tmdb_id, collection_id, collection_name, collection_order, collection_checked) "
                "VALUES (?, ?, 'movie', 7.5, 5000, ?, ?, ?, ?, ?, ?);",
                (title, year, genres, tmdb, cid, "Harry Potter Collection" if cid else None, order, checked),
            )
    return db


def ids_by_title(db):
    with db.get_connection() as conn:
        return {r["title"]: r["id"] for r in conn.execute("SELECT id, title FROM media_items")}


def ranked(db, titles):
    t = ids_by_title(db)
    return [{"id": t[x], "title": x, "media_type": "movie", "recommendation_score": 10 - i}
            for i, x in enumerate(titles)]


def see(db, item_id, rating=8.0, profile=1, status="seen", when="2026-10-01 10:00:00"):
    with db.get_connection() as conn:
        conn.execute(
            "INSERT INTO watch_history (media_item_id, profile_id, user_rating, status, watched_at) "
            "VALUES (?, ?, ?, ?, ?);",
            (item_id, profile, rating if status == "seen" else None, status, when),
        )


def test_series(tmp):
    print("\nSeries order")
    db = build(os.path.join(tmp, "s.db"))
    t = ids_by_title(db)

    with db.get_connection() as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    check("migration v5 sets user_version >= 5", version >= 5, version)

    out = apply_series_order(ranked(db, ["Harry Potter and the Deathly Hallows: Part 1", "Inception"]), db)
    check("unseen sequel replaced by film #1",
          out[0]["title"] == "Harry Potter and the Philosopher's Stone", [o["title"] for o in out])
    check("replacement keeps the sequel's slot/score",
          out[0]["recommendation_score"] == 10 and out[1]["title"] == "Inception", out)
    check("replacement explains itself", "Watch first" in out[0].get("series_note", ""), out[0])

    out = apply_series_order(ranked(db, ["Harry Potter and the Prisoner of Azkaban",
                                         "Harry Potter and the Deathly Hallows: Part 1",
                                         "Harry Potter and the Chamber of Secrets"]), db)
    check("one film per series", len(out) == 1, [o["title"] for o in out])

    see(db, t["Harry Potter and the Philosopher's Stone"])
    out = apply_series_order(ranked(db, ["Harry Potter and the Deathly Hallows: Part 1"]), db)
    check("after #1 is seen, #2 is next", out[0]["title"] == "Harry Potter and the Chamber of Secrets",
          out[0]["title"])

    see(db, t["Harry Potter and the Chamber of Secrets"], status="not_seen")
    out = apply_series_order(ranked(db, ["Harry Potter and the Deathly Hallows: Part 1"]), db)
    check("'haven't seen it' does not count as seen",
          out[0]["title"] == "Harry Potter and the Chamber of Secrets", out[0]["title"])

    out = apply_series_order(ranked(db, ["Toy Story 3"]), db)
    check("title fallback: Toy Story 3 -> Toy Story", out[0]["title"] == "Toy Story", out[0]["title"])
    see(db, t["Toy Story"])
    out = apply_series_order(ranked(db, ["Toy Story 3"]), db)
    check("title fallback walks the numbers: -> Toy Story 2", out[0]["title"] == "Toy Story 2",
          out[0]["title"])

    out = apply_series_order(ranked(db, ["Super 8"]), db)
    check("Super 8 is not a sequel to Super (no shared genre)", out[0]["title"] == "Super 8",
          out[0]["title"])
    out = apply_series_order(ranked(db, ["Shrek 2"]), db)
    check("TMDB-confirmed standalone skips the title fallback", out[0]["title"] == "Shrek 2",
          out[0]["title"])

    tv = [{"id": 999, "title": "Some Show 2", "media_type": "tv"}]
    check("TV items untouched", apply_series_order(tv, db) == tv)


def test_history(tmp):
    print("\nWatched films and candidates")
    db = build(os.path.join(tmp, "h.db"))
    t = ids_by_title(db)
    see(db, t["Toy Story"], 9, profile=1, when="2026-09-01 10:00:00")
    see(db, t["Inception"], 3, profile=1, when="2026-09-02 10:00:00")          # disliked
    see(db, t["Shrek"], None, profile=1, status="not_seen", when="2026-09-03 10:00:00")
    see(db, t["Super"], 8, profile=2, when="2026-09-04 10:00:00")
    see(db, t["Toy Story 2"], 7, profile=1, when="2026-09-05 10:00:00")

    rows = db.get_candidates(media_type="movie", exclude_watched=True)
    cand_ids = {r["id"] for r in rows}
    check("'haven't seen it' films stay recommendable", t["Shrek"] in cand_ids)
    check("seen films are excluded", t["Toy Story"] not in cand_ids)


if __name__ == "__main__":
    tmp = tempfile.mkdtemp()
    try:
        test_series(tmp)
        test_history(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{'ALL PASSED' if not failures else f'{len(failures)} FAILED'}")
    sys.exit(1 if failures else 0)
