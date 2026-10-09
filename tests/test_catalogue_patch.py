"""
Offline tests for the catalogue patch (services/catalogue_patch.py)

    uv run python tests/test_catalogue_patch.py
"""

import json
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import DatabaseManager  # noqa: E402
from services.catalogue_patch import PatchError, apply_patch  # noqa: E402

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} — got: {detail}")
        failures.append(label)


def lines(*rows):
    return [json.dumps(r) for r in rows]


def q(path, sql, *args):
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def rejects(db, rows):
    try:
        apply_patch(db, lines(*rows))
    except PatchError as e:
        return str(e)
    return None


with tempfile.TemporaryDirectory() as tmp:
    db_file = os.path.join(tmp, "c.db")
    DatabaseManager(db_file)
    conn = sqlite3.connect(db_file)
    conn.execute("INSERT INTO media_items (media_type, tmdb_id, title, rating) VALUES ('movie', 603, 'The Matrix', 8.2)")
    conn.execute("INSERT INTO media_items (media_type, tmdb_id, title) VALUES ('tv', 603, 'Same id, a show')")
    conn.execute("INSERT INTO watch_history (media_item_id, user_rating) VALUES (1, 9.0)")
    conn.commit()
    conn.close()

    print("inserts and updates")
    c = apply_patch(db_file, lines(
        {"media_type": "movie", "tmdb_id": 603, "fields": {"facets": "{\"cozy\": 0.1}", "rating": 8.3}},
        {"media_type": "movie", "tmdb_id": 9999, "fields": {"title": "New Film", "release_year": 2026}},
        {"media_type": "tv", "tmdb_id": 9999, "fields": {"title": "New Show"}},
        {"media_type": "movie", "tmdb_id": 8888, "fields": {"rating": 5}},
    ))
    check("counts", c == {"inserted": 2, "updated": 1, "unchanged": 0, "skipped": 1, "replaced_rows": 0}, c)
    check("known item updated by (type, tmdb_id), id untouched",
          q(db_file, "SELECT id, rating, facets FROM media_items WHERE media_type='movie' AND tmdb_id=603")
          == [(1, 8.3, "{\"cozy\": 0.1}")])
    check("the tv show with the same tmdb_id is untouched",
          q(db_file, "SELECT title FROM media_items WHERE media_type='tv' AND tmdb_id=603") == [("Same id, a show",)])
    check("new rows got the next ids, old ids survived",
          [r[0] for r in q(db_file, "SELECT id FROM media_items ORDER BY id")] == [1, 2, 3, 4])
    check("an untitled unknown item was skipped", q(db_file, "SELECT COUNT(*) FROM media_items WHERE tmdb_id=8888") == [(0,)])

    print("idempotent")
    again = apply_patch(db_file, lines(
        {"media_type": "movie", "tmdb_id": 603, "fields": {"facets": "{\"cozy\": 0.1}", "rating": 8.3}},
        {"media_type": "movie", "tmdb_id": 9999, "fields": {"title": "New Film", "release_year": 2026}},
    ))
    check("second run changes nothing", again["inserted"] == 0 and again["updated"] == 0 and again["unchanged"] == 2, again)
    check("still four rows", q(db_file, "SELECT COUNT(*) FROM media_items") == [(4,)])

    print("replace block")
    block = {"table": "vibe_clusters", "rows": [{"id": 1, "label": "cozy"}, {"id": 2, "label": "tense"}]}
    c = apply_patch(db_file, lines(block))
    check("vibe_clusters filled", c["replaced_rows"] == 2 and q(db_file, "SELECT COUNT(*) FROM vibe_clusters") == [(2,)], c)
    apply_patch(db_file, lines({"table": "vibe_clusters", "rows": [{"id": 7, "label": "only"}]}))
    check("vibe_clusters replaced, not appended", q(db_file, "SELECT id FROM vibe_clusters") == [(7,)])

    print("rejections (nothing applied)")
    before = q(db_file, "SELECT COUNT(*), SUM(rating) FROM media_items")
    check("user table refused", rejects(db_file, [{"table": "watch_history", "rows": [{"id": 1}]}]))
    check("user-table column refused", rejects(db_file, [
        {"media_type": "movie", "tmdb_id": 603, "fields": {"user_rating": 1}}]))
    check("id column refused", rejects(db_file, [{"media_type": "movie", "tmdb_id": 603, "fields": {"id": 50}}]))
    check("bad media_type refused", rejects(db_file, [{"media_type": "book", "tmdb_id": 1, "fields": {"title": "x"}}]))
    check("non-scalar value refused", rejects(db_file, [
        {"media_type": "movie", "tmdb_id": 603, "fields": {"facets": {"a": 1}}}]))
    try:
        apply_patch(db_file, ["{nope"])
        check("bad JSON refused", False)
    except PatchError:
        check("bad JSON refused", True)
    # A bad line late in the file must undo the good lines before it.
    rejects(db_file, [
        {"media_type": "movie", "tmdb_id": 4242, "fields": {"title": "Should not land"}},
        {"media_type": "movie", "tmdb_id": 603, "fields": {"user_rating": 1}}])
    check("all or nothing", q(db_file, "SELECT COUNT(*), SUM(rating) FROM media_items") == before)
    check("watch history untouched", q(db_file, "SELECT user_rating FROM watch_history") == [(9.0,)])

if failures:
    print(f"\n{len(failures)} failed")
    sys.exit(1)
print("\nall catalogue patch tests passed")
