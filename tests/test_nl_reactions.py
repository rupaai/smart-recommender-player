"""
Offline tests for Northern Lights M9c, "reactions and spoilers" (tests/test_nl_reactions.py): W10 "How was it?"
after finishing, mapped onto the existing verdict ratings, and W11 spoiler hiding for unwatched episodes.

    uv run python tests/test_nl_reactions.py
"""

import os
import shutil
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nl_fake  # noqa: E402
from nl_fake import SECRET  # noqa: E402
from test_nl_together import TogetherJellyfin  # noqa: E402

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


def main():
    test_pure()
    tmp = tempfile.mkdtemp(prefix="nl_react_test_")
    try:
        test_database(tmp)
        run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    test_static()
    if failures:
        print(f"\n{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("\nAll Northern Lights M9c (reactions and spoilers) tests passed")


def test_pure():
    from services import nl_reactions as nr
    from services import nl_people as np
    from services.verdicts import VERDICTS

    print("reactions map onto the existing verdicts")
    check("five canonical answers in order", [r["label"] for r in nr.REACTIONS] == [v["label"] for v in VERDICTS.values()])
    check("canonical IDs map to themselves", [nr.verdict_for(i) for i in VERDICTS] == list(VERDICTS))
    check("old player IDs remain compatible", [nr.verdict_for(i) for i in ("fine", "not_for_us")] == ["meh", "not_for_me"])
    check("every answer is a real verdict", all(r["verdict"] in VERDICTS for r in nr.REACTIONS))
    check("unknown answers map to nothing", nr.verdict_for("bogus") is None and nr.verdict_for("") is None and nr.verdict_for(None) is None)
    check("the question names the title", nr.question("Alpha", False) == "How was Alpha?" and nr.question("Alpha", True) == "How was Alpha for the two of you?" and nr.question("", False) == "How was it?")
    check("profiles: the people at the screen", nr.profiles_to_rate([1, 2], False) == [1, 2] and nr.profiles_to_rate([2], False) == [2])
    check("the implicit viewer is profile 1, a person without a profile gets none", nr.profiles_to_rate([], True) == [1] and nr.profiles_to_rate([None], False) == [])
    check("only an own verdict counts as already rated", nr.has_own_verdict({"verdict": "liked", "inferred": False}) and not nr.has_own_verdict({"verdict": "liked", "inferred": True})
          and not nr.has_own_verdict(None) and not nr.has_own_verdict({"verdict": None}))

    print("the spoiler pref is validated")
    check("true and false pass, null clears", np.clean_prefs({"hide_spoilers": True}) == {"hide_spoilers": True} and np.clean_prefs({"hide_spoilers": None}) == {"hide_spoilers": None})
    try:
        np.clean_prefs({"hide_spoilers": "yes"})
        check("a string is refused", False, "accepted")
    except ValueError:
        check("a string is refused", True)
    check("Prefs reads it, off by default", np.Prefs.from_row({"hide_spoilers": 1}).public()["hide_spoilers"] is True and np.Prefs.from_row(None).public()["hide_spoilers"] is False)


def test_database(tmp):
    from database import DatabaseManager

    print("migration v19")
    fresh = DatabaseManager(os.path.join(tmp, "fresh.db"))
    with fresh.get_connection() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(nl_person_prefs)")}
    check("fresh DB is at schema 19 or newer, with the column", fresh.get_schema_version() >= 19 and "hide_spoilers" in cols, (fresh.get_schema_version(), cols))
    path = os.path.join(tmp, "old.db")
    DatabaseManager(path)
    with sqlite3.connect(path) as conn:  # how a v18 database looks: the column does not exist
        conn.execute("ALTER TABLE nl_person_prefs DROP COLUMN hide_spoilers")
        conn.execute("PRAGMA user_version = 18")
    up = DatabaseManager(path)
    cols = {r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(nl_person_prefs)")}
    check("v18 upgrades and gets the column", up.get_schema_version() >= 19 and "hide_spoilers" in cols, (up.get_schema_version(), cols))
    check("running again is harmless", DatabaseManager(path).get_schema_version() >= 19)
    check("nothing saved: no prefs, off", up.get_person_prefs("x") is None)
    up.set_person_prefs("x", {"audio_lang": "eng"})
    check("a row without the setting reads as unset", up.get_person_prefs("x")["hide_spoilers"] is None)
    up.set_person_prefs("x", {"hide_spoilers": True})
    p = up.get_person_prefs("x")
    check("on is kept and the other prefs stay", p["hide_spoilers"] is True and p["audio_lang"] == "eng", p)
    up.set_person_prefs("x", {"hide_spoilers": False})
    check("off is kept", up.get_person_prefs("x")["hide_spoilers"] is False)


def run(tmp):
    from services import nl_people as np
    from services.verdicts import VERDICTS

    ctx = nl_fake.boot(tmp)
    server, client, ids, db = ctx["server"], ctx["client"], ctx["ids"], ctx["db"]
    fake = TogetherJellyfin()
    server.jellyfin_service.session = fake
    server.jellyfin_playback._ids = {}
    server.jellyfin_playback._last_refresh = None
    alpha, beta, sigma = ids["Alpha"], ids["Beta"], ids["Sigma"]
    client.follow_redirects = False

    print("W10 no people configured: the one viewer is profile 1")
    r = client.get(f"/api/nl/react/{alpha}")
    d = r.json()
    check("the question is asked, with the five canonical answers", r.status_code == 200 and d["ask"] is True and d["text"] == "How was Alpha?"
          and [o["label"] for o in d["options"]] == [v["label"] for v in VERDICTS.values()], d)
    r = client.post(f"/api/nl/react/{alpha}", json={"reaction": "loved"})
    check("an answer is saved as the 'loved' verdict", r.status_code == 200 and r.json() == {"success": True, "verdict": "loved", "profiles": 1}, r.text)
    det = db.get_rating_details(alpha, 1)
    check("it is the ordinary rating: verdict, legacy number, taste score", det["verdict"] == "loved" and det["inferred"] is False and det["taste_score"] == 0.6, det)
    with db.get_connection() as conn:
        wh = conn.execute("SELECT user_rating, status FROM watch_history WHERE media_item_id = ? AND profile_id = 1", (alpha,)).fetchone()
    check("... and the seen row with the legacy 1-10 number", wh and wh["status"] == "seen" and wh["user_rating"] == 9.0, dict(wh) if wh else None)
    check("a verdict they already gave is not asked about again", client.get(f"/api/nl/react/{alpha}").json()["ask"] is False)
    check("another title is still asked", client.get(f"/api/nl/react/{beta}").json()["ask"] is True)
    r = client.post(f"/api/nl/react/{alpha}", json={"reaction": "fine"})
    check("a later answer replaces it (Fine is the existing 'meh')", db.get_rating_details(alpha, 1)["verdict"] == "meh" and r.status_code == 200)
    r = client.post(f"/api/nl/react/{alpha}", json={"reaction": "not_for_us"})
    check("Not for us is 'not_for_me'", db.get_rating_details(alpha, 1)["verdict"] == "not_for_me")
    client.post(f"/api/nl/react/{alpha}", json={"reaction": "liked"})
    check("Liked it is 'liked'", db.get_rating_details(alpha, 1)["verdict"] == "liked")
    check("an unknown answer is a 400", client.post(f"/api/nl/react/{alpha}", json={"reaction": "bogus"}).status_code == 400)
    check("an unknown title is a 404 and asks nothing", client.post("/api/nl/react/99999", json={"reaction": "liked"}).status_code == 404
          and client.get("/api/nl/react/99999").json()["ask"] is False)
    check("the key is never in a reply", SECRET not in client.get(f"/api/nl/react/{beta}").text)

    server.nl_people = np.People(np.parse_people([
        {"id": "labiba", "name": "Labiba", "jellyfin_user_id": LAB, "profile_id": 1},
        {"id": "partner", "name": "Sam", "jellyfin_user_id": PAR, "profile_id": 2},
    ]))
    print("W10 two people: the answer goes to the profile(s) at the screen")
    r = client.post(f"/api/nl/react/{beta}", json={"reaction": "loved"}, headers=cookie("both"))
    check("Both of us: one answer, both profiles", r.json()["profiles"] == 2 and db.get_rating_details(beta, 1)["verdict"] == "loved" and db.get_rating_details(beta, 2)["verdict"] == "loved", r.text)
    check("... and the question reads for the two of you", client.get(f"/api/nl/react/{sigma}", headers=cookie("both")).json()["text"] == "How was Sigma for the two of you?")
    r = client.post(f"/api/nl/react/{sigma}", json={"reaction": "not_for_us"}, headers=cookie("partner"))
    check("Sam alone rates only his own profile", r.json()["profiles"] == 1 and db.get_rating_details(sigma, 2)["verdict"] == "not_for_me" and db.get_rating_details(sigma, 1) is None, r.text)
    check("Labiba is still asked about it, Sam is not", client.get(f"/api/nl/react/{sigma}", headers=cookie("labiba")).json()["ask"] is True
          and client.get(f"/api/nl/react/{sigma}", headers=cookie("partner")).json()["ask"] is False)
    check("Both of us are asked while either has no verdict", client.get(f"/api/nl/react/{sigma}", headers=cookie("both")).json()["ask"] is True)
    client.post(f"/api/nl/react/{sigma}", json={"reaction": "liked"}, headers=cookie("labiba"))
    check("... and not once both have one", client.get(f"/api/nl/react/{sigma}", headers=cookie("both")).json()["ask"] is False)
    db.rate_item(alpha, profile_id=2, user_rating=8.0, status="seen")
    check("a bare star rating (no verdict) is not a verdict, so the question is still asked", client.get(f"/api/nl/react/{alpha}", headers=cookie("partner")).json()["ask"] is True)
    server.nl_people = np.People(np.parse_people([{"id": "solo", "name": "Solo", "jellyfin_user_id": LAB}]))
    check("a person with no profile is not asked and cannot answer", client.get(f"/api/nl/react/{beta}", headers=cookie("solo")).json()["ask"] is False
          and client.post(f"/api/nl/react/{beta}", json={"reaction": "liked"}, headers=cookie("solo")).status_code == 400)
    server.nl_people = np.People(np.parse_people([
        {"id": "labiba", "name": "Labiba", "jellyfin_user_id": LAB, "profile_id": 1},
        {"id": "partner", "name": "Sam", "jellyfin_user_id": PAR, "profile_id": 2},
    ]))

    print("W11 spoiler hiding is a per-viewer setting")
    show = client.get(f"/api/nl/show/{sigma}", headers=cookie("labiba")).json()
    check("off by default, in the show payload", show["hide_spoilers"] is False)
    check("the prefs say so too", client.get("/api/nl/prefs", headers=cookie("labiba")).json()["prefs"]["hide_spoilers"] is False)
    r = client.put("/api/nl/prefs", json={"hide_spoilers": True}, headers=cookie("labiba"))
    check("Labiba turns it on", r.status_code == 200 and r.json()["prefs"]["hide_spoilers"] is True, r.text)
    check("her show page carries it, Sam's does not", client.get(f"/api/nl/show/{sigma}", headers=cookie("labiba")).json()["hide_spoilers"] is True
          and client.get(f"/api/nl/show/{sigma}", headers=cookie("partner")).json()["hide_spoilers"] is False)
    check("Both of us follow the first person's choice", client.get(f"/api/nl/show/{sigma}", headers=cookie("both")).json()["hide_spoilers"] is True)
    client.put("/api/nl/prefs", json={"hide_spoilers": False}, headers=cookie("both"))
    check("... unless Both of us set their own", client.get(f"/api/nl/show/{sigma}", headers=cookie("both")).json()["hide_spoilers"] is False
          and client.get(f"/api/nl/show/{sigma}", headers=cookie("labiba")).json()["hide_spoilers"] is True)
    r = client.put("/api/nl/prefs", json={"hide_spoilers": "yes"}, headers=cookie("labiba"))
    check("a bad value is a 400", r.status_code == 400)
    r = client.put("/api/nl/prefs", json={"audio_lang": "fra"}, headers=cookie("labiba"))
    check("saving another pref leaves it on", r.json()["prefs"]["hide_spoilers"] is True and r.json()["prefs"]["audio_lang"] == "fra")
    nopeople = server.nl_people
    server.nl_people = np.People([])
    client.put("/api/nl/prefs", json={"hide_spoilers": True})
    check("it works with no people configured", client.get(f"/api/nl/show/{sigma}").json()["hide_spoilers"] is True)
    server.nl_people = nopeople

    print("pages")
    check("the player has the question screen", 'id="nl-react"' in client.get(f"/nl/play/{alpha}", headers=cookie("labiba")).text)
    check("the web me page has the switch", "nl-pref-spoilers" in client.get("/nl/me", headers=cookie("labiba")).text)
    check("the TV me page has the switch", "tv-pref-spoilers" in client.get("/nl/tv/me", headers=cookie("labiba")).text)


def test_static():
    print("scripts and styles")
    player = read("static/nl/player.js")
    check("the player asks only when nothing follows, through the react endpoint", "askReaction" in player and "/api/nl/react/" in player and "!S.next" in player)
    check("Back and Escape close the question", "reactOpen()) { closeReaction" in player)
    body = player[player.index("function askReaction"):player.index("nl-react-skip\").addEventListener")]
    check("the question's code never uses innerHTML", "innerHTML" not in body)
    show = read("static/nl/show.js")
    check("web show page blurs only unstarted episodes", "spoilerHidden" in show and "!e.played && !e.progress" in show and "is-spoiler" in show)
    tv = read("static/nl/tv/tv.js")
    check("TV show page too, and a switch on the me page", "spoilerHidden" in tv and "is-spoiler" in tv and "hide_spoilers" in tv)
    check("the blur is in both style sheets", "is-spoiler" in read("static/nl/nl.css") and "is-spoiler" in read("static/nl/tv/tv.css"))
    check("the prefs script knows the setting", "hide_spoilers" in read("static/nl/prefs.js") and "hide_spoilers" in read("static/nl/me.js"))


if __name__ == "__main__":
    main()
