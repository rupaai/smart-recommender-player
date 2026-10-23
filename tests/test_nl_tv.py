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
    check("audio: the old list plus truehd, mlp, dts, dca, pcm and alac (the app decodes them)",
          set(split(d["AudioCodec"])) == {"aac", "mp3", "ac3", "eac3", "opus", "flac", "vorbis", "truehd", "mlp", "dts", "dca",
                                          "pcm_s16le", "pcm_s24le", "alac"}, d["AudioCodec"])
    check("mkv + hevc is direct play", "mkv" in split(d["Container"]) and "hevc" in split(d["VideoCodec"]))
    t = TV_PROFILE["TranscodingProfiles"]
    check("transcode: HLS fMP4, hevc/h264 + aac/ac3/eac3, 6 channels",
          len(t) == 1 and t[0]["Protocol"] == "hls" and t[0]["Container"] == "mp4"
          and set(split(t[0]["VideoCodec"])) == {"hevc", "h264"}
          and set(split(t[0]["AudioCodec"])) == {"aac", "ac3", "eac3"} and t[0]["MaxAudioChannels"] == "6", t)
    check("400 Mbps ceiling (the original is never limited)",
          TV_PROFILE["MaxStreamingBitrate"] == 400_000_000 and TV_PROFILE["MaxStaticBitrate"] == 400_000_000)
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


TRUEHD_STREAMS = [
    {"Type": "Video", "Index": 0, "Codec": "hevc", "VideoRangeType": "DOVIWithHDR10"},
    {"Type": "Audio", "Index": 1, "Codec": "truehd", "Language": "eng", "Channels": 8, "IsDefault": True, "DisplayTitle": "English - TrueHD Atmos - 7.1"},
    {"Type": "Audio", "Index": 2, "Codec": "ac3", "Language": "ita", "Channels": 6, "DisplayTitle": "Italian - AC3 - 5.1"},
    {"Type": "Audio", "Index": 4, "Codec": "dts", "Language": "fra", "Channels": 6, "DisplayTitle": "French - DTS - 5.1"},
    {"Type": "Subtitle", "Index": 3, "Codec": "PGSSUB", "Language": "eng", "IsGraphicalSubtitleStream": True, "DisplayTitle": "English - PGS"},
]


MIXED_SUBTITLES = [
    {"Type": "Subtitle", "Index": 3, "Codec": "PGSSUB", "Language": "eng", "IsGraphicalSubtitleStream": True, "DisplayTitle": "English - PGS"},
    {"Type": "Subtitle", "Index": 5, "Codec": "subrip", "Language": "eng", "IsTextSubtitleStream": True, "DisplayTitle": "English - SRT"},
    {"Type": "Subtitle", "Index": 6, "Codec": "PGSSUB", "Language": "ita", "IsGraphicalSubtitleStream": True, "DisplayTitle": "Italian - PGS"},
    {"Type": "Subtitle", "Index": 7, "Codec": "subrip", "Language": "ita", "IsTextSubtitleStream": True, "IsExternal": True,
     "DeliveryMethod": "External", "DeliveryUrl": "/Videos/1/ms/Subtitles/7/0/Stream.srt", "DisplayTitle": "Italian - external"},
    {"Type": "Subtitle", "Index": 8, "Codec": "PGSSUB", "Language": "eng", "IsForced": True, "IsGraphicalSubtitleStream": True, "DisplayTitle": "English - PGS forced"},
]


class OriginalFileJellyfin(nl_fake.FakeJellyfin):
    """A TrueHD + Dolby Vision 7 + PGS file. `mode` "direct" offers the file itself; "convert" only an HLS conversion."""

    def __init__(self):
        super().__init__()
        self.mode = "direct"
        self.bodies = []
        self.streams = TRUEHD_STREAMS

    def request(self, method, url, **kw):
        path = url.split("jellyfin.test:8096", 1)[1] if "jellyfin.test:8096" in url else url
        if path.endswith("/PlaybackInfo") and method == "POST":
            body = kw.get("json") or {}
            self.bodies.append(body)
            jf = path.split("/")[2]
            base = {"Id": "ms-" + jf, "Container": "mkv", "RunTimeTicks": 50 * 60 * nl_fake.TICKS, "Bitrate": 92_000_000,
                    "DefaultAudioStreamIndex": 1, "MediaStreams": self.streams}
            wants_conversion = body.get("AudioStreamIndex") not in (None, 1) or (body.get("SubtitleStreamIndex") or -1) >= 0
            if self.mode == "direct" and not wants_conversion:
                ms = {**base, "SupportsDirectPlay": True, "SupportsDirectStream": True}
            else:
                url_ = f"/videos/{jf}/master.m3u8?MediaSourceId=ms-{jf}&TranscodeReasons=AudioCodecNotSupported"
                ms = {**base, "SupportsDirectPlay": False, "SupportsDirectStream": False, "TranscodingUrl": url_}
            return nl_fake.FakeResponse(json_data={"PlaySessionId": "ps-" + jf, "MediaSources": [ms]})
        return super().request(method, url, **kw)


def run_original_file(ctx):
    print("The TV always plays the original file")
    server, client, ids = ctx["server"], ctx["client"], ctx["ids"]
    fake = OriginalFileJellyfin()
    server.jellyfin_service.session = fake
    server.jellyfin_playback._ids = {}
    server.jellyfin_playback._last_refresh = None
    alpha = ids["Alpha"]

    def play(**params):
        fake.bodies.clear()
        return client.get(f"/api/nl/play/{alpha}", params=params)

    r = play(client="tv")
    d = r.json()
    check("TrueHD + DV7 + PGS: direct play of the static file", r.status_code == 200 and d["kind"] == "file" and d["play_method"] == "DirectPlay"
          and "static=true" in d["url"], r.text[:300])
    check("... audio_order 0 for the default track", d["audio_index"] == 1 and d["audio_order"] == 0, (d.get("audio_index"), d.get("audio_order")))
    check("... all audio tracks listed, in file order", [t["index"] for t in d["audio_tracks"]] == [1, 2, 4], d["audio_tracks"])
    check("... the TV profile allowed 400 Mbps", fake.bodies[0]["DeviceProfile"]["MaxStreamingBitrate"] == 400_000_000)

    r = play(client="tv", audio=2)
    d = r.json()
    check("a non-default audio track: still the static file", r.status_code == 200 and d["kind"] == "file" and "static=true" in d["url"]
          and "master.m3u8" not in d["url"], r.text[:300])
    check("... with the right audio_order", d["audio_index"] == 2 and d["audio_order"] == 1, (d.get("audio_index"), d.get("audio_order")))
    check("... and the track choice never reached Jellyfin", all("AudioStreamIndex" not in b for b in fake.bodies), fake.bodies)
    r = play(client="tv", audio=4)
    check("the third audio track has audio_order 2 (not its stream index 4)", r.json()["audio_order"] == 2, r.text[:300])

    r = play(client="tv", audio=2, subtitle=3)
    d = r.json()
    check("a picture subtitle is not burned in for the TV", r.status_code == 200 and d["kind"] == "file" and d["subtitle_index"] == -1
          and all("SubtitleStreamIndex" not in b for b in fake.bodies), r.text[:300])

    r = play(client="tv", max_kbps=1000)
    check("the viewer's quality cap does not lower the TV's original", r.status_code == 200 and r.json()["kind"] == "file"
          and fake.bodies[0]["DeviceProfile"]["MaxStreamingBitrate"] == 400_000_000, r.text[:300])

    r = play(caps="v=h264.hevc;a=aac;h=h264.hevc.aac", audio=2)
    d = r.json()
    check("the browser still converts for a non-default audio track", r.status_code == 200 and d["kind"] == "hls" and "audio_order" not in d, r.text[:300])

    print("Disc subtitles: drawn by the app, never burned in")
    r = play(client="tv")
    d = r.json()
    check("a PGS-only film on the TV: one embedded track with order 0, no url",
          d["subtitle_tracks"] and d["subtitle_tracks"][0]["kind"] == "embedded" and d["subtitle_tracks"][0]["order"] == 0
          and d["subtitle_tracks"][0]["url"] is None, d["subtitle_tracks"])
    check("... and a direct-play file URL", d["kind"] == "file" and d["play_method"] == "DirectPlay" and "static=true" in d["url"])
    r = client.put("/api/nl/prefs", json={"audio_lang": "eng", "subtitle_lang": "eng", "subtitles_on": True})
    check("prefs set (English, subtitles on)", r.status_code == 200, r.text[:200])
    r = play(client="tv")
    d = r.json()
    check("subtitles on by default: the embedded picture track is the default, not burned in",
          r.status_code == 200 and d["kind"] == "file" and d["default_subtitle_index"] == 3 and d["subtitle_index"] == -1
          and all("SubtitleStreamIndex" not in b for b in fake.bodies), r.text[:300])
    r = play(caps="v=h264.hevc;a=aac;h=h264.hevc.aac")
    d = r.json()
    check("the browser still burns the picture subtitle in",
          r.status_code == 200 and d["kind"] == "hls" and d["subtitle_index"] == 3 and d["subtitle_tracks"][0]["kind"] == "image"
          and "order" not in d["subtitle_tracks"][0], r.text[:300])

    fake.streams = TRUEHD_STREAMS[:4] + MIXED_SUBTITLES
    r = play(client="tv")
    d = r.json()
    tracks = {t["index"]: t for t in d["subtitle_tracks"]}
    check("a text track in the viewer's language comes before the picture track", d["default_subtitle_index"] == 5, d["default_subtitle_index"])
    check("embedded picture tracks count every subtitle stream inside the file, text ones too",
          [tracks[i].get("order") for i in (3, 6, 8)] == [0, 2, 3] and tracks[5]["kind"] == "text" and "order" not in tracks[5],
          {i: (t["kind"], t.get("order")) for i, t in tracks.items()})
    check("an external subtitle file is not inside the file: no order, keeps its url",
          tracks[7]["kind"] == "text" and "order" not in tracks[7] and tracks[7]["url"].endswith("Stream.vtt"), tracks[7])
    client.put("/api/nl/prefs", json={"subtitles_on": False})
    r = play(client="tv")
    check("subtitles off by default and the audio is in the viewer's language: no forced track", r.json()["default_subtitle_index"] == -1, r.text[:200])
    r = play(client="tv", audio=2)
    check("the audio is Italian and the viewer is English: the forced English track switches on",
          r.json()["default_subtitle_index"] == 8, r.json()["default_subtitle_index"])
    client.put("/api/nl/prefs", json={"audio_lang": None, "subtitle_lang": None, "subtitles_on": None})
    fake.streams = TRUEHD_STREAMS

    fake.mode = "convert"
    r = play(client="tv")
    d = r.json()
    check("no original from Jellyfin: 415 naming the reason, with the converted-copy flag",
          r.status_code == 415 and d.get("can_convert") is True and d["detail"].startswith("This TV can't play the original file as it is")
          and "AudioCodecNotSupported" in d["detail"], r.text[:300])
    r = play(client="tv", allow_conversion=1)
    d = r.json()
    check("with allow_conversion: the converted stream", r.status_code == 200 and d["kind"] == "hls" and "master.m3u8" in d["url"], r.text[:300])
    r = play(caps="v=h264;a=aac;h=h264.aac")
    check("the browser never gets the flag", "can_convert" not in r.text, r.text[:200])


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
    check("player-core.js drives bridge v2 (pause, resume, seekTo, setSubtitle, version, time events)",
          all(n in r.text for n in ('"pause"', '"resume"', '"seekTo"', '"setSubtitle"', "version()", 'case "time"')),
          [n for n in ('"pause"', '"resume"', '"seekTo"', '"setSubtitle"', "version()", 'case "time"') if n not in r.text])
    check("player-core.js drives bridge v5 (setAudioTrack, audioOrder)", all(n in r.text for n in ('"setAudioTrack"', "audioOrder")))
    check("player-core.js drives bridge v6 (stats, embedded subtitle order)",
          all(n in r.text for n in ("stats()", "canDrawEmbeddedSubtitles", "o.order = t.order", "nativeVersion: v6 ? 6")))
    js_v6 = client.get("/static/nl/player.js").text
    check("player.js: Playback info panel for the TV (v6 only), embedded subtitles chosen without a reload",
          "Playback info" in js_v6 and "canShowStats" in js_v6 and 'track.kind === "embedded"' in js_v6 and "canDrawEmbeddedSubtitles" in js_v6)
    page = client.get(f"/nl/play/{alpha}?tv=1")
    check("the player page has the hidden converted-copy button", 'id="nl-convert"' in page.text and "Play a converted copy (lower quality)" in page.text, page.status_code)
    js = client.get("/static/nl/player.js").text
    check("player.js sends allow_conversion only after the button and shows it for can_convert",
          "allow_conversion=1" in js and "err.canConvert" in js and "canSwitchAudioInPlace" in js)
    css_player = client.get("/static/nl/player.css").text
    check("player.css makes the page transparent in native mode and keeps the v1 fallback",
          "html.native body" in css_player and "background: transparent" in css_player and "native-v1" in css_player)
    js_player = client.get("/static/nl/player.js").text
    check("player.js keeps the web controls in native v2 and hides them only for v1",
          "isNativeV2" in js_player and "native-v1" in js_player)

    print("data the TV pages read (same APIs as M2)")
    home = client.get("/api/nl/home")
    check("/api/nl/home", home.status_code == 200 and "continue_watching" in home.json() and SECRET not in home.text)
    lib = client.get("/api/nl/library", params={"type": "movie"})
    check("/api/nl/library", lib.status_code == 200 and len(lib.json()["items"]) >= 2 and SECRET not in lib.text)

    run_original_file(ctx)


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
