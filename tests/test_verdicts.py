"""
Offline tests for verdict ratings (tests/test_verdicts.py)

Throwaway SQLite DB in a tempdir.

    uv run python tests/test_verdicts.py
"""

import json
import os
import shutil
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


def make_db(tmp, name="v.db", films=12):
    db = DatabaseManager(os.path.join(tmp, name))
    with db.get_connection() as conn:
        for i in range(1, films + 1):
            conn.execute(
                "INSERT INTO media_items (id, media_type, tmdb_id, title, genres, rating, vote_count) "
                "VALUES (?, 'movie', ?, ?, ?, 7.0, 5000)",
                (i, i, f"Film {i}", "Drama, Comedy" if i % 2 else "Thriller"),
            )
    return db


def test_maths():
    print("Verdict maths")
    check("scores run -1..+1", vd.taste_score("rewatch") == 1.0 and vd.taste_score("not_for_me") == -1.0)
    check("no verdict, no score", vd.taste_score(None) is None)
    check("didn't finish caps a liked film", vd.taste_score("liked", True) == vd.DID_NOT_FINISH_CAP)
    check("didn't finish keeps a worse score", vd.taste_score("not_for_me", True) == -1.0)
    check("legacy rating: liked passes >= 7, meh does not",
          vd.legacy_rating("liked") >= 7.0 and vd.legacy_rating("meh") < 6.0)
    check("legacy rating: didn't finish is low", vd.legacy_rating("loved", True) <= 3.0)
    try:
        vd.validate_verdict("fantastic")
        check("unknown verdict rejected", False)
    except ValueError:
        check("unknown verdict rejected", True)
    check("chips: unknown dropped, dupes dropped", vd.clean_chips(["story", "nope", "story", "ending"]) == ["story", "ending"])
    vc = vd.clean_vibe_check({"leaves": "lifted", "darker": "sideways", "felt": ["cozy", "dreamy", "extra"], "bogus": "x"})
    check("vibe check: valid kept, invalid dropped", vc == {"leaves": "lifted", "felt": ["cozy", "dreamy"]}, vc)


def test_inference():
    print("Inferred verdicts (Labiba's real spread: 10x9, 25x8, 31x7, 7x6, 1x5)")
    ratings = [9.0] * 10 + [8.0] * 25 + [7.0] * 31 + [6.0] * 7 + [5.0]
    out = {r: vd.infer_verdict(r, ratings) for r in (9.0, 8.0, 7.0, 6.0, 5.0)}
    check("9 -> loved", out[9.0]["verdict"] == "loved", out[9.0])
    check("8 -> liked", out[8.0]["verdict"] == "liked", out[8.0])
    check("7 -> liked", out[7.0]["verdict"] == "liked", out[7.0])
    check("6 and 5 -> meh", out[6.0]["verdict"] == "meh" and out[5.0]["verdict"] == "meh", (out[6.0], out[5.0]))
    check("a high 8 scores above a low 7", out[8.0]["taste_score"] > out[7.0]["taste_score"], (out[8.0], out[7.0]))
    check("never guesses rewatch / not_for_me",
          all(o["verdict"] not in vd.EXTREME_VERDICTS for o in out.values()))
    check("9 still scores above any 8", out[9.0]["taste_score"] > out[8.0]["taste_score"])
    check("empty history is safe", vd.infer_verdict(7.0, [7.0])["verdict"] in vd.VERDICT_KEYS)


def test_migration(tmp):
    print("Migration v8 gives old ratings an inferred verdict")
    db = make_db(tmp, "m.db", films=12)
    # Put the DB back in its pre-v8 state with raw ratings.
    with db.get_connection() as conn:
        conn.execute("DROP TABLE rating_details")
        conn.execute("DROP TABLE rating_history")
        conn.execute("PRAGMA user_version = 7")
        for i, r in enumerate([9, 9, 8, 8, 8, 7, 7, 7, 7, 6], start=1):
            conn.execute(
                "INSERT INTO watch_history (media_item_id, profile_id, user_rating, status) VALUES (?, 1, ?, 'seen')",
                (i, float(r)),
            )
        conn.execute("INSERT INTO watch_history (media_item_id, profile_id, status) VALUES (?, 1, 'not_seen')", (11,))
    db2 = DatabaseManager(os.path.join(tmp, "m.db"))
    with db2.get_connection() as conn:
        rows = conn.execute("SELECT media_item_id, verdict, inferred FROM rating_details ORDER BY media_item_id").fetchall()
        raw = conn.execute("SELECT user_rating FROM watch_history WHERE media_item_id = 1").fetchone()[0]
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    check("one row per rated film, none for not_seen", len(rows) == 10, len(rows))
    check("all marked inferred", all(r["inferred"] == 1 for r in rows))
    check("raw rating untouched", raw == 9.0, raw)
    check("version is 8+", version >= 8, version)
    DatabaseManager(os.path.join(tmp, "m.db"))  # idempotent
    with db2.get_connection() as conn:
        n = conn.execute("SELECT COUNT(*) FROM rating_details").fetchone()[0]
    check("idempotent on reopen", n == 10, n)


def test_rate_item(tmp):
    print("rate_item with verdicts")
    db = make_db(tmp)
    s = db.rate_item(1, 1, verdict="loved", worked=["story", "ending"], didnt=["pace", "story"],
                     vibe_check={"leaves": "lifted"}, notes="Cried at the end")
    d = db.get_rating_details(1, 1)
    check("verdict stored, not inferred", d["verdict"] == "loved" and d["inferred"] is False, d)
    check("taste_score stored", d["taste_score"] == 0.6, d["taste_score"])
    check("chips stored; a chip can't be on both sides", d["worked"] == ["story", "ending"] and d["didnt"] == ["pace"], d)
    check("vibe check stored", d["vibe_check"] == {"leaves": "lifted"}, d["vibe_check"])
    check("legacy rating written", [v for v in s["verdicts"] if v["profile_id"] == 1][0]["user_rating"] == 9.0, s["verdicts"])
    check("summary carries the verdict", s["verdicts"][0]["verdict"] == "loved", s["verdicts"])

    db.rate_item(1, 1, verdict="meh")
    d = db.get_rating_details(1, 1)
    with db.get_connection() as conn:
        hist = conn.execute("SELECT verdict, user_rating, notes FROM rating_history WHERE media_item_id = 1").fetchall()
        notes = conn.execute("SELECT notes FROM watch_history WHERE media_item_id = 1").fetchone()[0]
    check("change of mind is kept in history", len(hist) == 1 and hist[0]["verdict"] == "loved" and hist[0]["user_rating"] == 9.0, [dict(h) for h in hist])
    check("re-rating without notes keeps the note", notes == "Cried at the end", notes)
    check("new verdict replaces the old", d["verdict"] == "meh", d)
    check("chips are kept when the new verdict sends none", d["worked"] == ["story", "ending"], d["worked"])
    db.rate_item(1, 1, verdict="meh", worked=[])
    check("an empty list clears them", db.get_rating_details(1, 1)["worked"] == [])

    db.rate_item(2, 1, verdict="liked", did_not_finish=True)
    d = db.get_rating_details(2, 1)
    check("didn't finish caps the score and the legacy rating",
          d["did_not_finish"] and d["taste_score"] == vd.DID_NOT_FINISH_CAP, d)

    db.rate_item(3, 1, user_rating=8)
    check("bare 1-10 rating works and leaves no verdict", db.get_rating_details(3, 1) is None)
    db.rate_item(1, 1, user_rating=6)
    check("bare rating removes a verdict that no longer matches", db.get_rating_details(1, 1) is None)

    db.rate_item(4, 1, verdict="loved")
    db.rate_item(4, 1, status="not_seen")
    check("not_seen drops the verdict and rating", db.get_rating_details(4, 1) is None)
    try:
        db.rate_item(5, 1, verdict="fantastic")
        check("bad verdict raises", False)
    except ValueError:
        check("bad verdict raises", True)


def test_consumers(tmp):
    print("Recommender readers use the verdict")
    db = make_db(tmp, "c.db")
    db.rate_item(1, 1, verdict="rewatch")     # Drama, Comedy
    db.rate_item(2, 1, verdict="meh")         # Thriller, rated 5 -> not liked
    db.rate_item(3, 1, verdict="loved")       # Drama, Comedy
    db.rate_item(4, 1, verdict="not_for_me")  # Thriller
    db.rate_item(6, 1, user_rating=8)         # old-style rating, no verdict
    liked = db.get_recent_liked_history([1])
    check("TIGER history: liked and rewatch in, meh / not for me out, old 8 in",
          set(liked) == {1, 3, 6}, liked)
    taste = db.get_profile_taste_profile(1)
    check("taste profile counts only liked films", taste["rating_count"] == 3, taste["rating_count"])
    check("thriller counts only through the old-style 8 (0.8), not meh / not for me",
          abs(taste["preferred_genres"].get("thriller", 0) - 0.8) < 1e-9, taste["preferred_genres"])
    check("rewatch weighs more than loved",
          db._taste_weight({"taste_score": 1.0, "user_rating": 10}) > db._taste_weight({"taste_score": 0.6, "user_rating": 9}))
    check("old rating weight unchanged", db._taste_weight({"taste_score": None, "user_rating": 8.0}) == 0.8)


def test_questions_and_queue(tmp):
    print("Vibe-check questions and the sort-out queue")
    q = vd.pick_vibe_questions({"darkness": 0.95, "humour": 0.5, "tension": 0.9, "pace": 0.5},
                               {"feels": ["tense", "eerie", "gritty"], "good_for": ["friends over"]})
    ids = [x["id"] for x in q["shown"]]
    check("three shown, 'How did it leave you?' first", len(ids) == 3 and ids[0] == "leaves", ids)
    check("extreme dials come next", set(ids[1:]) == {"darker", "tension"}, ids)
    check("rest are under More", len(q["more"]) == len(vd.VIBE_QUESTIONS) - 3, len(q["more"]))
    felt = [x for x in q["shown"] + q["more"] if x["id"] == "felt"][0]
    check("'felt like' options are the film's own tags", [o["id"] for o in felt["options"]] == ["tense", "eerie", "gritty"], felt)
    bare = vd.pick_vibe_questions(None, None)
    check("works with no tags at all", len(bare["shown"]) == 3 and all(x["options"] for x in bare["shown"] + bare["more"]))
    check("every question has 2-4 options", all(2 <= len(x["options"]) <= 4 for x in bare["shown"] + bare["more"]))

    db = make_db(tmp, "q.db", films=12)
    with db.get_connection() as conn:
        conn.execute("UPDATE media_items SET facets = ?, vibe_tags = ? WHERE id = 1",
                     (json.dumps({"darkness": 0.9, "tension": 0.9}), json.dumps({"feels": ["tense"]})))
        conn.execute("DROP TABLE rating_details")
        conn.execute("DROP TABLE rating_history")
        conn.execute("PRAGMA user_version = 7")
        for i, r in enumerate([8, 7, 9, 7, 8, 7], start=1):
            conn.execute("INSERT INTO watch_history (media_item_id, profile_id, user_rating, status) VALUES (?, 1, ?, 'seen')", (i, float(r)))
    db = DatabaseManager(os.path.join(tmp, "q.db"))
    check("DB method picks from the film's own tags", db.get_vibe_check_questions(1)["shown"][0]["id"] == "leaves")
    order = [round(i["old_rating"]) for i in db.get_inferred_rating_queue(1)]
    check("sort-out queue: 7s first, then 8s, then the rest", order == [7, 7, 7, 8, 8, 9], order)
    db.rate_item(2, 1, verdict="liked")
    n = len(db.get_inferred_rating_queue(1))
    check("confirming a film takes it off the queue", n == 5, n)


if __name__ == "__main__":
    tmp = tempfile.mkdtemp()
    try:
        test_maths()
        test_inference()
        test_migration(tmp)
        test_rate_item(tmp)
        test_consumers(tmp)
        test_questions_and_queue(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print()
    if failures:
        print(f"{len(failures)} check(s) failed")
        sys.exit(1)
    print("All verdict checks passed")
