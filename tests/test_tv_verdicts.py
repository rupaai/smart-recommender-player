"""
Offline tests for per-season TV verdicts (tests/test_tv_verdicts.py)

Throwaway SQLite DB in a tempdir.

    uv run python tests/test_tv_verdicts.py
"""

import os
import shutil
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import DatabaseManager  # noqa: E402
from services import verdicts as vd  # noqa: E402

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} — got: {detail}")
        failures.append(label)


SHOW, SHORT, FILES_ONLY, FILM = 1, 2, 3, 4


def make_db(tmp, name="tv.db"):
    db = DatabaseManager(os.path.join(tmp, name))
    with db.get_connection() as conn:
        conn.execute("INSERT INTO media_items (id, media_type, tmdb_id, title, genres, season_count) "
                     "VALUES (?, 'tv', 101, 'Long Show', 'Drama', 5)", (SHOW,))
        conn.execute("INSERT INTO media_items (id, media_type, tmdb_id, title, genres, season_count) "
                     "VALUES (?, 'tv', 102, 'Mini Series', 'Drama', 1)", (SHORT,))
        conn.execute("INSERT INTO media_items (id, media_type, tmdb_id, title, genres) "
                     "VALUES (?, 'tv', 103, 'Files Show', 'Comedy')", (FILES_ONLY,))
        conn.execute("INSERT INTO media_items (id, media_type, tmdb_id, title, genres) "
                     "VALUES (?, 'movie', 104, 'A Film', 'Drama')", (FILM,))
        for season in (1, 2, 3):
            conn.execute("INSERT INTO media_sources (media_item_id, file_path, parsed_title, season_number, episode_number) "
                         "VALUES (?, ?, 'Files Show', ?, 1)", (FILES_ONLY, f"/tv/files.s0{season}e01.mkv", season))
    return db


def test_maths():
    print("Season maths")
    check("season score reuses the verdict scores", vd.season_score("loved") == vd.taste_score("loved"))
    check("didn't finish alone scores the cap", vd.season_score(None, True) == vd.DID_NOT_FINISH_CAP)
    check("nothing chosen, no score", vd.season_score(None, False) is None)
    check("later seasons weigh more", vd.season_weight(4) > vd.season_weight(1))
    check("weight is capped", vd.season_weight(30) == vd.SEASON_WEIGHT_CAP)
    seasons = [
        {"season_number": 1, "verdict": "loved", "did_not_finish": False},
        {"season_number": 2, "verdict": "loved", "did_not_finish": False},
        {"season_number": 4, "verdict": None, "did_not_finish": True},
    ]
    d = vd.derive_show_score(seasons)
    plain_mean = (0.6 + 0.6 - 0.5) / 3
    check("loved S1-S2, dropped S4: below the plain mean", d["taste_score"] < plain_mean, d)
    check("...and records dropped at season 4", d["dropped_at_season"] == 4, d)
    check("...still lands on a mild verdict", d["verdict"] in ("liked", "meh"), d)
    check("no seasons, nothing derived", vd.derive_show_score([]) is None)
    check("nearest verdict", vd.nearest_verdict(0.95) == "rewatch" and vd.nearest_verdict(-0.4) == "meh")


def test_migration(tmp):
    print("Migration")
    db = make_db(tmp, "mig.db")
    with db.get_connection() as conn:
        version = conn.execute("PRAGMA user_version;").fetchone()[0]
        cols = {r[1] for r in conn.execute("PRAGMA table_info(media_items);")}
        rd_cols = {r[1] for r in conn.execute("PRAGMA table_info(rating_details);")}
        sr_cols = {r[1] for r in conn.execute("PRAGMA table_info(season_ratings);")}
    check("user_version is at least 10", version >= 10, version)
    check("media_items.season_count added", {"season_count", "season_count_checked"} <= cols)
    check("rating_details gains inferred_source / dropped_at_season", {"inferred_source", "dropped_at_season"} <= rd_cols)
    check("season_ratings has the asked-for columns",
          {"profile_id", "media_item_id", "season_number", "verdict", "did_not_finish", "taste_score", "updated_at"} <= sr_cols)
    try:
        with db.get_connection() as conn:
            for _ in range(2):
                conn.execute("INSERT INTO season_ratings (profile_id, media_item_id, season_number, verdict, taste_score) "
                             "VALUES (1, 1, 1, 'liked', 0.2)")
        check("UNIQUE(profile, show, season)", False)
    except sqlite3.IntegrityError:
        check("UNIQUE(profile, show, season)", True)

    # A v8 database with an old inferred rating upgrades cleanly, twice.
    path = os.path.join(tmp, "old.db")
    DatabaseManager(path)
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE season_ratings")
        conn.execute("PRAGMA user_version = 8")
    again = DatabaseManager(path)
    DatabaseManager(path)
    with again.get_connection() as conn:
        ok = conn.execute("SELECT name FROM sqlite_master WHERE name = 'season_ratings'").fetchone()
        version = conn.execute("PRAGMA user_version;").fetchone()[0]
    check("re-runs on a v8 DB and is idempotent", ok is not None and version >= 10, version)


def test_season_counts(tmp):
    print("Season counts")
    db = make_db(tmp, "counts.db")
    check("TMDB count wins", db.get_season_count(SHOW) == 5)
    check("files fall back to the highest season", db.get_season_count(FILES_ONLY) == 3)
    check("films have no season count", db.get_season_count(FILM) is None)
    filled = db.fill_season_counts_from_files()
    with db.get_connection() as conn:
        stored = conn.execute("SELECT season_count, season_count_checked FROM media_items WHERE id = ?",
                              (FILES_ONLY,)).fetchone()
    check("offline pass stores the file count, leaves it unchecked",
          filled == 1 and stored[0] == 3 and stored[1] == 0, (filled, tuple(stored)))
    pending = {r["id"] for r in db.get_tv_missing_season_count()}
    check("TMDB pass would look up unchecked shows", {SHOW, SHORT, FILES_ONLY} <= pending, pending)
    db.set_season_count(FILES_ONLY, 4)
    check("TMDB count overrides the file guess", db.get_season_count(FILES_ONLY) == 4)
    check("checked shows leave the TMDB list", FILES_ONLY not in {r["id"] for r in db.get_tv_missing_season_count()})


def test_write_read(tmp):
    print("Season write / read")
    db = make_db(tmp, "rw.db")
    out = db.rate_season(SHOW, 1, 1, "loved")
    db.rate_season(SHOW, 1, 2, "liked")
    db.rate_season(SHOW, 1, 3, None, True)
    rows = db.get_season_ratings(SHOW, 1)
    check("three seasons stored", [r["season_number"] for r in rows] == [1, 2, 3], rows)
    check("didn't finish with no verdict", rows[2]["verdict"] is None and rows[2]["did_not_finish"]
          and rows[2]["taste_score"] == vd.DID_NOT_FINISH_CAP, rows[2])
    check("write returns the seasons block", out["seasons"]["count"] == 5 and len(out["seasons"]["ratings"]) == 1, out["seasons"])
    db.rate_season(SHOW, 1, 2, "meh")
    check("re-rating a season updates it", db.get_season_ratings(SHOW, 1)[1]["verdict"] == "meh")
    db.rate_season(SHOW, 1, 2, None, False)
    check("empty clears a season", [r["season_number"] for r in db.get_season_ratings(SHOW, 1)] == [1, 3])
    check("profiles are separate", db.get_season_ratings(SHOW, 2) == [])
    for bad, args in (("film", (FILM, 1, 1, "liked")), ("unknown verdict", (SHOW, 1, 1, "fantastic")),
                      ("season 0", (SHOW, 1, 0, "liked")), ("unknown show", (999, 1, 1, "liked"))):
        try:
            db.rate_season(*args)
            check(f"rejects {bad}", False)
        except ValueError:
            check(f"rejects {bad}", True)


def test_derived_show_score(tmp):
    print("Derived show score")
    db = make_db(tmp, "derived.db")
    db.rate_season(SHOW, 1, 1, "loved")
    db.rate_season(SHOW, 1, 2, "loved")
    db.rate_season(SHOW, 1, 4, None, True)
    d = db.get_rating_details(SHOW, 1)
    expected = vd.derive_show_score(db.get_season_ratings(SHOW, 1))
    check("show gets the derived score", d and d["taste_score"] == expected["taste_score"], d)
    check("...marked inferred, from seasons", d["inferred"] and d["inferred_source"] == "seasons", d)
    check("...records dropped at season 4", d["dropped_at_season"] == 4, d)
    check("...not a show-level didn't finish", not d["did_not_finish"], d)
    summary = db.get_rating_summary(SHOW)
    mine = next(v for v in summary["verdicts"] if v["profile_id"] == 1)
    check("summary shows it as seen, from seasons",
          mine["status"] == "seen" and mine["inferred_source"] == "seasons"
          and mine["user_rating"] == vd.legacy_rating(expected["verdict"]), mine)
    dropped = db.get_dropped_shows(1)
    check("dropped shows list", [(r["media_item_id"], r["dropped_at_season"]) for r in dropped] == [(SHOW, 4)], dropped)
    queue_ids = [r["id"] for r in db.get_inferred_rating_queue(1, "tv")]
    check("season-derived shows stay out of 'Sort out my 7s'", SHOW not in queue_ids, queue_ids)

    # Readers use the derived score.
    db.rate_season(SHORT, 1, 1, "rewatch")
    taste = db.get_profile_taste_profile(1)
    check("taste profile reads the derived score", taste["rating_count"] >= 1 and "drama" in taste["preferred_genres"], taste)

    for s in (1, 2, 4):
        db.rate_season(SHOW, 1, s, None, False)
    check("clearing every season removes the derived verdict", db.get_rating_details(SHOW, 1) is None)
    summary = db.get_rating_summary(SHOW)
    check("...and the seen row it made", not any(v["profile_id"] == 1 for v in summary["verdicts"]), summary["verdicts"])


def test_show_verdict_wins(tmp):
    print("Show verdict wins")
    db = make_db(tmp, "wins.db")
    db.rate_item(SHOW, 1, verdict="loved")
    db.rate_season(SHOW, 1, 1, "not_for_me")
    db.rate_season(SHOW, 1, 3, None, True)
    d = db.get_rating_details(SHOW, 1)
    check("confirmed show verdict untouched by seasons",
          d["verdict"] == "loved" and d["taste_score"] == vd.taste_score("loved") and not d["inferred"], d)
    check("...but where they stopped is recorded", d["dropped_at_season"] == 3, d)

    # Seasons first, then a show verdict: the show verdict takes over.
    db.rate_season(SHORT, 1, 1, "meh")
    db.rate_item(SHORT, 1, verdict="rewatch")
    d = db.get_rating_details(SHORT, 1)
    check("show verdict replaces a derived one", d["verdict"] == "rewatch" and not d["inferred"]
          and d["inferred_source"] is None, d)
    db.rate_season(SHORT, 1, 1, "not_for_me")
    check("later season edits leave it alone", db.get_rating_details(SHORT, 1)["verdict"] == "rewatch")

    # An old 1-10 number's guessed verdict gives way to seasons.
    with db.get_connection() as conn:
        conn.execute("INSERT INTO watch_history (media_item_id, profile_id, user_rating, status) VALUES (?, 2, 8, 'seen')",
                     (FILES_ONLY,))
        conn.execute("INSERT INTO rating_details (profile_id, media_item_id, verdict, taste_score, inferred, inferred_source) "
                     "VALUES (2, ?, 'liked', 0.2, 1, 'rating')", (FILES_ONLY,))
    db.rate_season(FILES_ONLY, 2, 1, "not_for_me")
    d = db.get_rating_details(FILES_ONLY, 2)
    check("seasons replace a guess from an old rating", d["inferred_source"] == "seasons" and d["verdict"] == "not_for_me", d)
    with db.get_connection() as conn:
        hist = conn.execute("SELECT user_rating, verdict FROM rating_history WHERE profile_id = 2 AND media_item_id = ?",
                            (FILES_ONLY,)).fetchall()
    check("...the old state is kept in rating_history", [(h[0], h[1]) for h in hist] == [(8.0, "liked")], hist)


def test_one_season(tmp):
    print("One-season shows")
    db = make_db(tmp, "one.db")
    details = db.get_season_details(SHORT, 1)
    check("one-season show reports count 1 (the card hides the row)", details["count"] == 1, details)
    details = db.get_season_details(FILM, 1)
    check("a film has no seasons", details["count"] is None and details["ratings"] == [], details)
    db.rate_season(SHORT, 1, 2, "liked")
    check("a rated season past the count raises it", db.get_season_details(SHORT, 1)["count"] == 2)


def test_api(tmp):
    print("API")
    path = os.path.join(tmp, "api.db")
    make_db(tmp, "api.db")
    os.environ["DATABASE_PATH"] = path
    try:
        from fastapi.testclient import TestClient
        import server
    except Exception as e:  # pragma: no cover - deps missing
        print(f"  (skipped: {e})")
        return
    server.db = DatabaseManager(path)
    client = TestClient(server.app)
    r = client.post("/api/ratings/season", json={"media_item_id": SHOW, "profile_id": 1, "season_number": 2, "verdict": "loved"})
    check("POST /api/ratings/season", r.status_code == 200 and r.json()["seasons"]["ratings"][0]["season_number"] == 2, r.text)
    r = client.post("/api/ratings/season", json={"media_item_id": FILM, "profile_id": 1, "season_number": 1, "verdict": "loved"})
    check("season verdict on a film is a 400", r.status_code == 400, r.status_code)
    r = client.get(f"/api/ratings/details/{SHOW}?profile_id=1")
    body = r.json()
    check("details carries seasons", r.status_code == 200 and body["seasons"]["count"] == 5
          and body["details"]["inferred_source"] == "seasons", body.get("seasons"))
    r = client.post("/api/ratings", json={"media_item_id": SHOW, "profile_id": 1, "verdict": "meh"})
    check("POST /api/ratings still works", r.status_code == 200, r.text)
    r = client.get("/api/ratings/vocab")
    check("vocab lists the season extra", r.json().get("season_extra", {}).get("id") == "did_not_finish", r.json())


if __name__ == "__main__":
    tmp = tempfile.mkdtemp()
    try:
        test_maths()
        test_migration(tmp)
        test_season_counts(tmp)
        test_write_read(tmp)
        test_derived_show_score(tmp)
        test_show_verdict_wins(tmp)
        test_one_season(tmp)
        test_api(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if failures:
        print(f"\n{len(failures)} check(s) failed")
        sys.exit(1)
    print("\nAll TV verdict checks passed")
