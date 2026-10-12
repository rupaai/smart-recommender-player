"""
Offline tests for Northern Lights M3a: the TV profile, client=tv selection and the /nl/tv/ pages
(tests/test_nl_tv.py). Jellyfin is faked (tests/nl_fake.py); nothing leaves the process.

    uv run python tests/test_nl_tv.py
"""

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nl_fake  # noqa: E402
from nl_fake import SECRET  # noqa: E402

from services.device_profiles import BROWSER_PROFILE, TV_PROFILE, browser_profile, parse_caps, profile_for  # noqa: E402

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} — got: {detail}")
        failures.append(label)


def split(value):
    return [x.strip() for x in value.split(",")]


def test_profile_content():
    print("TV_PROFILE content")
    direct = TV_PROFILE["DirectPlayProfiles"]
    check("one direct play profile", len(direct) == 1, direct)
    d = direct[0]
    check("mkv, mp4, m4v, mov, webm, ts direct play",
          set(split(d["Container"])) == {"mkv", "mp4", "m4v", "mov", "webm", "ts"}, d["Container"])
    check("video h264, hevc, vp9, av1", set(split(d["VideoCodec"])) == {"h264", "hevc", "vp9", "av1"}, d["VideoCodec"])
    check("audio aac, mp3, ac3, eac3, opus, flac, vorbis",
          set(split(d["AudioCodec"])) == {"aac", "mp3", "ac3", "eac3", "opus", "flac", "vorbis"}, d["AudioCodec"])
    check("mkv + hevc is direct play", "mkv" in split(d["Container"]) and "hevc" in split(d["VideoCodec"]))
    everything = json.dumps(TV_PROFILE).lower()
    check("no dts and no truehd anywhere", "dts" not in everything.replace("dvdsub", "") and "truehd" not in everything)
    t = TV_PROFILE["TranscodingProfiles"]
    check("transcode: HLS fMP4, hevc/h264 + aac/ac3/eac3, 6 channels",
          len(t) == 1 and t[0]["Protocol"] == "hls" and t[0]["Container"] == "mp4"
          and set(split(t[0]["VideoCodec"])) == {"hevc", "h264"}
          and set(split(t[0]["AudioCodec"])) == {"aac", "ac3", "eac3"} and t[0]["MaxAudioChannels"] == "6", t)
    check("120 Mbps ceiling", TV_PROFILE["MaxStreamingBitrate"] == 120_000_000)
    subs = TV_PROFILE["SubtitleProfiles"]
    ext = {s["Format"] for s in subs if s["Method"] == "External"}
    emb = {s["Format"] for s in subs if s["Method"] == "Embed"}
    enc = {s["Format"] for s in subs if s["Method"] == "Encode"}
    check("text subtitles External", {"srt", "subrip", "vtt", "ass", "ssa"} <= ext, ext)
    check("picture subtitles Embed (direct play) and Encode (transcode)",
          {"pgssub", "dvdsub", "dvbsub"} <= emb and {"pgssub", "dvdsub", "dvbsub"} <= enc, (emb, enc))
    check("picture subtitles are not External", not ({"pgssub", "dvdsub", "dvbsub"} & ext))
    hevc = TV_PROFILE["CodecProfiles"][0]
    check("hevc limited to main / main 10", hevc["Codec"] == "hevc" and "main 10" in hevc["Conditions"][0]["Value"], hevc)

    print("profile_for")
    caps = "v=h264.hevc;a=aac;h=h264.hevc.aac"
    check("client=tv gives TV_PROFILE, caps ignored", profile_for("tv", caps) is TV_PROFILE and profile_for("tv", None) is TV_PROFILE)
    check("no client + caps gives the device-aware browser profile", profile_for(None, caps) == browser_profile(parse_caps(caps)))
    check("no client, no caps gives BROWSER_PROFILE", profile_for(None, None) == BROWSER_PROFILE)


def run(tmp):
    ctx = nl_fake.boot(tmp)
    client, fake, ids = ctx["client"], ctx["fake"], ctx["ids"]

    seen = []
    original = fake.request

    def recording(method, url, **kw):
        if method == "POST" and url.endswith("/PlaybackInfo"):
            seen.append(kw["json"]["DeviceProfile"])
        return original(method, url, **kw)

    fake.request = recording
    alpha = ids["Alpha"]

    print("GET /api/nl/play profile selection")
    caps = "v=h264.hevc;a=aac;h=h264.hevc.aac"
    r = client.get(f"/api/nl/play/{alpha}", params={"client": "tv"})
    check("client=tv: 200 and the TV profile was sent to Jellyfin", r.status_code == 200 and seen and seen[-1] == TV_PROFILE,
          (r.status_code, r.text[:200]))
    check("no API key in the response", SECRET not in r.text)
    r = client.get(f"/api/nl/play/{alpha}", params={"client": "tv", "caps": caps})
    check("client=tv beats caps", r.status_code == 200 and seen[-1] == TV_PROFILE)
    r = client.get(f"/api/nl/play/{alpha}", params={"caps": caps})
    check("caps alone: browser profile with hevc", r.status_code == 200 and seen[-1] == browser_profile(parse_caps(caps))
          and seen[-1] != TV_PROFILE)
    r = client.get(f"/api/nl/play/{alpha}")
    check("neither: today's BROWSER_PROFILE", r.status_code == 200 and seen[-1] == BROWSER_PROFILE)
    for bad in ("android", "TV", "tvx", "", "tv;x"):
        r = client.get(f"/api/nl/play/{alpha}", params={"client": bad})
        check(f"client={bad!r} rejected with 422", r.status_code == 422, r.status_code)

    print("/nl/tv/ pages")
    show_id = ids["Sigma"]
    pages = {
        "/nl/tv/": ["tv-continue-list", "tv-movies-list", "tv-shows-list", "/static/nl/tv/focus.js", "/static/nl/tv/tv.js", "/static/nl/tv/tv.css"],
        "/nl/tv/search": ["tv-q", "data-nl-input", "/static/nl/tv/focus.js"],
        f"/nl/tv/title/{alpha}": [f'data-item-id="{alpha}"', "tv-detail"],
        f"/nl/tv/show/{show_id}": [f'data-item-id="{show_id}"', "tv-detail"],
    }
    for path, needles in pages.items():
        r = client.get(path)
        check(f"GET {path} serves HTML", r.status_code == 200 and "text/html" in r.headers.get("content-type", ""), r.status_code)
        check(f"  {path} has its markup and scripts", all(n in r.text for n in needles), [n for n in needles if n not in r.text])
        check(f"  {path} has no API key", SECRET not in r.text)
    check("title page with a non-number id is 422", client.get("/nl/tv/title/abc").status_code == 422)
    check("phone pages are still there", client.get("/nl/").status_code == 200 and client.get(f"/nl/play/{alpha}").status_code == 200)

    print("static files")
    for f in ("focus.js", "tv.js", "tv.css"):
        r = client.get(f"/static/nl/tv/{f}")
        check(f"/static/nl/tv/{f} served", r.status_code == 200 and len(r.text) > 200, r.status_code)
        check(f"  {f} has no API key", SECRET not in r.text)
        check(f"  {f} is revalidated on every use (Cache-Control: no-cache)", r.headers.get("cache-control") == "no-cache",
              r.headers.get("cache-control"))
    css = client.get("/static/nl/tv/tv.css").text
    check("tv.css scales 16px per rem at 1920x1080 with valid CSS (no px * px)",
          "min(100vw / 120, 100vh / 67.5)" in css and "1080) * 16px" not in css)
    r = client.get("/static/nl/player-core.js")
    check("player-core.js carries the NativeAdapter contract",
          r.status_code == 200 and "NorthernLightsNative" in r.text and "NLNativeEvents" in r.text)

    print("data the TV pages read (same APIs as M2)")
    home = client.get("/api/nl/home")
    check("/api/nl/home", home.status_code == 200 and "continue_watching" in home.json() and SECRET not in home.text)
    lib = client.get("/api/nl/library", params={"type": "movie"})
    check("/api/nl/library", lib.status_code == 200 and len(lib.json()["items"]) >= 2 and SECRET not in lib.text)


def main():
    test_profile_content()
    tmp = tempfile.mkdtemp(prefix="nl_tv_test_")
    try:
        run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if failures:
        print(f"\n{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("\nAll Northern Lights TV tests passed")


if __name__ == "__main__":
    main()
