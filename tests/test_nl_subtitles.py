"""
Offline tests for Northern Lights M5, "Subtitles that fix themselves" (tests/test_nl_subtitles.py)

Jellyfin is faked (a stand-in requests session); nothing leaves the process.

    uv run python tests/test_nl_subtitles.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402
from services.jellyfin_playback import JellyfinPlayback  # noqa: E402

JellyfinPlayback.file_exists = staticmethod(lambda path: True)  # fake files: no real disk

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
failures = []
SECRET = "SECRET-KEY-123"
FILE = "/media/Movies/A (2020)/A.mkv"


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} — got: {detail}")
        failures.append(label)


class FakeResponse:
    def __init__(self, status=200, json_data=None, text=""):
        self.status_code = status
        self._json = json_data
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def json(self):
        if self._json is None:
            raise ValueError("no body")
        return self._json


REMOTE = [
    {"ThreeLetterISOLanguageName": "eng", "Id": "b3Blbg_100", "ProviderName": "Open Subtitles", "Name": "A.2020.1080p.WEB.srt",
     "Format": "srt", "CommunityRating": 7.46, "DownloadCount": 900},
    {"ThreeLetterISOLanguageName": "eng", "Id": "b3Blbg_200", "ProviderName": "Open Subtitles", "Name": "A.2020.BluRay.SDH.srt",
     "Format": "srt", "CommunityRating": 8.0, "DownloadCount": 5000},
    {"ThreeLetterISOLanguageName": "eng", "Id": "b3Blbg_300", "ProviderName": "Open Subtitles", "Name": "A.2020.exact.srt",
     "Format": "srt", "CommunityRating": 6.0, "DownloadCount": 10, "IsHashMatch": True},
    {"ThreeLetterISOLanguageName": "eng", "Id": "b3Blbg_400", "ProviderName": "Open Subtitles", "Name": "A.2020.foreign.parts.srt",
     "Format": "srt", "Forced": True, "DownloadCount": 3},
    {"Name": "no id, dropped"},
]


class FakeJellyfin:
    """Stands in for JellyfinService.session."""

    def __init__(self):
        self.items = [{"Id": "jf1", "Path": FILE}]
        self.calls = []          # (method, path, headers)
        self.down = False
        self.search_status = 200
        self.search_body = list(REMOTE)
        self.search_text = ""
        self.download_status = 204
        self.download_text = ""
        self.plugins = [{"Name": "Open Subtitles", "Status": "Active"}]  # list, or an int status

    def request(self, method, url, **kw):
        if self.down:
            raise requests.ConnectionError("down")
        path = url.split("jellyfin.test:8096", 1)[1]
        self.calls.append((method, path, kw.get("headers")))
        if path == "/Users" and method == "GET":
            return FakeResponse(json_data=[{"Name": "User1", "Id": "user1"}])
        if path == "/Items" and method == "GET":
            return FakeResponse(json_data={"Items": list(self.items)})
        if path == "/Plugins" and method == "GET":
            if isinstance(self.plugins, int):
                return FakeResponse(self.plugins, json_data={})
            return FakeResponse(json_data=self.plugins)
        if path.startswith("/Items/jf1/RemoteSearch/Subtitles/") and method == "GET":
            if self.search_status >= 400:
                return FakeResponse(self.search_status, text=self.search_text)
            return FakeResponse(json_data=self.search_body)
        if path.startswith("/Items/jf1/RemoteSearch/Subtitles/") and method == "POST":
            return FakeResponse(self.download_status, text=self.download_text)
        return FakeResponse(404, json_data={})

    def subtitle_calls(self):
        return [(m, p) for (m, p, _h) in self.calls if "RemoteSearch" in p]


def main():
    test_pure()
    tmp = tempfile.mkdtemp(prefix="nl_subtitles_test_")
    try:
        run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    test_static()
    test_js_behaviour()
    if failures:
        print(f"\n{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("\nAll Northern Lights subtitle tests passed")


def test_pure():
    from services import jellyfin_playback as jp

    print("validation")
    check("eng is a language", jp.valid_language("eng"))
    for bad in ("en", "ENG", "engl", "e/g", "../", "eng/..", "", "e n", None, 5):
        check(f"language {bad!r} refused", not jp.valid_language(bad))
    check("provider-style subtitle ids pass", all(jp.valid_subtitle_id(x) for x in ("b3Blbg_100", "abc-DEF_1.2", "x=y,z:1+2~3")))
    for bad in ("", "a/b", "../x", "a b", "a?b=1", "a#b", "-x", ".x", "a..b", "a%2fb", "x" * 600, None, 7, "a\nb", "a\\b"):
        check(f"subtitle id {bad!r} refused", not jp.valid_subtitle_id(bad))

    print("result parsing and order")
    parsed = jp.parse_subtitle_results(REMOTE)
    ids = [r["id"] for r in parsed]
    check("hash match first, then most downloaded", ids == ["b3Blbg_300", "b3Blbg_200", "b3Blbg_100", "b3Blbg_400"], ids)
    check("entries without an id are dropped", len(parsed) == 4)
    first = parsed[0]
    check("fields: language name, format, provider, downloads, hash_match",
          first["language"] == "eng" and first["language_name"] == "English" and first["format"] == "srt"
          and first["provider"] == "Open Subtitles" and first["downloads"] == 10 and first["hash_match"] is True, first)
    check("rating is rounded to one decimal", parsed[2]["rating"] == 7.5, parsed[2]["rating"])
    check("SDH is read from the name when the plugin has no flag", parsed[1]["hearing_impaired"] is True and parsed[2]["hearing_impaired"] is False)
    check("forced comes from the flag", parsed[3]["forced"] is True and parsed[0]["forced"] is False)
    check("a non-list reply gives no results", jp.parse_subtitle_results({"x": 1}) == [] and jp.parse_subtitle_results(None) == [])
    many = [{"Id": f"i{n}", "Name": "x", "DownloadCount": n} for n in range(80)]
    check("at most 50 results", len(jp.parse_subtitle_results(many)) == jp.MAX_SUBTITLE_RESULTS)
    check("odd numbers don't crash", jp.parse_subtitle_results([{"Id": "a", "DownloadCount": "many", "CommunityRating": "x"}])[0]["downloads"] is None)

    print("error mapping")
    e = jp.subtitle_error_for(429, "", "download")
    check("429 is the daily limit", e.code == "limit" and e.status == 429 and "limit" in e.message.lower(), e.message)
    e = jp.subtitle_error_for(500, "Download limit reached for this user", "download")
    check("a 500 that talks about the limit is the daily limit", e.code == "limit", e.code)
    e = jp.subtitle_error_for(401, "", "search")
    check("401 means the plugin isn't signed in", e.code == "plugin_unconfigured" and e.status == 409 and "signed in" in e.message, e.message)
    check("403 too", jp.subtitle_error_for(403, "", "download").code == "plugin_unconfigured")
    check("404 on search means no plugin", jp.subtitle_error_for(404, "", "search").code == "plugin_missing")
    check("404 on download means the subtitle is gone", jp.subtitle_error_for(404, "", "download").code == "not_found")
    e = jp.subtitle_error_for(500, "boom", "search")
    check("anything else is a plain failure that names the status", e.code == "failed" and e.status == 502 and "500" in e.message, e.message)
    check("the proxy allowlist does not grow", jp.safe_proxy_path("Items/jf1/RemoteSearch/Subtitles/eng") is None
          and jp.safe_proxy_path("Items/jf1/RemoteSearch/Subtitles/abc") is None)


def run(tmp):
    cfg_file = os.path.join(tmp, "config.yaml")
    with open(cfg_file, "w", encoding="utf-8") as f:
        json.dump({
            "media_folders": [os.path.join(tmp, "Movies"), os.path.join(tmp, "Shows")],
            "jellyfin_library": {"movie_dir": os.path.join(tmp, "Movies"), "tv_dir": os.path.join(tmp, "Shows")},
            "torrent": {"download_dir": os.path.join(tmp, "downloads")},
        }, f)
    os.environ["CONFIG_PATH"] = cfg_file
    os.environ["DATABASE_PATH"] = os.path.join(tmp, "nl.db")
    import server
    from fastapi.testclient import TestClient

    fake = FakeJellyfin()
    svc = server.jellyfin_service
    svc.base_url = "http://jellyfin.test:8096"
    svc.api_key = SECRET
    svc.user_id = "user1"
    svc.session = fake
    server.jellyfin_playback._ids = {}
    client = TestClient(server.app)
    server.db.register_source_file(FILE, "A", 2020)
    item_id = 1

    print("GET /api/nl/subtitles/{id}")
    r = client.get(f"/api/nl/subtitles/{item_id}")
    body = r.json()
    check("200 with results, English by default", r.status_code == 200 and body["language"] == "eng" and body["language_name"] == "English"
          and body["code"] == "ok", r.text[:300])
    check("exact (hash) match is first", body["results"][0]["id"] == "b3Blbg_300" and body["results"][0]["hash_match"] is True)
    check("Jellyfin was asked for Items/jf1/RemoteSearch/Subtitles/eng",
          ("GET", "/Items/jf1/RemoteSearch/Subtitles/eng") in fake.subtitle_calls(), fake.subtitle_calls())
    check("no API key in the JSON", SECRET not in r.text)
    r = client.get(f"/api/nl/subtitles/{item_id}", params={"language": "spa"})
    check("another language is passed on", r.status_code == 200 and ("GET", "/Items/jf1/RemoteSearch/Subtitles/spa") in fake.subtitle_calls())
    for bad in ("en", "ENG", "eng/..", "../x", "e%2fg", "eng;x"):
        n = len(fake.subtitle_calls())
        r = client.get(f"/api/nl/subtitles/{item_id}", params={"language": bad})
        check(f"language {bad!r} is 422 and never reaches Jellyfin", r.status_code == 422 and len(fake.subtitle_calls()) == n, r.status_code)
    check("unknown title is 404", client.get("/api/nl/subtitles/999").status_code == 404)
    check("unknown episode is 404", client.get(f"/api/nl/subtitles/{item_id}", params={"source": 99}).status_code == 404)
    check("source must be a positive number", client.get(f"/api/nl/subtitles/{item_id}", params={"source": 0}).status_code == 422)

    print("friendly errors on search")
    fake.search_body = []
    r = client.get(f"/api/nl/subtitles/{item_id}", params={"language": "eng"})
    check("no results: 200 'No subtitles found for English.'", r.status_code == 200 and r.json()["results"] == []
          and r.json()["code"] == "no_results" and r.json()["message"] == "No subtitles found for English.", r.text)
    r = client.get(f"/api/nl/subtitles/{item_id}", params={"language": "fra"})
    check("the language name is in the message", r.json()["message"] == "No subtitles found for French.", r.text)
    fake.plugins = []
    r = client.get(f"/api/nl/subtitles/{item_id}")
    check("no results and no subtitle plugin: plugin_missing (409)", r.status_code == 409 and r.json()["code"] == "plugin_missing"
          and "OpenSubtitles" in r.json()["detail"], r.text)
    fake.plugins = 403
    r = client.get(f"/api/nl/subtitles/{item_id}")
    check("Jellyfin won't list plugins: stay with plain no results", r.status_code == 200 and r.json()["code"] == "no_results", r.text)
    fake.plugins = [{"Name": "Open Subtitles", "Status": "Disabled"}]
    check("a disabled plugin counts as missing", client.get(f"/api/nl/subtitles/{item_id}").status_code == 409)
    fake.plugins = [{"Name": "Open Subtitles", "Status": "Active"}]
    fake.search_body = list(REMOTE)

    fake.search_status, fake.search_text = 401, "Unauthorized"
    r = client.get(f"/api/nl/subtitles/{item_id}")
    check("401: plugin not signed in (409)", r.status_code == 409 and r.json()["code"] == "plugin_unconfigured", r.text)
    fake.search_status, fake.search_text = 429, ""
    r = client.get(f"/api/nl/subtitles/{item_id}")
    check("429: daily limit (429)", r.status_code == 429 and r.json()["code"] == "limit" and "limit" in r.json()["detail"], r.text)
    fake.search_status, fake.search_text = 500, "Daily download limit reached"
    check("500 that mentions the limit: daily limit", client.get(f"/api/nl/subtitles/{item_id}").json()["code"] == "limit")
    fake.search_status, fake.search_text = 500, "NullReference"
    r = client.get(f"/api/nl/subtitles/{item_id}")
    check("500 with the plugin installed: plain failure (502)", r.status_code == 502 and r.json()["code"] == "failed", r.text)
    fake.plugins = []
    r = client.get(f"/api/nl/subtitles/{item_id}")
    check("500 with no plugin: plugin_missing", r.status_code == 409 and r.json()["code"] == "plugin_missing", r.text)
    fake.plugins = [{"Name": "Open Subtitles", "Status": "Active"}]
    fake.search_status, fake.search_text = 404, ""
    check("404 on search: plugin_missing", client.get(f"/api/nl/subtitles/{item_id}").json()["code"] == "plugin_missing")
    fake.search_status = 200
    fake.down = True
    server.jellyfin_playback._ids = {}
    r = client.get(f"/api/nl/subtitles/{item_id}")
    check("Jellyfin down: 503 with a message", r.status_code == 503 and r.json()["code"] == "unavailable" and "Jellyfin" in r.json()["detail"], r.text)
    fake.down = False

    print("POST /api/nl/subtitles/{id}")
    n = len(fake.calls)
    r = client.post(f"/api/nl/subtitles/{item_id}", json={"subtitle_id": "b3Blbg_300"})
    check("200 success", r.status_code == 200 and r.json() == {"success": True}, r.text)
    check("Jellyfin got POST Items/jf1/RemoteSearch/Subtitles/b3Blbg_300", ("POST", "/Items/jf1/RemoteSearch/Subtitles/b3Blbg_300") in fake.subtitle_calls())
    for bad in ("../x", "a/b", "a b", "a?x=1", "", ".", "..", "a..b", "%2e%2e", "a\\b"):
        before = len(fake.subtitle_calls())
        r = client.post(f"/api/nl/subtitles/{item_id}", json={"subtitle_id": bad})
        check(f"subtitle id {bad!r} refused, Jellyfin never asked", r.status_code in (400, 422) and len(fake.subtitle_calls()) == before, r.status_code)
    check("missing body is 422", client.post(f"/api/nl/subtitles/{item_id}", json={}).status_code == 422)
    check("unknown title is 404", client.post("/api/nl/subtitles/999", json={"subtitle_id": "abc"}).status_code == 404)
    fake.download_status = 429
    r = client.post(f"/api/nl/subtitles/{item_id}", json={"subtitle_id": "abc"})
    check("download 429: daily limit", r.status_code == 429 and r.json()["code"] == "limit", r.text)
    fake.download_status = 401
    check("download 401: plugin not signed in", client.post(f"/api/nl/subtitles/{item_id}", json={"subtitle_id": "abc"}).json()["code"] == "plugin_unconfigured")
    fake.download_status = 404
    r = client.post(f"/api/nl/subtitles/{item_id}", json={"subtitle_id": "abc"})
    check("download 404: the subtitle is gone", r.status_code == 404 and r.json()["code"] == "not_found", r.text)
    fake.download_status, fake.download_text = 500, "Download limit exceeded"
    check("download 500 about the limit: daily limit", client.post(f"/api/nl/subtitles/{item_id}", json={"subtitle_id": "abc"}).json()["code"] == "limit")
    fake.download_status, fake.download_text = 500, "boom"
    r = client.post(f"/api/nl/subtitles/{item_id}", json={"subtitle_id": "abc"})
    check("download 500: plain failure", r.status_code == 502 and r.json()["code"] == "failed", r.text)
    fake.download_status, fake.download_text = 204, ""
    fake.down = True
    check("Jellyfin down on download: 503", client.post(f"/api/nl/subtitles/{item_id}", json={"subtitle_id": "abc"}).status_code == 503)
    fake.down = False
    check("the key is only ever sent as a server-side header, never in a URL we built",
          all(SECRET not in p for (_m, p, _h) in fake.calls))

    print("routes with replaced service methods")
    real_search, real_download = server.jellyfin_playback.search_subtitles, server.jellyfin_playback.download_subtitle
    seen = {}
    server.jellyfin_playback.search_subtitles = lambda item, language: seen.update(file=item["sources"][0]["file_path"], language=language) or {
        "language": language, "language_name": "English", "results": []}
    server.jellyfin_playback.download_subtitle = lambda item, sid: seen.update(sid=sid)
    r = client.get(f"/api/nl/subtitles/{item_id}")
    check("search is given the title's file and the language", seen.get("file") == FILE and seen.get("language") == "eng", seen)
    check("empty list from the service is no_results", r.json()["code"] == "no_results")
    client.post(f"/api/nl/subtitles/{item_id}", json={"subtitle_id": "xyz_1"})
    check("download is given the id as is", seen.get("sid") == "xyz_1", seen)
    server.jellyfin_playback.search_subtitles, server.jellyfin_playback.download_subtitle = real_search, real_download

    print("a show's episode")
    server.db.register_source_file("/media/Shows/Sigma/Season 1/Sigma S01E01.mkv", "Sigma", 2020)
    with server.db.get_connection() as conn:
        rows = conn.execute("SELECT id, media_item_id FROM media_sources ORDER BY id").fetchall()
    check("registered a second file", len(rows) == 2, rows)
    show_item, show_source = rows[1][1], rows[1][0]
    fake.items.append({"Id": "jf2", "Path": "/media/Shows/Sigma/Season 1/Sigma S01E01.mkv"})
    server.jellyfin_playback._ids = {}
    r = client.get(f"/api/nl/subtitles/{show_item}", params={"source": show_source})
    check("the episode's own Jellyfin id is used (it is not in the fake, so 404 from Jellyfin = plugin_missing or not found)",
          any("jf2/RemoteSearch" in p for (_m, p) in fake.subtitle_calls()), fake.subtitle_calls()[-3:])


def test_static():
    print("static hooks (front end)")

    def read(rel):
        with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
            return f.read()

    mod = read("static/nl/subtitles.js")
    check("subtitles.js exposes NLSubtitles.open", "global.NLSubtitles = " in mod and "open: open" in mod)
    check("subtitles.js talks only to our /api/nl/subtitles route", "/api/nl/subtitles/" in mod and "/Items/" not in mod and "RemoteSearch" not in mod)
    check("subtitles.js saves the language per device", "nl.subLang" in mod)
    check("subtitles.js shows the exact-match badge", "Exact match" in mod and "hash_match" in mod)
    check("subtitles.js never builds HTML from results (textContent only)", "innerHTML" not in mod.replace("innerHTML = \"\"", ""), "innerHTML used")
    check("subtitles.js handles Back and OK for the remote", "Escape" in mod and "keyCode" in mod)

    css = read("static/nl/subtitles.css")
    check("subtitles.css has the panel and the badge", ".nls-panel" in css and ".nls-badge" in css and ".nls-tv" in css)

    core = read("static/nl/player-core.js")
    check("player-core.js: web delay shifts cue times and keeps the originals",
          "setSubtitleDelay" in core and "_nlStart" in core and "_nlEnd" in core)
    check("player-core.js: appearance via a generated ::cue rule and cue.line",
          "setSubtitleStyle" in core and "::cue" in core and ".line" in core)
    check("player-core.js: TV calls setSubtitleStyle only when the app is version 3",
          "setSubtitleStyle" in core and "Number(nativeVersion()) >= 3" in core)
    check("player-core.js: no setSubtitleDelay on the bridge (Media3 1.5.1 has no subtitle offset)",
          'call("setSubtitleDelay"' not in core and "canDelaySubtitles" in core)

    player = read("static/nl/player.js")
    for needle in ("Find subtitles", "Subtitle settings", "NLSubtitles.open", "nl.subStyle", "formatDelay", "0.25"):
        check(f"player.js has {needle!r}", needle in player)
    page = read("templates/nl/player.html")
    check("player page loads subtitles.js/css and has the panel host", "subtitles.js" in page and "subtitles.css" in page and 'id="nl-subfind"' in page)
    check("title page has a Find subtitles hook", "subtitles.js" in read("templates/nl/title.html") and "Find subtitles" in read("static/nl/title.js"))
    check("TV title page has a Find subtitles hook", "subtitles.js" in read("templates/nl/tv/base.html") and "Find subtitles" in read("static/nl/tv/tv.js"))

    kt = read("tv-app/app/src/main/java/app/northernlights/tv/NativeBridge.kt")
    check("bridge keeps setSubtitleStyle (v3) and reports version 5 (v4 setSpeed, v5 setAudioTrack)", "fun setSubtitleStyle" in kt and '"5"' in kt)
    ctl = read("tv-app/app/src/main/java/app/northernlights/tv/PlayerController.kt")
    check("PlayerController styles the PlayerView's SubtitleView",
          "CaptionStyleCompat" in ctl and "setFractionalTextSize" in ctl and "setBottomPaddingFraction" in ctl and "subtitleView" in ctl)


def test_js_behaviour():
    """The cue delay and look run in plain node (stand-in DOM); skipped where node isn't installed."""
    print("browser-side behaviour (node)")
    node = shutil.which("node")
    if not node:
        print("  (node not installed: skipped)")
        return
    out = subprocess.run([node, os.path.join(ROOT, "tests", "js", "nl_subtitles_core_check.js")], capture_output=True, text=True, timeout=60)
    check("delay, cue look, TV adapter and the +0.75 s label all behave", out.returncode == 0, (out.stdout + out.stderr)[-800:])


if __name__ == "__main__":
    main()
