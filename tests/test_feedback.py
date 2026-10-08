"""
Offline tests for feedback logging (tests/test_feedback.py)

Throwaway SQLite DB in a tempdir.

    uv run python tests/test_feedback.py
"""

import json
import os
import shutil
import sys
import tempfile
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import DatabaseManager  # noqa: E402

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} — got: {detail}")
        failures.append(label)


def test_feedback(tmp):
    print("Sessions, impressions, outcomes")
    db = DatabaseManager(os.path.join(tmp, "f.db"))
    with db.get_connection() as conn:
        for i in range(1, 6):
            conn.execute("INSERT INTO media_items (id, media_type, tmdb_id, title) VALUES (?, 'movie', ?, ?)",
                         (i, i, f"Film {i}"))

    sid = db.create_discover_session("together", "movie", "something cozy", {"mood_id": 3},
                                     engine="legacy", now=datetime(2026, 10, 6, 22, 40))  # a Tuesday
    with db.get_connection() as conn:
        row = dict(conn.execute("SELECT * FROM discover_sessions WHERE id = ?", (sid,)).fetchone())
    check("weekday and hour stored", (row["weekday"], row["hour"]) == (1, 22), row)
    check("request stored as JSON", json.loads(row["request_json"]) == {"mood_id": 3})

    items = [{"id": 1, "recommendation_score": 8.1, "taste_bonus": 0.4}, {"id": 2}, {"id": 3}]
    check("three impressions logged", db.log_impressions(sid, items, 1) == 3)
    check("page 2 continues the ranks; repeats keep their first rank",
          db.log_impressions(sid, [{"id": 3}, {"id": 4}], 4) == 1)
    with db.get_connection() as conn:
        ranks = dict(conn.execute("SELECT media_item_id, rank FROM discover_impressions").fetchall())
        score = json.loads(conn.execute(
            "SELECT score_json FROM discover_impressions WHERE media_item_id = 1").fetchone()[0])
    check("ranks", ranks == {1: 1, 2: 2, 3: 3, 4: 5}, ranks)
    check("score parts kept", score == {"recommendation_score": 8.1, "taste_bonus": 0.4}, score)

    check("opened", db.record_outcome(1, "opened", sid))
    check("played (no session: attributed to the recent impression)", db.record_outcome(1, "played"))
    check("opening again doesn't downgrade 'played'", not db.record_outcome(1, "opened", sid))
    check("rated with the rating", db.record_outcome(1, "rated", rating=9))
    check("dismissed", db.record_outcome(2, "dismissed", sid))
    check("a title never shown isn't logged", not db.record_outcome(5, "played"))
    try:
        db.record_outcome(1, "loved")
        check("unknown outcome rejected", False)
    except ValueError:
        check("unknown outcome rejected", True)

    sid2 = db.create_discover_session("me", "movie", None)
    db.log_impressions(sid2, [{"id": 1}], 1)
    db.record_outcome(1, "played")
    with db.get_connection() as conn:
        latest = conn.execute("SELECT outcome FROM discover_impressions WHERE session_id = ?", (sid2,)).fetchone()[0]
        first = conn.execute("SELECT outcome, rating FROM discover_impressions WHERE session_id = ? AND media_item_id = 1",
                             (sid,)).fetchone()
    check("a later play goes to the most recent impression", latest == "played", latest)
    check("the earlier one keeps its rating", tuple(first) == ("rated", 9.0), tuple(first))

    with db.get_connection() as conn:
        conn.execute("UPDATE discover_impressions SET shown_at = datetime('now', '-30 days') WHERE media_item_id = 4")
    check("impressions older than 7 days don't get credit", not db.record_outcome(4, "played"))

    stats = db.get_feedback_stats()
    check("stats", stats["sessions"] == 2 and stats["outcomes"].get("rated") == 1
          and stats["outcomes"].get("dismissed") == 1, stats)
    check("migration is idempotent", DatabaseManager(os.path.join(tmp, "f.db")) is not None)


if __name__ == "__main__":
    tmp = tempfile.mkdtemp()
    try:
        test_feedback(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{'ALL PASSED' if not failures else f'{len(failures)} FAILED'}")
    sys.exit(1 if failures else 0)
