"""
Offline tests for the Watch later list (tests/test_watch_later.py)

Throwaway SQLite DB in a tempdir.

    uv run python tests/test_watch_later.py
"""

import os
import shutil
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import DatabaseManager  # noqa: E402

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} — got: {detail}")
        failures.append(label)


FILM, SHOW, FILM2 = 1, 2, 3
LAB, GAB = 1, 2


def make_db(tmp, name="wl.db", initial_profiles=None):
    db = DatabaseManager(os.path.join(tmp, name), initial_profiles=initial_profiles)
    with db.get_connection() as conn:
        conn.execute("INSERT INTO media_items (id, media_type, tmdb_id, title, release_year, genres) "
                     "VALUES (?, 'movie', 201, 'Film One', 2001, 'Drama')", (FILM,))
        conn.execute("INSERT INTO media_items (id, media_type, tmdb_id, title, release_year, genres) "
                     "VALUES (?, 'tv', 202, 'Show Two', 2012, 'Comedy')", (SHOW,))
        conn.execute("INSERT INTO media_items (id, media_type, tmdb_id, title, release_year, genres) "
                     "VALUES (?, 'movie', 203, 'Film Three', 2020, 'Action')", (FILM2,))
        conn.execute("INSERT INTO media_sources (media_item_id, file_path, parsed_title) "
                     "VALUES (?, '/movies/film-one.mkv', 'Film One')", (FILM,))
    return db


def user_version(path):
    c = sqlite3.connect(path)
    try:
        return c.execute("PRAGMA user_version;").fetchone()[0]
    finally:
        c.close()


def set_added(db, item, profile, ts):
    with db.get_connection() as conn:
        conn.execute("UPDATE watch_later SET added_at = ? WHERE media_item_id = ? AND profile_id = ?",
                     (ts, item, profile))


def test_migration(tmp):
    print("Migration")
    db = make_db(tmp)
    path = os.path.join(tmp, "wl.db")
    check("user_version >= 11", user_version(path) >= 11, user_version(path))
    try:
        DatabaseManager(path)
        DatabaseManager(path)
        ok = True
    except Exception as e:  # noqa: BLE001
        ok = False
        print(e)
    check("runs again without error", ok)
    with db.get_connection() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(watch_later);")}
    check("watch_later columns", {"media_item_id", "profile_id", "added_at", "done_at", "done_reason"} <= cols, cols)


def test_names(tmp):
    print("Profile names")
    fresh = make_db(tmp, "fresh.db")
    names = {p["id"]: p["name"] for p in fresh.get_profiles()}
    check("fresh standalone DB uses neutral names", names == {1: "Profile 1", 2: "Profile 2"}, names)

    configured = [
        {"id": 1, "name": "Alex"},
        {"id": 2, "name": "Sam"},
        {"id": 3, "name": "Kids"},
    ]
    household_db = make_db(tmp, "household.db", initial_profiles=configured)
    names = {p["id"]: p["name"] for p in household_db.get_profiles()}
    check("fresh household DB uses configured names and IDs", names == {1: "Alex", 2: "Sam", 3: "Kids"}, names)

    # Existing profile IDs and their ratings remain intact when names come from config.
    fresh.rate_item(FILM, profile_id=1, user_rating=8.0)
    fresh.sync_configured_profiles(configured)
    names = {p["id"]: p["name"] for p in fresh.get_profiles()}
    with fresh.get_connection() as conn:
        stored_rating = conn.execute(
            "SELECT user_rating FROM watch_history WHERE media_item_id = ? AND profile_id = 1", (FILM,)
        ).fetchone()[0]
    check("config renames by ID and adds profiles without deleting stored data",
          names == {1: "Alex", 2: "Sam", 3: "Kids"}
          and stored_rating == 8.0, (names, stored_rating))

    def old_db(name, n1, n2):
        path = os.path.join(tmp, name)
        make_db(tmp, name)
        c = sqlite3.connect(path)
        c.execute("UPDATE profiles SET name = ? WHERE id = 1", (n1,))
        c.execute("UPDATE profiles SET name = ? WHERE id = 2", (n2,))
        c.execute("PRAGMA user_version = 10;")
        c.commit()
        c.close()
        return path

    path = old_db("old.db", "Me", "Partner")
    names = {p["id"]: p["name"] for p in DatabaseManager(path).get_profiles()}
    check("legacy names remain intact until household config is loaded", names == {1: "Me", 2: "Partner"}, names)
    # A later custom rename is not undone on restart.
    db = DatabaseManager(path)
    db.update_profile_name(1, "Me")
    names = {p["id"]: p["name"] for p in DatabaseManager(path).get_profiles()}
    check("profile name remains stable on restart", names[1] == "Me", names)

    path = old_db("custom.db", "Sam", "Alex")
    names = {p["id"]: p["name"] for p in DatabaseManager(path).get_profiles()}
    check("custom names left alone", names == {1: "Sam", 2: "Alex"}, names)

    path = old_db("clash.db", "Me", "Labiba")
    names = {p["id"]: p["name"] for p in DatabaseManager(path).get_profiles()}
    check("legacy migration leaves existing names unchanged", names[1] == "Me" and names[2] == "Labiba", names)


def test_list(tmp):
    print("Watch later list")
    db = make_db(tmp, "list.db", initial_profiles=[
        {"id": 1, "name": "Labiba"}, {"id": 2, "name": "Gabriele"},
    ])
    e = db.add_to_watch_later(FILM, LAB)
    check("add returns grouped entry", e["media_item_id"] == FILM and [a["profile_id"] for a in e["added_by"]] == [LAB], e)
    check("entry has file_path and fields", e["file_path"] == "/movies/film-one.mkv" and e["title"] == "Film One"
          and e["media_type"] == "movie" and e["both"] is False, e)
    db.add_to_watch_later(FILM, LAB)
    check("adding twice is a no-op", len(db.get_watch_later()) == 1 and len(db.get_watch_later()[0]["added_by"]) == 1)
    e = db.add_to_watch_later(FILM, GAB)
    check("both people -> one entry, both=True", len(db.get_watch_later()) == 1 and e["both"] is True
          and {a["name"] for a in e["added_by"]} == {"Labiba", "Gabriele"}, e)
    check("remove one person only", db.remove_from_watch_later(FILM, LAB) is True
          and [a["profile_id"] for a in db.get_watch_later()[0]["added_by"]] == [GAB])
    check("remove missing row is False", db.remove_from_watch_later(FILM, LAB) is False)

    db.add_to_watch_later(SHOW, LAB)
    db.add_to_watch_later(FILM2, GAB)
    set_added(db, FILM, GAB, "2026-01-01 10:00:00")
    set_added(db, SHOW, LAB, "2026-01-03 10:00:00")
    set_added(db, FILM2, GAB, "2026-01-02 10:00:00")
    ids = [x["media_item_id"] for x in db.get_watch_later()]
    check("newest add first", ids == [SHOW, FILM2, FILM], ids)
    check("media_type filter", [x["media_item_id"] for x in db.get_watch_later(media_type="tv")] == [SHOW])
    check("added_by filter", [x["media_item_id"] for x in db.get_watch_later(added_by=LAB)] == [SHOW])
    check("added_by keeps the whole entry", [x["media_item_id"] for x in db.get_watch_later(added_by=GAB)] == [FILM2, FILM])

    check("mark done returns rows", db.mark_watch_later_done(FILM, "rated") == 1)
    check("done hidden by default", FILM not in [x["media_item_id"] for x in db.get_watch_later()])
    done = [x for x in db.get_watch_later(include_done=True) if x["media_item_id"] == FILM][0]
    check("include_done shows it with reason", done["done_at"] and done["done_reason"] == "rated", done)
    check("get_watch_later_ids ignores done rows", FILM not in db.get_watch_later_ids([FILM, SHOW, FILM2])
          and db.get_watch_later_ids([FILM, SHOW, FILM2]) == {SHOW: [LAB], FILM2: [GAB]})
    check("done not marked twice", db.mark_watch_later_done(FILM, "watched") == 0)
    check("undo reopens", db.undo_watch_later_done(FILM) == 1 and FILM in [x["media_item_id"] for x in db.get_watch_later()])
    db.mark_watch_later_done(FILM, "watched")
    db.add_to_watch_later(FILM, GAB)
    again = [x for x in db.get_watch_later() if x["media_item_id"] == FILM]
    check("re-adding a done title reopens it", len(again) == 1 and again[0]["done_at"] is None, again)
    check("get_watch_later_ids empty input", db.get_watch_later_ids([]) == {})

    with db.get_connection() as conn:
        conn.execute("DELETE FROM media_items WHERE id = ?", (SHOW,))
        left = conn.execute("SELECT COUNT(*) FROM watch_later WHERE media_item_id = ?", (SHOW,)).fetchone()[0]
    check("deleting a media_item cascades", left == 0, left)

    for bad, label in (((9999, LAB), "unknown media_item_id"), ((FILM, 9999), "unknown profile_id")):
        try:
            db.add_to_watch_later(*bad)
            raised = False
        except ValueError:
            raised = True
        check(f"ValueError on {label}", raised)


def test_hooks(tmp):
    print("Auto-done hooks (database)")
    db = make_db(tmp, "hooks.db")
    db.add_to_watch_later(FILM, LAB)
    db.rate_item(FILM, GAB, status="not_seen")
    check("not_seen does not mark done", len(db.get_watch_later()) == 1)
    db.rate_item(FILM, GAB, verdict="liked")
    check("seen rating marks done", db.get_watch_later() == [])
    done = db.get_watch_later(include_done=True)
    check("done_reason is rated", done and done[0]["done_reason"] == "rated", done)
    db.add_to_watch_later(SHOW, LAB)
    db.rate_season(SHOW, LAB, 1, verdict="loved")
    check("season verdict marks done", db.get_watch_later(media_type="tv") == [])


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
    server.catalogue_search.db = server.db
    try:
        server.catalogue_search.invalidate()
    except Exception:
        pass
    client = TestClient(server.app)

    r = client.get("/api/watch-later")
    check("GET empty list", r.status_code == 200 and r.json() == {"items": [], "count": 0}, r.text)
    r = client.post("/api/watch-later", json={"media_item_id": FILM, "profile_id": LAB})
    check("POST adds", r.status_code == 200 and r.json()["media_item_id"] == FILM, r.text)
    r = client.post("/api/watch-later", json={"media_item_id": FILM, "profile_id": GAB})
    check("both people -> both", r.json()["both"] is True, r.text)
    r = client.post("/api/watch-later", json={"media_item_id": 9999, "profile_id": LAB})
    check("unknown title is 400", r.status_code == 400, r.status_code)
    r = client.post("/api/watch-later", json={"media_item_id": FILM, "profile_id": 9999})
    check("unknown profile is 400", r.status_code == 400, r.status_code)
    r = client.get("/api/watch-later", params={"media_type": "tv"})
    check("media_type filter", r.json()["count"] == 0, r.text)
    r = client.get("/api/watch-later", params={"added_by": GAB})
    check("added_by filter", r.json()["count"] == 1, r.text)

    r = client.get("/api/catalogue/search", params={"q": "Film One"})
    items = r.json().get("items", [])
    check("catalogue search carries on_list",
          r.status_code == 200 and items and sorted(items[0].get("on_list", [])) == [LAB, GAB], r.text[:300])
    r = client.get("/api/ratings/queue", params={"profile_id": 1, "media_type": "movie"})
    qi = r.json().get("items", [])
    check("ratings queue items have on_list", r.status_code == 200 and all("on_list" in i for i in qi), r.text[:300])
    r = client.post("/api/recommend", json={"media_type": "movie", "include_watched": True})
    ri = r.json().get("items", [])
    check("recommend items have on_list", r.status_code == 200 and all("on_list" in i for i in ri), r.text[:300])

    r = client.delete(f"/api/watch-later/{FILM}", params={"profile_id": GAB})
    check("DELETE removes", r.json() == {"removed": True}, r.text)
    r = client.delete(f"/api/watch-later/{FILM}", params={"profile_id": GAB})
    check("DELETE again is false", r.json() == {"removed": False}, r.text)

    r = client.post("/api/ratings", json={"media_item_id": FILM, "profile_id": GAB, "status": "not_seen"})
    check("not_seen via API keeps it open", client.get("/api/watch-later").json()["count"] == 1, r.text)
    r = client.post("/api/ratings", json={"media_item_id": FILM, "profile_id": GAB, "verdict": "liked"})
    check("seen via API marks done", r.status_code == 200 and client.get("/api/watch-later").json()["count"] == 0, r.text)
    r = client.get("/api/watch-later", params={"include_done": "true"})
    check("include_done shows it", r.json()["count"] == 1 and r.json()["items"][0]["done_reason"] == "rated", r.text)
    r = client.post(f"/api/watch-later/{FILM}/undo")
    check("undo reopens", r.json() == {"reopened": 1} and client.get("/api/watch-later").json()["count"] == 1, r.text)

    r = client.post(f"/api/watch-later/{FILM}/done")
    check("done endpoint marks watched", r.json() == {"done": 1} and client.get("/api/watch-later").json()["count"] == 0, r.text)
    client.post(f"/api/watch-later/{FILM}/undo")

    # finishing a watch session marks 'watched'
    client.post("/api/watch-later", json={"media_item_id": FILM2, "profile_id": LAB})
    with server.db.get_connection() as conn:
        conn.execute("INSERT INTO active_watch_sessions (media_item_id) VALUES (?)", (FILM2,))
        sid = conn.execute("SELECT MAX(id) FROM active_watch_sessions").fetchone()[0]
    r = client.post("/api/watch/finish", json={"session_id": sid})
    done = [i for i in client.get("/api/watch-later", params={"include_done": "true"}).json()["items"]
            if i["media_item_id"] == FILM2]
    check("watch finish marks watched", r.status_code == 200 and done and done[0]["done_reason"] == "watched", r.text)


def main():
    tmp = tempfile.mkdtemp(prefix="wl_test_")
    try:
        test_migration(tmp)
        test_names(tmp)
        test_list(tmp)
        test_hooks(tmp)
        test_api(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if failures:
        print(f"\n{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("\nAll watch-later tests passed")


if __name__ == "__main__":
    main()
