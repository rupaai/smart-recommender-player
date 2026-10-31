"""
Offline tests for the device-aware browser profile (tests/test_nl_device_profile.py)

parse_caps / browser_profile are pure; the API test fakes Jellyfin and records the
DeviceProfile the page's `caps` produced.

    uv run python tests/test_nl_device_profile.py
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

from services.device_profiles import BROWSER_PROFILE, browser_profile, parse_caps  # noqa: E402

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


class FakeJellyfin:
    def __init__(self):
        self.profiles = []

    def request(self, method, url, **kw):
        path = url.split("jellyfin.test:8096", 1)[1]
        if path == "/Users" and method == "GET":  # JELLYFIN_USER_ID "user1" is looked up as a name
            return FakeResponse(json_data=[{"Name": "User1", "Id": "user1"}])
        if path == "/Items" and method == "GET":
            return FakeResponse(json_data={"Items": [{"Id": "jf1", "Path": FILE}]})
        if path.endswith("/PlaybackInfo") and method == "POST":
            self.profiles.append(kw["json"]["DeviceProfile"])
            ms = {"Id": "ms1", "Container": "mkv", "RunTimeTicks": 1, "DefaultAudioStreamIndex": 1,
                  "SupportsDirectPlay": False, "SupportsDirectStream": True,
                  "MediaStreams": [{"Index": 0, "Type": "Video", "Codec": "hevc"}],
                  "TranscodingUrl": f"/videos/jf1/master.m3u8?api_key={SECRET}&PlaySessionId=ps1"}
            return FakeResponse(json_data={"PlaySessionId": "ps1", "MediaSources": [ms]})
        if path.startswith("/Users/user1/Items/"):
            return FakeResponse(json_data={"Id": "jf1", "Type": "Movie", "UserData": {}})
        return FakeResponse(404, json_data={})


def direct_video(profile, container):
    return [p for p in profile["DirectPlayProfiles"] if container in p["Container"].split(",")]


def run_pure():
    print("parse_caps")
    caps = parse_caps("v=h264.hevc;a=aac.mp3;h=h264.hevc.aac")
    check("valid string", caps == {"video": ["h264", "hevc"], "audio": ["aac", "mp3"], "hls": ["h264", "hevc", "aac"]}, caps)
    caps = parse_caps("v=h264.bogus.HEVC.h264;a=evil;x=1;h=")
    check("unknown tokens and keys dropped, case folded, deduped",
          caps == {"video": ["h264", "hevc"], "audio": [], "hls": []}, caps)
    check("None, empty and garbage give None",
          parse_caps(None) is None and parse_caps("") is None and parse_caps("garbage") is None
          and parse_caps("v=bogus") is None and parse_caps(";;=;.") is None)
    check("overlong gives None", parse_caps("v=" + "h264." * 100) is None)

    print("browser_profile")
    check("None gives exactly BROWSER_PROFILE", browser_profile(None) is BROWSER_PROFILE or browser_profile(None) == BROWSER_PROFILE)
    snapshot = json.dumps(BROWSER_PROFILE, sort_keys=True)
    hevc = browser_profile(parse_caps("v=h264.hevc;a=aac.mp3;h=h264.hevc.aac"))
    first = hevc["TranscodingProfiles"][0]
    check("hevc in hls: first transcoding profile is fMP4 HLS, hevc first",
          first["Container"] == "mp4" and first["Protocol"] == "hls" and first["VideoCodec"].split(",")[0] == "hevc"
          and first["BreakOnNonKeyFrames"] is True and "aac" in first["AudioCodec"], first)
    ts = [p for p in hevc["TranscodingProfiles"] if p["Container"] == "ts"]
    check("ts h264/aac fallback still present", len(ts) == 1 and ts[0]["VideoCodec"] == "h264" and ts[0]["AudioCodec"] == "aac", ts)
    mp4 = direct_video(hevc, "mp4")
    check("direct play mp4 includes hevc", len(mp4) == 1 and "hevc" in mp4[0]["VideoCodec"].split(","), mp4)
    check("no mkv direct play", not direct_video(hevc, "mkv"))
    plain = browser_profile(parse_caps("v=h264;a=aac;h=h264.aac"))
    check("no hevc: no mp4 transcoding profile, no hevc direct play",
          all(p["Container"] != "mp4" for p in plain["TranscodingProfiles"])
          and all("hevc" not in p["VideoCodec"] for p in plain["DirectPlayProfiles"]), plain)
    webm = browser_profile(parse_caps("v=h264.vp9.av1;a=aac;h=h264.aac"))
    check("webm direct play vp9,av1 when supported",
          direct_video(webm, "webm") and direct_video(webm, "webm")[0]["VideoCodec"] == "vp9,av1", webm["DirectPlayProfiles"])
    check("subtitle profiles unchanged", hevc["SubtitleProfiles"] == BROWSER_PROFILE["SubtitleProfiles"])
    check("BROWSER_PROFILE was not mutated", json.dumps(BROWSER_PROFILE, sort_keys=True) == snapshot)


def run_api(tmp):
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
    from web import state  # noqa: E402
    from fastapi.testclient import TestClient

    fake = FakeJellyfin()
    svc = state.jellyfin_service
    svc.base_url = "http://jellyfin.test:8096"
    svc.api_key = SECRET
    svc.user_id = "user1"
    svc.session = fake
    state.jellyfin_playback._ids = {}
    client = TestClient(server.app)
    state.db.register_source_file(FILE, "A", 2020)

    print("GET /api/nl/play with caps")
    r = client.get("/api/nl/play/1", params={"caps": "v=h264.hevc;a=aac;h=h264.hevc.aac"})
    check("200 and no API key in response", r.status_code == 200 and SECRET not in r.text, r.text[:200])
    check("video_codec returned", r.json().get("video_codec") == "hevc", r.json().get("video_codec"))
    prof = fake.profiles[-1]
    check("posted profile has the fMP4 hevc transcoding profile first",
          prof["TranscodingProfiles"][0]["Container"] == "mp4" and "hevc" in prof["TranscodingProfiles"][0]["VideoCodec"], prof["TranscodingProfiles"])
    r = client.get("/api/nl/play/1")
    check("without caps: today's profile", r.status_code == 200 and fake.profiles[-1] == BROWSER_PROFILE
          and "hevc" not in json.dumps(fake.profiles[-1]))
    r = client.get("/api/nl/play/1", params={"caps": "nonsense"})
    check("garbage caps: today's profile", r.status_code == 200 and fake.profiles[-1] == BROWSER_PROFILE)
    r = client.get("/api/nl/play/1", params={"caps": "v=" + "h264." * 100})
    check("overlong caps (over 300) rejected cleanly", r.status_code == 422, r.status_code)


def main():
    run_pure()
    tmp = tempfile.mkdtemp(prefix="nl_device_profile_test_")
    try:
        run_api(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if failures:
        print(f"\n{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("\nAll Northern Lights device profile tests passed")


if __name__ == "__main__":
    main()
