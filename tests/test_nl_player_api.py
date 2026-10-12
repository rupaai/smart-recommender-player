"""
Offline tests for the Northern Lights M1 player API (tests/test_nl_player_api.py)

Jellyfin is faked (a stand-in requests session); nothing leaves the process.

    uv run python tests/test_nl_player_api.py
"""

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402
from services.jellyfin_playback import JellyfinPlayback  # noqa: E402

JellyfinPlayback.file_exists = staticmethod(lambda path: True)  # fake files: no real disk

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
    def __init__(self, status=200, json_data=None):
        self.status_code = status
        self._json = json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def json(self):
        if self._json is None:
            raise ValueError("no body")
        return self._json


STREAMS = [
    {"Index": 0, "Type": "Video", "Codec": "h264"},
    {"Index": 1, "Type": "Audio", "Codec": "eac3", "Language": "eng", "DisplayLanguage": "English",
     "Channels": 6, "IsDefault": True, "DisplayTitle": "English - EAC3 - 5.1 - Default"},
    {"Index": 2, "Type": "Audio", "Codec": "aac", "Language": "spa", "Channels": 2},
    {"Index": 3, "Type": "Audio", "Codec": "ac3", "Language": "eng", "Channels": 2, "Title": "Director's Commentary"},
    {"Index": 4, "Type": "Subtitle", "Codec": "subrip", "Language": "eng", "DisplayLanguage": "English",
     "IsTextSubtitleStream": True, "IsExternal": False, "DeliveryMethod": "External",
     "DeliveryUrl": f"/Videos/jf1/ms1/Subtitles/4/0/Stream.vtt?api_key={SECRET}"},
    {"Index": 5, "Type": "Subtitle", "Codec": "subrip", "Language": "eng", "DisplayLanguage": "English",
     "IsTextSubtitleStream": True, "IsHearingImpaired": True},
    {"Index": 6, "Type": "Subtitle", "Codec": "ass", "Language": "spa", "IsForced": True,
     "IsTextSubtitleStream": True, "IsExternal": True, "DeliveryMethod": "External"},
    {"Index": 7, "Type": "Subtitle", "Codec": "hdmv_pgs_subtitle", "Language": "fre", "IsTextSubtitleStream": False,
     "DeliveryMethod": "Embed"},
]


class FakeJellyfin:
    """Stands in for JellyfinService.session."""

    def __init__(self):
        self.items = [{"Id": "jf1", "Path": FILE}]
        self.playback_calls = []
        self.sent = []          # (method, path, json)
        self.down = False
        self.user_data = {"PlaybackPositionTicks": 25_200_000_000, "Played": False}  # 42:00
        self.item_extra = {"Type": "Movie"}
        self.mp4_direct = False

    def request(self, method, url, **kw):
        if self.down:
            raise requests.ConnectionError("down")
        path = url.split("jellyfin.test:8096", 1)[1]
        if path == "/Users" and method == "GET":  # JELLYFIN_USER_ID "user1" is looked up as a name
            return FakeResponse(json_data=[{"Name": "other", "Id": "x" * 32}, {"Name": "User1", "Id": "user1"}])
        if path == "/Items" and method == "GET":
            return FakeResponse(json_data={"Items": list(self.items)})
        if path.endswith("/PlaybackInfo") and method == "POST":
            self.playback_calls.append({"path": path, "params": kw.get("params"), "body": kw.get("json")})
            return FakeResponse(json_data=self._playback(kw.get("json")))
        if path == "/Users/user1/Items/jf1" and method == "GET":
            return FakeResponse(json_data={"Id": "jf1", "UserData": self.user_data, **self.item_extra})
        if path.startswith("/Sessions/Playing") or path.startswith("/Users/user1/PlayedItems/"):
            self.sent.append((method, path, kw.get("json")))
            return FakeResponse(204)  # no body, like Jellyfin
        return FakeResponse(404, json_data={})

    def _playback(self, body):
        ms = {"Id": "ms1", "Container": "mkv", "RunTimeTicks": 72_000_000_000, "MediaStreams": STREAMS,
              "DefaultAudioStreamIndex": 1, "SupportsDirectPlay": False, "SupportsDirectStream": True}
        if self.mp4_direct and body["EnableDirectPlay"]:
            ms.update({"Container": "mp4", "SupportsDirectPlay": True})  # plays as is, no transcode offered
            return {"PlaySessionId": "ps1", "MediaSources": [ms]}
        # otherwise an HLS transcode (a real mkv would)
        ms["TranscodingUrl"] = (
            f"/videos/jf1/master.m3u8?MediaSourceId=ms1&AudioStreamIndex={body.get('AudioStreamIndex', 1)}"
            f"&api_key={SECRET}&PlaySessionId=ps1")
        if body["EnableDirectStream"] is False:
            ms["SupportsDirectStream"] = False
        return {"PlaySessionId": "ps1", "MediaSources": [ms]}


def main():
    tmp = tempfile.mkdtemp(prefix="nl_player_api_test_")
    try:
        run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if failures:
        print(f"\n{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("\nAll Northern Lights player API tests passed")


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
    from services import jellyfin_playback as jp

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

    # ---- labels and track extraction (pure)
    print("track extraction and labels")
    t = jp.extract_tracks({"MediaStreams": STREAMS}, "jf1", "ms1")
    audio, subs = t["audio_tracks"], t["subtitle_tracks"]
    check("3 audio tracks, 4 subtitle tracks, video ignored", len(audio) == 3 and len(subs) == 4, (len(audio), len(subs)))
    check("audio label: English · 5.1 · E-AC-3", audio[0]["label"] == "English · 5.1 · E-AC-3", audio[0]["label"])
    check("audio label from language code: Spanish · Stereo · AAC", audio[1]["label"] == "Spanish · Stereo · AAC", audio[1]["label"])
    check("commentary is marked", audio[2]["label"].endswith("(Commentary)"), audio[2]["label"])
    check("audio fields", audio[0]["index"] == 1 and audio[0]["language"] == "eng" and audio[0]["codec"] == "eac3"
          and audio[0]["channels"] == 6 and audio[0]["is_default"] is True and audio[0]["is_forced"] is False, audio[0])
    check("subtitle label plain", subs[0]["label"] == "English", subs[0]["label"])
    check("subtitle label SDH", subs[1]["label"] == "English (SDH)" and subs[1]["is_hearing_impaired"] is True, subs[1]["label"])
    check("subtitle label Forced", subs[2]["label"] == "Spanish · Forced" and subs[2]["is_forced"] is True, subs[2]["label"])
    check("image subtitle label and kind", subs[3]["label"] == "French · PGS" and subs[3]["kind"] == "image"
          and subs[3]["url"] is None, subs[3])
    check("text subtitle kind and delivery fields", subs[0]["kind"] == "text" and subs[0]["delivery_method"] == "External"
          and subs[2]["is_external"] is True, subs[0])
    check("DeliveryUrl is proxied with the key stripped",
          subs[0]["url"] == "/nl/jf/Videos/jf1/ms1/Subtitles/4/0/Stream.vtt" and SECRET not in subs[0]["url"], subs[0]["url"])
    check("embedded text sub gets a built proxied .vtt url",
          subs[1]["url"] == "/nl/jf/Videos/jf1/ms1/Subtitles/5/0/Stream.vtt", subs[1]["url"])
    check("subtitle url passes the proxy allowlist", all(jp.safe_proxy_path(s["url"][len("/nl/jf/"):]) for s in subs if s["url"]))
    check("evil DeliveryUrl falls back to our own path",
          jp.extract_tracks({"MediaStreams": [{"Index": 9, "Type": "Subtitle", "Codec": "subrip", "DeliveryMethod": "External",
                                               "DeliveryUrl": "/Users/x/Items?api_key=K"}]}, "jf1", "ms1")
          ["subtitle_tracks"][0]["url"] == "/nl/jf/Videos/jf1/ms1/Subtitles/9/0/Stream.vtt")
    check("seconds_to_ticks", jp.seconds_to_ticks(1) == 10_000_000 and jp.seconds_to_ticks(42.5) == 425_000_000
          and jp.seconds_to_ticks(-3) == 0)

    # ---- GET /api/nl/play
    print("GET /api/nl/play/{id}")
    r = client.get(f"/api/nl/play/{item_id}")
    body = r.json()
    check("200 with tracks, resume and played", r.status_code == 200 and len(body["audio_tracks"]) == 3
          and len(body["subtitle_tracks"]) == 4 and body["played"] is False, r.text[:300])
    check("resume_seconds comes from UserData.PlaybackPositionTicks", body["resume_seconds"] == 2520.0, body["resume_seconds"])
    check("default audio index selected, subtitles off", body["audio_index"] == 1 and body["subtitle_index"] == -1, body)
    check("no API key in the JSON", SECRET not in r.text)
    check("plain play sends no track params",
          "AudioStreamIndex" not in fake.playback_calls[-1]["body"] and "StartTimeTicks" not in fake.playback_calls[-1]["body"],
          fake.playback_calls[-1]["body"])
    fake.user_data = {"PlaybackPositionTicks": 0, "Played": True}
    body = client.get(f"/api/nl/play/{item_id}").json()
    check("played flag from UserData", body["played"] is True and body["resume_seconds"] == 0, body)
    fake.user_data = {"PlaybackPositionTicks": 25_200_000_000, "Played": False}

    fake.item_extra = {"Type": "Episode", "SeriesName": "The Show", "ParentIndexNumber": 1, "IndexNumber": 3, "Name": "Pilot"}
    body = client.get(f"/api/nl/play/{item_id}").json()
    check("episode info for S1 · E3 · name", body["title"] == "The Show" and body["episode"] == {"season": 1, "episode": 3, "name": "Pilot"},
          (body["title"], body["episode"]))
    fake.item_extra = {"Type": "Movie"}
    check("movie has no episode block", client.get(f"/api/nl/play/{item_id}").json()["episode"] is None)

    print("audio, subtitle and start params reach PlaybackInfo")
    r = client.get(f"/api/nl/play/{item_id}?audio=2&start=1512.5")
    call = fake.playback_calls[-1]
    check("200", r.status_code == 200, r.text[:200])
    check("AudioStreamIndex in body and query", call["body"]["AudioStreamIndex"] == 2 and call["params"]["AudioStreamIndex"] == 2, call)
    check("StartTimeTicks = seconds x 10,000,000", call["body"]["StartTimeTicks"] == 15_125_000_000
          and call["params"]["StartTimeTicks"] == 15_125_000_000, call["body"])
    check("response reports the chosen audio", r.json()["audio_index"] == 2, r.json()["audio_index"])
    check("stream stays proxied, key stripped",
          r.json()["url"].startswith("/nl/jf/videos/jf1/master.m3u8") and SECRET not in r.text, r.json()["url"])
    check("non-default audio makes Jellyfin transcode (direct play off)",
          any(c["body"]["EnableDirectPlay"] is False for c in fake.playback_calls[-2:]) or r.json()["kind"] == "hls")

    r = client.get(f"/api/nl/play/{item_id}?subtitle=7&start=60")
    call = fake.playback_calls[-1]
    check("SubtitleStreamIndex reaches PlaybackInfo", call["body"]["SubtitleStreamIndex"] == 7
          and call["params"]["SubtitleStreamIndex"] == 7, call["body"])
    check("burn-in asks for Encode on picture formats",
          {"Format": "hdmv_pgs_subtitle", "Method": "Encode"} in call["body"]["DeviceProfile"]["SubtitleProfiles"]
          and {"Format": "dvdsub", "Method": "Encode"} in call["body"]["DeviceProfile"]["SubtitleProfiles"],
          call["body"]["DeviceProfile"]["SubtitleProfiles"])
    check("burn-in response reports subtitle index", r.json()["subtitle_index"] == 7, r.json()["subtitle_index"])
    r = client.get(f"/api/nl/play/{item_id}?subtitle=-1")
    check("subtitle=-1 (off) is passed on, no Encode profiles",
          fake.playback_calls[-1]["body"]["SubtitleStreamIndex"] == -1
          and len(fake.playback_calls[-1]["body"]["DeviceProfile"]["SubtitleProfiles"]) == 1)
    from services.device_profiles import BROWSER_PROFILE
    check("BROWSER_PROFILE subtitle profiles unchanged", BROWSER_PROFILE["SubtitleProfiles"] == [{"Format": "vtt", "Method": "External"}])
    check("bad params are 422", client.get(f"/api/nl/play/{item_id}?audio=-2").status_code == 422
          and client.get(f"/api/nl/play/{item_id}?start=-1").status_code == 422)

    print("direct-play file asked for another audio track")
    fake.mp4_direct = True
    n = len(fake.playback_calls)
    r = client.get(f"/api/nl/play/{item_id}")
    check("default audio keeps direct play", r.json()["kind"] == "file" and r.json()["play_method"] == "DirectPlay"
          and len(fake.playback_calls) == n + 1, r.json())
    n = len(fake.playback_calls)
    r = client.get(f"/api/nl/play/{item_id}?audio=2")
    check("other audio retries with direct play off and gets HLS",
          len(fake.playback_calls) == n + 2 and fake.playback_calls[-1]["body"]["EnableDirectPlay"] is False
          and fake.playback_calls[-1]["body"]["EnableDirectStream"] is True and r.json()["kind"] == "hls", r.json())
    fake.mp4_direct = False

    # ---- progress
    print("POST /api/nl/progress")
    base = {"jellyfin_id": "jf1", "play_session_id": "ps1", "media_source_id": "ms1",
            "position_seconds": 42 * 60 + 10, "paused": False, "play_method": "Transcode"}
    expected_paths = {"start": "/Sessions/Playing", "progress": "/Sessions/Playing/Progress", "stop": "/Sessions/Playing/Stopped"}
    for event, path in expected_paths.items():
        fake.sent.clear()
        r = client.post("/api/nl/progress", json={**base, "event": event, "paused": event == "progress"})
        method, sent_path, sent = fake.sent[-1] if fake.sent else (None, None, None)
        check(f"{event}: 200, POST {path}", r.status_code == 200 and method == "POST" and sent_path == path, f"{r.status_code} {fake.sent}")
        check(f"{event}: seconds converted to ticks", sent and sent["PositionTicks"] == 25_300_000_000, sent)
        check(f"{event}: ids, pause flag and play method", sent and sent["ItemId"] == "jf1" and sent["MediaSourceId"] == "ms1"
              and sent["PlaySessionId"] == "ps1" and sent["IsPaused"] is (event == "progress") and sent["PlayMethod"] == "Transcode", sent)
    check("unknown event is 422", client.post("/api/nl/progress", json={**base, "event": "explode"}).status_code == 422)
    check("negative position is 422", client.post("/api/nl/progress", json={**base, "event": "start", "position_seconds": -1}).status_code == 422)
    check("bad item id is 400", client.post("/api/nl/progress", json={**base, "event": "start", "jellyfin_id": "../x"}).status_code == 400)
    fake.sent.clear()
    r = client.post("/api/nl/progress", content=json.dumps({**base, "event": "stop"}), headers={"Content-Type": "application/json"})
    check("works with a beacon-style JSON body", r.status_code == 200 and fake.sent, r.status_code)
    fake.down = True
    r = client.post("/api/nl/progress", json={**base, "event": "progress"})
    check("503 when Jellyfin is down", r.status_code == 503 and SECRET not in r.text, r.status_code)
    fake.down = False

    # ---- played marks
    print("played / unplayed")
    fake.sent.clear()
    r = client.post("/api/nl/played/jf1")
    check("POST marks played", r.status_code == 200 and fake.sent[-1][:2] == ("POST", "/Users/user1/PlayedItems/jf1"), fake.sent)
    r = client.delete("/api/nl/played/jf1")
    check("DELETE marks unplayed", r.status_code == 200 and fake.sent[-1][:2] == ("DELETE", "/Users/user1/PlayedItems/jf1"), fake.sent)
    n = len(fake.sent)
    check("bad id is refused before Jellyfin", client.post("/api/nl/played/a%20b").status_code == 400 and len(fake.sent) == n)
    fake.down = True
    check("503 when Jellyfin is down", client.post("/api/nl/played/jf1").status_code == 503)
    fake.down = False

    # ---- library
    print("GET /api/nl/library")
    server.db.register_source_file("/media/Movies/B (2021)/B.mp4", "B", 2021)
    with server.db.get_connection() as conn:
        conn.execute("UPDATE media_items SET title='Beta', release_year=2021, runtime=95, poster_url='https://img.test/b.jpg' WHERE id=2;")
        conn.execute("UPDATE media_items SET title='alpha', release_year=2020, runtime=120 WHERE id=1;")
        conn.execute("INSERT INTO media_items (media_type, tmdb_id, title) VALUES ('movie', 99, 'No file');")
        conn.execute("INSERT INTO media_items (media_type, tmdb_id, title) VALUES ('tv', 5, 'A show');")
        conn.commit()
    r = client.get("/api/nl/library?type=movie")
    items = r.json()["items"]
    check("only titles with a file, sorted by title", [i["title"] for i in items] == ["alpha", "Beta"], items)
    check("card fields: id, title, year, runtime, poster_url, href",
          all(items[1].get(k) == v for k, v in {"id": 2, "title": "Beta", "year": 2021, "runtime": 95,
                                                "poster_url": "https://img.test/b.jpg", "href": "/nl/title/2"}.items()), items[1])
    check("default type is movie", client.get("/api/nl/library").json() == r.json())
    check("bad type is 422", client.get("/api/nl/library?type=x").status_code == 422)

    # ---- pages
    print("pages")
    r = client.get("/nl/")
    check("/nl/ serves the home page", r.status_code == 200 and "home.js" in r.text and SECRET not in r.text, r.status_code)
    r = client.get("/nl/play/1")
    check("player page has the controls and no native controls attribute",
          r.status_code == 200 and "nl-seek" in r.text and "player.js" in r.text and "<video id=\"nl-video\" playsinline>" in r.text, r.text[:200])
    for f in ("player.js", "player.css", "player-core.js", "nl.js", "nl.css"):
        check(f"static/nl/{f} served", client.get(f"/static/nl/{f}").status_code == 200)

    # ---- S4: Jellyfin down -> 503 with a plain message (the page shows "Is mars on?")
    print("server unreachable")
    fake.down = True
    server.jellyfin_playback._ids = {}
    r = client.get(f"/api/nl/play/{item_id}")
    check("play is 503 with a short message, no key", r.status_code == 503 and "Jellyfin" in r.json()["detail"] and SECRET not in r.text, r.text)
    fake.down = False
    js = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static/nl/player.js"), encoding="utf-8").read()
    check("player.js has the S4 message", "reach the movie server. Is mars on?" in js)

    # ---- JELLYFIN_USER_ID may be a user name (the Mac's .env had one); it's resolved once
    print("user id resolution")
    hexid = "0123456789abcdef0123456789abcdef"

    class UsersOnly:
        calls = 0

        def request(self, method, url, **kw):
            UsersOnly.calls += 1
            return FakeResponse(json_data=[{"Name": "labiba", "Id": hexid}])

    class Svc:
        configured = True
        base_url = "http://jellyfin.test:8096"
        session = UsersOnly()
        user_id = "Labiba"

    pb = jp.JellyfinPlayback(Svc())
    check("a user name resolves to its id", pb._user_id() == hexid, pb._user_id())
    pb._user_id()
    check("the lookup happens once", UsersOnly.calls == 1, UsersOnly.calls)
    Svc.user_id = hexid
    UsersOnly.calls = 0
    check("a real id is used as is, no lookup", jp.JellyfinPlayback(Svc())._user_id() == hexid and UsersOnly.calls == 0)
    Svc.user_id = "nobody"
    try:
        jp.JellyfinPlayback(Svc())._user_id()
        check("an unknown name gives a clear error", False)
    except jp.JellyfinUnavailable as e:
        check("an unknown name gives a clear error", "JELLYFIN_USER_ID" in str(e), str(e))


if __name__ == "__main__":
    main()
