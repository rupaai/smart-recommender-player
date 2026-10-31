"""
Offline tests for Northern Lights M7a, "player polish" (tests/test_nl_player_extras.py):
seek-bar thumbnails (Trickplay), chapters, skip intro / next episode (MediaSegments), the per-show
"always skip intro" switch, speed, phone gestures, and the TV bridge v4 (setSpeed).

Jellyfin is faked (a stand-in requests session); nothing leaves the process.

    uv run python tests/test_nl_player_extras.py
"""

import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nl_fake  # noqa: E402
import requests  # noqa: E402
from nl_fake import ALPHA, EPS, SECRET, TICKS, FakeResponse  # noqa: E402

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


MANIFEST = {
    "jfA": {
        "480": {"Width": 480, "Height": 270, "TileWidth": 5, "TileHeight": 5, "ThumbnailCount": 30, "Interval": 10000, "Bandwidth": 1},
        "320": {"Width": 320, "Height": 180, "TileWidth": 10, "TileHeight": 10, "ThumbnailCount": 590, "Interval": 10000, "Bandwidth": 1},
    }
}
CHAPTERS = [
    {"StartPositionTicks": 0, "Name": "Opening"},
    {"StartPositionTicks": 600 * TICKS, "Name": ""},
    {"StartPositionTicks": 3000 * TICKS, "Name": "Finale"},
]
SEGMENTS = {"Items": [
    {"Id": "s2", "ItemId": "jfA", "Type": "Outro", "StartTicks": 5800 * TICKS, "EndTicks": 5990 * TICKS},
    {"Id": "s1", "ItemId": "jfA", "Type": "Intro", "StartTicks": 30 * TICKS, "EndTicks": 90 * TICKS},
], "TotalRecordCount": 2, "StartIndex": 0}


class ExtrasJellyfin(nl_fake.FakeJellyfin):
    """The usual fake plus item details with Trickplay / Chapters and MediaSegments."""

    def __init__(self):
        super().__init__()
        self.details = {
            "jfA": {"Id": "jfA", "Type": "Movie", "UserData": nl_fake.ud(40), "RunTimeTicks": 100 * 60 * TICKS,
                    "Trickplay": MANIFEST, "Chapters": CHAPTERS},
            "jfS1E1": {"Id": "jfS1E1", "Type": "Episode", "SeriesId": "series1", "UserData": {},
                       "Trickplay": {"jfS1E1": {"320": MANIFEST["jfA"]["320"]}}, "Chapters": []},
        }
        self.segments = {"jfA": SEGMENTS, "jfS1E1": {"Items": [
            {"Type": "Intro", "StartTicks": 12 * TICKS, "EndTicks": 72 * TICKS},
            {"Type": "Outro", "StartTicks": 2700 * TICKS, "EndTicks": 2800 * TICKS},
        ]}}
        self.segment_status = 200
        self.segment_calls = []

    def request(self, method, url, **kw):
        path = url.split("jellyfin.test:8096", 1)[1]
        if path.startswith("/MediaSegments/"):
            if self.down:
                raise requests.ConnectionError("down")
            self.segment_calls.append((method, path, kw.get("params")))
            if self.segment_status != 200:
                return FakeResponse(self.segment_status, json_data={})
            return FakeResponse(json_data=self.segments.get(path.rsplit("/", 1)[1], {"Items": []}))
        if path.startswith("/Users/user1/Items/") and path.rsplit("/", 1)[1] in self.details:
            if self.down:
                raise requests.ConnectionError("down")
            self.calls.append((method, path, kw.get("params") or {}))
            return FakeResponse(json_data=self.details[path.rsplit("/", 1)[1]])
        return super().request(method, url, **kw)


def main():
    tmp = tempfile.mkdtemp(prefix="nl_extras_test_")
    try:
        test_parsers()
        test_proxy_paths()
        test_migration(tmp)
        run(tmp)
        test_static()
        test_js_behaviour()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if failures:
        print(f"\n{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("\nAll Northern Lights player polish (M7a) tests passed")


# ---------------------------------------------------------------------- pure parsers


def test_parsers():
    from services.nl_player_extras import parse_chapters, parse_segments, parse_trickplay

    print("parse_trickplay")
    t = parse_trickplay({"Trickplay": MANIFEST}, "jfA")
    check("the smallest width wins", t["width"] == 320 and t["height"] == 180 and t["tile_width"] == 10 and t["tile_height"] == 10, t)
    check("interval is in seconds", t["interval_seconds"] == 10.0 and t["count"] == 590, t)
    check("url is our proxy with a literal {index}", t["url"] == "/nl/jf/Videos/jfA/Trickplay/320/{index}.jpg", t["url"])
    other = {"Trickplay": {"zzz": {"160": {"Width": 160, "Height": 90, "TileWidth": 5, "TileHeight": 5, "ThumbnailCount": 9, "Interval": 5000}}}}
    check("a manifest for another media source id still works (first one)", parse_trickplay(other, "jfA")["width"] == 160)
    check("the media source matching the item is preferred",
          parse_trickplay({"Trickplay": {"zzz": MANIFEST["jfA"], "J-F-A": {"320": {**MANIFEST["jfA"]["320"], "Interval": 5000}}}}, "jfA")["interval_seconds"] == 5.0)
    check("nothing to show: no manifest, empty, wrong type, bad item id",
          parse_trickplay({}, "jfA") is None and parse_trickplay({"Trickplay": {}}, "jfA") is None
          and parse_trickplay({"Trickplay": [1]}, "jfA") is None and parse_trickplay({"Trickplay": MANIFEST}, "../x") is None
          and parse_trickplay(None, "jfA") is None)
    bad = {"Trickplay": {"jfA": {"320": {"Width": 320, "Height": 180, "TileWidth": 0, "TileHeight": 10, "ThumbnailCount": 5, "Interval": 1000},
                                  "321": {"Width": 321, "Height": 180, "TileWidth": 10, "TileHeight": 10, "ThumbnailCount": 5, "Interval": 0},
                                  "999999": {"Width": 999999, "Height": 180, "TileWidth": 10, "TileHeight": 10, "ThumbnailCount": 5, "Interval": 10},
                                  "abc": "x"}}}
    check("unusable entries are dropped", parse_trickplay(bad, "jfA") is None, parse_trickplay(bad, "jfA"))

    print("parse_chapters")
    c = parse_chapters({"Chapters": CHAPTERS})
    check("chapters keep order and times", [x["start_seconds"] for x in c] == [0, 600, 3000], c)
    check("an empty name becomes Chapter N", [x["name"] for x in c] == ["Opening", "Chapter 2", "Finale"], c)
    check("sorted even when Jellyfin is not", [x["start_seconds"] for x in parse_chapters({"Chapters": list(reversed(CHAPTERS))})] == [0, 600, 3000])
    check("one chapter (or none) is no chapter list", parse_chapters({"Chapters": CHAPTERS[:1]}) == [] and parse_chapters({}) == [] and parse_chapters(None) == [])
    check("junk and negative and duplicate starts are dropped",
          [x["start_seconds"] for x in parse_chapters({"Chapters": [CHAPTERS[0], "x", {"StartPositionTicks": -5}, {"StartPositionTicks": 0, "Name": "dup"}, CHAPTERS[2]]})] == [0, 3000])
    check("names are trimmed to a sane length", len(parse_chapters({"Chapters": [CHAPTERS[0], {"StartPositionTicks": 5 * TICKS, "Name": "x" * 500}]})[1]["name"]) == 120)

    print("parse_segments")
    s = parse_segments(SEGMENTS)
    check("intro and outro, in time order, in seconds", [(x["type"], x["start_seconds"], x["end_seconds"]) for x in s] == [("intro", 30, 90), ("outro", 5800, 5990)], s)
    check("numbers for the type work too (Intro 5, Outro 4)", [x["type"] for x in parse_segments({"Items": [
        {"Type": 5, "StartTicks": 0, "EndTicks": TICKS}, {"Type": 4, "StartTicks": 2 * TICKS, "EndTicks": 3 * TICKS}]})] == ["intro", "outro"])
    check("other types, backwards or empty ranges and junk are dropped", parse_segments({"Items": [
        {"Type": "Recap", "StartTicks": 0, "EndTicks": TICKS}, {"Type": "Intro", "StartTicks": 5 * TICKS, "EndTicks": 5 * TICKS},
        {"Type": "Intro", "StartTicks": 9 * TICKS, "EndTicks": 2 * TICKS}, {"Type": "Intro", "StartTicks": -1, "EndTicks": TICKS}, "x",
        {"Type": "Intro", "EndTicks": TICKS}]}) == [])
    check("not a dict / no Items", parse_segments(None) == [] and parse_segments([]) == [] and parse_segments({}) == [] and parse_segments({"Items": None}) == [])
    many = {"Items": [{"Type": "Intro", "StartTicks": i * 10 * TICKS, "EndTicks": (i * 10 + 5) * TICKS} for i in range(20)]}
    check("at most 6 segments", len(parse_segments(many)) == 6)


# ------------------------------------------------------------------------ proxy allowlist


def test_proxy_paths():
    from services.jellyfin_playback import is_trickplay_path, safe_proxy_path

    print("proxy: the trickplay path is exact, everything else Jellyfin offers stays out")
    good = "Videos/jfA/Trickplay/320/0.jpg"
    check("a tile sheet passes", safe_proxy_path(good) == good and is_trickplay_path(good))
    check("sheet numbers up to six digits pass", is_trickplay_path("Videos/abc-123/Trickplay/160/123456.jpg"))
    for bad in (
        "Videos/jfA/Trickplay/320/tiles.m3u8",          # the playlist is not used
        "Videos/jfA/Trickplay/abc/0.jpg", "Videos/jfA/Trickplay/320/x.jpg", "Videos/jfA/Trickplay/320/0.png",
        "Videos/jfA/Trickplay/320/0.jpg/extra", "Videos/jfA/Trickplay/3/0.jpg", "Videos/jfA/Trickplay/32000/0.jpg",
        "Videos/jfA/Trickplay/320/1234567.jpg", "Videos/jfA/Trickplay/320/-1.jpg", "Videos/Trickplay/320/0.jpg",
        "Videos/jf A/Trickplay/320/0.jpg", "Videos/jfA/Trickplay/320/0.jpg?x=1", "Items/jfA/Trickplay/320/0.jpg",
        "videos/jfA/Trickplay/320/0.JPG",
    ):
        check(f"{bad} is not a trickplay path", not is_trickplay_path(bad))
    for bad in (
        "Videos/jfA/Trickplay/320/../../../Users", "Videos/jfA/Trickplay/320/%2e%2e/0.jpg", "Videos/jfA/Trickplay/320/%252e%252e/0.jpg",
        "MediaSegments/jfA", "mediasegments/jfA", "Items/jfA", "Items/jfA/PlaybackInfo", "Users/user1/Items/jfA",
        "System/Info", "Sessions", "Items/jfA/Trickplay/320/0.jpg", "Plugins", "Videos/../System/Info", "Trickplay/320/0.jpg",
    ):
        check(f"{bad} stays refused", safe_proxy_path(bad) is None, safe_proxy_path(bad))


# ------------------------------------------------------------------------------ schema


def test_migration(tmp):
    import sqlite3

    from database import DatabaseManager

    print("migration v15")
    fresh = DatabaseManager(os.path.join(tmp, "fresh15.db"))
    with fresh.get_connection() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(nl_show_viewer_prefs)")}
    check("fresh DB has the per-viewer show table", {"media_item_id", "viewer_key", "skip_intro", "updated_at"} <= cols, cols)
    check("fresh DB is at schema 15 or newer", fresh.get_schema_version() >= 15, fresh.get_schema_version())

    path = os.path.join(tmp, "old15.db")
    old = DatabaseManager(path)
    old.register_source_file("/media/Shows/Z/Season 1/Z S01E01.mkv", "Z", 2020, "tv", season_number=1, episode_number=1)
    show = old.get_library_items("tv")[0]["id"]
    old.set_show_autoplay(show, False)
    with sqlite3.connect(path) as conn:  # how a v14 database looks
        conn.execute("DROP TABLE nl_show_viewer_prefs")
        conn.execute("PRAGMA user_version = 14")
    up = DatabaseManager(path)
    check("a v14 database upgrades", up.get_schema_version() >= 15, up.get_schema_version())
    check("the new table is back, empty, and nothing else was touched", up.get_show_skip_intro(show, "labiba") is False and up.get_show_autoplay(show) is False)
    up.set_show_skip_intro(show, "labiba", True)
    check("it stores per viewer", up.get_show_skip_intro(show, "labiba") is True and up.get_show_skip_intro(show, "partner") is False)
    check("running again is harmless", DatabaseManager(path).get_show_skip_intro(show, "labiba") is True)


# ------------------------------------------------------------------------------ the app


def run(tmp):
    ctx = nl_fake.boot(tmp)
    server, client, ids, db = ctx["server"], ctx["client"], ctx["ids"], ctx["db"]
    state = ctx["state"]
    fake = ExtrasJellyfin()
    state.jellyfin_service.session = fake
    state.jellyfin_playback._ids = {}
    state.jellyfin_playback._last_refresh = None
    alpha, show = ids["Alpha"], ids["Sigma"]
    src = ids["sources"]
    ep1 = src[EPS[(1, 1)]]

    print("GET /api/nl/player-extras/{id}: a movie with everything set up")
    r = client.get(f"/api/nl/player-extras/{alpha}")
    x = r.json()
    check("200 with trickplay, chapters and segments", r.status_code == 200 and x["trickplay"] and x["chapters"] and x["segments"], r.text[:300])
    check("trickplay geometry and the proxy template", x["trickplay"]["width"] == 320 and x["trickplay"]["url"] == "/nl/jf/Videos/jfA/Trickplay/320/{index}.jpg", x["trickplay"])
    check("chapters", [c["name"] for c in x["chapters"]] == ["Opening", "Chapter 2", "Finale"], x["chapters"])
    check("segments come back as our json", [(s["type"], s["start_seconds"]) for s in x["segments"]] == [("intro", 30), ("outro", 5800)], x["segments"])
    check("a movie has no skip-intro switch", x["is_show"] is False and x["skip_intro"] is False)
    check("the key and Jellyfin's own field names never reach the page", SECRET not in r.text and "StartTicks" not in r.text and "StartPositionTicks" not in r.text and "ThumbnailCount" not in r.text, r.text[:200])
    method, path, params = fake.segment_calls[-1]
    check("MediaSegments is asked server-side, GET, for Intro and Outro",
          method == "GET" and path == "/MediaSegments/jfA" and params == {"includeSegmentTypes": ["Intro", "Outro"]}, (method, path, params))

    print("Jellyfin without Trickplay or without the plugin: the parts are just empty")
    fake.details["jfA"] = {"Id": "jfA", "Type": "Movie", "UserData": {}}
    fake.segment_status = 404
    r = client.get(f"/api/nl/player-extras/{alpha}")
    x = r.json()
    check("no trickplay, no chapters, no segments, still a 200",
          r.status_code == 200 and x["trickplay"] is None and x["chapters"] == [] and x["segments"] == [], r.text)
    fake.segment_status = 500
    check("a failing MediaSegments (500) is not an error either", client.get(f"/api/nl/player-extras/{alpha}").json()["segments"] == [])
    fake.segment_status = 200
    fake.segments["jfA"] = {"Items": []}
    check("a provider with nothing found: empty list", client.get(f"/api/nl/player-extras/{alpha}").json()["segments"] == [])
    fake.segments["jfA"] = "not json shaped"
    check("a surprising reply shape is survived", client.get(f"/api/nl/player-extras/{alpha}").status_code == 200)
    fake.details["jfA"] = {"Id": "jfA", "Trickplay": {"jfA": {"320": {"Width": "x"}}}, "Chapters": "nope"}
    r = client.get(f"/api/nl/player-extras/{alpha}")
    check("odd Trickplay and Chapters values are survived", r.status_code == 200 and r.json()["trickplay"] is None and r.json()["chapters"] == [], r.text)
    fake.details["jfA"] = {"Id": "jfA", "Type": "Movie", "UserData": {}, "Trickplay": MANIFEST, "Chapters": CHAPTERS}
    fake.segments["jfA"] = SEGMENTS

    print("Jellyfin down / unknown file / unknown title")
    fake.down = True
    state.jellyfin_playback._ids = {}
    state.jellyfin_playback._last_refresh = None
    r = client.get(f"/api/nl/player-extras/{alpha}")
    check("Jellyfin down: everything empty, 200, no error text",
          r.status_code == 200 and r.json()["trickplay"] is None and r.json()["chapters"] == [] and r.json()["segments"] == [], r.text)
    fake.down = False
    state.jellyfin_playback._ids = {}
    state.jellyfin_playback._last_refresh = None
    check("an unknown title is a 404", client.get("/api/nl/player-extras/99999").status_code == 404)
    check("an unknown episode is a 404", client.get(f"/api/nl/player-extras/{show}", params={"source": 99999}).status_code == 404)
    check("source must be a positive number", client.get(f"/api/nl/player-extras/{show}", params={"source": 0}).status_code == 422)
    saved = dict(fake.items)
    fake.items = {}
    state.jellyfin_playback._ids = {}
    state.jellyfin_playback._last_refresh = None
    check("a file Jellyfin doesn't know yet: empty, 200", client.get(f"/api/nl/player-extras/{alpha}").json()["segments"] == [])
    fake.items = saved
    state.jellyfin_playback._ids = {}
    state.jellyfin_playback._last_refresh = None

    print("an episode: segments, the show switch is per viewer")
    from services import nl_people as np

    state.nl_people = np.People(np.parse_people([
        {"id": "labiba", "name": "Labiba", "jellyfin_user_id": "User1", "profile_id": 1},
        {"id": "partner", "name": "Sam", "jellyfin_user_id": "User1", "profile_id": 2},
    ]))
    lab, par, both = {"Cookie": "nl_viewer=labiba"}, {"Cookie": "nl_viewer=partner"}, {"Cookie": "nl_viewer=both"}
    r = client.get(f"/api/nl/player-extras/{show}", params={"source": ep1}, headers=lab)
    x = r.json()
    check("episode: intro 12-72 and outro, is_show, switch off", r.status_code == 200 and x["is_show"] and x["skip_intro"] is False
          and [(s["type"], s["start_seconds"], s["end_seconds"]) for s in x["segments"]] == [("intro", 12, 72), ("outro", 2700, 2800)], r.text[:300])
    check("episode trickplay uses the episode's Jellyfin id", x["trickplay"]["url"] == "/nl/jf/Videos/jfS1E1/Trickplay/320/{index}.jpg", x["trickplay"])
    check("an episode with no chapters has none", x["chapters"] == [])
    check("default is off", client.get(f"/api/nl/show/{show}/skip-intro", headers=lab).json() == {"skip_intro": False})
    r = client.put(f"/api/nl/show/{show}/skip-intro", json={"skip_intro": True}, headers=lab)
    check("Labiba turns it on", r.status_code == 200 and r.json() == {"skip_intro": True}, r.text)
    check("it is remembered for Labiba", client.get(f"/api/nl/show/{show}/skip-intro", headers=lab).json()["skip_intro"] is True)
    check("Sam is not affected", client.get(f"/api/nl/show/{show}/skip-intro", headers=par).json()["skip_intro"] is False)
    check("Both of us is not affected either", client.get(f"/api/nl/show/{show}/skip-intro", headers=both).json()["skip_intro"] is False)
    check("the extras carry the viewer's own switch",
          client.get(f"/api/nl/player-extras/{show}", params={"source": ep1}, headers=lab).json()["skip_intro"] is True
          and client.get(f"/api/nl/player-extras/{show}", params={"source": ep1}, headers=par).json()["skip_intro"] is False)
    client.put(f"/api/nl/show/{show}/skip-intro", json={"skip_intro": True}, headers=both)
    client.put(f"/api/nl/show/{show}/skip-intro", json={"skip_intro": False}, headers=lab)
    check("Both has its own switch, and turning Labiba's off leaves it",
          client.get(f"/api/nl/show/{show}/skip-intro", headers=both).json()["skip_intro"] is True
          and client.get(f"/api/nl/show/{show}/skip-intro", headers=lab).json()["skip_intro"] is False)
    with db.get_connection() as conn:
        rows = {r["viewer_key"]: r["skip_intro"] for r in conn.execute("SELECT viewer_key, skip_intro FROM nl_show_viewer_prefs WHERE media_item_id = ?", (show,))}
    check("one row per show and viewer in the database", rows == {"labiba": 0, "both": 1}, rows)
    check("autoplay (nl_show_prefs) is untouched", client.get(f"/api/nl/show/{show}/prefs").json() == {"autoplay": True})
    check("a movie has no such switch", client.put(f"/api/nl/show/{alpha}/skip-intro", json={"skip_intro": True}, headers=lab).status_code == 404)
    check("unknown show 404, bad body 422",
          client.put("/api/nl/show/9999/skip-intro", json={"skip_intro": True}, headers=lab).status_code == 404
          and client.put(f"/api/nl/show/{show}/skip-intro", json={"skip_intro": "maybe"}, headers=lab).status_code == 422
          and client.put(f"/api/nl/show/{show}/skip-intro", json={}, headers=lab).status_code == 422)
    state.nl_people = np.People([])
    client.put(f"/api/nl/show/{show}/skip-intro", json={"skip_intro": True})
    with db.get_connection() as conn:
        keys = sorted(r["viewer_key"] for r in conn.execute("SELECT viewer_key FROM nl_show_viewer_prefs WHERE media_item_id = ?", (show,)))
    check("with nobody configured the implicit viewer ('default') has its own switch", "default" in keys, keys)

    print("GET /nl/jf/ trickplay sheet")
    fake.stream_response = FakeResponse(200, {"content-type": "image/jpeg", "content-length": "4", "x-secret-header": SECRET}, body=b"JPEG")
    fake.get_calls.clear()
    r = client.get("/nl/jf/Videos/jfA/Trickplay/320/3.jpg?api_key=" + SECRET)
    url, hdrs = fake.get_calls[-1]
    check("the sheet is proxied", r.status_code == 200 and r.content == b"JPEG" and r.headers["content-type"] == "image/jpeg", r.status_code)
    check("upstream url is Jellyfin's exact path, with no client key", url == "http://jellyfin.test:8096/Videos/jfA/Trickplay/320/3.jpg" and SECRET not in url, url)
    check("sheets are cacheable, other headers not leaked, key not in the reply", "max-age" in r.headers.get("cache-control", "")
          and "x-secret-header" not in r.headers and SECRET not in str(dict(r.headers)))
    for bad in ("MediaSegments/jfA", "Items/jfA/PlaybackInfo", "System/Info", "Users/user1/Items/jfA", "Items/jfA/Trickplay/320/0.jpg",
                "Videos/jfA/Trickplay/320/%2e%2e/%2e%2e/%2e%2e/%2e%2e/Users", "Videos/jfA/Trickplay/320/%2e%2e/0.jpg", "Sessions"):
        fake.get_calls.clear()
        r = client.get(f"/nl/jf/{bad}")
        check(f"proxy still refuses {bad}", r.status_code == 404 and not fake.get_calls, f"{r.status_code} calls={len(fake.get_calls)}")
    for method in ("post", "put", "delete", "patch"):
        r = getattr(client, method)("/nl/jf/Videos/jfA/Trickplay/320/3.jpg")
        check(f"trickplay is read-only: {method.upper()} refused", r.status_code in (404, 405), r.status_code)

    print("player page and files")
    r = client.get(f"/nl/play/{alpha}")
    html = r.text
    needed = ('id="nl-thumb"', 'id="nl-ticks"', 'id="nl-chapters"', 'id="nl-speed"', 'id="nl-seg"', 'id="nl-seg-go"', 'id="nl-seg-always"', "player-extras.js")
    check("the player page has the thumbnail bubble, ticks, chapters, speed and skip button", r.status_code in (200, 302) and (r.status_code == 302 or all(n in html for n in needed)),
          (r.status_code, [n for n in needed if n not in html]))
    check("the key isn't in the page", SECRET not in html)


def test_static():
    print("static files")
    js = read("static/nl/player.js")
    core = read("static/nl/player-core.js")
    css = read("static/nl/player.css")
    html = read("templates/nl/player.html")
    check("player.js loads the extras once per episode and tolerates failure", "/api/nl/player-extras/" in js and "function loadExtras" in js and "optional" in js)
    check("player.js: thumbnails on hover and scrub, and on the TV when skipping",
          "function showThumb" in js and 'seek.addEventListener("pointermove"' in js and "flashThumb(r.target, r.target / d)" in js and "remote && hasTrickplay()" in js)
    check("player.js: chapter ticks and the chapters menu", "function renderTicks" in js and "function openChaptersMenu" in js)
    check("player.js: skip intro, next episode, always-skip, auto skip once per segment",
          "function checkSegments" in js and "Skip intro" in html and "Next episode" in html and "S.autoSkipped[key]" in js and "/skip-intro" in js)
    check("player.js: the remote can use, step through and dismiss the skip button", "dismissSeg()" in js and '$("nl-seg").contains(document.activeElement)' in js)
    check("player.js: speed menu with the five speeds, < and > keys", "function openSpeedMenu" in js and "X.SPEEDS" in js and 'case "<"' in js and 'case ">"' in js)
    check("player.js: phone gestures only for touch, never on the TV",
          "touchGestures()" in js and 'S.pointerType === "touch" && !remote' in js and "X.tapAction" in js and "X.swipeVolume" in js and "player.canSetVolume" in js)
    check("player-core.js: speed behind feature detection, bridge v4", "canChangeSpeed: v4" in core and 'call("setSpeed"' in core and "Number(nativeVersion()) >= 4" in core)
    check("css: touch-action and the thumbnail bubble", "touch-action: none" in css and ".nl-thumb" in css and ".nl-seg" in css)

    print("TV app (Kotlin is not compiled here; these check the text)")
    bridge = read("tv-app/app/src/main/java/app/northernlights/tv/NativeBridge.kt")
    ctl = read("tv-app/app/src/main/java/app/northernlights/tv/PlayerController.kt")
    main_kt = read("tv-app/app/src/main/java/app/northernlights/tv/MainActivity.kt")
    check("bridge: setSpeed(String?) is a JavascriptInterface method and version() says 7 (v7 keeps setSpeed)",
          "@JavascriptInterface\n    fun setSpeed(rate: String?)" in bridge and 'if (host.isTrusted()) "7" else ""' in bridge)
    check("bridge: speed is range-checked and only for trusted pages", "MIN_SPEED" in bridge and "MAX_SPEED" in bridge and "!host.isTrusted()" in bridge.split("fun setSpeed")[1].split("fun version")[0])
    check("bridge: the older methods are all still there", all(f"fun {n}(" in bridge for n in ("play", "stop", "pause", "resume", "seekTo", "setSubtitle", "setSubtitleStyle")))
    check("Host has onSpeedRequested and MainActivity hops to the UI thread for it",
          "fun onSpeedRequested(rate: Double)" in bridge and "override fun onSpeedRequested(rate: Double)" in main_kt
          and "runOnUiThread { if (pageTrusted) player.setSpeed(rate) }" in main_kt)
    check("PlayerController: Media3 setPlaybackSpeed, remembered for the next stream",
          "setPlaybackSpeed(rate.toFloat())" in ctl and "exo.setPlaybackSpeed(speed.toFloat())" in ctl and "private var speed = 1.0" in ctl)
    check("no navigation rule was changed (off-origin links are still dropped)",
          "return ServerConfig.originOf(request.url.toString()) != serverOrigin" in main_kt)


def test_js_behaviour():
    print("browser-side behaviour (node)")
    node = shutil.which("node")
    if not node:
        print("  (node not installed: skipped)")
        return
    out = subprocess.run([node, os.path.join(ROOT, "tests", "js", "nl_player_extras_check.js")], capture_output=True, text=True, timeout=60)
    check("tiles, chapters, segments, speed, gestures and the TV v4 adapter all behave", out.returncode == 0, (out.stdout + out.stderr)[-800:])


if __name__ == "__main__":
    main()
