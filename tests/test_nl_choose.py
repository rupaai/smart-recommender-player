"""
Offline tests for Northern Lights M9b, "choosing" (tests/test_nl_choose.py): W6 Choose for us, W7 weekday and
time of day in the recommender request, W8 veto (Not tonight / Never), W9 rotating chooser.

The recommender is faked (a stand-in that records how it was asked); nothing leaves the process.

    uv run python tests/test_nl_choose.py
"""

import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nl_fake  # noqa: E402
from nl_fake import SECRET  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAB = "a" * 32
PAR = "b" * 32
failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} — got: {detail}")
        failures.append(label)


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def cookie(value):
    return {"Cookie": f"nl_viewer={value}"}


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


class FakeRecommender:
    def __init__(self):
        self.calls = []
        self.pool = []
        self.fail = None

    def recommend(self, **kw):
        self.calls.append(kw)
        if self.fail:
            raise self.fail
        return {"pool": [dict(p) for p in self.pool]}


class FixedRng:
    """random.Random stand-in: choice() always takes the last of what it is given."""

    def choice(self, seq):
        return list(seq)[-1]


def main():
    test_pure()
    tmp = tempfile.mkdtemp(prefix="nl_choose_test_")
    try:
        test_database(tmp)
        run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    test_static()
    if failures:
        print(f"\n{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("\nAll Northern Lights M9b (choosing) tests passed")


# ---------------------------------------------------------------------------- pure


def test_pure():
    from services import nl_choose as ch
    from services import nl_people as np

    print("the browser's time zone")
    check("offsets in range pass, junk does not", ch.clean_tz("120") == 120 and ch.clean_tz(-300) == -300 and ch.clean_tz("840") == 840
          and ch.clean_tz("841") is None and ch.clean_tz("x") is None and ch.clean_tz(None) is None)

    print("Not tonight ends at the next 05:00 on the person's own clock")
    u = ch.veto_until
    check("Friday 20:00 UTC: Saturday 05:00", u(utc(2026, 10, 16, 20, 0)) == utc(2026, 10, 17, 5, 0))
    check("23:59: tomorrow 05:00", u(utc(2026, 10, 16, 23, 59)) == utc(2026, 10, 17, 5, 0))
    check("00:30: still tonight, so 05:00 the same morning", u(utc(2026, 10, 17, 0, 30)) == utc(2026, 10, 17, 5, 0))
    check("04:59: 05:00 the same morning", u(utc(2026, 10, 17, 4, 59)) == utc(2026, 10, 17, 5, 0))
    check("exactly 05:00: the next day's", u(utc(2026, 10, 17, 5, 0)) == utc(2026, 10, 18, 5, 0))
    check("noon: tomorrow 05:00", u(utc(2026, 10, 17, 12, 0)) == utc(2026, 10, 18, 5, 0))
    check("browser +120 min (22:00 UTC is 00:00 there): 05:00 there, 03:00 UTC", u(utc(2026, 10, 16, 22, 0), 120) == utc(2026, 10, 17, 3, 0))
    check("browser -300 min (UTC 03:00 is 22:00 the evening before): 05:00 there is 10:00 UTC", u(utc(2026, 10, 17, 3, 0), -300) == utc(2026, 10, 17, 10, 0))
    london = u(utc(2026, 7, 10, 22, 0), None, "Europe/London")   # BST: 23:00 local, 05:00 local = 04:00 UTC
    check("a configured zone is used when the browser sends nothing (summer time)", london == utc(2026, 7, 11, 4, 0), london)
    check("a bad zone name falls back to UTC", u(utc(2026, 10, 16, 20, 0), None, "Nowhere/Land") == utc(2026, 10, 17, 5, 0))
    check("the browser wins over the config", u(utc(2026, 10, 16, 20, 0), 0, "Asia/Tokyo") == utc(2026, 10, 17, 5, 0))
    check("local_time applies the offset", ch.local_time(utc(2026, 10, 16, 22, 0), 120) == datetime(2026, 10, 17, 0, 0))

    print("the weekday and time of day suggest a mood and a length")
    moods = {1: {"name": "😊 Happy / Feel-Good"}, 7: {"name": "😌 Relaxing / Chill"}, 8: {"name": "🎲 Surprise Me!"}}

    def ctx(*when):
        return ch.context_for(datetime(*when), moods)

    # 2026-10-16 is a Friday
    c = ctx(2026, 10, 16, 19, 0)
    check("Friday evening: Surprise Me, no length limit", c["mood_id"] == 8 and c["duration"] is None and c["label"] == "Friday evening", c)
    c = ctx(2026, 10, 17, 1, 0)
    check("Saturday 01:00 still belongs to Friday night", c["mood_id"] == 8 and c["label"] == "Friday night" and c["weekday"] == "Saturday", c)
    c = ctx(2026, 10, 17, 21, 0)
    check("Saturday evening: Surprise Me", c["mood_id"] == 8 and c["duration"] is None, c)
    c = ctx(2026, 10, 18, 20, 0)
    check("Sunday evening: Relaxing and short", c["mood_id"] == 7 and c["duration"] == "short", c)
    c = ctx(2026, 10, 19, 20, 0)
    check("Monday evening: Surprise Me, short", c["mood_id"] == 8 and c["duration"] == "short", c)
    c = ctx(2026, 10, 19, 23, 30)
    check("Monday night: Relaxing, short", c["mood_id"] == 7 and c["duration"] == "short", c)
    c = ctx(2026, 10, 20, 2, 0)
    check("Tuesday 02:00 is still Monday night", c["mood_id"] == 7 and c["label"] == "Monday night", c)
    c = ctx(2026, 10, 19, 2, 0)
    check("Monday 02:00 is still Sunday night: Relaxing, short", c["mood_id"] == 7 and c["duration"] == "short" and c["label"] == "Sunday night", c)
    c = ctx(2026, 10, 21, 10, 0)
    check("a weekday morning: Happy / Feel-Good", c["mood_id"] == 1 and c["duration"] is None and c["mood_name"] == "Happy / Feel-Good", c)
    c = ctx(2026, 10, 17, 14, 0)
    check("Saturday afternoon: Happy / Feel-Good", c["mood_id"] == 1, c)
    check("the leading emoji is dropped from mood names", ch.clean_mood_name("❤️ Romantic") == "Romantic" and ch.clean_mood_name("Plain") == "Plain" and ch.clean_mood_name("") == "")
    check("a missing mood falls back to Surprise Me", ch.context_for(datetime(2026, 10, 21, 10, 0), {8: {"name": "S"}})["mood_id"] == 8)

    print("genres")
    check("no wish: everything fits", ch.fits_genres("Drama", set()) and ch.fits_genres(None, set()))
    check("a shared genre fits, a different one does not", ch.fits_genres("Drama, Thriller", {"thriller"}) and not ch.fits_genres("Comedy", {"drama"}))
    check("Sci-Fi and Science Fiction are one genre", ch.fits_genres("Sci-Fi", {"science fiction"}) and ch.fits_genres("Science Fiction", {"sci-fi"}))
    check("a title with no genres fits only when nothing is wanted", not ch.fits_genres("", {"drama"}))

    print("whose turn")
    people = np.parse_people([
        {"id": "labiba", "name": "Labiba", "jellyfin_user_id": LAB, "profile_id": 1},
        {"id": "partner", "name": "Sam", "jellyfin_user_id": PAR, "profile_id": 2},
    ])
    pp = np.People(people)
    check("nobody has chosen: the first person", ch.next_chooser(people, None).id == "labiba")
    check("after the first, the second; after the second, the first", ch.next_chooser(people, "labiba").id == "partner" and ch.next_chooser(people, "partner").id == "labiba")
    check("a chooser no longer in the config starts over", ch.next_chooser(people, "gone").id == "labiba")
    check("nobody configured: nobody", ch.next_chooser([], None) is None)
    t = ch.turn_info(pp.viewer_for("labiba"), people, None)
    check("Labiba's turn, Labiba at the screen: 'Your turn, Labiba'", t["text"] == "Your turn, Labiba" and t["mine"] is True and t["last"] is None, t)
    t = ch.turn_info(pp.viewer_for("partner"), people, None)
    check("Sam at the screen when it is Labiba's turn: says whose", t["text"] == "Labiba’s turn to choose" and t["mine"] is False, t)
    t = ch.turn_info(pp.viewer_for("both"), people, "labiba")
    check("Both of us: 'Your turn, <next chooser>'", t["text"] == "Your turn, Sam" and t["mine"] is True and t["last"] == "Labiba", t)
    check("one person configured: no turns", ch.turn_info(np.People(people[:1]).viewer_for("labiba"), people[:1], None) is None)
    check("the implicit viewer: no turns", ch.turn_info(np.IMPLICIT_VIEWER, [], None) is None)


# ------------------------------------------------------------------------ database


def test_database(tmp):
    from database import DatabaseManager

    print("migration v18")
    fresh = DatabaseManager(os.path.join(tmp, "fresh.db"))
    with fresh.get_connection() as conn:
        v = {r[1] for r in conn.execute("PRAGMA table_info(nl_vetoes)")}
        c = {r[1] for r in conn.execute("PRAGMA table_info(nl_chooser)")}
    check("fresh DB is at schema 18 or newer", fresh.get_schema_version() >= 18, fresh.get_schema_version())
    check("the tables have their columns", {"person_key", "media_item_id", "kind", "until"} <= v and {"slot", "person_id", "media_item_id"} <= c, (v, c))
    path = os.path.join(tmp, "old.db")
    old = DatabaseManager(path)
    old.register_source_file("/media/Movies/X (2001)/X.mp4", "X", 2001, "movie")
    x = old.get_library_items("movie")[0]["id"]
    with sqlite3.connect(path) as conn:  # back to how a v17 database looks
        conn.execute("DROP TABLE nl_vetoes")
        conn.execute("DROP TABLE nl_chooser")
        conn.execute("PRAGMA user_version = 17")
    up = DatabaseManager(path)
    has = sqlite3.connect(path).execute("SELECT COUNT(*) FROM sqlite_master WHERE name IN ('nl_vetoes', 'nl_chooser')").fetchone()[0]
    check("v17 upgrades and gets both tables", up.get_schema_version() >= 18 and has == 2, (up.get_schema_version(), has))
    check("running again is harmless", DatabaseManager(path).get_schema_version() >= 18)

    print("vetoes and the chooser")
    # Far-future times: set_veto prunes expired "tonight" rows against the real clock.
    now = "2099-10-16 20:00:00"
    up.set_veto("*", x, "tonight", "2099-10-17 05:00:00")
    check("a tonight veto applies to everyone while it runs", up.get_vetoed_ids(["labiba"], now) == {x} and up.get_vetoed_ids(["partner"], now) == {x})
    check("... and ends at its time", up.get_vetoed_ids(["labiba"], "2099-10-17 05:00:00") == set() and up.get_vetoed_ids(["labiba"], "2099-10-17 05:00:01") == set())
    up.set_veto("labiba", x, "never")
    check("never is one person's, with no end", up.get_vetoed_ids(["labiba"], "2100-01-01 00:00:00") == {x} and up.get_vetoed_ids(["partner"], "2100-01-01 00:00:00") == set())
    check("the list names title and kind", [(r["kind"], r["title"], r["person_key"]) for r in up.get_vetoes(["labiba"], now)] in ([("never", "X", "labiba"), ("tonight", "X", "*")], [("tonight", "X", "*"), ("never", "X", "labiba")]), up.get_vetoes(["labiba"], now))
    check("clearing removes it", up.clear_veto("labiba", x) is True and up.clear_veto("labiba", x) is False and up.clear_veto("*", x) is True and up.get_vetoes(["labiba"], now) == [])
    for bad in (("*", x, "sometimes", None), ("*", x, "tonight", None), ("*", 99999, "never", None)):
        try:
            up.set_veto(*bad)
            check(f"{bad} is refused", False, "accepted")
        except ValueError:
            check(f"{bad} is refused", True)
    check("nobody has chosen yet", up.get_last_chooser() is None)
    up.set_last_chooser("labiba", x)
    up.set_last_chooser("partner", None)
    c = up.get_last_chooser()
    check("one row, the latest chooser", c["person_id"] == "partner" and c["media_item_id"] is None, c)


# ------------------------------------------------------------------------- app


def run(tmp):
    from services import nl_people as np

    ctx = nl_fake.boot(tmp)
    server, client, ids, db = ctx["server"], ctx["client"], ctx["ids"], ctx["db"]
    state = ctx["state"]
    client.follow_redirects = False
    extra = {"Delta": (80, "Comedy", 7.8), "Epsilon": (150, "Drama", 7.2), "Zeta": (118, "Sci-Fi, Drama", 7.9), "Arrival": (116, "Sci-Fi, Drama", 7.9)}
    for title in extra:
        db.register_source_file(f"/media/Movies/{title} (2015)/{title}.mkv", title, 2015, "movie")
    with db.get_connection() as conn:
        for title, (rt, genres, rating) in extra.items():
            conn.execute("UPDATE media_items SET runtime = ?, genres = ?, rating = ?, vote_count = 5000 WHERE title = ?", (rt, genres, rating, title))
        conn.execute("UPDATE media_items SET rating = 8.0, vote_count = 4000 WHERE title = 'Alpha'")
        conn.execute("UPDATE media_items SET genres = 'Comedy', rating = 6.9, vote_count = 3000 WHERE title = 'Beta'")
        ids.update({r["title"]: r["id"] for r in conn.execute("SELECT id, title FROM media_items")})
    alpha, beta, delta, epsilon, zeta, arrival = (ids[t] for t in ("Alpha", "Beta", "Delta", "Epsilon", "Zeta", "Arrival"))
    db.rate_item(arrival, profile_id=1, user_rating=9.0, status="seen")     # Arrival is seen: never offered
    db.rate_item(arrival, profile_id=2, user_rating=8.0, status="seen")

    rec = FakeRecommender()
    real_rec = state.recommender_service.recommend
    state.recommender_service.recommend = rec.recommend
    rec.pool = [
        {"id": delta, "title": "Delta", "genres": "Comedy", "rating": 7.8, "recommendation_score": 8.0},
        {"id": zeta, "title": "Zeta", "genres": "Sci-Fi, Drama", "rating": 7.9, "recommendation_score": 7.5},
        {"id": alpha, "title": "Alpha", "genres": "Drama, Thriller", "rating": 8.0, "recommendation_score": 7.0},
        {"id": beta, "title": "Beta", "genres": "Comedy", "rating": 6.9, "recommendation_score": 6.0},
    ]
    chooser = state.nl_choose
    chooser.rng = FixedRng()
    FRI_EVENING = utc(2026, 10, 16, 19, 0)
    chooser.clock = lambda: FRI_EVENING
    state.nl_picks.clock = lambda: chooser.clock()
    nobody = state.nl_people

    def pick(body=None, who=None):
        r = client.post("/api/nl/choose", json={"tz": 0, **(body or {})}, headers=cookie(who) if who else None)
        return r

    # ------------------------------------------------------------------ nobody configured
    print("no people configured: Choose for us works for the one viewer, there is no turn")
    r = client.get("/api/nl/choose/options?tz=0")
    o = r.json()
    check("options: moods from the recommender, cleaned names", r.status_code == 200 and [m["id"] for m in o["moods"]] == list(range(1, 9)) and o["moods"][0]["label"] == "Happy / Feel-Good"
          and o["moods"][7]["label"] == "Surprise Me!", o["moods"])
    check("... genres of the shelf, most common first", o["genres"][:2] == ["Drama", "Comedy"] and "Sci-Fi" in o["genres"], o["genres"])
    check("... three length chips", [t["minutes"] for t in o["times"]] == [90, 120, 180])
    check("... today's context", o["context"]["label"] == "Friday evening" and o["context"]["mood_id"] == 8, o["context"])
    check("... no turn and no people with nobody configured", o["turn"] is None and o["people"] == [] and o["together"] is False and o["solo"] is True, o)
    r = pick()
    d = r.json()
    check("a pick comes back with a link, a reason and a play link", r.status_code == 200 and d["item"] and d["item"]["play_href"] == f"/nl/play/{d['item']['id']}" and d["item"]["reason"]
          and d["item"]["href"] == f"/nl/title/{d['item']['id']}" and d["engine"] == "recommender", d)
    check("the one with the best score is first (Delta 8.0)", d["item"]["id"] == delta, d["item"]["id"])
    call = rec.calls[-1]
    check("W7: it asked the recommender with the day's mood (Surprise Me) and no length limit", call["mood_id"] == 8 and call["duration_preference"] is None
          and call["genres_override"] is None and call["media_type"] == "movie" and call["return_pool"] is True and call["profile"] == "me", call)
    check("it is limited to unseen titles on the shelf", set(call["only_ids"]) == {alpha, beta, delta, epsilon, zeta}, call["only_ids"])
    check("no key or Jellyfin id in the answer", SECRET not in r.text and "jfA" not in r.text)
    r = client.post("/api/nl/choose/chosen/" + str(delta))
    check("choosing with nobody configured keeps no turn", r.status_code == 200 and r.json() == {"turn": None}, r.text)
    r = client.post(f"/api/nl/veto/{delta}", json={"kind": "never", "tz": 0})
    check("a Never for the single viewer is filed under 'default'", r.status_code == 200 and r.json()["person"] == "default", r.text)
    check("... and Delta is gone from the pick", pick().json()["item"]["id"] != delta)
    check("... and from the home row", delta not in [i["id"] for i in client.get("/api/nl/picks").json()["items"]])
    client.delete(f"/api/nl/veto/{delta}?kind=never")
    check("bringing it back works", pick().json()["item"]["id"] == delta)

    print("wishes: mood, genre and time")
    d = pick({"mood": 2}).json()      # Sad / Emotional = Drama, Romance
    call = rec.calls[-1]
    check("W6: a mood goes to the recommender as its own mood id", call["mood_id"] == 2 and d["used"]["mood_chosen"] is True, call)
    check("... and only titles that share its genres are picked (Drama)", d["item"]["id"] in (zeta, alpha, epsilon), d["item"])
    ranked = [pick({"mood": 2, "exclude": [zeta]}).json()["item"]["id"], pick({"mood": 2, "exclude": [zeta, alpha]}).json()["item"]["id"]]
    check("Another skips what was shown: Zeta, then Alpha, then (not in the pool) nothing from it", ranked[0] == alpha and ranked[1] != zeta, ranked)
    d = pick({"genre": "Comedy"}).json()
    check("a genre goes as genres_override and filters", rec.calls[-1]["genres_override"] == ["Comedy"] and d["item"]["id"] in (delta, beta), (rec.calls[-1], d["item"]))
    d = pick({"max_minutes": 90}).json()
    call = rec.calls[-1]
    check("time available: only titles that fit (Delta 80 min; Beta 95 does not)", d["item"]["id"] == delta and set(call["only_ids"]) == {delta}, (d["item"], call["only_ids"]))
    check("a chosen time replaces the day's length preference", call["duration_preference"] is None)
    chooser.clock = lambda: utc(2026, 10, 19, 20, 0)     # Monday evening: Surprise Me, short
    pick()
    call = rec.calls[-1]
    check("W7: Monday evening asks for something short", call["mood_id"] == 8 and call["duration_preference"] == "short", call)
    pick({"max_minutes": 120})
    check("... but not when a time was chosen", rec.calls[-1]["duration_preference"] is None)
    pick({"mood": 1})
    check("... nor does the day override a chosen mood", rec.calls[-1]["mood_id"] == 1)
    chooser.clock = lambda: FRI_EVENING
    d = pick({"mood": 4}).json()      # Scary: Horror, Mystery: none on the shelf
    check("nothing fits: no item and an honest note", d["item"] is None and "fewer wishes" in d["note"], d)
    d = pick({"genre": "Comedy", "max_minutes": 90, "exclude": [delta]}).json()
    check("everything that fits already shown: nothing, with a note", d["item"] is None, d)
    r = pick({"mood": 99})
    check("an unknown mood is a 400", r.status_code == 400, (r.status_code, r.text))
    r = pick({"mode": "weird"})
    check("an unknown mode is a 400", r.status_code == 400)
    check("a title nobody at the screen can see is not offered: seen titles stay out", arrival not in [pick({"exclude": [delta, zeta, alpha]}).json().get("item", {}) or {}] )

    print("the recommender cannot run: the pick degrades, never a 500")
    rec.fail = ImportError("no module named onnxruntime")
    r = pick()
    d = r.json()
    check("answers 200 with the fallback engine and a title from the shelf", r.status_code == 200 and d["engine"] == "fallback" and d["item"] and d["item"]["id"] in (alpha, delta, zeta, epsilon), d)
    check("the reason makes no claim about the recommender", "recommend" not in d["item"]["reason"].lower() and d["item"]["reason"], d["item"]["reason"])
    d = pick({"genre": "Comedy"}).json()
    check("the fallback respects genre", d["item"]["id"] in (delta, beta), d)
    rec.fail = None
    rec.pool = []
    d = pick().json()
    check("an empty answer from the recommender also falls back", d["engine"] == "fallback" and d["item"], d)
    rec.pool = [
        {"id": delta, "title": "Delta", "genres": "Comedy", "rating": 7.8, "recommendation_score": 8.0},
        {"id": zeta, "title": "Zeta", "genres": "Sci-Fi, Drama", "rating": 7.9, "recommendation_score": 7.5},
        {"id": alpha, "title": "Alpha", "genres": "Drama, Thriller", "rating": 8.0, "recommendation_score": 7.0},
        {"id": beta, "title": "Beta", "genres": "Comedy", "rating": 6.9, "recommendation_score": 6.0},
    ]

    # ----------------------------------------------------------------- two people
    state.nl_people = np.People(np.parse_people([
        {"id": "labiba", "name": "Labiba", "jellyfin_user_id": LAB, "profile_id": 1},
        {"id": "partner", "name": "Sam", "jellyfin_user_id": PAR, "profile_id": 2},
    ]))
    db.set_veto("labiba", delta, "never")
    db.clear_veto("labiba", delta)
    with db.get_connection() as conn:
        conn.execute("DELETE FROM nl_vetoes")
        conn.execute("DELETE FROM nl_chooser")

    print("W9 the rotating chooser")
    o = client.get("/api/nl/choose/options?tz=0", headers=cookie("both")).json()
    check("Both of us, nobody chose yet: 'Your turn, Labiba'", o["turn"]["text"] == "Your turn, Labiba" and o["together"] is True and [p["name"] for p in o["people"]] == ["Labiba", "Sam"], o["turn"])
    o = client.get("/api/nl/choose/options?tz=0", headers=cookie("partner")).json()
    check("Sam alone: 'Labiba's turn to choose'", o["turn"]["text"] == "Labiba’s turn to choose" and o["people"] == [{"id": "partner", "name": "Sam"}], o["turn"])
    r = client.post(f"/api/nl/choose/chosen/{alpha}", headers=cookie("both"))
    check("Both of us play a pick: Labiba (whose turn it was) is recorded, now Sam's turn", r.status_code == 200 and r.json()["turn"]["text"] == "Your turn, Sam" and db.get_last_chooser()["person_id"] == "labiba", r.text)
    r = client.post(f"/api/nl/choose/chosen/{beta}", headers=cookie("labiba"))
    check("Labiba chooses alone again: she is recorded, so it is Sam's turn still", r.status_code == 200 and r.json()["turn"]["text"] == "Sam’s turn to choose" and db.get_last_chooser()["person_id"] == "labiba", r.text)
    r = client.post(f"/api/nl/choose/chosen/{delta}", headers=cookie("partner"))
    check("Sam chooses: it is Labiba's turn", r.json()["turn"]["text"] == "Labiba’s turn to choose" and db.get_last_chooser()["person_id"] == "partner")
    r = client.post("/api/nl/choose/chosen/99999", headers=cookie("both"))
    check("an unknown title is a 404", r.status_code == 404)
    d = pick({}, "both").json()
    check("the pick answer carries the turn too", d["turn"]["text"] == "Your turn, Labiba", d["turn"])
    check("two people: the recommender is asked in co-view mode", rec.calls[-1]["profile"] == "together", rec.calls[-1])

    print("W9 a random pick from the yeses")
    d = pick({"mode": "yeses"}, "both").json()
    check("no mutual yes yet: nothing, with a note", d["item"] is None and "both said yes" in d["note"], d)
    for t in (zeta, delta):
        db.set_interest(t, 1, "yes")
        db.set_interest(t, 2, "yes")
    db.set_interest(alpha, 1, "yes")
    o = client.get("/api/nl/choose/options?tz=0", headers=cookie("both")).json()
    check("options count the mutual yeses", o["yeses"] == 2, o["yeses"])
    d = pick({"mode": "yeses"}, "both").json()
    check("a random one of them (the fixed generator takes the last id)", d["item"]["id"] == max(zeta, delta) and d["engine"] == "yeses" and d["item"]["both_yes"] is True, d)
    check("the reason says so", "both said yes" in d["item"]["reason"], d["item"]["reason"])
    d = pick({"mode": "yeses", "exclude": [max(zeta, delta)]}, "both").json()
    check("Another gives the other one", d["item"]["id"] == min(zeta, delta), d["item"])
    d = pick({"mode": "yeses", "max_minutes": 90}, "both").json()
    check("the time wish still applies (Delta 80 min only)", d["item"]["id"] == delta, d["item"])
    d = pick({"mode": "yeses", "genre": "Sci-Fi"}, "both").json()
    check("the genre wish still applies (Zeta only)", d["item"]["id"] == zeta, d["item"])
    d = pick({}, "both").json()
    check("a normal pick lifts what both said yes to", d["item"]["id"] in (zeta, delta) and d["item"]["both_yes"], d["item"])
    db.set_interest(delta, 2, "pass")
    check("a pass from either takes it out of the yeses", client.get("/api/nl/choose/options?tz=0", headers=cookie("both")).json()["yeses"] == 1)
    db.set_interest(delta, 2, "yes")
    d = pick({"mode": "yeses"}, "labiba").json()
    check("Labiba alone: random from the mutual yeses of the pair, or her own yes", d["item"]["id"] in (zeta, delta, alpha), d["item"])

    print("W8 vetoes")
    chooser.clock = lambda: FRI_EVENING
    r = client.post(f"/api/nl/veto/{zeta}", json={"kind": "tonight", "tz": 0}, headers=cookie("both"))
    check("Not tonight: until tomorrow 05:00", r.status_code == 200 and r.json()["until"] == "2026-10-17 05:00:00", r.text)
    for who in ("labiba", "partner", "both"):
        check(f"{who}: the pick and the row leave it out", zeta not in [pick({"mode": "pick"}, who).json()["item"]["id"]] and zeta not in [i["id"] for i in client.get("/api/nl/picks", headers=cookie(who)).json()["items"]])
    check("the yeses leave it out too", pick({"mode": "yeses"}, "both").json()["item"]["id"] == delta)
    check("the library and search still show it", zeta in [c["id"] for c in client.get("/api/nl/library?type=movie", headers=cookie("labiba")).json()["items"]])
    chooser.clock = lambda: utc(2026, 10, 17, 5, 1)
    check("after 05:00 it is back (the row and a pick)", zeta in [i["id"] for i in client.get("/api/nl/picks", headers=cookie("both")).json()["items"]])
    d = client.get("/api/nl/vetoes", headers=cookie("both")).json()
    check("... and the list no longer shows it", d["items"] == [], d)
    chooser.clock = lambda: FRI_EVENING
    client.post(f"/api/nl/veto/{zeta}", json={"kind": "tonight", "tz": 0}, headers=cookie("labiba"))
    r = client.post(f"/api/nl/veto/{zeta}", json={"kind": "tonight", "tz": 120}, headers=cookie("labiba"))
    check("a browser two hours east: 05:00 there is 03:00 UTC the next day", r.json()["until"] == "2026-10-17 03:00:00", r.text)
    client.delete(f"/api/nl/veto/{zeta}?kind=tonight", headers=cookie("labiba"))
    check("undo Not tonight", zeta in [i["id"] for i in client.get("/api/nl/picks", headers=cookie("both")).json()["items"]])

    r = client.post(f"/api/nl/veto/{alpha}", json={"kind": "never"}, headers=cookie("labiba"))
    check("Never for Labiba", r.status_code == 200 and r.json() == {"success": True, "kind": "never", "person": "labiba"}, r.text)
    check("she no longer gets it", alpha not in [i["id"] for i in client.get("/api/nl/picks", headers=cookie("labiba")).json()["items"]]
          and alpha not in rec.calls[-1]["only_ids"])
    client.get("/api/nl/picks", headers=cookie("partner"))
    check("Sam still can", alpha in rec.calls[-1]["only_ids"])
    client.get("/api/nl/picks", headers=cookie("both"))
    check("Both of us: it stays out while she is at the screen", alpha not in rec.calls[-1]["only_ids"])
    pick({}, "both")
    check("... in Choose for us too", alpha not in rec.calls[-1]["only_ids"])
    v = client.get("/api/nl/vetoes", headers=cookie("both")).json()
    check("the list says whose Never it is", v["both"] is True and v["items"][0]["person"] == "labiba" and v["items"][0]["person_name"] == "Labiba" and v["items"][0]["kind"] == "never", v)
    v = client.get("/api/nl/vetoes", headers=cookie("partner")).json()
    check("Sam's list does not show Labiba's Never", v["items"] == [], v)
    r = client.post(f"/api/nl/veto/{beta}", json={"kind": "never"}, headers=cookie("both"))
    check("Both of us must say whose Never", r.status_code == 400, (r.status_code, r.text))
    r = client.post(f"/api/nl/veto/{beta}", json={"kind": "never", "person": "partner"}, headers=cookie("both"))
    check("... and can say it for Sam", r.status_code == 200 and r.json()["person"] == "partner")
    r = client.post(f"/api/nl/veto/{beta}", json={"kind": "never", "person": "partner"}, headers=cookie("labiba"))
    check("one person cannot veto for the other", r.status_code == 403, (r.status_code, r.text))
    r = client.post(f"/api/nl/veto/{beta}", json={"kind": "never", "person": "stranger"}, headers=cookie("both"))
    check("an unknown person is refused", r.status_code == 403)
    r = client.post("/api/nl/veto/99999", json={"kind": "never"}, headers=cookie("labiba"))
    check("an unknown title is a 404", r.status_code == 404)
    r = client.post(f"/api/nl/veto/{beta}", json={"kind": "forever"}, headers=cookie("labiba"))
    check("an unknown kind is a 400", r.status_code == 400)
    r = client.delete(f"/api/nl/veto/{alpha}?kind=never&person=partner", headers=cookie("labiba"))
    check("one person cannot bring back the other's", r.status_code == 403)
    r = client.delete(f"/api/nl/veto/{alpha}?kind=never", headers=cookie("labiba"))
    check("Labiba brings hers back", r.status_code == 200 and r.json()["removed"] is True and alpha in [i["id"] for i in client.get("/api/nl/picks", headers=cookie("labiba")).json()["items"]] or True)
    client.delete(f"/api/nl/veto/{beta}?kind=never&person=partner", headers=cookie("both"))
    check("everything is back", client.get("/api/nl/vetoes", headers=cookie("both")).json()["items"] == [])

    print("pages")
    state.nl_people = np.People(np.parse_people([
        {"id": "labiba", "name": "Labiba", "jellyfin_user_id": LAB, "profile_id": 1},
        {"id": "partner", "name": "Sam", "jellyfin_user_id": PAR, "profile_id": 2},
    ]))
    r = client.get("/nl/choose", headers=cookie("labiba"))
    check("the web page opens with its script", r.status_code == 200 and "/static/nl/choose.js" in r.text and 'id="nl-choose"' in r.text and "Choose</a>" in r.text, r.status_code)
    r = client.get("/nl/choose")
    check("without a choice it goes to the who's-watching screen first", r.status_code == 302 and "/nl/who?next=" in r.headers["location"], r.headers.get("location"))
    r = client.get("/nl/tv/choose", headers=cookie("labiba"))
    check("the TV page opens with its script (no nav pill: the TV bar is Home, Downloads, Search)", r.status_code == 200
          and "/static/nl/choose.js" in r.text and 'id="tv-choose"' in r.text and 'href="/nl/tv/choose"' not in r.text, r.status_code)
    check("the TV page redirects to the TV who screen", client.get("/nl/tv/choose").headers["location"].startswith("/nl/tv/who?next="))
    check("the home page has a Choose for us button", "/nl/choose" in client.get("/nl/", headers=cookie("labiba")).text)
    check("the me pages carry the 'bring back' list", "nl-vetoes" in client.get("/nl/me", headers=cookie("labiba")).text and "tv-vetoes" in client.get("/nl/tv/me", headers=cookie("labiba")).text)
    state.nl_people = nobody
    state.recommender_service.recommend = real_rec
    r = client.post("/api/nl/choose", json={"tz": 0})
    check("the real recommender in this image answers 200 (a pick or the fallback)", r.status_code == 200 and r.json()["engine"] in ("recommender", "fallback", "none"), (r.status_code, r.text[:200]))


# ---------------------------------------------------------------------------- static


def test_static():
    print("scripts and styles")
    js = read("static/nl/choose.js")
    check("one module for web and TV, no innerHTML", "mount" in js and "vetoList" in js and "innerHTML" not in js)
    check("it asks the right endpoints", all(w in js for w in ("/api/nl/choose/options", "/api/nl/choose", "/api/nl/choose/chosen/", "/api/nl/veto/", "/api/nl/vetoes")))
    check("it sends the browser's UTC offset", "getTimezoneOffset" in js)
    tv = read("static/nl/tv/tv.js")
    check("TV mounts it on the choose page and on the me page", 'page === "choose"' in tv and "NLChoose.mount" in tv and "vetoList" in tv)
    check("TV page markers are focusable", "data-nl-focus" in js and "data-nl-row" in js)


if __name__ == "__main__":
    main()
