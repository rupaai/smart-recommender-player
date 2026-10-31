"""
Offline tests for Northern Lights M10c, "settings and picture" (tests/test_nl_settings.py): H5 playback defaults per
person (skip length, autoplay, subtitles on, quality), H6 the short TV settings (Playback, Display), P18 picture size,
P19 the quality picker (PlaybackInfo with a lower MaxStreamingBitrate), P20 plain-words conversion notes and format
errors that name the codec, and P24 audio-description tracks labelled "AD". Jellyfin is faked.

    uv run python tests/test_nl_settings.py
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
from nl_fake import FakeResponse, SECRET, TICKS  # noqa: E402

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
    tmp = tempfile.mkdtemp(prefix="nl_settings_test_")
    try:
        test_database(tmp)
        run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    test_static()
    if failures:
        print(f"\n{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("\nAll Northern Lights M10c (settings and picture) tests passed")


def test_pure():
    from services import nl_people as np
    from services import nl_picture as pic
    from services import nl_settings as st

    print("setting values")
    check("skip lengths", all(st.valid_skip(v) for v in (10, 15, 30, 60)) and not any(st.valid_skip(v) for v in (0, 5, 20, 90, "10", True, None, 10.5)))
    check("qualities", all(st.valid_quality(v) for v in (8000, 4000, 2000, 1000)) and not any(st.valid_quality(v) for v in (0, 20000, 3000, "4000", True, None)))
    check("picture sizes", all(st.valid_fit(v) for v in ("fit", "fill", "original")) and not any(st.valid_fit(v) for v in ("zoom", "", None, 1)))
    check("Auto is first in the options, then best to lowest", [o["kbps"] for o in st.quality_options()] == [None, 8000, 4000, 2000, 1000])
    check("a chosen quality only ever lowers the ceiling", st.effective_cap_bps(20_000_000, 4000) == 4_000_000 and st.effective_cap_bps(3_000_000, 8000) == 3_000_000
          and st.effective_cap_bps(20_000_000, None) == 20_000_000 and st.effective_cap_bps(None, 2000) == 2_000_000 and st.effective_cap_bps(None, None) is None)

    print("prefs validation")
    ok = np.clean_prefs({"skip_seconds": 30, "autoplay": False, "quality_kbps": 2000, "picture_fit": "fill"})
    check("good values pass", ok == {"skip_seconds": 30, "autoplay": False, "quality_kbps": 2000, "picture_fit": "fill"}, ok)
    check("null clears each", np.clean_prefs({"skip_seconds": None, "autoplay": None, "quality_kbps": None, "picture_fit": None})
          == {"skip_seconds": None, "autoplay": None, "quality_kbps": None, "picture_fit": None})
    for bad in ({"skip_seconds": 20}, {"skip_seconds": "10"}, {"autoplay": "yes"}, {"quality_kbps": 5000}, {"quality_kbps": True}, {"picture_fit": "stretch"}):
        try:
            np.clean_prefs(bad)
            check(f"{bad} is refused", False, "accepted")
        except ValueError:
            check(f"{bad} is refused", True)
    pub = np.Prefs.from_row(None).public()
    check("defaults: no skip choice, autoplay on, Auto, fit", pub["skip_seconds"] is None and pub["autoplay"] is True and pub["quality_kbps"] is None and pub["picture_fit"] == "fit", pub)
    pub = np.Prefs.from_row({"skip_seconds": 15, "autoplay": 0, "quality_kbps": 4000, "picture_fit": "original"}).public()
    check("saved values read back (autoplay 0 = off)", pub["skip_seconds"] == 15 and pub["autoplay"] is False and pub["quality_kbps"] == 4000 and pub["picture_fit"] == "original", pub)
    pub = np.Prefs.from_row({"skip_seconds": 99, "quality_kbps": 1234, "picture_fit": "weird"}).public()
    check("junk in the database falls back", pub["skip_seconds"] is None and pub["quality_kbps"] is None and pub["picture_fit"] == "fit", pub)

    print("P24 audio description")
    ad = pic.is_audio_description
    check("recognised by name", ad({"Title": "Audio Description"}) and ad({"DisplayTitle": "English - Audio Description - AAC - Stereo"})
          and ad({"Title": "Descriptive Audio"}) and ad({"Title": "Visually Impaired"}) and ad({"DisplayTitle": "English - AD - AAC"}) and ad({"Title": "English (AD)"}))
    check("ordinary tracks are not", not ad({"Title": "English", "DisplayTitle": "English - DTS - 5.1 - Default"}) and not ad({"Title": "Commentary"})
          and not ad({"Title": "Nomad"}) and not ad({"Title": "ad lib"}) and not ad({}) and not ad({"Title": "Dad's cut"}))
    check("a subtitle is not an audio description", not ad({"Type": "Subtitle", "Title": "Audio Description"}))
    from services.jellyfin_playback import audio_label, extract_tracks
    check("the label says AD", audio_label({"Language": "eng", "Channels": 2, "Codec": "aac", "Title": "Audio Description"}).endswith("(AD)"))
    check("a commentary stays a commentary", audio_label({"Language": "eng", "Channels": 2, "Codec": "aac", "Title": "Director's Commentary"}).endswith("(Commentary)"))
    tracks = extract_tracks({"MediaStreams": [
        {"Type": "Audio", "Index": 1, "Language": "eng", "Codec": "dts", "Channels": 6, "IsDefault": True, "Title": "English"},
        {"Type": "Audio", "Index": 2, "Language": "eng", "Codec": "aac", "Channels": 2, "IsDefault": True, "Title": "Audio Description"}]}, "jfA", "ms")["audio_tracks"]
    check("tracks carry the flag", [t["is_audio_description"] for t in tracks] == [False, True] and tracks[1]["label"] == "English · Stereo · AAC (AD)", [t["label"] for t in tracks])
    check("the preferred-language pick never lands on the AD track, even when it is the file's default",
          np.pick_audio_index([tracks[1], tracks[0]], "eng") == 1 and np.pick_audio_index([tracks[1]], "eng") is None)

    print("P20 conversion notes")
    f_hevc = {"video": "HEVC", "audio": "DTS", "hdr": None}
    d = pic.describe_conversion("Transcode", ["VideoCodecNotSupported", "AudioCodecNotSupported"], f_hevc, "This TV")
    check("names the screen and the parts", d["state"] == "converting" and d["note"] == "This TV can't play this format, so it's being converted." and d["detail"] == "HEVC video, DTS sound", d)
    d = pic.describe_conversion("Transcode", ["AudioCodecNotSupported"], f_hevc, "This browser")
    check("sound only", d["detail"] == "DTS sound" and d["note"].startswith("This browser can't play this format"), d)
    d = pic.describe_conversion("DirectStream", ["ContainerNotSupported"], f_hevc, "This browser")
    check("an mkv repackaged as HLS is not worth a note", d["state"] == "direct" and d["note"] is None, d)
    check("direct play: nothing", pic.describe_conversion("DirectPlay", [], f_hevc, "This TV")["state"] == "direct")
    d = pic.describe_conversion("Transcode", ["VideoRangeTypeNotSupported"], {"video": "HEVC", "audio": "AAC", "hdr": "HDR10"}, "This browser")
    check("HDR turned into a normal picture has its own sentence", "HDR10" in d["note"] and "standard colours" in d["note"], d)
    d = pic.describe_conversion("Transcode", ["ContainerBitrateExceedsLimit"], f_hevc, "This TV", cap_kbps=4000)
    check("a conversion that is only the chosen quality says so", d["note"] == "Playing at 4 Mbps or less as you chose, so it's being converted." and d["detail"] is None, d)
    d = pic.describe_conversion("Transcode", ["ContainerBitrateExceedsLimit"], f_hevc, "This TV")
    check("... or that the file is too big for the connection", "too big for the connection" in d["note"])
    d = pic.describe_conversion("Transcode", ["SubtitleCodecNotSupported"], f_hevc, "This TV")
    check("burned-in subtitles", "burned" in d["note"])
    d = pic.describe_conversion("Transcode", [], f_hevc, "This TV")
    check("no reasons given still gets a plain line", d["state"] == "converting" and d["note"])
    check("reasons come from the transcoding URL, junk dropped", pic.transcode_reasons("/videos/x/master.m3u8?a=1&TranscodeReasons=VideoCodecNotSupported,AudioCodecNotSupported,../x&b=2")
          == ["VideoCodecNotSupported", "AudioCodecNotSupported"] and pic.transcode_reasons(None) == [] and pic.transcode_reasons("/x") == [])
    check("codec names", pic.codec_name("video", "hevc") == "HEVC" and pic.codec_name("audio", "eac3") == "Dolby Digital Plus" and pic.codec_name("audio", "weirdo") == "WEIRDO" and pic.codec_name("video", "") == "")
    check("HDR names, SDR is none", pic.hdr_name("HDR10") == "HDR10" and pic.hdr_name("DOVI") == "Dolby Vision" and pic.hdr_name("SDR") is None and pic.hdr_name(None) is None)


def test_database(tmp):
    from database import DatabaseManager

    print("migration v20")
    fresh = DatabaseManager(os.path.join(tmp, "fresh.db"))
    with fresh.get_connection() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(nl_person_prefs)")}
    want = {"skip_seconds", "autoplay", "quality_kbps", "picture_fit"}
    check("a fresh DB is at schema 20 with the four columns", fresh.get_schema_version() >= 20 and want <= cols, (fresh.get_schema_version(), cols))
    path = os.path.join(tmp, "old.db")
    DatabaseManager(path)
    with sqlite3.connect(path) as conn:  # how a v19 database looks
        for name in want:
            conn.execute(f"ALTER TABLE nl_person_prefs DROP COLUMN {name}")
        conn.execute("PRAGMA user_version = 19")
    up = DatabaseManager(path)
    cols = {r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(nl_person_prefs)")}
    check("v19 upgrades and gets the columns", up.get_schema_version() >= 20 and want <= cols, (up.get_schema_version(), cols))
    check("running again is harmless", DatabaseManager(path).get_schema_version() >= 20)
    up.set_person_prefs("x", {"audio_lang": "eng"})
    p = up.get_person_prefs("x")
    check("a row from before reads as not set", p["skip_seconds"] is None and p["autoplay"] is None and p["quality_kbps"] is None and p["picture_fit"] is None and p["audio_lang"] == "eng", p)
    up.set_person_prefs("x", {"skip_seconds": 30, "autoplay": False, "quality_kbps": 2000, "picture_fit": "fill"})
    p = up.get_person_prefs("x")
    check("values are kept, the other prefs stay", p["skip_seconds"] == 30 and p["autoplay"] is False and p["quality_kbps"] == 2000 and p["picture_fit"] == "fill" and p["audio_lang"] == "eng", p)
    up.set_person_prefs("x", {"quality_kbps": None, "autoplay": None})
    p = up.get_person_prefs("x")
    check("null clears", p["quality_kbps"] is None and p["autoplay"] is None and p["skip_seconds"] == 30, p)
    check("a show with no switch of its own reports none", up.get_show_autoplay_row(424242) is None)


MS_STREAMS = [
    {"Type": "Video", "Index": 0, "Codec": "hevc", "VideoRangeType": "SDR"},
    {"Type": "Audio", "Index": 1, "Codec": "dts", "Language": "eng", "Channels": 6, "IsDefault": True, "Title": "English", "DisplayTitle": "English - DTS - 5.1"},
    {"Type": "Audio", "Index": 2, "Codec": "aac", "Language": "eng", "Channels": 2, "Title": "Audio Description", "DisplayTitle": "English - Audio Description - AAC"},
]


class PlayJellyfin(nl_fake.FakeJellyfin):
    """The fake Jellyfin with a PlaybackInfo the test can steer and whose request bodies it records."""

    def __init__(self):
        super().__init__()
        self.bodies = []
        self.mode = "direct"
        self.reasons = ""
        self.streams = MS_STREAMS
        self.bitrate = 12_000_000
        self.detail_streams = None

    def request(self, method, url, **kw):
        path = url.split("jellyfin.test:8096", 1)[1] if "jellyfin.test:8096" in url else url
        if path.endswith("/PlaybackInfo") and method == "POST":
            self.bodies.append(kw.get("json") or {})
            jf = path.split("/")[2]
            self.calls.append((method, path, kw.get("params") or {}))
            base = {"Id": "ms-" + jf, "Container": "mkv", "RunTimeTicks": 50 * 60 * TICKS, "Bitrate": self.bitrate,
                    "DefaultAudioStreamIndex": 1, "MediaStreams": self.streams}
            if self.mode == "direct":
                ms = {**base, "SupportsDirectPlay": True, "SupportsDirectStream": True}
            elif self.mode == "transcode":
                url_ = f"/videos/{jf}/master.m3u8?MediaSourceId=ms-{jf}&TranscodeReasons={self.reasons}"
                ms = {**base, "SupportsDirectPlay": False, "SupportsDirectStream": False, "TranscodingUrl": url_}
            elif self.mode == "remux":
                url_ = f"/videos/{jf}/master.m3u8?MediaSourceId=ms-{jf}&TranscodeReasons=ContainerNotSupported"
                ms = {**base, "SupportsDirectPlay": False, "SupportsDirectStream": True, "TranscodingUrl": url_}
            elif self.mode == "no_way":
                ms = {**base, "SupportsDirectPlay": False, "SupportsDirectStream": False}
            elif self.mode == "error":
                return FakeResponse(json_data={"ErrorCode": "NoCompatibleStream", "MediaSources": []})
            return FakeResponse(json_data={"PlaySessionId": "ps-" + jf, "MediaSources": [ms]})
        if self.detail_streams is not None and path == "/Users/user1/Items/jfB":
            return FakeResponse(json_data={"Id": "jfB", "Type": "Movie", "UserData": {}, "MediaStreams": self.detail_streams})
        return super().request(method, url, **kw)


def run(tmp):
    from services import nl_people as np

    ctx = nl_fake.boot(tmp)
    server, client, ids, db = ctx["server"], ctx["client"], ctx["ids"], ctx["db"]
    state = ctx["state"]
    fake = PlayJellyfin()
    state.jellyfin_service.session = fake
    state.jellyfin_playback._ids = {}
    state.jellyfin_playback._last_refresh = None
    alpha, beta, sigma = ids["Alpha"], ids["Beta"], ids["Sigma"]
    client.follow_redirects = False

    def play(item=alpha, query="", headers=None):
        fake.bodies.clear()
        return client.get(f"/api/nl/play/{item}" + (("?" + query) if query else ""), headers=headers)

    def sent_cap():
        return fake.bodies[0].get("MaxStreamingBitrate"), (fake.bodies[0].get("DeviceProfile") or {}).get("MaxStreamingBitrate")

    print("P19 the quality picker asks Jellyfin for a lower rate")
    r = play()
    d = r.json()
    check("no choice: the profile's own ceiling, Auto", r.status_code == 200 and sent_cap() == (20_000_000, 20_000_000) and d["quality"]["applied_kbps"] is None and d["quality"]["default_kbps"] is None, (sent_cap(), d.get("quality")))
    check("the reply offers the options and the file's own rate", [o["kbps"] for o in d["quality"]["options"]] == [None, 8000, 4000, 2000, 1000] and d["quality"]["source_kbps"] == 12000, d["quality"])
    r = play(query="max_kbps=4000")
    check("max_kbps=4000: MaxStreamingBitrate 4 Mbps in the body and the profile", sent_cap() == (4_000_000, 4_000_000) and r.json()["quality"]["applied_kbps"] == 4000, sent_cap())
    play(query="max_kbps=1000")
    check("1000 -> 1 Mbps", sent_cap() == (1_000_000, 1_000_000))
    check("an odd value is a 400, and nothing was asked of Jellyfin", play(query="max_kbps=3000").status_code == 400 and fake.bodies == [])
    r = client.put("/api/nl/prefs", json={"quality_kbps": 2000})
    check("the default is a saved setting", r.status_code == 200 and r.json()["prefs"]["quality_kbps"] == 2000, r.text)
    r = play()
    check("the default applies when the request names none", sent_cap() == (2_000_000, 2_000_000) and r.json()["quality"]["applied_kbps"] == 2000 and r.json()["quality"]["default_kbps"] == 2000, sent_cap())
    play(query="max_kbps=8000")
    check("a choice for this viewing beats the default", sent_cap()[0] == 8_000_000)
    r = play(query="max_kbps=0")
    check("0 = Auto for this viewing, even with a default", sent_cap() == (20_000_000, 20_000_000) and r.json()["quality"]["applied_kbps"] is None, sent_cap())
    client.put("/api/nl/prefs", json={"quality_kbps": 8000})
    play(query="client=tv")
    check("the TV's original file ignores the cap (400 Mbps profile ceiling)", sent_cap() == (400_000_000, 400_000_000), sent_cap())
    play(query="client=tv&allow_conversion=1")
    check("a converted copy for the TV gets it, below the profile's ceiling", sent_cap() == (8_000_000, 8_000_000), sent_cap())
    client.put("/api/nl/prefs", json={"quality_kbps": None})
    play(query="client=tv")
    check("TV with no choice keeps what it had (the body's 20 Mbps, the TV profile's own 400)", sent_cap() == (400_000_000, 400_000_000), sent_cap())
    client.put("/api/nl/prefs", json={"quality_kbps": 1000})
    play(query="client=tv&max_kbps=8000&allow_conversion=1")
    check("the quality never raises a ceiling", sent_cap() == (8_000_000, 8_000_000))
    client.put("/api/nl/prefs", json={"quality_kbps": None})

    print("P20 conversion notes in the play reply")
    fake.mode = "direct"
    d = play().json()
    check("direct play: nothing to say", d["conversion"]["state"] == "direct" and d["conversion"]["note"] is None, d["conversion"])
    check("the formats are named anyway (for errors)", d["conversion"]["formats"] == {"video": "HEVC", "audio": "DTS", "hdr": None}, d["conversion"]["formats"])
    check("the audio codec of the playing track is in the reply", d["audio_codec"] == "dts", d.get("audio_codec"))
    fake.mode = "remux"
    check("an mkv sent as HLS without touching picture or sound: no note", play().json()["conversion"]["state"] == "direct")
    fake.mode = "transcode"
    fake.reasons = "VideoCodecNotSupported,AudioCodecNotSupported"
    d = play().json()
    check("browser: 'This browser can't play this format, so it's being converted.' with the codecs",
          d["conversion"]["state"] == "converting" and d["conversion"]["note"] == "This browser can't play this format, so it's being converted." and d["conversion"]["detail"] == "HEVC video, DTS sound", d["conversion"])
    d = play(query="client=tv&allow_conversion=1").json()
    check("TV, after the viewer agreed to a converted copy: the same sentence for 'This TV'", d["conversion"]["note"] == "This TV can't play this format, so it's being converted.", d["conversion"])
    fake.reasons = "ContainerBitrateExceedsLimit"
    d = play(query="max_kbps=4000").json()
    check("converted only because of the quality chosen", d["conversion"]["note"] == "Playing at 4 Mbps or less as you chose, so it's being converted.", d["conversion"])
    fake.streams = [{**MS_STREAMS[0], "VideoRangeType": "HDR10"}] + MS_STREAMS[1:]
    fake.reasons = "VideoRangeTypeNotSupported"
    d = play().json()
    check("HDR the screen can't show", "HDR10" in d["conversion"]["note"] and d["conversion"]["formats"]["hdr"] == "HDR10", d["conversion"])
    fake.streams = MS_STREAMS
    fake.mode = "direct"

    print("P20 an error that names the format")
    fake.mode = "no_way"
    r = play()
    check("no way to play: 415 naming the codecs", r.status_code == 415 and "HEVC video, DTS sound" in r.json()["detail"] and r.json()["detail"].startswith("This browser can't play this format"), r.text)
    r = play(query="client=tv&allow_conversion=1")
    check("... for the TV (conversion allowed) it says so", r.status_code == 415 and r.json()["detail"].startswith("This TV can't play this format"), r.text)
    r = play(query="client=tv")
    check("... for the TV's original file it says so and offers a converted copy", r.status_code == 415 and r.json()["detail"].startswith("This TV can't play the original file as it is (HEVC video, DTS sound)") and r.json()["can_convert"] is True, r.text)
    fake.mode = "error"
    r = play(item=beta)
    check("Jellyfin's own error with nothing to name still reads plainly", r.status_code == 415 and "can't play this format" in r.json()["detail"] and "NoCompatibleStream" in r.json()["detail"], r.text)
    fake.detail_streams = [{"Type": "Video", "Index": 0, "Codec": "vc1"}, {"Type": "Audio", "Index": 1, "Codec": "truehd"}]
    r = play(item=beta)
    check("... and names them when the item itself can tell", "VC-1 video, TrueHD sound" in r.json()["detail"], r.text)
    fake.detail_streams = None
    fake.mode = "direct"

    print("P24 audio-description tracks")
    d = play().json()
    labels = [t["label"] for t in d["audio_tracks"]]
    check("labelled AD in the audio list", labels == ["English · 5.1 · DTS", "English · Stereo · AAC (AD)"] and [t["is_audio_description"] for t in d["audio_tracks"]] == [False, True], labels)
    fake.streams = [MS_STREAMS[0], {**MS_STREAMS[2], "IsDefault": True}, {**MS_STREAMS[1], "Index": 3}]
    client.put("/api/nl/prefs", json={"audio_lang": "eng"})
    fake.bodies.clear()
    d = play().json()
    asked = [b.get("AudioStreamIndex") for b in fake.bodies]
    check("a preferred language never selects the AD track by itself", 2 not in asked, asked)
    fake.streams = MS_STREAMS
    client.put("/api/nl/prefs", json={"audio_lang": None})

    print("H5 autoplay: the person's default, the show's own switch wins")
    nxt = f"/api/nl/next/{sigma}?source={ids['sources'][nl_fake.EPS[(1, 1)]]}"
    check("on to start", client.get(nxt).json()["autoplay"] is True and client.get(f"/api/nl/show/{sigma}").json()["autoplay"] is True)
    client.put("/api/nl/prefs", json={"autoplay": False})
    check("off in the settings: off for a show nobody set", client.get(nxt).json()["autoplay"] is False and client.get(f"/api/nl/show/{sigma}").json()["autoplay"] is False)
    r = client.put(f"/api/nl/show/{sigma}/prefs", json={"autoplay": True})
    check("a show's own switch wins", r.json()["autoplay"] is True and client.get(nxt).json()["autoplay"] is True and client.get(f"/api/nl/show/{sigma}").json()["autoplay"] is True)
    client.put(f"/api/nl/show/{sigma}/prefs", json={"autoplay": False})
    client.put("/api/nl/prefs", json={"autoplay": True})
    check("... also when it says off", client.get(nxt).json()["autoplay"] is False)

    print("H5 the settings API")
    for field, good, bad in (("skip_seconds", 30, 20), ("autoplay", False, "no"), ("quality_kbps", 4000, 5000), ("picture_fit", "fill", "zoom")):
        r = client.put("/api/nl/prefs", json={field: good})
        check(f"{field}: {good} saved", r.status_code == 200 and r.json()["prefs"][field] == good, r.text)
        check(f"{field}: {bad!r} is a 400", client.put("/api/nl/prefs", json={field: bad}).status_code == 400)
    r = client.put("/api/nl/prefs", json={"skip_seconds": None, "picture_fit": None})
    check("null clears (picture size falls back to fit)", r.json()["prefs"]["skip_seconds"] is None and r.json()["prefs"]["picture_fit"] == "fit", r.text)
    client.put("/api/nl/prefs", json={"autoplay": True, "quality_kbps": None})

    state.nl_people = np.People(np.parse_people([
        {"id": "labiba", "name": "Labiba", "jellyfin_user_id": LAB, "profile_id": 1},
        {"id": "partner", "name": "Sam", "jellyfin_user_id": PAR, "profile_id": 2},
    ]))
    print("settings belong to a person")
    client.put("/api/nl/prefs", json={"quality_kbps": 2000, "skip_seconds": 60, "picture_fit": "fill", "autoplay": False}, headers=cookie("labiba"))
    sam = client.get("/api/nl/prefs", headers=cookie("partner")).json()["prefs"]
    check("Sam is untouched", sam["quality_kbps"] is None and sam["skip_seconds"] is None and sam["picture_fit"] == "fit" and sam["autoplay"] is True, sam)
    fake.bodies.clear()
    client.get(f"/api/nl/play/{alpha}", headers=cookie("labiba"))
    labiba_cap = sent_cap()
    fake.bodies.clear()
    client.get(f"/api/nl/play/{alpha}", headers=cookie("partner"))
    check("Labiba's quality is hers: Sam still plays at the full rate", labiba_cap == (2_000_000, 2_000_000) and sent_cap() == (20_000_000, 20_000_000), (labiba_cap, sent_cap()))
    both = client.get("/api/nl/prefs", headers=cookie("both")).json()["prefs"]
    check("Both of us follow the first person's, as for every pref", both["quality_kbps"] == 2000 and both["skip_seconds"] == 60 and both["picture_fit"] == "fill", both)
    client.put("/api/nl/prefs", json={"quality_kbps": 8000}, headers=cookie("both"))
    check("... until they set their own", client.get("/api/nl/prefs", headers=cookie("both")).json()["prefs"]["quality_kbps"] == 8000
          and client.get("/api/nl/prefs", headers=cookie("labiba")).json()["prefs"]["quality_kbps"] == 2000)
    check("autoplay default follows the viewer", client.get(f"/api/nl/show/{ids['Alpha']}", headers=cookie("labiba")).status_code in (200, 404)
          and client.get(nxt, headers=cookie("partner")).json()["autoplay"] is False)   # Sigma has its own switch (off) from above
    state.nl_people = np.People([])

    print("pages")
    me = client.get("/nl/me").text
    check("the web me page has Playback and Display sections with every setting",
          "Playback</h2>" in me and "Display</h2>" in me and all(i in me for i in ("nl-pref-skip", "nl-pref-autoplay", "nl-pref-quality", "nl-pref-fit", "nl-pref-spoilers", "nl-pref-audio", "nl-pref-sub", "nl-pref-on")))
    tv = client.get("/nl/tv/me").text
    headings = re.findall(r"<h2[^>]*>(.*?)</h2>", tv)
    check("the TV me page has exactly two settings sections, Playback and Display", [h.split(": ")[-1] for h in headings if "tv-vetoes" not in h][:2] == ["Playback", "Display"] and len([h for h in headings if h.endswith("Playback") or h.endswith("Display")]) == 2, headings)
    check("every TV setting is a focusable row", all(f'id="tv-pref-{n}"' in tv for n in ("audio", "sub", "on", "skip", "autoplay", "quality", "fit", "spoilers")) and tv.count("data-nl-focus") >= 8)
    check("the TV me page has no admin link", "admin" not in tv.lower())
    check("the TV me page has the app settings button, hidden until the app says it has one", 'id="tv-app-settings-row"' in tv and re.search(r'id="tv-app-settings-row"[^>]*hidden', tv) is not None)
    player = client.get(f"/nl/play/{alpha}").text
    check("the player has the Picture button", 'id="nl-picture"' in player)
    check("no key in any reply", SECRET not in me and SECRET not in tv and SECRET not in player and SECRET not in play().text)


def test_static():
    print("scripts")
    p = read("static/nl/player.js")
    check("the quality picker re-requests the stream at the same place", "reloadStream({})" in p and "max_kbps=" in p and "function setQuality" in p)
    check("a choice in the player is for this film only (no save)", "NLPrefs.save" not in p[p.index("function setQuality"):p.index("function openQualityMenu")])
    check("the person's skip and picture defaults are read before the first stream", "applyPrefs" in p and "NLPrefs.get().then(applyPrefs" in p)
    check("picture size is a CSS class on the player, never in the TV app", 'S.fit === f' in p and "!isNative" in p and "fit-fill" in read("static/nl/player.css") and "fit-original" in read("static/nl/player.css"))
    check("the format error names the codecs", "unplayableText" in p and "conversion.formats" in p)
    check("the conversion note is a toast", "noteConversion" in p and "toast(c.note" in p)
    check("menu notes are text, not HTML", "nl-menu-note" in p and "n.textContent = it.text" in p)
    tv = read("static/nl/tv/tv.js")
    check("the TV me page cycles each setting and hides picture size in the app", "tv-pref-quality" in tv and "tv-pref-skip" in tv and "NorthernLightsNative" in tv and "tv-pref-fit-row" in tv)
    check("the app settings button shows only when the TV app has openSettings", 'typeof native.openSettings === "function"' in tv and "tv-app-settings-row" in tv)
    check("the prefs script knows the new settings", all(k in read("static/nl/prefs.js") for k in ("skip_seconds", "autoplay", "quality_kbps", "picture_fit", "qualityChoices")))
    bridge = read("tv-app/app/src/main/java/app/northernlights/tv/NativeBridge.kt")
    check("the Kotlin bridge is documented as v7", "bridge v7" in bridge)
    check("the Kotlin bridge can open the app's settings", "fun openSettings()" in bridge)
    check("the proxy allowlist is untouched", "videos/" in read("services/jellyfin_playback.py").lower() and "_IMAGE_PATH" in read("services/jellyfin_playback.py"))


if __name__ == "__main__":
    main()
