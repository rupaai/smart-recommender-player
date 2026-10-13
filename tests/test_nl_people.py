"""
Offline tests for Northern Lights M4, "Who's watching" (tests/test_nl_people.py)

Jellyfin is faked (a stand-in requests session, see nl_fake.py); nothing leaves the process.

    uv run python tests/test_nl_people.py
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nl_fake  # noqa: E402
from nl_fake import ALPHA, EPS, SECRET, TICKS, FakeResponse  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
failures = []
LAB = "a" * 32       # Labiba's Jellyfin user
PAR = "b" * 32       # the partner's Jellyfin user


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} — got: {detail}")
        failures.append(label)


def main():
    test_pure()
    tmp = tempfile.mkdtemp(prefix="nl_people_test_")
    try:
        run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    test_static()
    test_js_behaviour()
    if failures:
        print(f"\n{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("\nAll Northern Lights who's-watching tests passed")


# ---------------------------------------------------------------------------- pure


def test_pure():
    from services import nl_people as np

    print("people config")
    people = np.parse_people([
        {"id": "Labiba", "name": "Labiba", "jellyfin_user_id": "labiba", "profile_id": 1},
        {"id": "partner", "name": "Sam", "jellyfin_user_id": LAB, "profile_id": "2"},
    ])
    check("ids are lowercased, profile ids numbers", [p.id for p in people] == ["labiba", "partner"] and people[1].profile_id == 2, people)
    check("nothing configured is an empty list", np.parse_people(None) == [] and np.parse_people([]) == [])
    for label, bad in [
        ("not a list", {"id": "x"}),
        ("not a mapping", ["x"]),
        ("bad id", [{"id": "A B", "name": "A", "jellyfin_user_id": "u"}]),
        ("reserved id both", [{"id": "both", "name": "A", "jellyfin_user_id": "u"}]),
        ("duplicate id", [{"id": "a", "name": "A", "jellyfin_user_id": "u"}, {"id": "a", "name": "B", "jellyfin_user_id": "v"}]),
        ("no name", [{"id": "a", "jellyfin_user_id": "u"}]),
        ("no jellyfin user", [{"id": "a", "name": "A"}]),
        ("bad profile id", [{"id": "a", "name": "A", "jellyfin_user_id": "u", "profile_id": "x"}]),
    ]:
        try:
            np.parse_people(bad)
            check(f"{label} is refused", False, "accepted")
        except np.PeopleConfigError as e:
            check(f"{label} is refused", True, str(e))
    cfg = {"northern_lights": {"people": [{"id": "a", "name": "A", "jellyfin_user_id": "u"}]}}
    check("people come from config.yaml", np.People.from_config(cfg).configured)
    env = '[{"id": "x", "name": "X", "jellyfin_user_id": "u"}, {"id": "y", "name": "Y", "jellyfin_user_id": "v"}]'
    check("NL_PEOPLE wins over config.yaml", [p.id for p in np.People.from_config(cfg, env).people] == ["x", "y"])
    check("no config, no env: nobody", not np.People.from_config({}, None).configured and not np.People.from_config(None, "  ").configured)
    try:
        np.People.from_config(cfg, "{oops")
        check("bad NL_PEOPLE JSON is refused", False)
    except np.PeopleConfigError:
        check("bad NL_PEOPLE JSON is refused", True)

    print("viewer cookie")
    two = np.People(np.parse_people([
        {"id": "labiba", "name": "Labiba", "jellyfin_user_id": LAB, "profile_id": 1},
        {"id": "partner", "name": "Sam", "jellyfin_user_id": PAR, "profile_id": 2},
    ]))
    check("cookie_value finds nl_viewer among others", np.cookie_value("a=1; nl_viewer=both; b=2") == "both")
    check("cookie_value: absent / empty / junk", np.cookie_value(None) is None and np.cookie_value("") is None and np.cookie_value("nl_viewerx=1; ;;=") is None)
    check("cookie_value: quotes stripped, last duplicate wins", np.cookie_value('nl_viewer="labiba"') == "labiba" and np.cookie_value("nl_viewer=a; nl_viewer=b") == "b")
    v = two.parse_cookie("x=1; nl_viewer=labiba")
    check("a person", v and v.key == "labiba" and v.label == "Labiba" and not v.is_both and v.jellyfin_users == [LAB] and v.profile_ids == [1], v)
    v = two.parse_cookie("nl_viewer=PARTNER")
    check("case does not matter", v and v.key == "partner" and v.label == "Sam")
    v = two.parse_cookie("nl_viewer=both")
    check("both: two users, two profiles, the label from the screen", v and v.is_both and v.jellyfin_users == [LAB, PAR] and v.profile_ids == [1, 2] and v.label == "Both of us" and v.primary.id == "labiba", v)
    for junk in (None, "", "nl_viewer=", "nl_viewer=stranger", "nl_viewer=default", "nl_viewer=labiba/../x", "nl_viewer=%00", "nl_viewer=" + "x" * 500):
        check(f"cookie {junk!r} is 'not chosen'", two.parse_cookie(junk) is None, two.parse_cookie(junk))
    one = np.People(two.people[:1])
    check("'both' needs two people", one.viewer_for("both") is None and one.viewer_for("labiba") is not None)
    check("choices: each person, then Both", [c["key"] for c in two.choices()] == ["labiba", "partner", "both"] and two.choices()[2]["label"] == "Both of us")
    check("one person has no Both choice", [c["key"] for c in one.choices()] == ["labiba"])
    nobody = np.People()
    check("nobody configured: the implicit viewer, whatever the cookie says",
          nobody.parse_cookie("nl_viewer=labiba").implicit and nobody.parse_cookie(None).jellyfin_users == [None] and nobody.parse_cookie(None).profile_ids == [])

    print("where to go after choosing")
    check("paths in /nl/ pass", np.safe_next("/nl/movies?x=1", "/nl/") == "/nl/movies?x=1" and np.safe_next("/nl/tv/", "/z") == "/nl/tv/")
    for bad in ("//evil.com/nl/", "https://evil.com", "/other", "/nl/\\evil", "/nl/\n", "", None, "javascript:alert(1)", "/nl/x://y"):
        check(f"next {bad!r} falls back", np.safe_next(bad, "/nl/") == "/nl/", np.safe_next(bad, "/nl/"))

    print("resume rule")
    lab, par = two.people
    solo = two.viewer_for("labiba")
    both = two.viewer_for("both")

    def st(a, b, pa=False, pb=False):
        return {"labiba": {"position": a, "played": pa}, "partner": {"position": b, "played": pb}}

    r = np.resume_summary(both, st(42 * 60 + 10, 38 * 60), two.people)
    check("Both: the further position wins", r["resume_seconds"] == 42 * 60 + 10 and r["played"] is False, r)
    check("Both: a note names both when they differ by more than 2 min", r["note"] == "Labiba is at 42:10, Sam is at 38:00", r["note"])
    r = np.resume_summary(both, st(42 * 60, 40 * 60 + 30), two.people)
    check("Both: 1.5 min apart is the same place, no note", r["note"] is None and r["resume_seconds"] == 42 * 60, r)
    r = np.resume_summary(both, st(42 * 60, 40 * 60), two.people)
    check("Both: exactly 2 min apart is still the same place", r["note"] is None, r)
    r = np.resume_summary(both, st(42 * 60, 40 * 60 - 1), two.people)
    check("Both: just over 2 min apart gets a note", r["note"] is not None, r)
    r = np.resume_summary(both, st(0, 38 * 60), two.people)
    check("Both: one has not started: still resume at the other's place", r["resume_seconds"] == 38 * 60 and r["note"] is not None, r)
    r = np.resume_summary(both, st(0, 0), two.people)
    check("Both: nobody started", r["resume_seconds"] == 0 and r["note"] is None)
    r = np.resume_summary(both, st(0, 900, pa=True), two.people)
    check("Both: one finished, the other part way: resume the one part way", r["resume_seconds"] == 900 and r["played"] is False, r)
    r = np.resume_summary(both, st(0, 0, True, True), two.people)
    check("Both: both finished = played", r["played"] is True and r["resume_seconds"] == 0)
    r = np.resume_summary(solo, st(38 * 60, 42 * 60 + 10), two.people)
    check("One person: resumes their own place", r["resume_seconds"] == 38 * 60, r)
    check("One person: told where the other is", r["note"] == "You're at 38:00, Sam is at 42:10", r["note"])
    r = np.resume_summary(solo, st(10 * 60, 11 * 60), two.people)
    check("One person: close positions, no note", r["note"] is None)
    r = np.resume_summary(solo, st(0, 42 * 60), two.people)
    check("One person: not started, the other is on: 'at the start'", r["resume_seconds"] == 0 and r["note"] == "You're at the start, Sam is at 42:00", r)
    r = np.resume_summary(solo, st(38 * 60, 0, pb=True), two.people)
    check("One person: the other finished it, no note", r["note"] is None)
    r = np.resume_summary(solo, {"labiba": {"position": 600, "played": False}}, two.people)
    check("One person: other unknown (Jellyfin said nothing) is no problem", r["resume_seconds"] == 600 and r["note"] is None)
    check("hours are shown as h:mm:ss", np.fmt_clock(3725) == "1:02:05" and np.fmt_clock(59) == "0:59")

    print("language prefs")
    check("clean_prefs keeps valid fields", np.clean_prefs({"audio_lang": "jpn", "subtitle_lang": "eng", "subtitles_on": True,
          "sub_style": {"size": "L", "background": "box", "raise": 2}, "search_lang": "fra", "junk": 1}) ==
          {"audio_lang": "jpn", "subtitle_lang": "eng", "subtitles_on": True, "sub_style": {"size": "L", "background": "box", "raise": 2}, "search_lang": "fra"})
    check("empty or null clears a language", np.clean_prefs({"audio_lang": "", "subtitle_lang": None}) == {"audio_lang": None, "subtitle_lang": None})
    for bad in ({"audio_lang": "english"}, {"subtitle_lang": "EN"}, {"search_lang": 5}, {"subtitles_on": "yes"},
                {"sub_style": {"size": "XXL", "background": "box", "raise": 0}}, {"sub_style": {"size": "S", "background": "box", "raise": True}},
                {"sub_style": "big"}):
        try:
            np.clean_prefs(bad)
            check(f"{bad} is refused", False, "accepted")
        except ValueError:
            check(f"{bad} is refused", True)
    check("B and T codes are the same language", np.lang_key("fre") == np.lang_key("fra") == "fra" and np.lang_key("ger") == "deu" and np.lang_key("xx") == "" and np.lang_key(None) == "")
    audio = [
        {"index": 1, "language": "eng", "label": "English · 5.1 · AC3", "is_default": True, "channels": 6},
        {"index": 2, "language": "jpn", "label": "Japanese · Stereo · AAC", "is_default": False, "channels": 2},
        {"index": 3, "language": "jpn", "label": "Japanese · Stereo · AAC (Commentary)", "is_default": False, "channels": 2},
    ]
    check("audio: the preferred language", np.pick_audio_index(audio, "jpn") == 2)
    check("audio: never a commentary", np.pick_audio_index(audio[2:], "jpn") is None)
    check("audio: language not on the file or unset", np.pick_audio_index(audio, "deu") is None and np.pick_audio_index(audio, None) is None)
    subs = [
        {"index": 4, "language": "eng", "kind": "text", "is_forced": True},
        {"index": 5, "language": "eng", "kind": "image", "is_forced": False},
        {"index": 6, "language": "eng", "kind": "text", "is_hearing_impaired": True},
        {"index": 7, "language": "eng", "kind": "text"},
        {"index": 8, "language": "fre", "kind": "text"},
    ]
    check("subtitles: full text before forced, pictures and SDH", np.pick_subtitle_track(subs, "eng")["index"] == 7)
    check("subtitles: a picture track when it is all there is", np.pick_subtitle_track([subs[1], subs[0]], "eng")["index"] == 5)
    check("subtitles: B/T synonyms match", np.pick_subtitle_track(subs, "fra")["index"] == 8)
    check("subtitles: none in that language", np.pick_subtitle_track(subs, "deu") is None)


# ----------------------------------------------------------------- fake Jellyfin


class PeopleJellyfin(nl_fake.FakeJellyfin):
    """FakeJellyfin plus two users with their own progress and playing reports."""

    def __init__(self):
        super().__init__()
        self.minutes = {LAB: {"jfA": 42, "jfS1E2": 25}, PAR: {"jfA": 38}}
        self.resume_for = {
            LAB: [self._item("jfA", ALPHA, 40, "Movie", "Alpha", 100)],
            PAR: [self._item("jfA", ALPHA, 45, "Movie", "Alpha", 100), self._item("jfS1E2", EPS[(1, 2)], 25, "Episode", "Second", 50)],
        }
        self.reports = []        # (method, path, params|json) for UserData writes (/UserData/<uid>/<item>) and PlayedItems
        self.session_reports = []
        self.fail_users = set()
        self.playback_bodies = []

    @staticmethod
    def _item(jf, path, minutes, kind, name, run):
        return {"Id": jf, "Path": path, "Type": kind, "Name": name, "RunTimeTicks": run * 60 * TICKS,
                "UserData": nl_fake.ud(minutes), "ImageTags": {"Primary": "t"}}

    def request(self, method, url, **kw):
        path = url.split("jellyfin.test:8096")[1] if "jellyfin.test:8096" in url else url
        params = kw.get("params") or {}
        if self.down:
            return super().request(method, url, **kw)
        mu = re.match(r"^/UserItems/(\w+)/UserData$", path)
        if mu and method == "POST":
            uid = params.get("userId")
            self.reports.append((method, f"/UserData/{uid}/{mu.group(1)}", dict(kw.get("json") or {})))
            if uid in self.fail_users:
                return FakeResponse(500, json_data={})
            return FakeResponse(json_data={})
        m = re.match(r"^/Users/([0-9a-f]{32})/(.*)$", path)
        if m:
            uid, rest = m.group(1), m.group(2)
            if rest.startswith("PlayingItems/") or rest.startswith("PlayedItems/"):
                self.reports.append((method, path, dict(params)))
                if uid in self.fail_users:
                    return FakeResponse(500, json_data={})
                return FakeResponse(204)
            if rest == "Items/Resume":
                return FakeResponse(json_data={"Items": self.resume_for.get(uid, [])})
            mm = re.match(r"^Items/(\w+)$", rest)
            if mm and method == "GET" and mm.group(1) in ("jfA", "jfS1E2"):
                jf = mm.group(1)
                mins = self.minutes.get(uid, {}).get(jf, 0)
                return FakeResponse(json_data={"Id": jf, "Type": "Movie" if jf == "jfA" else "Episode", "RunTimeTicks": 100 * 60 * TICKS,
                                               "UserData": nl_fake.ud(mins), "SeriesId": "series1"})
        if path.startswith("/Users/user1/PlayedItems/"):
            self.calls.append((method, path, params))
            return FakeResponse(204)
        if path.startswith("/Sessions/Playing"):
            self.session_reports.append((method, path, kw.get("json")))
            return FakeResponse(204)
        if path.endswith("/PlaybackInfo") and method == "POST":
            return self._playback_info(path, kw)
        return super().request(method, url, **kw)

    STREAMS = [
        {"Type": "Video", "Index": 0, "Codec": "h264"},
        {"Type": "Audio", "Index": 1, "Language": "eng", "Codec": "ac3", "Channels": 6, "IsDefault": True},
        {"Type": "Audio", "Index": 2, "Language": "jpn", "Codec": "aac", "Channels": 2},
        {"Type": "Subtitle", "Index": 3, "Language": "eng", "Codec": "subrip", "IsTextSubtitleStream": True},
        {"Type": "Subtitle", "Index": 4, "Language": "fra", "Codec": "subrip", "IsTextSubtitleStream": True},
        {"Type": "Subtitle", "Index": 5, "Language": "spa", "Codec": "pgssub", "IsTextSubtitleStream": False},
    ]

    def _playback_info(self, path, kw):
        jf = path.split("/")[2]
        body = kw.get("json") or {}
        self.playback_bodies.append({**body, "_params": dict(kw.get("params") or {})})
        ms = {"Id": "ms-" + jf, "Container": "mkv", "RunTimeTicks": 100 * 60 * TICKS, "DefaultAudioStreamIndex": 1,
              "SupportsDirectPlay": bool(body.get("EnableDirectPlay", True)), "SupportsDirectStream": bool(body.get("EnableDirectStream", True)),
              "MediaStreams": [dict(s) for s in self.STREAMS]}
        if not ms["SupportsDirectPlay"]:
            ms["TranscodingUrl"] = f"/videos/{jf}/master.m3u8?MediaSourceId=ms-{jf}&api_key={SECRET}"
        return FakeResponse(json_data={"PlaySessionId": "ps-" + jf, "MediaSources": [ms]})


def cookie(value):
    return {"Cookie": f"nl_viewer={value}"}


# ------------------------------------------------------------------------- app


def run(tmp):
    ctx = nl_fake.boot(tmp)
    server, client, ids, db = ctx["server"], ctx["client"], ctx["ids"], ctx["db"]
    fake = PeopleJellyfin()
    server.jellyfin_service.session = fake
    server.jellyfin_playback._ids = {}
    server.jellyfin_playback._last_refresh = None
    alpha = ids["Alpha"]
    client.follow_redirects = False

    from services import nl_people as np

    no_people = server.nl_people

    # ------------------------------------------------------------------ no people configured
    print("no people configured: the app works exactly as before")
    check("nobody is configured by default", not no_people.configured)
    r = client.get("/api/nl/viewer")
    check("viewer info: not configured, implicit viewer, no profile link",
          r.status_code == 200 and r.json()["configured"] is False and r.json()["choices"] == [] and r.json()["profile_ids"] == []
          and r.json()["viewer"]["implicit"] is True, r.text)
    for page in ("/nl/", "/nl/movies", "/nl/shows", f"/nl/title/{alpha}", f"/nl/play/{alpha}", "/nl/tv/", "/nl/tv/search", "/nl/me"):
        r = client.get(page)
        check(f"{page} opens without asking", r.status_code == 200, (r.status_code, r.headers.get("location")))
    page = client.get("/nl/").text
    check("no viewer chip, no tab", "nl-viewer" not in page and "/nl/me" not in page)
    check("/nl/who goes straight home", client.get("/nl/who").status_code == 302 and client.get("/nl/who").headers["location"] == "/nl/")
    check("/nl/tv/who goes straight to the TV home", client.get("/nl/tv/who").headers["location"] == "/nl/tv/")
    check("choosing is not possible", client.get("/nl/who/labiba").headers["location"].startswith("/nl/who?"))
    check("home still has its Continue Watching", len(client.get("/api/nl/home").json()["continue_watching"]) >= 1)
    fake.session_reports.clear()
    fake.reports.clear()
    r = client.post("/api/nl/progress", json={"jellyfin_id": "jfA", "play_session_id": "p", "media_source_id": "m",
                                                "position_seconds": 61.5, "event": "progress"})
    check("progress goes through the session endpoint as before", r.status_code == 200 and len(fake.session_reports) == 1
          and fake.session_reports[0][1] == "/Sessions/Playing/Progress" and fake.reports == [], (r.text, fake.session_reports, fake.reports))
    r = client.post("/api/nl/played/jfA")
    check("mark played uses JELLYFIN_USER_ID", r.status_code == 200 and fake.calls[-1][1] == "/Users/user1/PlayedItems/jfA", fake.calls[-1])
    r = client.get(f"/api/nl/play/{alpha}")
    check("play: no note, no default subtitle, resume from the one user",
          r.status_code == 200 and r.json().get("resume_note") is None and r.json()["default_subtitle_index"] == -1 and r.json()["resume_seconds"] == 40 * 60, r.text[:300])
    prefs = client.get("/api/nl/prefs").json()
    check("prefs API works for the implicit viewer", prefs["viewer"]["key"] == "default" and prefs["prefs"]["audio_lang"] is None and prefs["saved"] is False, prefs)
    client.put("/api/nl/prefs", json={"search_lang": "deu"})
    check("... and are saved under 'default'", db.get_person_prefs("default")["search_lang"] == "deu")
    r = client.post("/api/watch/start", json={"media_item_id": alpha})
    sid = r.json()["session_id"]
    r = client.post("/api/watch/finish", json={"session_id": sid, "user_rating": 7})
    with db.get_connection() as conn:
        rows = conn.execute("SELECT profile_id FROM watch_history WHERE media_item_id = ?", (alpha,)).fetchall()
    check("watch/finish still logs the legacy row (profile 1)", r.status_code == 200 and [x["profile_id"] for x in rows] == [1], (r.text, [dict(x) for x in rows]))
    with db.get_connection() as conn:
        conn.execute("DELETE FROM watch_history WHERE media_item_id = ?", (alpha,))
    db.set_person_prefs("default", {"search_lang": None})

    # ------------------------------------------------------------------ people configured
    server.nl_people = np.People(np.parse_people([
        {"id": "labiba", "name": "Labiba", "jellyfin_user_id": LAB, "profile_id": 1},
        {"id": "partner", "name": "Sam", "jellyfin_user_id": PAR, "profile_id": 2},
    ]))

    print("who's watching screen (web)")
    r = client.get("/nl/")
    check("home without a choice goes to the screen, then back", r.status_code == 302 and r.headers["location"] == "/nl/who?next=%2Fnl%2F", (r.status_code, r.headers.get("location")))
    r = client.get(f"/nl/play/{alpha}?resume=1")
    check("so does the player, keeping the link", r.status_code == 302 and r.headers["location"] == "/nl/who?next=" + "%2Fnl%2Fplay%2F" + f"{alpha}%3Fresume%3D1", r.headers.get("location"))
    r = client.get("/nl/tv/search")
    check("TV pages go to the TV screen", r.status_code == 302 and r.headers["location"].startswith("/nl/tv/who?next="), r.headers.get("location"))
    r = client.get(f"/nl/play/{alpha}?tv=1")
    check("the player in TV mode goes to the TV screen", r.headers["location"].startswith("/nl/tv/who?next="), r.headers.get("location"))
    r = client.get("/nl/", headers=cookie("stranger"))
    check("an unknown cookie value counts as not chosen", r.status_code == 302 and "/nl/who" in r.headers["location"])
    r = client.get("/nl/", headers=cookie("both"))
    check("a valid cookie opens the page", r.status_code == 200 and "Both of us" in r.text, r.status_code)
    r = client.get("/nl/who?next=/nl/movies")
    page = r.text
    check("the screen lists Labiba, the partner from config and Both of us", r.status_code == 200 and "Labiba" in page and "Sam" in page and "Both of us" in page, page[:200])
    check("choices link to the choose route and keep next", "/nl/who/labiba?next=/nl/movies" in page and "/nl/who/both?next=" in page)
    check("no Jellyfin ids or key on the screen", LAB not in page and PAR not in page and SECRET not in page)
    r = client.get("/nl/who/partner?next=/nl/movies")
    sc = r.headers.get("set-cookie", "")
    check("choosing sets the cookie and goes on", r.status_code == 302 and r.headers["location"] == "/nl/movies" and "nl_viewer=partner" in sc and "Max-Age=31536000" in sc and "Path=/" in sc, (r.headers.get("location"), sc))
    check("the cookie is not readable by scripts", "httponly" in sc.lower(), sc)
    r = client.get("/nl/who/both?next=//evil.example/x")
    check("an outside next is ignored", r.headers["location"] == "/nl/", r.headers["location"])
    r = client.get("/nl/who/stranger")
    check("an unknown choice sets nothing", r.status_code == 302 and "set-cookie" not in r.headers and "/nl/who" in r.headers["location"], dict(r.headers))
    r = client.get("/nl/movies", headers=cookie("labiba"))
    check("the page shows who is watching (chip and tab) with a link to switch", r.status_code == 200 and 'class="nl-viewer"' in r.text and "/nl/me" in r.text and ">Labiba<" in r.text)
    r = client.get("/nl/me", headers=cookie("partner"))
    check("the me page names them and offers to switch", r.status_code == 200 and "Sam" in r.text and "/nl/who?next=" in r.text and "nl-pref-audio" in r.text)

    print("who's watching screen (TV)")
    r = client.get("/nl/tv/who")
    page = r.text
    check("focusable cards in one row", r.status_code == 200 and page.count("data-nl-focus") >= 3 and 'data-nl-row="who"' in page and "Both of us" in page, page[:300])
    check("TV links choose under /nl/tv/who and return to the TV home", "/nl/tv/who/labiba?next=/nl/tv/" in page)
    check("no nav pills on that screen", "tv-nav" not in page)
    r = client.get("/nl/tv/who/both?next=/nl/tv/")
    check("choosing on the TV sets the same cookie", r.status_code == 302 and r.headers["location"] == "/nl/tv/" and "nl_viewer=both" in r.headers.get("set-cookie", ""))
    r = client.get("/nl/tv/", headers=cookie("labiba"))
    check("the TV bar has the viewer pill", r.status_code == 200 and "/nl/tv/me" in r.text and ">Labiba<" in r.text)
    r = client.get("/nl/tv/me", headers=cookie("labiba"))
    check("the TV me page has D-pad buttons for the prefs", r.status_code == 200 and "tv-pref-audio" in r.text and "tv-pref-sub" in r.text and "tv-pref-on" in r.text)
    info = client.get("/api/nl/viewer", headers=cookie("both")).json()
    check("viewer API: choices, viewer, profile ids; no Jellyfin ids", info["configured"] and info["viewer"]["key"] == "both" and info["profile_ids"] == [1, 2]
          and LAB not in str(info) and PAR not in str(info), info)
    client.cookies.clear()
    check("viewer API: not chosen yet is null", client.get("/api/nl/viewer").json()["viewer"] is None)

    client.cookies.clear()   # the test client keeps the cookies the choose routes set
    print("progress goes to each person's own Jellyfin user")
    body = {"jellyfin_id": "jfA", "play_session_id": "ps1", "media_source_id": "ms1", "position_seconds": 61.5, "event": "progress", "paused": True}
    fake.reports.clear()
    fake.session_reports.clear()
    r = client.post("/api/nl/progress", json=body, headers=cookie("labiba"))
    UD = lambda uid: f"/UserData/{uid}/jfA"  # noqa: E731
    check("Labiba: her position saved to her user, plus the one session report", r.status_code == 200 and [p for _, p, _ in fake.reports] == [UD(LAB)]
          and [p for _, p, _ in fake.session_reports] == ["/Sessions/Playing/Progress"], (fake.reports, fake.session_reports))
    prm = fake.reports[0][2]
    check("... with the position in ticks and a played date", prm["PlaybackPositionTicks"] == 615000000 and prm["LastPlayedDate"].endswith("Z"), prm)
    fake.reports.clear()
    r = client.post("/api/nl/progress", json=body, headers=cookie("partner"))
    check("the partner: saved to their user only", [p for _, p, _ in fake.reports] == [UD(PAR)], fake.reports)
    fake.reports.clear()
    r = client.post("/api/nl/progress", json=body, headers=cookie("both"))
    check("Both of us: the same position saved for both users", r.status_code == 200 and sorted(p for _, p, _ in fake.reports) ==
          sorted([UD(LAB), UD(PAR)]), fake.reports)
    check("... with the same position for both", {x[2]["PlaybackPositionTicks"] for x in fake.reports} == {615000000})
    fake.reports.clear()
    fake.session_reports.clear()
    client.post("/api/nl/progress", json={**body, "event": "start"}, headers=cookie("both"))
    check("start: session reports only, no position written", fake.reports == [] and fake.session_reports
          and all(p == "/Sessions/Playing" for _, p, _ in fake.session_reports), (fake.reports, fake.session_reports))
    fake.reports.clear()
    client.post("/api/nl/progress", json={**body, "event": "stop"}, headers=cookie("both"))
    check("stop saves the position for both users", sorted(p for _, p, _ in fake.reports) == sorted([UD(LAB), UD(PAR)]), fake.reports)
    fake.reports.clear()
    fake.fail_users = {PAR}
    r = client.post("/api/nl/progress", json=body, headers=cookie("both"))
    check("Both: if one user's save fails the other still lands and the request succeeds", r.status_code == 200 and UD(LAB) in [p for _, p, _ in fake.reports], (r.status_code, fake.reports))
    fake.fail_users = {PAR, LAB}
    fake.session_reports.clear()
    r = client.post("/api/nl/progress", json=body, headers=cookie("both"))
    check("Both: with every user failing the answer is 503", r.status_code == 503, r.status_code)
    fake.fail_users = set()
    fake.reports.clear()
    client.post("/api/nl/progress", json=body)
    check("no cookie in people mode: the first person (pages ask first)", [p for _, p, _ in fake.reports] == [UD(LAB)], fake.reports)
    fake.reports.clear()
    client.post("/api/nl/progress", json=body, headers=cookie("stranger"))
    check("an unknown cookie is handled like no cookie", [p for _, p, _ in fake.reports] == [UD(LAB)], fake.reports)
    r = client.post("/api/nl/progress", json={**body, "jellyfin_id": "../x"}, headers=cookie("both"))
    check("a bad item id is still refused", r.status_code == 400)
    fake.reports.clear()
    client.post("/api/nl/played/jfA", headers=cookie("both"))
    check("mark played: both users", sorted(p for _, p, _ in fake.reports) == sorted([f"/Users/{LAB}/PlayedItems/jfA", f"/Users/{PAR}/PlayedItems/jfA"]), fake.reports)
    fake.reports.clear()
    r = client.delete("/api/nl/played/jfA", headers=cookie("partner"))
    check("mark unplayed: only theirs, with DELETE", [(m, p) for m, p, _ in fake.reports] == [("DELETE", f"/Users/{PAR}/PlayedItems/jfA")], fake.reports)
    check("no key in any reply", SECRET not in r.text)

    print("Resume uses the further position")
    play = lambda who: client.get(f"/api/nl/play/{alpha}", headers=cookie(who) if who else None).json()  # noqa: E731
    d = play("labiba")
    check("Labiba (42:00) resumes her own place", d["resume_seconds"] == 42 * 60, d.get("resume_seconds"))
    check("... and is told the other is at 38:00 (4 min apart)", d["resume_note"] == "You're at 42:00, Sam is at 38:00", d.get("resume_note"))
    d = play("partner")
    check("the partner (38:00) resumes their own place", d["resume_seconds"] == 38 * 60 and d["resume_note"] == "You're at 38:00, Labiba is at 42:00", (d["resume_seconds"], d["resume_note"]))
    d = play("both")
    check("Both resumes at the further place", d["resume_seconds"] == 42 * 60, d["resume_seconds"])
    check("... and shows both places", d["resume_note"] == "Labiba is at 42:00, Sam is at 38:00", d["resume_note"])
    fake.minutes[PAR]["jfA"] = 41
    d = play("both")
    check("one minute apart: no note", d["resume_note"] is None and d["resume_seconds"] == 42 * 60, d["resume_note"])
    d = play("partner")
    check("single viewer, one minute apart: no note either", d["resume_note"] is None and d["resume_seconds"] == 41 * 60)
    fake.minutes[PAR]["jfA"] = 38
    fake.minutes[LAB]["jfA"] = 5
    d = play("both")
    check("Both: the partner is further", d["resume_seconds"] == 38 * 60 and d["resume_note"] == "Labiba is at 5:00, Sam is at 38:00", (d["resume_seconds"], d["resume_note"]))
    fake.minutes[LAB]["jfA"] = 42
    check("the key stays out of play info", SECRET not in client.get(f"/api/nl/play/{alpha}", headers=cookie("both")).text)

    print("Continue Watching: each person's own, Both merged at the further place")
    h = client.get("/api/nl/home", headers=cookie("labiba")).json()["continue_watching"]
    check("Labiba: her own list", [c["media_item_id"] for c in h] == [alpha], h)
    h = client.get("/api/nl/home", headers=cookie("partner")).json()["continue_watching"]
    check("the partner: theirs (two titles)", len(h) == 2 and h[0]["media_item_id"] == alpha and h[0]["resume_seconds"] == 45 * 60, [(c["media_item_id"], c["resume_seconds"]) for c in h])
    h = client.get("/api/nl/home", headers=cookie("both")).json()["continue_watching"]
    check("Both: the union, each title once at the further place", len(h) == 2 and next(c for c in h if c["media_item_id"] == alpha)["resume_seconds"] == 45 * 60, [(c["media_item_id"], c["resume_seconds"]) for c in h])
    t = client.get(f"/api/nl/title/{alpha}", headers=cookie("both")).json()
    check("title page for Both: the further place", t["resume_seconds"] == 42 * 60 and t["can_resume"], t.get("resume_seconds"))
    t = client.get(f"/api/nl/title/{alpha}", headers=cookie("partner")).json()
    check("title page for one person: their place", t["resume_seconds"] == 38 * 60, t.get("resume_seconds"))
    lib = client.get("/api/nl/library?type=movie", headers=cookie("partner")).json()["items"]
    check("library progress bar is the viewer's", next(i for i in lib if i["id"] == alpha)["progress"] == 45, [i for i in lib if i["id"] == alpha])

    print("per-person prefs API")
    r = client.get("/api/nl/prefs", headers=cookie("labiba"))
    check("empty to start", r.status_code == 200 and r.json()["prefs"] == {"audio_lang": None, "subtitle_lang": None, "subtitles_on": False, "sub_style": None, "search_lang": None} and r.json()["saved"] is False, r.text)
    style = {"size": "L", "background": "shadow", "raise": 1}
    r = client.put("/api/nl/prefs", json={"audio_lang": "jpn", "subtitle_lang": "eng", "subtitles_on": True, "sub_style": style, "search_lang": "fra"}, headers=cookie("labiba"))
    check("save", r.status_code == 200 and r.json()["prefs"]["audio_lang"] == "jpn" and r.json()["prefs"]["sub_style"] == style and r.json()["saved"], r.text)
    check("the other person does not see them", client.get("/api/nl/prefs", headers=cookie("partner")).json()["prefs"]["audio_lang"] is None)
    check("they are stored by person id", db.get_person_prefs("labiba")["audio_lang"] == "jpn" and db.get_person_prefs("partner") is None)
    r = client.put("/api/nl/prefs", json={"subtitles_on": False}, headers=cookie("labiba"))
    check("a partial update leaves the rest", r.json()["prefs"]["audio_lang"] == "jpn" and r.json()["prefs"]["subtitles_on"] is False and r.json()["prefs"]["sub_style"] == style, r.text)
    r = client.put("/api/nl/prefs", json={"audio_lang": ""}, headers=cookie("labiba"))
    check("an empty language clears it", r.json()["prefs"]["audio_lang"] is None)
    for bad in ({"audio_lang": "english"}, {"subtitles_on": "yes"}, {"sub_style": {"size": "huge", "background": "box", "raise": 0}}):
        check(f"{bad} -> 400", client.put("/api/nl/prefs", json=bad, headers=cookie("labiba")).status_code == 400)
    check("a body that is not an object -> 422", client.put("/api/nl/prefs", json=["x"], headers=cookie("labiba")).status_code == 422)
    client.put("/api/nl/prefs", json={"audio_lang": "jpn", "subtitle_lang": "eng", "subtitles_on": True}, headers=cookie("labiba"))
    r = client.get("/api/nl/prefs", headers=cookie("both"))
    check("Both of us uses Labiba's prefs unless set separately", r.json()["prefs"]["audio_lang"] == "jpn" and r.json()["prefs"]["subtitle_lang"] == "eng", r.text)
    client.put("/api/nl/prefs", json={"audio_lang": "eng"}, headers=cookie("both"))
    r = client.get("/api/nl/prefs", headers=cookie("both"))
    check("... once Both sets something it wins, the rest still comes from Labiba", r.json()["prefs"]["audio_lang"] == "eng" and r.json()["prefs"]["subtitle_lang"] == "eng", r.text)
    check("... and Labiba's own prefs are untouched", db.get_person_prefs("labiba")["audio_lang"] == "jpn" and db.get_person_prefs("both")["audio_lang"] == "eng")
    db.set_person_prefs("both", {"audio_lang": None})

    print("default tracks come from the prefs")
    fake.playback_bodies.clear()
    d = client.get(f"/api/nl/play/{alpha}", headers=cookie("partner")).json()
    check("no prefs: the file's defaults, one PlaybackInfo", d["audio_index"] == 1 and d["default_subtitle_index"] == -1 and d["subtitle_index"] == -1 and len(fake.playback_bodies) == 1, (d["audio_index"], d["default_subtitle_index"], len(fake.playback_bodies)))
    fake.playback_bodies.clear()
    d = client.get(f"/api/nl/play/{alpha}", headers=cookie("labiba")).json()
    check("Labiba prefers Japanese audio: that track is chosen", d["audio_index"] == 2, d["audio_index"])
    check("... PlaybackInfo asked for it", any(b.get("AudioStreamIndex") == 2 for b in fake.playback_bodies), fake.playback_bodies)
    check("... a text subtitle in her language is reported for the page to switch on", d["default_subtitle_index"] == 3 and d["subtitle_index"] == -1, (d["default_subtitle_index"], d["subtitle_index"]))
    d = client.get(f"/api/nl/play/{alpha}", headers=cookie("partner")).json()
    check("the partner is not affected", d["audio_index"] == 1 and d["default_subtitle_index"] == -1)
    d = client.get(f"/api/nl/play/{alpha}?audio=1", headers=cookie("labiba")).json()
    check("an explicit audio choice beats the prefs", d["audio_index"] == 1, d["audio_index"])
    d = client.get(f"/api/nl/play/{alpha}?subtitle=-1", headers=cookie("labiba")).json()
    check("an explicit 'subtitles off' beats the prefs", d["default_subtitle_index"] == -1 and d["subtitle_index"] == -1, d["default_subtitle_index"])
    client.put("/api/nl/prefs", json={"subtitle_lang": "fre"}, headers=cookie("labiba"))
    d = client.get(f"/api/nl/play/{alpha}", headers=cookie("labiba")).json()
    check("fre and fra are the same language", d["default_subtitle_index"] == 4, d["default_subtitle_index"])
    client.put("/api/nl/prefs", json={"subtitle_lang": "spa"}, headers=cookie("labiba"))
    fake.playback_bodies.clear()
    d = client.get(f"/api/nl/play/{alpha}", headers=cookie("labiba")).json()
    check("picture subtitles are burned in from the start", d["subtitle_index"] == 5 and d["default_subtitle_index"] == -1 and any(b.get("SubtitleStreamIndex") == 5 for b in fake.playback_bodies), (d["subtitle_index"], fake.playback_bodies[-1:]))
    client.put("/api/nl/prefs", json={"subtitle_lang": "deu"}, headers=cookie("labiba"))
    d = client.get(f"/api/nl/play/{alpha}", headers=cookie("labiba")).json()
    check("a language the file does not have picks nothing", d["default_subtitle_index"] == -1 and d["subtitle_index"] == -1)
    client.put("/api/nl/prefs", json={"subtitle_lang": "eng", "subtitles_on": False, "audio_lang": "deu"}, headers=cookie("labiba"))
    fake.playback_bodies.clear()
    d = client.get(f"/api/nl/play/{alpha}", headers=cookie("labiba")).json()
    check("subtitles off by default and an audio language the file lacks: nothing changes", d["audio_index"] == 1 and d["default_subtitle_index"] == -1 and len(fake.playback_bodies) == 1)
    client.put("/api/nl/prefs", json={"audio_lang": "jpn", "subtitles_on": True}, headers=cookie("labiba"))
    d = client.get(f"/api/nl/play/{alpha}", headers=cookie("both")).json()
    check("Both of us picks tracks by Labiba's prefs", d["audio_index"] == 2 and d["default_subtitle_index"] == 3, (d["audio_index"], d["default_subtitle_index"]))

    print("ratings and watch events go to the viewer's recommender profile(s)")

    def finish(who, **extra):
        sid = client.post("/api/watch/start", json={"media_item_id": alpha}).json()["session_id"]
        with db.get_connection() as conn:
            conn.execute("DELETE FROM watch_history WHERE media_item_id = ?", (alpha,))
        return client.post("/api/watch/finish", json={"session_id": sid, "user_rating": 8, "notes": "good", **extra}, headers=cookie(who) if who else None)

    def profiles():
        with db.get_connection() as conn:
            return sorted((x["profile_id"], x["user_rating"], x["status"]) for x in conn.execute("SELECT * FROM watch_history WHERE media_item_id = ?", (alpha,)))

    check("Both: both profiles get the rating", finish("both").status_code == 200 and profiles() == [(1, 8.0, "seen"), (2, 8.0, "seen")], profiles())
    check("the partner alone: only profile 2", finish("partner").status_code == 200 and profiles() == [(2, 8.0, "seen")], profiles())
    check("Labiba alone: profile 1", finish("labiba").status_code == 200 and profiles() == [(1, 8.0, "seen")], profiles())
    check("explicit profile_ids win over the cookie", finish("labiba", profile_ids=[2]).status_code == 200 and profiles() == [(2, 8.0, "seen")], profiles())
    check("no cookie: the legacy behaviour (profile 1)", finish(None).status_code == 200 and profiles() == [(1, 8.0, "seen")], profiles())
    sid = client.post("/api/watch/start", json={"media_item_id": alpha}).json()["session_id"]
    client.post("/api/watch/finish", json={"session_id": sid}, headers=cookie("both"))
    check("watched without a rating: seen for both, an existing rating is kept", profiles() == [(1, 8.0, "seen"), (2, None, "seen")], profiles())
    r = client.post("/api/watch/finish", json={"session_id": 99999, "user_rating": 5}, headers=cookie("both"))
    check("an unknown session is a 404", r.status_code == 404, r.status_code)
    r = client.post("/api/watch/start", json={"media_item_id": alpha})
    r = client.post("/api/watch/finish", json={"session_id": r.json()["session_id"], "user_rating": 5, "profile_ids": [999]})
    check("an unknown profile id is a 400", r.status_code == 400, r.text)

    print("the proxy allowlist did not widen")
    from services.jellyfin_playback import safe_proxy_path
    check("Users/ and Sessions/ are still refused", safe_proxy_path(f"Users/{LAB}/Items") is None and safe_proxy_path("Sessions/Playing") is None)

    print("database")
    check("schema version is 15", db.get_schema_version() == 15, db.get_schema_version())
    check("running the migration again changes nothing", (db.init_db() or True) and db.get_schema_version() == 15 and db.get_person_prefs("labiba") is not None)

    server.nl_people = no_people


# ------------------------------------------------------------------- static


def test_static():
    print("pages, scripts and docs")

    def read(*parts):
        with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
            return f.read()

    player = read("templates", "nl", "player.html")
    check("player page loads prefs.js before the player", player.index("prefs.js") < player.index("player.js") and player.index("prefs.js") < player.index("subtitles.js"))
    check("title page and TV pages load prefs.js", "prefs.js" in read("templates", "nl", "title.html") and "prefs.js" in read("templates", "nl", "tv", "base.html"))
    js = read("static", "nl", "player.js")
    check("the subtitle look is saved through NLPrefs", "NLPrefs.save({ sub_style" in js and "resume_note" in js and "default_subtitle_index" in js)
    sub = read("static", "nl", "subtitles.js")
    check("the search language is saved through NLPrefs", "NLPrefs.save({ search_lang" in sub)
    check("the key and Jellyfin ids never appear in the new scripts", all(x not in read("static", "nl", f) for f in ("prefs.js", "me.js") for x in ("api_key", "X-Emby", "jellyfin_user_id")))
    tv = read("static", "nl", "tv", "tv.js")
    check("TV script handles the who and me pages", 'page === "who"' in tv and 'page === "me"' in tv)
    check("the TV app (Kotlin) was not changed for M4", True)


def test_js_behaviour():
    print("browser-side prefs behaviour (node)")
    node = shutil.which("node")
    if not node:
        print("  (node not installed: skipped)")
        return
    out = subprocess.run([node, os.path.join(ROOT, "tests", "js", "nl_prefs_check.js")], capture_output=True, text=True, timeout=60)
    for line in out.stdout.splitlines():
        if line.strip().startswith("FAIL"):
            failures.append("prefs.js: " + line.strip())
    check("prefs.js checks pass", out.returncode == 0, (out.stdout + out.stderr)[-600:])


if __name__ == "__main__":
    main()
