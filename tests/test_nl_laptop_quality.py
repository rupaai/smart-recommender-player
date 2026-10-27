"""
Offline tests for the laptop app's original quality (tests/test_nl_laptop_quality.py): the laptop profile, the
"is the video copied?" check, and /api/nl/play?client=laptop. Jellyfin is faked (tests/nl_fake.py); nothing leaves
the process.

    uv run python tests/test_nl_laptop_quality.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nl_fake  # noqa: E402
from nl_fake import SECRET  # noqa: E402

from services.device_profiles import (  # noqa: E402
    BROWSER_PROFILE, CAST_PROFILE, TV_PROFILE, browser_profile, laptop_profile, parse_caps, profile_for,
)
from services.jellyfin_playback import VIDEO_COPY_REASONS, video_is_copied  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
failures = []
CAPS = "v=h264.hevc;a=aac.mp3.flac;h=h264.hevc.aac.flac.mp3"
CAPS_NO_FLAC = "v=h264.hevc;a=aac.mp3;h=h264.hevc.aac.mp3.ac3.eac3"


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} — got: {detail}")
        failures.append(label)


def fmp4(profile):
    return [p for p in profile["TranscodingProfiles"] if p["Container"] == "mp4" and p["Protocol"] == "hls"]


def test_profile():
    print("laptop_profile")
    p = laptop_profile(parse_caps(CAPS))
    check("400 Mbps ceiling, profile and static", p["MaxStreamingBitrate"] == 400_000_000 and p["MaxStaticBitrate"] == 400_000_000)
    t = fmp4(p)
    check("fMP4 HLS profile with hevc, stereo", len(t) == 1 and "hevc" in t[0]["VideoCodec"] and t[0]["MaxAudioChannels"] == "2", t)
    check("FLAC first when the browser plays it, AAC next, the rest kept",
          t[0]["AudioCodec"].split(",")[:2] == ["flac", "aac"] and "mp3" in t[0]["AudioCodec"], t[0]["AudioCodec"])
    t2 = fmp4(laptop_profile(parse_caps(CAPS_NO_FLAC)))
    check("AAC first without FLAC; AC3 / E-AC3 stay listed (Safari copies them)",
          t2[0]["AudioCodec"].split(",")[0] == "aac" and {"ac3", "eac3"} <= set(t2[0]["AudioCodec"].split(",")), t2[0]["AudioCodec"])
    cp = [c for c in p["CodecProfiles"] if c.get("Codec") == "hevc"][0]
    props = {c["Property"]: c for c in cp["Conditions"]}
    check("HEVC keeps main / main 10", props["VideoProfile"]["Value"] == "main|main 10")
    check("HEVC dynamic range: SDR, HDR10, HDR10Plus, HLG; no Dolby Vision; not required",
          props["VideoRangeType"]["Condition"] == "EqualsAny" and props["VideoRangeType"]["Value"] == "SDR|HDR10|HDR10Plus|HLG"
          and props["VideoRangeType"]["IsRequired"] is False and "DOVI" not in props["VideoRangeType"]["Value"], props)
    check("the h264 TS profile stays (the converted copy uses it)", any(x["Container"] == "ts" and x["VideoCodec"] == "h264" for x in p["TranscodingProfiles"]))
    none = laptop_profile(None)
    check("no caps: the conservative browser profile at 400 Mbps",
          none["MaxStreamingBitrate"] == 400_000_000 and none["DirectPlayProfiles"] == BROWSER_PROFILE["DirectPlayProfiles"]
          and not fmp4(none) and none["CodecProfiles"] == [], none)
    check("profile_for('laptop') gives it, caps or not",
          profile_for("laptop", CAPS) == p and profile_for("laptop", None) == none and profile_for("laptop", "junk") == none)

    print("the other profiles did not change")
    plain = browser_profile(parse_caps(CAPS))
    check("browser and phone: 20 Mbps, no VideoRangeType, FLAC not first",
          plain["MaxStreamingBitrate"] == 20_000_000 and plain["MaxStaticBitrate"] == 20_000_000
          and "VideoRangeType" not in json.dumps(plain) and fmp4(plain)[0]["AudioCodec"].startswith("aac,"), plain)
    check("BROWSER_PROFILE is untouched", BROWSER_PROFILE["MaxStreamingBitrate"] == 20_000_000 and BROWSER_PROFILE["CodecProfiles"] == []
          and BROWSER_PROFILE["Name"] == "Northern Lights (browser)")
    check("cast is 20 Mbps, TV is 400 Mbps, both as before",
          CAST_PROFILE["MaxStreamingBitrate"] == 20_000_000 and TV_PROFILE["MaxStreamingBitrate"] == 400_000_000
          and profile_for("tv", CAPS) is TV_PROFILE and profile_for("cast", CAPS) is CAST_PROFILE)
    check("no caps and no client: BROWSER_PROFILE itself", profile_for(None, None) == BROWSER_PROFILE)


def test_video_is_copied():
    print("video_is_copied")
    base = "/videos/jf1/master.m3u8?MediaSourceId=ms1&VideoCodec=hevc&AudioCodec=flac"
    check("remux (container + audio reasons, hevc in the list)",
          video_is_copied(base + "&TranscodeReasons=ContainerNotSupported,AudioCodecNotSupported", "hevc"))
    check("no reasons at all, codec listed", video_is_copied(base, "hevc"))
    check("source codec compared in lower case and by alias", video_is_copied(base + "&TranscodeReasons=AudioCodecNotSupported", "HEVC")
          and video_is_copied("/v/master.m3u8?VideoCodec=hevc,h264&TranscodeReasons=ContainerNotSupported", "h265"))
    check("every allowed reason passes", video_is_copied(base + "&TranscodeReasons=" + ",".join(sorted(VIDEO_COPY_REASONS)), "hevc"))
    for reason in ("VideoCodecNotSupported", "VideoRangeTypeNotSupported", "VideoProfileNotSupported", "VideoLevelNotSupported",
                   "VideoBitDepthNotSupported", "ContainerBitrateExceedsLimit", "VideoBitrateNotSupported", "SubtitleCodecNotSupported"):
        check(f"{reason} means the video is converted",
              not video_is_copied(base + f"&TranscodeReasons=AudioCodecNotSupported,{reason}", "hevc") and reason not in VIDEO_COPY_REASONS)
    check("AllowVideoStreamCopy=false means converted", not video_is_copied(base + "&AllowVideoStreamCopy=false", "hevc")
          and not video_is_copied(base + "&allowvideostreamcopy=False", "hevc"))
    check("AllowVideoStreamCopy=true is fine", video_is_copied(base + "&AllowVideoStreamCopy=true", "hevc"))
    check("the video codec list must hold the source codec", not video_is_copied("/v/master.m3u8?VideoCodec=h264", "hevc"))
    check("no VideoCodec in the URL, no source codec, no URL: not proven, so False",
          not video_is_copied("/v/master.m3u8?TranscodeReasons=ContainerNotSupported", "hevc")
          and not video_is_copied(base, None) and not video_is_copied(None, "hevc") and not video_is_copied("", "hevc"))


STREAMS = [
    {"Type": "Video", "Index": 0, "Codec": "hevc", "VideoRangeType": "HDR10"},
    {"Type": "Audio", "Index": 1, "Codec": "truehd", "Language": "eng", "Channels": 8, "IsDefault": True, "DisplayTitle": "English - TrueHD"},
    {"Type": "Audio", "Index": 2, "Codec": "ac3", "Language": "ita", "Channels": 6, "DisplayTitle": "Italian - AC3"},
    {"Type": "Subtitle", "Index": 3, "Codec": "PGSSUB", "Language": "eng", "IsGraphicalSubtitleStream": True, "DisplayTitle": "English - PGS"},
]
TEXT_SUB = {"Type": "Subtitle", "Index": 5, "Codec": "subrip", "Language": "eng", "IsTextSubtitleStream": True, "DisplayTitle": "English - SRT"}

REMUX = "ContainerNotSupported,AudioCodecNotSupported"


class LaptopJellyfin(nl_fake.FakeJellyfin):
    """A TrueHD + HEVC file. `mode`: "direct" offers the file itself (unless another audio / a subtitle is wanted), else an
    HLS stream built from `reasons` and `vcodec`."""

    def __init__(self):
        super().__init__()
        self.mode = "hls"
        self.reasons = REMUX
        self.vcodec = "hevc"
        self.streams = STREAMS
        self.bodies = []

    def request(self, method, url, **kw):
        path = url.split("jellyfin.test:8096", 1)[1] if "jellyfin.test:8096" in url else url
        if path.endswith("/PlaybackInfo") and method == "POST":
            body = kw.get("json") or {}
            self.bodies.append(body)
            jf = path.split("/")[2]
            base = {"Id": "ms-" + jf, "Container": "mkv", "RunTimeTicks": 50 * 60 * nl_fake.TICKS, "Bitrate": 92_000_000,
                    "DefaultAudioStreamIndex": 1, "MediaStreams": self.streams}
            wants_other = body.get("AudioStreamIndex") not in (None, 1) or (body.get("SubtitleStreamIndex") or -1) >= 0
            if self.mode == "direct" and not wants_other and body.get("EnableDirectPlay", True):
                ms = {**base, "SupportsDirectPlay": True, "SupportsDirectStream": True}
            else:
                u = f"/videos/{jf}/master.m3u8?MediaSourceId=ms-{jf}&VideoCodec={self.vcodec}&AudioCodec=flac&TranscodeReasons={self.reasons}"
                ms = {**base, "SupportsDirectPlay": False, "SupportsDirectStream": False, "TranscodingUrl": u}
            return nl_fake.FakeResponse(json_data={"PlaySessionId": "ps-" + jf, "MediaSources": [ms]})
        return super().request(method, url, **kw)


def run_api(tmp):
    ctx = nl_fake.boot(tmp)
    server, client, ids = ctx["server"], ctx["client"], ctx["ids"]
    fake = LaptopJellyfin()
    server.jellyfin_service.session = fake
    server.jellyfin_playback._ids = {}
    server.jellyfin_playback._last_refresh = None
    alpha = ids["Alpha"]

    def play(**params):
        fake.bodies.clear()
        return client.get(f"/api/nl/play/{alpha}", params=params)

    print("client=laptop: the video is copied or the viewer is asked")
    fake.mode, fake.reasons, fake.vcodec = "hls", REMUX, "hevc"
    r = play(client="laptop", caps=CAPS)
    d = r.json()
    check("a remux (container + audio reasons): 200 hls", r.status_code == 200 and d["kind"] == "hls" and "master.m3u8" in d["url"] and SECRET not in r.text, r.text[:300])
    b = fake.bodies[0]
    check("the laptop profile went to Jellyfin: 400 Mbps in the profile and at request level",
          b["DeviceProfile"]["MaxStreamingBitrate"] == 400_000_000 and b["MaxStreamingBitrate"] == 400_000_000
          and b["DeviceProfile"] == laptop_profile(parse_caps(CAPS)), b["MaxStreamingBitrate"])
    r = play(client="laptop", caps=CAPS, audio=2)
    check("another audio track: still HLS, video copied", r.status_code == 200 and r.json()["kind"] == "hls" and r.json()["audio_index"] == 2, r.text[:200])
    r = play(client="laptop")
    check("no caps: the default profile at 400 Mbps", r.status_code == 200 and fake.bodies[0]["MaxStreamingBitrate"] == 400_000_000
          and fake.bodies[0]["DeviceProfile"] == laptop_profile(None))

    fake.mode = "direct"
    r = play(client="laptop", caps=CAPS)
    check("the file itself (direct play) is fine", r.status_code == 200 and r.json()["kind"] == "file" and "static=true" in r.json()["url"], r.text[:200])

    fake.mode, fake.vcodec = "hls", "h264"
    for reasons in ("VideoCodecNotSupported", "AudioCodecNotSupported,VideoRangeTypeNotSupported", "ContainerBitrateExceedsLimit"):
        fake.reasons = reasons
        r = play(client="laptop", caps=CAPS)
        d = r.json()
        check(f"video not copyable ({reasons}): 415 with can_convert and a plain reason",
              r.status_code == 415 and d.get("can_convert") is True and d["detail"].startswith("This laptop can't play the original file as it is")
              and reasons.split(",")[-1] in d["detail"], r.text[:300])
    fake.reasons, fake.vcodec = "AudioCodecNotSupported", "h264"
    r = play(client="laptop", caps=CAPS)
    check("reasons look harmless but the URL's VideoCodec lacks the source codec: 415", r.status_code == 415 and r.json().get("can_convert") is True, r.text[:200])
    fake.reasons = "VideoRangeTypeNotSupported"
    r = play(client="laptop", caps=CAPS, allow_conversion=1)
    check("allow_conversion=1: the converted stream, laptop profile kept (400 Mbps)",
          r.status_code == 200 and r.json()["kind"] == "hls" and fake.bodies[0]["MaxStreamingBitrate"] == 400_000_000, r.text[:200])
    r = client.get(f"/api/nl/play/{alpha}", params={"caps": CAPS})
    check("the phone page (caps only) is never asked: same stream, no can_convert", r.status_code == 200 and r.json()["kind"] == "hls"
          and "can_convert" not in r.text and fake.bodies[-1]["DeviceProfile"] == browser_profile(parse_caps(CAPS)))

    print("quality: the saved cap is ignored, a picked cap counts as agreeing to a converted copy")
    r = client.put("/api/nl/prefs", json={"quality_kbps": 2000})
    check("saved quality cap set to 2000", r.status_code == 200, r.text[:200])
    fake.mode, fake.reasons, fake.vcodec = "hls", REMUX, "hevc"
    r = play(client="laptop", caps=CAPS)
    check("the laptop ignores it (400 Mbps, 200)", r.status_code == 200 and fake.bodies[0]["MaxStreamingBitrate"] == 400_000_000, r.text[:200])
    r = client.get(f"/api/nl/play/{alpha}", params={"caps": CAPS})
    check("the phone still uses it", fake.bodies[-1]["MaxStreamingBitrate"] == 2_000_000, fake.bodies[-1]["MaxStreamingBitrate"])
    fake.reasons, fake.vcodec = "VideoCodecNotSupported", "h264"
    fake.bodies.clear()
    r = play(client="laptop", caps=CAPS, max_kbps=4000)
    check("max_kbps=4000 applies and needs no extra agreement", r.status_code == 200 and fake.bodies[0]["MaxStreamingBitrate"] == 4_000_000, (r.status_code, r.text[:200]))
    r = play(client="laptop", caps=CAPS, max_kbps=0)
    check("max_kbps=0 (Auto) is the original again: 415 for a video that cannot be copied", r.status_code == 415, r.status_code)
    client.put("/api/nl/prefs", json={"quality_kbps": None})

    print("picture subtitles ask first")
    fake.mode, fake.reasons, fake.vcodec = "hls", REMUX, "hevc"
    r = play(client="laptop", caps=CAPS, subtitle=3)
    d = r.json()
    check("a picture subtitle without agreement: 415 can_convert, plain message",
          r.status_code == 415 and d.get("can_convert") is True and "picture" in d["detail"] and "converted copy" in d["detail"], r.text[:300])
    check("... and nothing was sent to Jellyfin with that subtitle", all("SubtitleStreamIndex" not in x for x in fake.bodies), fake.bodies)
    r = play(client="laptop", caps=CAPS, subtitle=3, allow_conversion=1)
    check("with allow_conversion: burned in as in the browser (SubtitleStreamIndex sent)",
          r.status_code == 200 and fake.bodies[0].get("SubtitleStreamIndex") == 3, r.text[:200])
    fake.streams = STREAMS + [TEXT_SUB]
    r = play(client="laptop", caps=CAPS, subtitle=5)
    check("a text subtitle index is never sent to Jellyfin (it is a Stream.vtt side file)",
          r.status_code == 200 and all("SubtitleStreamIndex" not in x for x in fake.bodies), r.text[:200])
    check("... and its track keeps its Stream.vtt URL", [t["url"] for t in r.json()["subtitle_tracks"] if t["index"] == 5][0].endswith("Stream.vtt"))

    r = client.put("/api/nl/prefs", json={"audio_lang": "eng", "subtitle_lang": "eng", "subtitles_on": True})
    fake.streams = STREAMS
    r = play(client="laptop", caps=CAPS)
    d = r.json()
    check("subtitles on, only a picture track: the laptop shows none by default (nothing burned in)",
          r.status_code == 200 and d["default_subtitle_index"] == -1 and d["subtitle_index"] == -1
          and all("SubtitleStreamIndex" not in x for x in fake.bodies), r.text[:300])
    fake.streams = STREAMS + [TEXT_SUB]
    r = play(client="laptop", caps=CAPS)
    check("subtitles on, a text track in the language: that one is the default", r.json()["default_subtitle_index"] == 5, r.text[:200])
    fake.streams = STREAMS
    r = play(caps=CAPS)
    check("the phone still burns the picture subtitle in by default", r.status_code == 200 and r.json()["subtitle_index"] == 3, r.text[:200])
    r = play(client="laptop", caps=CAPS, allow_conversion=1)
    check("laptop with allow_conversion: the old default (picture subtitle burned in)", r.status_code == 200 and r.json()["subtitle_index"] == 3, r.text[:200])
    client.put("/api/nl/prefs", json={"audio_lang": None, "subtitle_lang": None, "subtitles_on": None})

    print("client values")
    for bad in ("Laptop", "laptops", "tv;laptop"):
        check(f"client={bad!r} rejected with 422", play(client=bad).status_code == 422)
    fake.mode = "direct"
    r = play(client="tv")
    check("the TV is untouched: static file, TV profile", r.status_code == 200 and r.json()["kind"] == "file" and fake.bodies[0]["DeviceProfile"] == TV_PROFILE)


def run_js():
    node = shutil.which("node")
    if not node:
        print("  (node not installed: JS check skipped)")
        return
    print("player.js device query")
    out = subprocess.run([node, os.path.join(ROOT, "tests", "js", "nl_laptop_quality_check.js")], capture_output=True, text=True, timeout=120)
    print(out.stdout.rstrip())
    check("nl_laptop_quality_check.js passes", out.returncode == 0, out.stdout[-300:] + out.stderr[-300:])


def main():
    test_profile()
    test_video_is_copied()
    run_js()
    tmp = tempfile.mkdtemp(prefix="nl_laptop_quality_test_")
    try:
        run_api(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if failures:
        print(f"\n{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("\nAll Northern Lights laptop quality tests passed")


if __name__ == "__main__":
    main()
