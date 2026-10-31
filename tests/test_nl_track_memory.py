"""
Offline tests for the remembered audio and subtitle choice per title (tests/test_nl_track_memory.py)

services/nl_track_memory.py, the nl_track_memory table, POST /api/nl/tracks/{id} and how /api/nl/play uses it.
Jellyfin is faked (nl_fake.py); nothing leaves the process.

    uv run python tests/test_nl_track_memory.py
"""

import os
import re
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nl_fake  # noqa: E402
from nl_fake import EPS, TICKS, FakeResponse  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} — got: {detail}")
        failures.append(label)


def main():
    test_pure()
    tmp = tempfile.mkdtemp(prefix="nl_tracks_test_")
    try:
        run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    test_static()
    if failures:
        print(f"\n{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("\nAll remembered-track tests passed")


# ---------------------------------------------------------------------------- pure


def test_pure():
    from services import nl_track_memory as tm

    print("clean_choice")
    c = tm.clean_choice({"index": 4, "language": "ger", "label": "German (forced)", "codec": "PGSSUB", "is_forced": True, "picture": 1}, 7)
    check("keeps index, source, folded language, label, codec and flags",
          c == {"index": 4, "source_id": 7, "language": "deu", "label": "German (forced)", "codec": "pgssub",
                "is_forced": True, "is_hearing_impaired": False, "picture": True, "is_commentary": False}, c)
    check("off", tm.clean_choice({"off": True}, 7) == {"off": True})
    check("nothing to save", tm.clean_choice(None, 7) is None)
    check("commentary read from the label", tm.clean_choice({"index": 1, "label": "English - Director's Commentary"}, 1)["is_commentary"])
    for bad in ({"index": -1}, {"index": "2"}, {"index": True}, {}, "x", 3):
        try:
            tm.clean_choice(bad, 1)
            check(f"rejects {bad!r}", False, "accepted")
        except ValueError:
            check(f"rejects {bad!r}", True)

    print("recall")
    tracks = [
        {"index": 2, "language": "eng", "label": "English", "codec": "truehd", "is_default": True},
        {"index": 3, "language": "eng", "label": "English - Commentary", "codec": "ac3"},
        {"index": 4, "language": "ita", "label": "Italian", "codec": "ac3"},
        {"index": 5, "language": "ita", "label": "Italian (forced)", "codec": "subrip", "is_forced": True},
    ]
    saved = tm.clean_choice({"index": 4, "language": "ita", "label": "Italian", "codec": "ac3"}, 10)
    check("same file: the saved index", tm.recall(tracks, saved, 10)["index"] == 4)
    moved = [dict(t, index=t["index"] + 10) for t in tracks]
    check("another file: same language, not the forced one", tm.recall(moved, saved, 11)["index"] == 14)
    forced = tm.clean_choice({"index": 5, "language": "ita", "label": "Italian (forced)", "is_forced": True}, 10)
    check("another file: a forced choice stays forced", tm.recall(moved, forced, 11)["index"] == 15)
    eng = tm.clean_choice({"index": 2, "language": "eng", "label": "English", "codec": "truehd"}, 10)
    check("another file: English, not the commentary", tm.recall(moved, eng, 11)["index"] == 12)
    comm = tm.clean_choice({"index": 3, "language": "eng", "label": "English - Commentary"}, 10)
    check("... and the commentary when that was chosen", tm.recall(moved, comm, 11)["index"] == 13)
    shifted = [dict(t) for t in tracks]
    shifted[2]["language"] = "hin"
    check("same file but the index is now another language: matched by language", tm.recall(shifted, saved, 10)["index"] == 5)
    check("no track in that language: nothing", tm.recall([t for t in tracks if t["language"] != "ita"], saved, 11) is None)
    blank = tm.clean_choice({"index": 9, "label": "Track 2"}, 10)
    check("no language: only the exact label", tm.recall([{"index": 1, "label": "Track 2"}], blank, 11)["index"] == 1
          and tm.recall([{"index": 1, "label": "Track 3"}], blank, 11) is None)
    check("off recalls no track", tm.recall(tracks, {"off": True}, 10) is None and tm.is_off({"off": True}))
    check("nothing saved", tm.recall(tracks, None, 10) is None and not tm.is_off(None))


# ----------------------------------------------------------------- fake Jellyfin


class TracksJellyfin(nl_fake.FakeJellyfin):
    """Every file has two audio and three subtitle tracks; the episodes' stream numbers are shifted by the episode,
    the way real episodes rarely share numbering."""

    def __init__(self):
        super().__init__()
        self.bodies = []

    @staticmethod
    def streams(shift):
        return [
            {"Type": "Video", "Index": 0, "Codec": "h264"},
            {"Type": "Audio", "Index": 1 + shift, "Language": "eng", "Codec": "ac3", "Channels": 6, "IsDefault": True},
            {"Type": "Audio", "Index": 2 + shift, "Language": "ita", "Codec": "ac3", "Channels": 6},
            {"Type": "Subtitle", "Index": 3 + shift, "Language": "eng", "Codec": "subrip", "IsTextSubtitleStream": True},
            {"Type": "Subtitle", "Index": 4 + shift, "Language": "ita", "Codec": "subrip", "IsTextSubtitleStream": True},
            {"Type": "Subtitle", "Index": 5 + shift, "Language": "ita", "Codec": "subrip", "IsTextSubtitleStream": True, "IsForced": True},
        ]

    def request(self, method, url, **kw):
        path = url.split("jellyfin.test:8096", 1)[1]
        if path.endswith("/PlaybackInfo") and method == "POST":
            jf = path.split("/")[2]
            body = kw.get("json") or {}
            self.bodies.append(dict(body))
            m = re.match(r"^jfS(\d)E(\d)$", jf)
            shift = 0 if not m else int(m.group(1)) * 10 + int(m.group(2))
            ms = {"Id": jf, "Container": "mkv", "RunTimeTicks": 50 * 60 * TICKS, "DefaultAudioStreamIndex": 1 + shift,
                  "SupportsDirectPlay": bool(body.get("EnableDirectPlay", True)),
                  "SupportsDirectStream": bool(body.get("EnableDirectStream", True)), "MediaStreams": self.streams(shift)}
            if not ms["SupportsDirectPlay"]:
                ms["TranscodingUrl"] = f"/videos/{jf}/master.m3u8?MediaSourceId={jf}"
            return FakeResponse(json_data={"PlaySessionId": "ps-" + jf, "MediaSources": [ms]})
        return super().request(method, url, **kw)


# ------------------------------------------------------------------------- app


def run(tmp):
    ctx = nl_fake.boot(tmp)
    server, client, ids, db = ctx["server"], ctx["client"], ctx["ids"], ctx["db"]
    state = ctx["state"]
    fake = TracksJellyfin()
    state.jellyfin_service.session = fake
    state.jellyfin_playback._ids = {}
    state.jellyfin_playback._last_refresh = None
    alpha, sigma = ids["Alpha"], ids["Sigma"]
    ep = {k: ids["sources"][p] for k, p in EPS.items()}

    print("schema")
    check("fresh DB is at schema 23 or newer", db.get_schema_version() >= 23, db.get_schema_version())

    print("nothing remembered: the defaults, as before")
    r = client.get(f"/api/nl/play/{alpha}").json()
    check("file's default audio, no subtitle", r["audio_index"] == 1 and r["default_subtitle_index"] == -1, (r["audio_index"], r["default_subtitle_index"]))

    print("save a choice")
    r = client.post(f"/api/nl/tracks/{alpha}", json={"audio": {"index": 2, "language": "ita", "label": "Italian", "codec": "ac3"}})
    check("audio saved", r.status_code == 200 and r.json() == {"success": True}, r.text)
    r = client.post(f"/api/nl/tracks/{alpha}", json={"subtitle": {"index": 3, "language": "eng", "label": "English"}})
    check("subtitle saved", r.status_code == 200, r.text)
    mem = db.get_track_memory("default", alpha)
    check("both kept (the second save did not clear the audio)", mem["audio"]["index"] == 2 and mem["subtitle"]["index"] == 3, mem)
    for bad, label in (({}, "empty"), ({"audio": {"off": True}}, "audio off"), ({"subtitle": {"index": -2}}, "bad index"),
                       ({"audio": {"index": 1}, "source": ep[(1, 1)]}, "another title's episode")):
        r = client.post(f"/api/nl/tracks/{alpha}", json=bad)
        check(f"refused: {label}", r.status_code in (400, 404, 422), (r.status_code, r.text))
    check("unknown title is 404", client.post("/api/nl/tracks/99999", json={"audio": {"index": 1}}).status_code == 404)

    print("play again: the choice comes back (browser)")
    r = client.get(f"/api/nl/play/{alpha}").json()
    check("Italian audio, English subtitles switched on", r["audio_index"] == 2 and r["default_subtitle_index"] == 3, (r["audio_index"], r["default_subtitle_index"]))
    check("a non-default audio track needs Jellyfin's stream", r["kind"] == "hls", r["kind"])
    r = client.get(f"/api/nl/play/{alpha}?audio=1&subtitle=-1").json()
    check("an explicit choice in the request still wins", r["audio_index"] == 1 and r["default_subtitle_index"] == -1, (r["audio_index"], r["default_subtitle_index"]))

    print("play again on the TV (original file)")
    r = client.get(f"/api/nl/play/{alpha}?client=tv").json()
    check("Italian is the second audio track of the file", r["audio_index"] == 2 and r["audio_order"] == 1, (r["audio_index"], r.get("audio_order")))
    check("English subtitles switched on", r["default_subtitle_index"] == 3, r["default_subtitle_index"])
    check("the original file, no track sent to Jellyfin", r["kind"] == "file" and fake.bodies[-1].get("SubtitleStreamIndex") == -1
          and "AudioStreamIndex" not in fake.bodies[-1], fake.bodies[-1])

    print("subtitles off is remembered, even over prefs and forced tracks")
    client.put("/api/nl/prefs", json={"subtitles_on": True, "subtitle_lang": "eng"})
    client.post(f"/api/nl/tracks/{alpha}", json={"subtitle": {"off": True}})
    r = client.get(f"/api/nl/play/{alpha}?client=tv").json()
    check("off, although prefs say on and the audio is Italian", r["default_subtitle_index"] == -1, r["default_subtitle_index"])
    client.put("/api/nl/prefs", json={"subtitles_on": False, "subtitle_lang": None})

    print("shows: a choice carries over to the other episodes by language")
    client.post(f"/api/nl/tracks/{sigma}", json={"source": ep[(1, 1)], "audio": {"index": 13, "language": "ita", "label": "Italian"},
                                                  "subtitle": {"index": 16, "language": "ita", "label": "Italian (forced)", "is_forced": True}})
    r = client.get(f"/api/nl/play/{sigma}?source={ep[(1, 2)]}").json()
    check("S1E2: its own Italian audio and forced Italian subtitles", r["audio_index"] == 14 and r["default_subtitle_index"] == 17,
          (r["audio_index"], r["default_subtitle_index"]))
    r = client.get(f"/api/nl/play/{sigma}?source={ep[(2, 1)]}&client=tv").json()
    check("S2E1 on the TV too", r["audio_index"] == 23 and r["audio_order"] == 1 and r["default_subtitle_index"] == 26,
          (r["audio_index"], r.get("audio_order"), r["default_subtitle_index"]))

    print("people: own choice first, else anyone's")
    from services import nl_people as np
    state.nl_people = np.People(np.parse_people([
        {"id": "labiba", "name": "Labiba", "jellyfin_user_id": "a" * 32, "profile_id": 1},
        {"id": "partner", "name": "Sam", "jellyfin_user_id": "b" * 32, "profile_id": 2},
    ]))
    beta = ids["Beta"]
    lab, sam = {"Cookie": "nl_viewer=labiba"}, {"Cookie": "nl_viewer=partner"}
    client.post(f"/api/nl/tracks/{beta}", json={"audio": {"index": 2, "language": "ita", "label": "Italian"}}, headers=lab)
    check("saved under the viewer", db.get_track_memory("labiba", beta)["audio"]["index"] == 2
          and db.get_track_memory("nobody", beta)["audio"]["index"] == 2, db.get_track_memory("labiba", beta))
    client.post(f"/api/nl/tracks/{beta}", json={"audio": {"index": 1, "language": "eng", "label": "English"}}, headers=sam)
    check("Labiba keeps hers", db.get_track_memory("labiba", beta)["audio"]["index"] == 2)
    check("Sam gets his", db.get_track_memory("partner", beta)["audio"]["index"] == 1)
    check("Both of us: the latest anyone chose", db.get_track_memory("both", beta)["audio"]["index"] == 1)


# ---------------------------------------------------------------------- static


def test_static():
    print("player page")
    with open(os.path.join(ROOT, "static", "nl", "player.js"), encoding="utf-8") as f:
        js = f.read()
    check("saves to /api/nl/tracks", '"/api/nl/tracks/"' in js and "function rememberTrack(axis, track)" in js)
    choose = js[js.index("function chooseSubtitle(index)"):js.index("function cycleSubtitle()")]
    check("every subtitle pick is saved (off, embedded, picture, text)", choose.count("rememberTrack(") == 4, choose.count("rememberTrack("))
    audio = js[js.index("function openAudioMenu()"):js.index("var subHold")]
    check("an audio pick is saved", 'rememberTrack("audio", t)' in audio)
    start = js[js.index("function startFirst(opts)"):js.index("fetchPlay({ start: 0 })")]
    check("the next episode starts from the server's tracks", "S.textIndex = null" in start and "S.defaultSubDone = false" in start)


if __name__ == "__main__":
    main()
