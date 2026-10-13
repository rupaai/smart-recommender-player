"""
Offline tests for Northern Lights M9a, "together" (tests/test_nl_together.py): U4 seen together,
U5 the watch-together pact and its question, U6 "Mark unwatched for me".

Jellyfin is faked (see nl_fake.py and test_nl_people.PeopleJellyfin); nothing leaves the process.

    uv run python tests/test_nl_together.py
"""

import os
import re
import shutil
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nl_fake  # noqa: E402
from nl_fake import EPS, SECRET, FakeResponse  # noqa: E402
from test_nl_people import LAB, PAR, PeopleJellyfin  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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


class TogetherJellyfin(PeopleJellyfin):
    """PeopleJellyfin plus per-user state for any episode (played flag and minutes)."""

    def __init__(self):
        super().__init__()
        self.state = {}      # (user id, jellyfin item id) -> (minutes, played)

    def request(self, method, url, **kw):
        path = url.split("jellyfin.test:8096")[1] if "jellyfin.test:8096" in url else url
        m = re.match(r"^/Users/([0-9a-f]{32})/Items/(jfS\w+)$", path)
        if m and method == "GET" and not self.down:
            uid, jf = m.group(1), m.group(2)
            mins, played = self.state.get((uid, jf), (0, False))
            return FakeResponse(json_data={"Id": jf, "Type": "Episode", "SeriesId": "series1",
                                           "RunTimeTicks": 50 * 60 * nl_fake.TICKS, "UserData": nl_fake.ud(mins, played=played)})
        return super().request(method, url, **kw)


def main():
    test_pure()
    tmp = tempfile.mkdtemp(prefix="nl_together_test_")
    try:
        test_database(tmp)
        run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    test_static()
    if failures:
        print(f"\n{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("\nAll Northern Lights M9a (together) tests passed")


# ---------------------------------------------------------------------------- pure


def test_pure():
    from services import nl_people as np
    from services import nl_together as tg

    print("finished means the end or the last tenth")
    check("ended flag counts", tg.is_finished(10, 6000, True))
    check("90 percent counts", tg.is_finished(5400, 6000) and tg.is_finished(5999, 6000))
    check("just under 90 percent does not", not tg.is_finished(5399, 6000))
    check("no length and no ended flag: not finished", not tg.is_finished(5999, None) and not tg.is_finished(5999, 0))
    check("junk values are not finished", not tg.is_finished("x", 100) and not tg.is_finished(None, None))

    people = np.People(np.parse_people([
        {"id": "labiba", "name": "Labiba", "jellyfin_user_id": LAB, "profile_id": 1},
        {"id": "partner", "name": "Sam", "jellyfin_user_id": PAR, "profile_id": 2},
    ]))
    lab, both = people.viewer_for("labiba"), people.viewer_for("both")
    st = lambda a=0, pa=False, b=0, pb=False: {"labiba": {"position": a, "played": pa}, "partner": {"position": b, "played": pb}}  # noqa: E731

    print("the pact question")
    q = tg.pact_prompt(lab, people.people, st(), True, False)
    check("one person starts a fresh episode of a pact show: asked", q and q["text"] == "You said you'd watch this together. Watch anyway?", q)
    check("... with a note naming the other person from config", q["note"] == "Sam hasn't seen it yet" and q["yes"] == "Watch anyway", q)
    check("pact off: not asked", tg.pact_prompt(lab, people.people, st(), False, False) is None)
    check("Both of us: not asked", tg.pact_prompt(both, people.people, st(), True, False) is None)
    check("the implicit viewer: not asked", tg.pact_prompt(np.IMPLICIT_VIEWER, [], {}, True, False) is None)
    check("one person configured: not asked", tg.pact_prompt(lab, people.people[:1], st(), True, False) is None)
    check("already seen together: not asked", tg.pact_prompt(lab, people.people, st(), True, True) is None)
    check("already part way through: not asked", tg.pact_prompt(lab, people.people, st(a=600), True, False) is None)
    check("already watched by me: not asked", tg.pact_prompt(lab, people.people, st(pa=True), True, False) is None)
    check("the other already watched it alone: nothing to wait for", tg.pact_prompt(lab, people.people, st(pb=True), True, False) is None)
    check("the other is part way: still asked", tg.pact_prompt(lab, people.people, st(b=900), True, False) is not None)
    check("a state Jellyfin could not give counts as not seen", tg.pact_prompt(lab, people.people, {"labiba": {"position": 0, "played": False}}, True, False) is not None)
    three = np.People(np.parse_people([
        {"id": "a", "name": "A", "jellyfin_user_id": LAB}, {"id": "b", "name": "B", "jellyfin_user_id": PAR}, {"id": "c", "name": "C", "jellyfin_user_id": "c" * 32},
    ]))
    q3 = tg.pact_prompt(three.viewer_for("a"), three.people, {}, True, False)
    check("more than two people: the note is left out rather than naming one", q3 and q3["note"] is None, q3)


# ------------------------------------------------------------------------ database


def test_database(tmp):
    from database import DatabaseManager

    print("migration v17")
    fresh = DatabaseManager(os.path.join(tmp, "fresh.db"))
    with fresh.get_connection() as conn:
        seen = {r[1] for r in conn.execute("PRAGMA table_info(nl_seen_together)")}
        pacts = {r[1] for r in conn.execute("PRAGMA table_info(nl_pacts)")}
    check("fresh DB is at schema 17", fresh.get_schema_version() >= 17, fresh.get_schema_version())
    check("the two tables have their columns", {"media_item_id", "source_id", "seen_at"} <= seen and {"media_item_id", "updated_at"} <= pacts, (seen, pacts))

    path = os.path.join(tmp, "old.db")
    old = DatabaseManager(path)
    old.register_source_file("/media/Movies/X (2001)/X.mp4", "X", 2001, "movie")
    old.register_source_file("/media/Shows/Y/S01E01.mkv", "Y", 2000, "tv", season_number=1, episode_number=1)
    movie = [r for r in old.get_library_items("movie")][0]["id"]
    show = [r for r in old.get_library_items("tv")][0]["id"]
    with sqlite3.connect(path) as conn:  # back to how a v16 database looks
        conn.execute("DROP TABLE nl_seen_together")
        conn.execute("DROP TABLE nl_pacts")
        conn.execute("PRAGMA user_version = 16")
    up = DatabaseManager(path)
    with up.get_connection() as conn:
        has = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name IN ('nl_seen_together', 'nl_pacts')").fetchone()[0]
    check("v16 upgrades to 17 and gets both tables", up.get_schema_version() >= 17 and has == 2, (up.get_schema_version(), has))
    check("running again is harmless", DatabaseManager(path).get_schema_version() >= 17)

    print("together rows")
    src = up.get_nl_title(show)["sources"][0]["id"]
    other_src = up.get_nl_title(movie)["sources"][0]["id"]
    check("a movie is marked with source 0", up.mark_seen_together(movie, None) and up.get_seen_together(movie) == {0})
    check("marking twice is one row", up.mark_seen_together(movie, 999) and up.get_seen_together(movie) == {0})
    check("an episode needs one of the show's own files", not up.mark_seen_together(show, None) and not up.mark_seen_together(show, other_src) and up.get_seen_together(show) == set())
    check("an unknown title is refused", not up.mark_seen_together(424242, None))
    check("an episode is marked by source", up.mark_seen_together(show, src) and up.get_seen_together(show) == {src})
    check("counts per title", up.get_together_counts() == {movie: 1, show: 1}, up.get_together_counts())
    up.clear_seen_together(show, src)
    check("clearing drops the mark and refreshes the counts", up.get_seen_together(show) == set() and up.get_together_counts() == {movie: 1}, up.get_together_counts())
    check("no pact to begin with", up.get_pact(show) is False)
    check("a pact switches on and off", up.set_pact(show, True) is True and up.get_pact(show) and up.set_pact(show, False) is False and not up.get_pact(show))
    try:
        up.set_pact(movie, True)
        check("a movie cannot have a pact", False, "accepted")
    except ValueError:
        check("a movie cannot have a pact", True)


# ------------------------------------------------------------------------- app


def run(tmp):
    from services import nl_people as np

    ctx = nl_fake.boot(tmp)
    server, client, ids, db = ctx["server"], ctx["client"], ctx["ids"], ctx["db"]
    fake = TogetherJellyfin()
    server.jellyfin_service.session = fake
    server.jellyfin_playback._ids = {}
    server.jellyfin_playback._last_refresh = None
    alpha, beta, sigma = ids["Alpha"], ids["Beta"], ids["Sigma"]
    s11, s12, s13 = (ids["sources"][EPS[k]] for k in ((1, 1), (1, 2), (1, 3)))
    no_people = server.nl_people
    client.follow_redirects = False

    def stop(who, item, secs, dur=6000, ended=False, source=None, jf="jfA", event="stop"):
        body = {"jellyfin_id": jf, "play_session_id": "p", "media_source_id": "m", "position_seconds": secs, "event": event,
                "media_item_id": item, "duration_seconds": dur, "ended": ended}
        if source:
            body["source_id"] = source
        return client.post("/api/nl/progress", json=body, headers=cookie(who) if who else None)

    print("no people configured: the together features are not there")
    check("a stop at the end records nothing", stop(None, alpha, 5990).status_code == 200 and db.get_together_counts() == {})
    t = client.get(f"/api/nl/title/{alpha}").json()
    check("the movie page says not together", t["together"] is False)
    show = client.get(f"/api/nl/show/{sigma}").json()
    check("the show page offers no pact", show["pact"] == {"available": False, "together": False} and show["together_count"] == 0, show["pact"])
    r = client.put(f"/api/nl/show/{sigma}/pact", json={"together": True})
    check("switching a pact on is refused", r.status_code == 400 and db.get_pact(sigma) is False, (r.status_code, r.text))
    check("pact GET says unavailable", client.get(f"/api/nl/show/{sigma}/pact").json() == {"available": False, "together": False})
    check("no together_prompt in play info", client.get(f"/api/nl/play/{sigma}?source={s13}").json()["together_prompt"] is None)
    check("every library card says together false", all(c["together"] is False for c in client.get("/api/nl/library?type=movie").json()["items"]))

    server.nl_people = np.People(np.parse_people([
        {"id": "labiba", "name": "Labiba", "jellyfin_user_id": LAB, "profile_id": 1},
        {"id": "partner", "name": "Sam", "jellyfin_user_id": PAR, "profile_id": 2},
    ]))

    print("U4 seen together: only Both of us finishing")
    stop("labiba", alpha, 5990)
    check("Labiba alone finishing records nothing", db.get_together_counts() == {})
    stop("both", alpha, 3000)
    check("Both stopping half way records nothing", db.get_together_counts() == {})
    stop("both", alpha, 5990, dur=None)
    check("no length and no ended flag records nothing", db.get_together_counts() == {})
    stop("both", alpha, 5990, event="progress")
    check("only a stop counts, not a progress report", db.get_together_counts() == {})
    r = stop("both", alpha, 5500)
    check("Both stopping in the credits records it", r.status_code == 200 and db.get_seen_together(alpha) == {0}, r.text)
    stop("both", beta, 10, ended=True, jf="jfB")
    check("the ended flag counts even at an odd position", db.get_seen_together(beta) == {0})
    r = client.post("/api/nl/progress", json={"jellyfin_id": "jfA", "event": "stop", "position_seconds": 6000, "media_item_id": 99999, "duration_seconds": 6000}, headers=cookie("both"))
    check("an unknown title is ignored, the report still succeeds", r.status_code == 200 and 99999 not in db.get_together_counts())
    stop("both", sigma, 2990, dur=3000, source=s12, jf="jfS1E2")
    check("an episode is recorded by its file", db.get_seen_together(sigma) == {s12}, db.get_seen_together(sigma))
    stop("both", sigma, 2990, dur=3000, source=ids["sources"]["/media/Movies/Alpha (2020)/Alpha.mp4"], jf="jfS1E2")
    check("a file that is not this show's is refused", db.get_seen_together(sigma) == {s12})
    stop("both", sigma, 2990, dur=3000, jf="jfS1E2")
    check("an episode report without its file is refused", db.get_seen_together(sigma) == {s12})

    print("... and shows up on cards and pages")
    movies = {c["id"]: c for c in client.get("/api/nl/library?type=movie", headers=cookie("labiba")).json()["items"]}
    check("movie cards carry the mark", movies[alpha]["together"] is True and movies[beta]["together"] is True)
    shows = client.get("/api/nl/library?type=tv", headers=cookie("labiba")).json()["items"]
    check("the show card carries it once an episode was seen together", shows[0]["together"] is True, shows)
    t = client.get(f"/api/nl/title/{alpha}", headers=cookie("labiba")).json()
    check("the movie page carries it", t["together"] is True)
    show = client.get(f"/api/nl/show/{sigma}", headers=cookie("labiba")).json()
    eps = {e["source_id"]: e for sn in show["seasons"] for e in sn["episodes"]}
    check("the episode is marked, the others are not", eps[s12]["together"] is True and eps[s11]["together"] is False and eps[s13]["together"] is False)
    check("the count is there", show["together_count"] == 1)
    check("the pact is offered, off", show["pact"] == {"available": True, "together": False})
    check("unwatch choices: one person for Labiba, both names on Both",
          show["unwatch_people"] == [{"id": "labiba", "name": "Labiba"}]
          and client.get(f"/api/nl/show/{sigma}", headers=cookie("both")).json()["unwatch_people"] == [{"id": "labiba", "name": "Labiba"}, {"id": "partner", "name": "Sam"}])

    print("U5 the pact")
    r = client.put(f"/api/nl/show/{sigma}/pact", json={"together": True}, headers=cookie("labiba"))
    check("switching it on works for the pair", r.status_code == 200 and r.json() == {"available": True, "together": True} and db.get_pact(sigma))
    check("... and shows on the show page and the GET", client.get(f"/api/nl/show/{sigma}", headers=cookie("partner")).json()["pact"]["together"] is True
          and client.get(f"/api/nl/show/{sigma}/pact").json()["together"] is True)
    check("a movie cannot have one", client.put(f"/api/nl/show/{alpha}/pact", json={"together": True}).status_code == 404)

    def play(who, source):
        return client.get(f"/api/nl/play/{sigma}?source={source}", headers=cookie(who)).json()

    d = play("labiba", s13)
    check("Labiba starting the next fresh episode alone is asked", d["together_prompt"] and d["together_prompt"]["text"] == "You said you'd watch this together. Watch anyway?", d.get("together_prompt"))
    check("... with the partner's name from config in the note", d["together_prompt"]["note"] == "Sam hasn't seen it yet", d["together_prompt"])
    check("the partner is asked too", play("partner", s13)["together_prompt"] is not None)
    check("Both of us are not asked", play("both", s13)["together_prompt"] is None)
    check("an episode seen together is not asked about", play("labiba", s12)["together_prompt"] is None)
    fake.state[(LAB, "jfS1E3")] = (10, False)
    check("an episode she already started is not asked about", play("labiba", s13)["together_prompt"] is None)
    fake.state[(LAB, "jfS1E3")] = (0, False)
    fake.state[(PAR, "jfS1E3")] = (0, True)
    check("an episode the partner already watched alone is not asked about", play("labiba", s13)["together_prompt"] is None)
    fake.state[(PAR, "jfS1E3")] = (0, False)
    check("asked again once that is gone", play("labiba", s13)["together_prompt"] is not None)
    client.put(f"/api/nl/show/{sigma}/pact", json={"together": False})
    check("pact off: no question", play("labiba", s13)["together_prompt"] is None)
    client.put(f"/api/nl/show/{sigma}/pact", json={"together": True})
    d = client.get(f"/api/nl/play/{alpha}", headers=cookie("labiba")).json()
    check("a movie is never asked about", d["together_prompt"] is None)
    check("play info keeps the key out", SECRET not in str(d))

    print("U6 mark unwatched for one person")
    fake.reports.clear()
    r = client.post(f"/api/nl/unwatch/{sigma}", json={"source_id": s12}, headers=cookie("labiba"))
    check("Labiba resets her own episode", r.status_code == 200 and r.json() == {"success": True, "person": "labiba"}, r.text)
    calls = [(m, p) for m, p, _ in fake.reports]
    check("only her Jellyfin user is touched: PlayedItems DELETE and a zero position", (calls == [("DELETE", f"/Users/{LAB}/PlayedItems/jfS1E2"), ("POST", f"/UserData/{LAB}/jfS1E2")]), calls)
    check("the position is zeroed and Played false", fake.reports[1][2] == {"Played": False, "PlaybackPositionTicks": 0}, fake.reports[1])
    check("the together mark of that episode is gone", db.get_seen_together(sigma) == set())
    fake.reports.clear()
    r = client.post(f"/api/nl/unwatch/{sigma}", json={"source_id": s12}, headers=cookie("both"))
    check("Both of us must say whose", r.status_code == 400 and fake.reports == [], (r.status_code, r.text))
    r = client.post(f"/api/nl/unwatch/{sigma}", json={"source_id": s12, "person": "partner"}, headers=cookie("both"))
    check("Both of us can reset the partner only", r.status_code == 200 and {p.split("/")[2] for _, p, _ in fake.reports} == {PAR}, fake.reports)
    fake.reports.clear()
    r = client.post(f"/api/nl/unwatch/{sigma}", json={"source_id": s12, "person": "partner"}, headers=cookie("labiba"))
    check("one person cannot reset the other's", r.status_code == 403 and fake.reports == [], (r.status_code, r.text))
    r = client.post(f"/api/nl/unwatch/{sigma}", json={"source_id": s12, "person": "stranger"}, headers=cookie("both"))
    check("an unknown person is refused", r.status_code == 400 and fake.reports == [])
    db.mark_seen_together(alpha, None)
    r = client.post(f"/api/nl/unwatch/{alpha}", json={}, headers=cookie("partner"))
    check("a movie works without a source and clears its together mark", r.status_code == 200 and 0 not in db.get_seen_together(alpha) and {p.split("/")[2] for _, p, _ in fake.reports} == {PAR}, (r.text, fake.reports))
    r = client.post(f"/api/nl/unwatch/{sigma}", json={"source_id": 999999}, headers=cookie("labiba"))
    check("an episode that is not this show's is a 404", r.status_code == 404)
    fake.down = True
    r = client.post(f"/api/nl/unwatch/{sigma}", json={"source_id": s12}, headers=cookie("labiba"))
    fake.down = False
    check("Jellyfin away: 503, nothing cleared", r.status_code in (404, 503), r.status_code)
    check("the key stays out of every reply", SECRET not in r.text)
    server.jellyfin_playback._ids = {}
    server.jellyfin_playback._last_refresh = None

    print("no people again: the single implicit viewer can still reset an episode")
    server.nl_people = no_people
    fake.reports.clear()
    fake.calls.clear()
    r = client.post(f"/api/nl/unwatch/{sigma}", json={"source_id": s12})
    check("works for the one viewer", r.status_code == 200 and r.json()["person"] is None, (r.status_code, r.text))
    check("... through the default user", any(p == "/Users/user1/PlayedItems/jfS1E2" for _, p, _ in fake.calls), fake.calls)


# ---------------------------------------------------------------------------- static


def test_static():
    print("pages and scripts")
    base, tvbase = read("templates/nl/base.html"), read("templates/nl/tv/base.html")
    check("both bases load couple.js", "/static/nl/couple.js" in base and "/static/nl/couple.js" in tvbase)
    couple = read("static/nl/couple.js")
    check("couple.js has the pieces", all(w in couple for w in ("togetherBadge", "pactSwitch", "unwatchLabels", "/api/nl/unwatch/", "We watch this together", "Mark unwatched for me")))
    check("couple.js never uses innerHTML", "innerHTML" not in couple)
    player = read("static/nl/player.js")
    check("the player reports which title, how long and whether it ended", all(w in player for w in ("media_item_id", "duration_seconds", "ended: !!S.ended", "together_prompt")))
    show, tv = read("static/nl/show.js"), read("static/nl/tv/tv.js")
    check("web and TV show pages use the pact switch and unwatch", "pactSwitch" in show and "unwatchLabels" in show and "pactSwitch" in tv and "unwatchLabels" in tv)
    check("cards show the mark on web and TV", "togetherBadge" in read("static/nl/nl.js") and "togetherBadge" in tv)
    check("TV Back cancels the unwatch pick", "onBack" in tv and "unwatchMode" in tv)


if __name__ == "__main__":
    main()
