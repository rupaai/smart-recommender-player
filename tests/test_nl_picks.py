"""
Offline tests for Northern Lights M6, "Picks for two" (tests/test_nl_picks.py)

Jellyfin is faked (nl_fake.py) and the recommender is replaced by a stand-in where a test needs to
see how it is called; nothing leaves the process.

    uv run python tests/test_nl_picks.py
"""

import os
import shutil
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nl_fake  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
failures = []
LAB = "a" * 32
PAR = "b" * 32


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} — got: {detail}")
        failures.append(label)


def cookie(value):
    return {"Cookie": f"nl_viewer={value}"}


def main():
    test_pure()
    tmp = tempfile.mkdtemp(prefix="nl_picks_test_")
    try:
        test_migration(tmp)
        run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    test_static()
    if failures:
        print(f"\n{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("\nAll Northern Lights picks-for-two tests passed")


# ---------------------------------------------------------------------------- pure


def pair_ctx(names=("Labiba", "Sam"), viewer_key="both"):
    from services import nl_people as np
    from services import nl_picks as pk

    people = np.People(np.parse_people([
        {"id": "labiba", "name": names[0], "jellyfin_user_id": LAB, "profile_id": 1},
        {"id": "partner", "name": names[1], "jellyfin_user_id": PAR, "profile_id": 2},
    ]))
    return pk.build_ctx(people.people, people.viewer_for(viewer_key)), people


def test_pure():
    from services import nl_people as np
    from services import nl_picks as pk

    print("runtime filter")
    check("chips are Under 90 min and Under 2 h", [(c["minutes"], c["label"]) for c in pk.RUNTIME_CHIPS] == [(90, "Under 90 min"), (120, "Under 2 h")])
    check("the query value is parsed, junk is no filter", pk.parse_max_runtime("90") == 90 and pk.parse_max_runtime("120") == 120
          and pk.parse_max_runtime(None) is None and pk.parse_max_runtime("abc") is None and pk.parse_max_runtime("5") is None
          and pk.parse_max_runtime("99999") is None and pk.parse_max_runtime("") is None)
    check("90 minutes fits Under 90, 91 does not", pk.runtime_ok(90, 90) and not pk.runtime_ok(91, 90))
    check("Under 2 h keeps 120 and drops 121", pk.runtime_ok(120, 120) and not pk.runtime_ok(121, 120))
    check("no filter keeps everything, even no runtime", pk.runtime_ok(None, None) and pk.runtime_ok(0, None) and pk.runtime_ok(300, None))
    check("an unknown runtime cannot fit a filter", not pk.runtime_ok(None, 90) and not pk.runtime_ok(0, 90) and not pk.runtime_ok("x", 90))

    print("people at the screen")
    ctx, _ = pair_ctx()
    check("two people: For both of us", ctx.title == "For both of us" and not ctx.solo and ctx.pair == [1, 2] and ctx.mine == [1, 2])
    ctx_one, _ = pair_ctx(viewer_key="partner")
    check("one of two at the screen: still the pair row, their seat", ctx_one.title == "For both of us" and ctx_one.mine == [2] and ctx_one.pair == [1, 2])
    check("Just me rows: one person sees their own, Both sees none", pk.personal_visible(ctx_one) == [2] and pk.personal_visible(ctx) == [])
    solo = pk.build_ctx([], np.IMPLICIT_VIEWER)
    check("nobody configured: Picks for you, profile 1, called You", solo.title == "Picks for you" and solo.solo and solo.mine == [1] and solo.name_of(1) == "You")
    alone = pk.build_ctx([np.Person("a", "Ana", "u", None)], np.Viewer("a", "Ana", (np.Person("a", "Ana", "u", None),)))
    check("one person configured: Picks for you; a missing profile_id means 1", alone.solo and alone.title == "Picks for you" and alone.mine == [1])
    nop = pk.build_ctx([np.Person("a", "A", "u"), np.Person("b", "B", "v")], np.People([np.Person("a", "A", "u"), np.Person("b", "B", "v")]).viewer_for("both"))
    check("people without profile ids get 1 and 2 in order", nop.pair == [1, 2])

    print("yes / maybe / pass")
    check("both said yes needs both", pk.both_said_yes(ctx, {1: "yes", 2: "yes"}) and not pk.both_said_yes(ctx, {1: "yes"}) and not pk.both_said_yes(ctx, {1: "yes", 2: "maybe"}))
    check("alone, nobody can both say yes", not pk.both_said_yes(solo, {1: "yes"}))
    check("ranks: both yes 0, one yes 1, nothing or maybe 2, pass 3",
          [pk.watchlist_rank(ctx, s) for s in ({1: "yes", 2: "yes"}, {1: "yes"}, {}, {1: "maybe", 2: "maybe"}, {1: "yes", 2: "pass"}, {2: "pass"})] == [0, 1, 2, 2, 3, 3])
    check("alone: yes first, pass last", pk.watchlist_rank(solo, {1: "yes"}) == 1 and pk.watchlist_rank(solo, {}) == 2 and pk.watchlist_rank(solo, {1: "pass"}) == 3)
    view = pk.interest_view(ctx, {1: "yes"})
    check("the view lists both people with their state", [(v["person"], v["state"]) for v in view] == [("labiba", "yes"), ("partner", None)])
    check("alone you see only yourself", [v["person"] for v in pk.interest_view(ctx_one, {})] == ["labiba", "partner"]
          and [v["person"] for v in pk.interest_view(solo, {})] == ["default"])

    print("re-ranking with what they said")
    pool = [
        {"id": 1, "title": "Plain", "genres": "Comedy", "recommendation_score": 8.0},
        {"id": 2, "title": "Yes both", "genres": "Horror", "recommendation_score": 6.5},
        {"id": 3, "title": "Yes one", "genres": "Horror", "recommendation_score": 7.5},
        {"id": 4, "title": "Passed", "genres": "Comedy", "recommendation_score": 9.9},
        {"id": 5, "title": "Like the yes", "genres": "Sci-Fi, Drama", "recommendation_score": 7.0},
    ]
    interest = {2: {1: "yes", 2: "yes"}, 3: {1: "yes"}, 4: {2: "pass"}}
    out = pk.rerank(pool, ctx, interest, {"sci-fi", "drama"})
    check("a pass on either side drops the title", 4 not in [i["id"] for i in out])
    check("both said yes beats a better recommender score (6.5 + 2 over 8.0)", [i["id"] for i in out][0] == 2, [i["id"] for i in out])
    check("one yes (7.5 + 0.6) beats the plain 8.0, and two shared genres (7.0 + 0.5) come last", [i["id"] for i in out] == [2, 3, 1, 5], [i["id"] for i in out])
    check("the genre lift is capped", pk.rerank([{"id": 9, "title": "x", "genres": "A, B, C, D, E", "recommendation_score": 1.0}], ctx, {}, {"a", "b", "c", "d", "e"})[0]["_pick_score"] == 1.0 + pk.YES_GENRE_CAP)
    check("the input pool is not changed", "_pick_score" not in pool[0])

    print("the fallback when the recommender cannot run")
    items = {
        1: {"id": 1, "title": "Great", "rating": 8.4, "vote_count": 5000},
        2: {"id": 2, "title": "Fine", "rating": 7.0, "vote_count": 10},
        3: {"id": 3, "title": "Meh", "rating": 5.5, "vote_count": 9000},
        4: {"id": 4, "title": "Seen already", "rating": 9.0, "vote_count": 9000},
        5: {"id": 5, "title": "No rating yet", "rating": 0, "vote_count": 0},
    }
    fb = pk.fallback_pool(items, [1, 2, 3, 4, 5, 99], seen={4})
    check("only well rated, unseen titles, best first", [i["id"] for i in fb] == [1, 2], [i["id"] for i in fb])
    check("a title with few votes is pulled toward the middle", fb[0]["recommendation_score"] > fb[1]["recommendation_score"] and fb[1]["recommendation_score"] < 7.0)

    print("reasons")
    anchors = pk.build_anchors(
        ctx,
        [
            {"media_item_id": 10, "profile_id": 1, "rating": 9.0, "title": "Arrival", "genres": "Sci-Fi, Drama"},
            {"media_item_id": 10, "profile_id": 2, "rating": 8.0, "title": "Arrival", "genres": "Sci-Fi, Drama"},
            {"media_item_id": 11, "profile_id": 1, "rating": 9.0, "title": "Solo Love", "genres": "Horror, Thriller"},
            {"media_item_id": 11, "profile_id": 2, "rating": 4.0, "title": "Solo Love", "genres": "Horror, Thriller"},
            {"media_item_id": 12, "profile_id": 1, "rating": 6.0, "title": "Okay", "genres": "Comedy, Romance"},
            {"media_item_id": 12, "profile_id": 2, "rating": 6.0, "title": "Okay", "genres": "Comedy, Romance"},
        ],
        [{"title": "Yes Film", "genres": "Western, Adventure"}],
    )
    kinds = {a["title"]: a["kind"] for a in anchors}
    check("anchors: both loved, one loved, both said yes; a 6 is none", kinds == {"Arrival": "both_rated", "Solo Love": "one_rated", "Yes Film": "both_yes"}, kinds)
    check("the floor is the lower of the two ratings", [a["floor"] for a in anchors if a["title"] == "Arrival"] == [8])
    tastes = {
        1: {"rating_count": 5, "preferred_genres": {"Comedy": 3, "Drama": 2}, "name": "Labiba"},
        2: {"rating_count": 5, "preferred_genres": {"Comedy": 1}, "name": "Sam"},
    }
    item = {"id": 20, "title": "Contact", "genres": "Sci-Fi, Drama", "rating": 7.4}
    r = pk.build_reason(item, ctx, {}, anchors, tastes)
    check("both loved a similar title", r == "You both loved Arrival (rated 8+), and this is Drama and Sci-Fi too", r)
    r = pk.build_reason(item, ctx, {1: "yes", 2: "yes"}, anchors, tastes)
    check("both said yes comes first, with names", r == "Labiba and Sam both said yes", r)
    r = pk.build_reason(item, ctx, {2: "yes"}, anchors, tastes)
    check("one yes names who", r == "Sam said yes", r)
    r = pk.build_reason({"id": 21, "title": "Hunt", "genres": "Horror, Thriller", "rating": 6.5}, ctx, {}, anchors, tastes)
    check("one person loved a similar title: named, not claimed for both", r == "Labiba loved Solo Love (rated 9+), and this is Horror and Thriller too", r)
    r = pk.build_reason({"id": 22, "title": "Laughs", "genres": "Comedy", "rating": 6.8}, ctx, {}, [], tastes)
    check("genres they both rate 7 or more", r == "Matches Comedy, which you both usually rate 7 or higher", r)
    r = pk.build_reason({"id": 23, "title": "Talky", "genres": "Drama", "rating": 6.8}, ctx, {}, [], tastes)
    check("a genre only one rates well names them", r == "Matches Drama, which Labiba usually rates 7 or higher", r)
    r = pk.build_reason({"id": 24, "title": "Odd", "genres": "Musical", "rating": 7.46}, ctx, {}, [], tastes)
    check("otherwise the catalogue rating, to one decimal", r == "Rated 7.5 on TMDB", r)
    r = pk.build_reason({"id": 25, "title": "New", "genres": "Musical", "rating": 0}, ctx, {}, [], tastes)
    check("with nothing known it says so", r == "On your shelf, and neither of you has rated it", r)
    r = pk.build_reason(item, solo, {1: "yes"}, [], {})
    check("alone, a yes is yours", r == "You said yes", r)
    solo_anchors = pk.build_anchors(solo, [{"media_item_id": 10, "profile_id": 1, "rating": 9.0, "title": "Arrival", "genres": "Sci-Fi, Drama"}], [])
    r = pk.build_reason(item, solo, {}, solo_anchors, {})
    check("alone, a title you loved", r == "You loved Arrival (rated 9+), and this is Drama and Sci-Fi too", r)
    r2 = pk.build_reason({"id": 27, "title": "Two", "genres": "Drama, Romance", "rating": 6.0}, ctx, {}, anchors, {})
    check("a single overlapping genre of two does not make a 'like' claim", "Arrival" not in r2, r2)
    check("a title is never its own anchor", "Arrival" not in pk.build_reason({"id": 10, "title": "Arrival", "genres": "Sci-Fi, Drama", "rating": 8}, ctx, {}, anchors, {}))
    check("names come from config, not hard-coded", "Ada and Bo" in pk.build_reason(item, pair_ctx(("Ada", "Bo"))[0], {1: "yes", 2: "yes"}, [], {}))


# ------------------------------------------------------------------------ migration


def test_migration(tmp):
    from database import DatabaseManager

    print("migration v14")
    fresh = DatabaseManager(os.path.join(tmp, "fresh.db"))
    with fresh.get_connection() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(watch_later)")}
        interest = {r[1] for r in conn.execute("PRAGMA table_info(nl_interest)")}
    check("fresh DB is at schema 14", fresh.get_schema_version() == 14, fresh.get_schema_version())
    check("watch_later has the owner column", "owner_profile_id" in cols, cols)
    check("nl_interest has its columns", {"media_item_id", "profile_id", "state", "updated_at"} <= interest, interest)

    path = os.path.join(tmp, "old.db")
    old = DatabaseManager(path)
    _, sid = old.register_source_file("/media/Movies/X (2001)/X.mp4", "X", 2001, "movie")
    item = old.get_library_items("movie")[0]["id"]
    old.add_to_watch_later(item, 1)
    with sqlite3.connect(path) as conn:  # back to how a v13 database looks
        conn.execute("DROP TABLE nl_interest")
        conn.execute("ALTER TABLE watch_later DROP COLUMN owner_profile_id")
        conn.execute("PRAGMA user_version = 13")
    up = DatabaseManager(path)
    with up.get_connection() as conn:
        row = conn.execute("SELECT media_item_id, profile_id, owner_profile_id FROM watch_later").fetchone()
        has = conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'nl_interest'").fetchone()
    check("v13 upgrades to 14", up.get_schema_version() == 14, up.get_schema_version())
    check("an existing watchlist row stays and is shared", row and row[0] == item and row[2] is None, tuple(row) if row else row)
    check("the interest table appears", has is not None)
    check("running again is harmless", DatabaseManager(path).get_schema_version() == 14)

    print("watch_later: shared and Just me (database)")
    db = up
    db.add_to_watch_later(item, 2, owner_profile_id=2)
    shared_view = db.get_watch_later()
    check("the old reader sees only the shared row", len(shared_view) == 1 and [a["profile_id"] for a in shared_view[0]["added_by"]] == [1], shared_view)
    check("on_list for the old reader hides Just me", db.get_watch_later_ids([item]) == {item: [1]})
    check("the owner sees both", [a["profile_id"] for a in db.get_watch_later(personal_of=[2])[0]["added_by"]] == [1, 2])
    check("on_list shows the owner's row to the owner", db.get_watch_later_ids([item], personal_of=[2]) == {item: [1, 2]})
    try:
        db.add_to_watch_later(item, 1, owner_profile_id=2)
        check("Just me for someone else is refused", False)
    except ValueError:
        check("Just me for someone else is refused", True)
    db.add_to_watch_later(item, 2)
    check("adding it shared shares it (owner cleared)", [a["profile_id"] for a in db.get_watch_later()[0]["added_by"]] == [1, 2])
    db.add_to_watch_later(item, 2, owner_profile_id=2)
    check("Just me on a shared title leaves it shared", [a["profile_id"] for a in db.get_watch_later()[0]["added_by"]] == [1, 2])
    db.remove_from_watchlist(item, [1])
    check("removing takes the shared list off for both", db.get_watch_later(personal_of=[1, 2]) == [])

    print("interest (database)")
    check("set and read", db.set_interest(item, 1, "yes") == "yes" and db.get_interest([item]) == {item: {1: "yes"}})
    db.set_interest(item, 2, "maybe")
    db.set_interest(item, 1, "pass")
    check("one row per person per title, the latest wins", db.get_interest([item]) == {item: {1: "pass", 2: "maybe"}})
    db.set_interest(item, 2, None)
    check("clearing deletes", db.get_interest([item]) == {item: {1: "pass"}} and db.get_interest() == {item: {1: "pass"}})
    for bad in ("love", ""):
        try:
            db.set_interest(item, 1, bad)
            check(f"state {bad!r} is refused", False)
        except ValueError:
            check(f"state {bad!r} is refused", True)
    for args in ((999, 1), (item, 999)):
        try:
            db.set_interest(args[0], args[1], "yes")
            check(f"unknown id {args} is refused", False)
        except ValueError:
            check(f"unknown id {args} is refused", True)


# ------------------------------------------------------------------------- the app


class FakeRecommender:
    """Stands in for RecommenderService.recommend: records how it was called, returns a fixed pool."""

    def __init__(self):
        self.calls = []
        self.pool = []
        self.fail = None

    def recommend(self, **kw):
        self.calls.append(kw)
        if self.fail:
            raise self.fail
        return {"pool": [dict(p) for p in self.pool]}


def run(tmp):
    ctx = nl_fake.boot(tmp)
    server, client, ids, db = ctx["server"], ctx["client"], ctx["ids"], ctx["db"]
    client.follow_redirects = False
    from services import nl_people as np

    # More movies on the shelf. Runtimes: Alpha 100, Beta 95 (from boot), plus these.
    extra = {"Delta": (80, "Comedy", 7.8), "Epsilon": (150, "Drama", 7.2), "Zeta": (118, "Sci-Fi, Drama", 7.9), "Arrival": (116, "Sci-Fi, Drama", 7.9)}
    for title, (rt, genres, rating) in extra.items():
        db.register_source_file(f"/media/Movies/{title} (2015)/{title}.mkv", title, 2015, "movie")
    with db.get_connection() as conn:
        for title, (rt, genres, rating) in extra.items():
            conn.execute("UPDATE media_items SET runtime = ?, genres = ?, rating = ?, vote_count = 5000 WHERE title = ?", (rt, genres, rating, title))
        conn.execute("UPDATE media_items SET rating = 8.0, vote_count = 4000 WHERE title = 'Alpha'")
        conn.execute("UPDATE media_items SET genres = 'Comedy', rating = 6.9, vote_count = 3000 WHERE title = 'Beta'")
        conn.execute("UPDATE media_items SET runtime = NULL WHERE title = 'Sigma'")
        ids2 = {r["title"]: r["id"] for r in conn.execute("SELECT id, title FROM media_items")}
    ids.update(ids2)
    gamma = ids["Gamma Ray"]            # in the catalogue, not in the library
    alpha, beta, delta, epsilon, zeta, arrival, sigma = (ids[t] for t in ("Alpha", "Beta", "Delta", "Epsilon", "Zeta", "Arrival", "Sigma"))
    # Both loved Arrival.
    db.rate_item(arrival, profile_id=1, user_rating=9.0, status="seen")
    db.rate_item(arrival, profile_id=2, user_rating=8.0, status="seen")

    nobody = server.nl_people

    # ------------------------------------------------------------------ nobody configured
    print("no people configured: one implicit viewer")
    r = client.get("/api/nl/watchlist/state")
    st = r.json()
    check("state: one person, no Just me, not both", r.status_code == 200 and [p["id"] for p in st["people"]] == ["default"] and st["can_personal"] is False and st["solo"] is True and st["both"] is False, st)
    r = client.post("/api/nl/watchlist", json={"media_item_id": alpha})
    check("add: shared, listed, credited to profile 1", r.status_code == 200 and r.json()["listed"] is True and r.json()["personal"] is False, r.text)
    check("it is on the old list too (profile 1)", [a["profile_id"] for a in client.get("/api/watch-later").json()["items"][0]["added_by"]] == [1])
    r = client.post("/api/nl/watchlist", json={"media_item_id": beta, "scope": "me"})
    check("Just me is refused without people", r.status_code == 400, (r.status_code, r.text))
    r = client.post("/api/nl/watchlist", json={"media_item_id": 99999})
    check("an unknown title is a 404", r.status_code == 404, r.status_code)
    r = client.post("/api/nl/watchlist", json={"media_item_id": beta, "scope": "weird"})
    check("a bad scope is a 400", r.status_code == 400)
    r = client.put(f"/api/nl/interest/{alpha}", json={"state": "yes"})
    check("yes/maybe/pass works for the single viewer", r.status_code == 200 and r.json()["interest"] == [{"person": "default", "name": "You", "state": "yes", "mine": True}], r.text)
    r = client.put(f"/api/nl/interest/{alpha}", json={"state": "love"})
    check("a bad state is refused", r.status_code == 400)
    r = client.put(f"/api/nl/interest/{alpha}", json={"state": "yes", "person": "stranger"})
    check("someone who is not at the screen is refused", r.status_code == 403)
    wl = client.get("/api/nl/watchlist").json()
    check("watchlist: Alpha with your yes, Ready, solo", wl["solo"] and [i["id"] for i in wl["items"]] == [alpha] and wl["items"][0]["ready"] and wl["items"][0]["href"] == f"/nl/title/{alpha}", wl)
    check("the chips are offered", [c["label"] for c in wl["chips"]] == ["Under 90 min", "Under 2 h"])

    fake_rec = FakeRecommender()
    real_rec = server.recommender_service.recommend
    server.recommender_service.recommend = fake_rec.recommend
    fake_rec.pool = [
        {"id": delta, "title": "Delta", "genres": "Comedy", "rating": 7.8, "recommendation_score": 8.0},
        {"id": zeta, "title": "Zeta", "genres": "Sci-Fi, Drama", "rating": 7.9, "recommendation_score": 7.0},
        {"id": gamma, "title": "Gamma Ray", "genres": "Action", "rating": 7.0, "recommendation_score": 9.9},   # not on the shelf
    ]
    r = client.get("/api/nl/picks")
    p = r.json()
    call = fake_rec.calls[-1]
    check("picks: 'Picks for you' for one viewer", r.status_code == 200 and p["title"] == "Picks for you" and p["solo"] is True and p["engine"] == "recommender", p)
    check("the recommender is asked in profile 1's seat, movies, unwatched only", call["profile"] == "me" and call["media_type"] == "movie" and call["exclude_watched"] is True and call["return_pool"] is True, call)
    check("it is limited to what is on the shelf", set(call["only_ids"]) == {alpha, beta, delta, epsilon, zeta, arrival}, call["only_ids"])
    check("a title off the shelf never shows", [i["id"] for i in p["items"]] == [delta, zeta], [i["id"] for i in p["items"]])
    check("every card has a link and a reason", all(i["href"].startswith("/nl/title/") and i["reason"] for i in p["items"]), p["items"])
    zeta_reason = next(i["reason"] for i in p["items"] if i["id"] == zeta)
    check("reason from your own loved title", zeta_reason == "You loved Arrival (rated 9+), and this is Drama and Sci-Fi too", zeta_reason)
    check("no Jellyfin key or ids in the answer", SECRET_KEY not in r.text and "jfA" not in r.text and LAB not in r.text)

    print("runtime filter on the row and the list")
    r = client.get("/api/nl/picks?max_runtime=90")
    check("Under 90 min asks only about short movies", set(fake_rec.calls[-1]["only_ids"]) == {delta}, fake_rec.calls[-1]["only_ids"])
    r = client.get("/api/nl/picks?max_runtime=120")
    check("Under 2 h leaves out the 150 minute film", epsilon not in fake_rec.calls[-1]["only_ids"] and zeta in fake_rec.calls[-1]["only_ids"], fake_rec.calls[-1]["only_ids"])
    check("junk is no filter", epsilon in (client.get("/api/nl/picks?max_runtime=banana") and fake_rec.calls[-1]["only_ids"]))
    client.post("/api/nl/watchlist", json={"media_item_id": delta})
    client.post("/api/nl/watchlist", json={"media_item_id": epsilon})
    client.post("/api/nl/watchlist", json={"media_item_id": sigma})
    client.post("/api/nl/watchlist", json={"media_item_id": gamma})
    wl = client.get("/api/nl/watchlist?max_runtime=90").json()
    check("watchlist Under 90 min: Delta only (no runtime = cannot fit)", [i["id"] for i in wl["items"]] == [delta] and wl["max_minutes"] == 90, [i["id"] for i in wl["items"]])
    wl = client.get("/api/nl/watchlist?max_runtime=120").json()
    check("watchlist Under 2 h: Alpha and Delta", {i["id"] for i in wl["items"]} == {alpha, delta}, [i["id"] for i in wl["items"]])
    wl = client.get("/api/nl/watchlist").json()
    check("no filter: everything, including a show and a title not in the library", {i["id"] for i in wl["items"]} == {alpha, delta, epsilon, sigma, gamma}, [i["id"] for i in wl["items"]])
    g = next(i for i in wl["items"] if i["id"] == gamma)
    check("a title not in the library says so and points to the Download tab", g["ready"] is False and g["href"] is None and g["download_href"] == "/#direct", g)
    s = next(i for i in wl["items"] if i["id"] == sigma)
    check("a show links to its page", s["href"] == f"/nl/show/{sigma}" and s["runtime"] is None, s)
    check("your yes sorts first", wl["items"][0]["id"] == alpha, [i["id"] for i in wl["items"]])
    r = client.delete(f"/api/nl/watchlist/{epsilon}")
    check("remove", r.status_code == 200 and r.json()["removed"] is True and r.json()["listed"] is False)
    for t in (delta, sigma, gamma, alpha):
        client.delete(f"/api/nl/watchlist/{t}")
    db.set_interest(alpha, 1, None)

    print("the recommender cannot run: the row degrades, never a 500")
    fake_rec.fail = ImportError("no module named onnxruntime")
    r = client.get("/api/nl/picks")
    p = r.json()
    check("answers 200 with the fallback engine", r.status_code == 200 and p["engine"] == "fallback", (r.status_code, p.get("engine")))
    names = [i["title"] for i in p["items"]]
    check("well rated, unwatched titles from the shelf only (rating 7+, Arrival is seen)", names[:1] == ["Alpha"] and "Arrival" not in names and "Beta" not in names and "Gamma Ray" not in names, names)
    check("the note is honest about it", "recommender isn't running" in p["note"], p["note"])
    check("reasons make no claim about the recommender", all("recommend" not in i["reason"].lower() for i in p["items"]) and all(i["reason"] for i in p["items"]), [i["reason"] for i in p["items"]])
    fake_rec.fail = None
    fake_rec.pool = []
    p = client.get("/api/nl/picks").json()
    check("an empty answer from the recommender also falls back", p["engine"] == "fallback" and p["items"], p)

    print("the real recommender in this image")
    server.recommender_service.recommend = real_rec
    r = client.get("/api/nl/picks")
    p = r.json()
    check("the real thing answers 200 on the test library (recommender or fallback, either is fine)", r.status_code == 200 and p["engine"] in ("recommender", "fallback") and all(i["reason"] for i in p["items"]), (r.status_code, r.text[:300]))
    check("whatever it returns is on the shelf, once each", len({i["id"] for i in p["items"]}) == len(p["items"]) and all(i["id"] in (alpha, beta, delta, epsilon, zeta) for i in p["items"]), [i["id"] for i in p["items"]])
    server.recommender_service.recommend = fake_rec.recommend

    # ----------------------------------------------------------------- two people
    server.nl_people = np.People(np.parse_people([
        {"id": "labiba", "name": "Labiba", "jellyfin_user_id": LAB, "profile_id": 1},
        {"id": "partner", "name": "Sam", "jellyfin_user_id": PAR, "profile_id": 2},
    ]))
    fake_rec.fail, fake_rec.calls = None, []
    fake_rec.pool = [
        {"id": delta, "title": "Delta", "genres": "Comedy", "rating": 7.8, "recommendation_score": 8.0},
        {"id": zeta, "title": "Zeta", "genres": "Sci-Fi, Drama", "rating": 7.9, "recommendation_score": 7.0},
        {"id": alpha, "title": "Alpha", "genres": "Drama, Thriller", "rating": 8.0, "recommendation_score": 7.5},
    ]

    print("two people: watchlist, shared by default, Just me")
    r = client.get("/api/nl/watchlist/state", headers=cookie("labiba"))
    st = r.json()
    check("state: two people, Just me offered to one person", [p["name"] for p in st["people"]] == ["Labiba", "Sam"] and st["can_personal"] is True and st["both"] is False, st)
    r = client.get("/api/nl/watchlist/state", headers=cookie("both"))
    check("Both of us: no Just me", r.json()["can_personal"] is False and r.json()["both"] is True)
    r = client.post("/api/nl/watchlist", json={"media_item_id": alpha}, headers=cookie("labiba"))
    check("Labiba adds Alpha shared", r.status_code == 200 and r.json()["listed"] and not r.json()["personal"])
    r = client.post("/api/nl/watchlist", json={"media_item_id": beta, "scope": "me"}, headers=cookie("labiba"))
    check("Labiba adds Beta just for herself", r.status_code == 200 and r.json()["personal"] is True, r.text)
    sam = client.get("/api/nl/watchlist", headers=cookie("partner")).json()
    check("Sam sees Alpha but not Beta", [i["id"] for i in sam["items"]] == [alpha], [i["id"] for i in sam["items"]])
    check("Sam's state does not list Beta", str(beta) not in client.get("/api/nl/watchlist/state", headers=cookie("partner")).json()["listed"])
    lab = client.get("/api/nl/watchlist", headers=cookie("labiba")).json()
    check("Labiba sees both, Beta marked personal", {i["id"]: i["personal"] for i in lab["items"]} == {alpha: False, beta: True}, lab["items"])
    both = client.get("/api/nl/watchlist", headers=cookie("both")).json()
    check("Both of us shows the shared list only", [i["id"] for i in both["items"]] == [alpha])
    check("the old UI's list shows only the shared title", [e["media_item_id"] for e in client.get("/api/watch-later").json()["items"]] == [alpha])
    r = client.post("/api/nl/watchlist", json={"media_item_id": beta, "scope": "me"}, headers=cookie("both"))
    check("Both of us cannot add Just me", r.status_code == 400, r.status_code)
    r = client.post("/api/nl/watchlist", json={"media_item_id": delta}, headers=cookie("both"))
    check("Both of us adds a shared title for both", r.status_code == 200 and sorted(a["profile_id"] for a in db.get_watch_later()[0]["added_by"]) in ([1, 2], [1], [2]))
    with db.get_connection() as conn:
        rows = conn.execute("SELECT profile_id FROM watch_later WHERE media_item_id = ? ORDER BY profile_id", (delta,)).fetchall()
    check("and credits both people", [x[0] for x in rows] == [1, 2], [x[0] for x in rows])
    r = client.delete(f"/api/nl/watchlist/{alpha}", headers=cookie("partner"))
    check("Sam removing a shared title takes it off for both", r.status_code == 200 and client.get("/api/nl/watchlist", headers=cookie("labiba")).json()["count"] == 2)
    r = client.delete(f"/api/nl/watchlist/{beta}", headers=cookie("partner"))
    still = client.get("/api/nl/watchlist", headers=cookie("labiba")).json()
    check("Sam cannot remove Labiba's Just me title", beta in [i["id"] for i in still["items"]], [i["id"] for i in still["items"]])
    client.post("/api/nl/watchlist", json={"media_item_id": alpha}, headers=cookie("labiba"))

    print("two people: yes / maybe / pass and 'both said yes' first")
    r = client.put(f"/api/nl/interest/{alpha}", json={"state": "yes"}, headers=cookie("labiba"))
    check("Labiba says yes", r.status_code == 200 and [(v["person"], v["state"]) for v in r.json()["interest"]] == [("labiba", "yes"), ("partner", None)], r.text)
    r = client.put(f"/api/nl/interest/{delta}", json={"state": "yes"}, headers=cookie("partner"))
    check("Sam says yes to Delta, his own answer", [(v["person"], v["state"], v["mine"]) for v in r.json()["interest"]] == [("labiba", None, False), ("partner", "yes", True)], r.text)
    r = client.put(f"/api/nl/interest/{alpha}", json={"state": "yes", "person": "labiba"}, headers=cookie("partner"))
    check("Sam cannot answer for Labiba", r.status_code == 403, r.status_code)
    r = client.put(f"/api/nl/interest/{alpha}", json={"state": "yes"}, headers=cookie("both"))
    check("on Both of us the request must say whose answer", r.status_code == 400, r.status_code)
    r = client.put(f"/api/nl/interest/{alpha}", json={"state": "yes", "person": "partner"}, headers=cookie("both"))
    check("Both of us can answer for either", r.status_code == 200 and all(v["state"] == "yes" for v in r.json()["interest"]), r.text)
    client.put(f"/api/nl/interest/{delta}", json={"state": "maybe", "person": "labiba"}, headers=cookie("both"))
    client.post("/api/nl/watchlist", json={"media_item_id": epsilon}, headers=cookie("labiba"))
    client.put(f"/api/nl/interest/{epsilon}", json={"state": "pass", "person": "partner"}, headers=cookie("both"))
    client.post("/api/nl/watchlist", json={"media_item_id": zeta}, headers=cookie("labiba"))
    wl = client.get("/api/nl/watchlist", headers=cookie("labiba")).json()
    order = [i["id"] for i in wl["items"]]
    check("both said yes first, then one yes, then nothing, passed last", order[:1] == [alpha] and order[1] == delta and order[-1] == epsilon and zeta in order[2:-1] and beta in order[2:-1], order)
    check("the both-yes card says so", wl["items"][0]["both_yes"] is True and wl["items"][1]["both_yes"] is False)
    check("each card lists both people's answers", [(v["name"], v["state"]) for v in wl["items"][1]["interest"]] == [("Labiba", "maybe"), ("Sam", "yes")], wl["items"][1]["interest"])
    check("names on a card come from config", set(wl["items"][0]["added_by"]) == {"Labiba"} or "Labiba" in wl["items"][0]["added_by"], wl["items"][0]["added_by"])
    r = client.put(f"/api/nl/interest/{epsilon}", json={"state": None, "person": "partner"}, headers=cookie("both"))
    check("clearing an answer", r.status_code == 200 and all(v["state"] is None for v in r.json()["interest"]))
    client.put(f"/api/nl/interest/{epsilon}", json={"state": "pass", "person": "partner"}, headers=cookie("both"))
    st = client.get("/api/nl/watchlist/state", headers=cookie("labiba")).json()
    check("state carries everyone's answers per title", {v["person"]: v["state"] for v in st["interest"][str(alpha)]} == {"labiba": "yes", "partner": "yes"}, st["interest"].get(str(alpha)))

    print("two people: For both of us")
    fake_rec.calls = []
    r = client.get("/api/nl/picks", headers=cookie("both"))
    p = r.json()
    check("the row is called For both of us", r.status_code == 200 and p["title"] == "For both of us" and p["solo"] is False, p)
    call = fake_rec.calls[-1]
    check("Both of us asks the recommender in the together blend", call["profile"] == "together" and call["media_type"] == "movie" and call["return_pool"] is True, call)
    check("passed titles are not even asked about", epsilon not in call["only_ids"], call["only_ids"])
    ids_out = [i["id"] for i in p["items"]]
    check("the title both said yes to leads, even with a lower score", ids_out[0] == alpha, ids_out)
    check("its reason uses their names", p["items"][0]["reason"] == "Labiba and Sam both said yes", p["items"][0]["reason"])
    check("Sam's yes on Delta lifts it with his name", next(i["reason"] for i in p["items"] if i["id"] == delta) == "Sam said yes")
    zr = next(i["reason"] for i in p["items"] if i["id"] == zeta)
    check("Zeta: the title they both loved", zr == "You both loved Arrival (rated 8+), and this is Drama and Sci-Fi too", zr)
    check("cards keep the browse shape (poster card fields)", all({"id", "title", "href", "poster_url", "runtime", "genres", "reason"} <= set(i) for i in p["items"]), p["items"][0])
    r = client.get("/api/nl/picks", headers=cookie("partner"))
    check("one of the two: the same pair row from his seat", r.json()["title"] == "For both of us" and fake_rec.calls[-1]["profile"] == "partner", fake_rec.calls[-1]["profile"])
    r = client.get("/api/nl/picks", headers=cookie("labiba"))
    check("and from hers", fake_rec.calls[-1]["profile"] == "me")
    r = client.get("/api/nl/picks?max_runtime=120", headers=cookie("both"))
    check("the runtime chip applies to the row", epsilon not in fake_rec.calls[-1]["only_ids"] and all(i["runtime"] and i["runtime"] <= 120 for i in r.json()["items"]), r.json())
    wl = client.get("/api/nl/watchlist?max_runtime=90", headers=cookie("labiba")).json()
    check("and to the watchlist", all((i["runtime"] or 999) <= 90 for i in wl["items"]) and delta in [i["id"] for i in wl["items"]], [(i["id"], i["runtime"]) for i in wl["items"]])

    print("the pass of either person hides a title from the row")
    db.set_interest(zeta, 1, "pass")
    p = client.get("/api/nl/picks", headers=cookie("both")).json()
    check("Zeta is gone once Labiba passes", zeta not in [i["id"] for i in p["items"]] and zeta not in fake_rec.calls[-1]["only_ids"])
    db.set_interest(zeta, 1, None)

    print("two people: the recommender fails")
    fake_rec.fail = RuntimeError("model missing")
    r = client.get("/api/nl/picks", headers=cookie("both"))
    p = r.json()
    check("200, fallback, both-said-yes title still first with its reason", r.status_code == 200 and p["engine"] == "fallback" and p["items"][0]["id"] == alpha and p["items"][0]["reason"] == "Labiba and Sam both said yes", (r.status_code, p.get("engine"), p["items"][:1]))
    check("the fallback leaves out titles either has seen", arrival not in [i["id"] for i in p["items"]])
    fake_rec.fail = None

    print("pages and nav")
    r = client.get("/nl/watchlist")
    check("the watchlist page sends an unchosen viewer to the who screen", r.status_code == 302 and "/nl/who" in r.headers["location"])
    r = client.get("/nl/watchlist", headers=cookie("labiba"))
    check("the web page loads its script and has the chips", r.status_code == 200 and "/static/nl/watchlist.js" in r.text and 'id="nl-chips"' in r.text, r.status_code)
    r = client.get("/nl/tv/watchlist", headers=cookie("labiba"))
    check("the TV page has a focusable chips row and a list", r.status_code == 200 and 'data-nl-row="chips"' in r.text and 'id="tv-wl-list"' in r.text, r.text[:300])
    r = client.get("/nl/", headers=cookie("labiba"))
    check("home has the picks row", r.status_code == 200 and 'id="nl-picks"' in r.text and "picks.js" in r.text)
    r = client.get("/nl/tv/", headers=cookie("labiba"))
    check("TV home has the picks row and a Watchlist pill", r.status_code == 200 and 'id="tv-picks"' in r.text and "/nl/tv/watchlist" in r.text)
    r = client.get("/nl/movies", headers=cookie("labiba"))
    check("web nav links the watchlist", "/nl/watchlist" in r.text)

    server.nl_people = nobody
    server.recommender_service.recommend = real_rec


SECRET_KEY = nl_fake.SECRET


# ---------------------------------------------------------------------------- static


def test_static():
    print("scripts and styles")

    def read(*parts):
        with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
            return f.read()

    nl = read("static", "nl", "nl.js")
    check("the shared script builds the buttons and chips both layouts use", all(x in read("static", "nl", "watch.js") for x in ("iconButton", "fullControl", "interestControl", "cycleButton", "chips")))
    check("cards and search rows get the + button", "NLWatch.iconButton" in nl)
    for f in ("watch.js", "picks.js", "watchlist.js"):
        src = read("static", "nl", f)
        check(f"{f} puts text in through textContent, never innerHTML", "innerHTML" not in src)
        check(f"{f} has no key or Jellyfin id", all(x not in src for x in ("api_key", "X-Emby", "jellyfin_user_id")))
    tv = read("static", "nl", "tv", "tv.js")
    check("TV script handles the watchlist page and the picks row", 'page === "watchlist"' in tv and "picks" in tv)
    check("the old ticket-look UI is untouched", not read("templates", "index.html").count("nl_interest") and "api/nl" not in read("static", "app.js"))


if __name__ == "__main__":
    main()
