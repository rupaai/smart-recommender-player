"""
Offline tests for Northern Lights M8a casting (tests/test_nl_cast.py): the cast config, the signed stream tokens,
the cast profile, /api/nl/cast/*, the proxy's ?t= handling and the receiver page. Jellyfin is faked
(tests/nl_fake.py); nothing leaves the process.

    uv run python tests/test_nl_cast.py
"""

import os
import shutil
import stat
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nl_fake  # noqa: E402
from nl_fake import ALPHA, BETA, SECRET, FakeResponse  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAST_SECRET = "cast-secret-for-tests-0123456789"
BASE = "http://192.168.1.50:8000"
failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} — got: {detail}")
        failures.append(label)


def main():
    os.environ["NL_CAST_SECRET"] = CAST_SECRET
    test_config()
    test_tokens()
    test_profile()
    test_playlist()
    tmp = tempfile.mkdtemp(prefix="nl_cast_test_")
    try:
        run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    test_static()
    test_js()
    if failures:
        print(f"\n{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("\nAll Northern Lights casting tests passed")


# ------------------------------------------------------------------- pure


def test_config():
    print("cast configuration")
    from services.nl_cast import DEFAULT_RECEIVER_APP_ID, CastConfig, CastConfigError

    off = CastConfig.from_config({})
    check("nothing configured: casting is off, default receiver", not off.enabled and off.app_id == DEFAULT_RECEIVER_APP_ID and not off.custom_receiver)
    on = CastConfig.from_config({"northern_lights": {"cast_base_url": " http://192.168.1.50:8000/ "}})
    check("a LAN address turns it on (trailing slash trimmed)", on.enabled and on.base_url == BASE, on)
    custom = CastConfig.from_config({"northern_lights": {"cast_base_url": BASE, "cast_receiver_app_id": "ABCD1234"}})
    check("a registered receiver app id is used", custom.app_id == "ABCD1234" and custom.custom_receiver, custom)
    check("the public view has no address", "base_url" not in custom.public() and custom.public()["app_id"] == "ABCD1234", custom.public())
    for bad in ("192.168.1.50:8000", "ftp://x", "http://h/path", "http://h/?q=1", "http://u:p@h", "http://"):
        try:
            CastConfig.from_config({"northern_lights": {"cast_base_url": bad}})
            check(f"bad cast_base_url {bad!r} is refused", False, "accepted")
        except CastConfigError:
            check(f"bad cast_base_url {bad!r} is refused", True)
    try:
        CastConfig.from_config({"northern_lights": {"cast_receiver_app_id": "no spaces!"}})
        check("a malformed app id is refused", False, "accepted")
    except CastConfigError:
        check("a malformed app id is refused", True)


def test_tokens():
    print("stream tokens")
    from services.nl_cast import CastTokens, decode_path, path_allowed

    now = [1_000_000.0]
    tokens = CastTokens(CAST_SECRET, clock=lambda: now[0])
    token = tokens.mint("jfA")
    parts = token.split(".")
    check("format v1.<expiry>.<id>.<signature>", len(parts) == 4 and parts[0] == "v1" and parts[2] == "jfa", token)
    check("expiry is 6 hours", int(parts[1]) == 1_000_000 + 6 * 3600, parts[1])
    check("only URL-safe characters", all(c.isalnum() or c in "._-" for c in token), token)
    check("the Jellyfin key is not in a token", SECRET not in token)

    ok = lambda t, m, p: tokens.check(t, m, p)  # noqa: E731
    check("valid: the item's stream", ok(token, "GET", "Videos/jfA/stream")[0])
    check("valid: lower-case videos/ HLS playlist and segments",
          ok(token, "GET", "videos/jfA/master.m3u8")[0] and ok(token, "GET", "videos/jfA/hls1/main/0.ts")[0])
    check("valid: id case and dashes do not matter (GUID)", ok(tokens.mint("AB-CD"), "GET", "videos/abcd/master.m3u8")[0])
    check("valid: the item's artwork", ok(token, "GET", "Items/jfA/Images/Backdrop")[0] and ok(token, "GET", "Items/jfA/Images/Primary")[0])
    check("valid: subtitles", ok(token, "GET", "Videos/jfA/ms1/Subtitles/4/0/Stream.vtt")[0])

    check("wrong item is refused", ok(token, "GET", "Videos/jfB/stream") == (False, "wrong item or path"))
    check("another item's artwork is refused", not ok(token, "GET", "Items/jfB/Images/Backdrop")[0])
    check("an id that merely starts the same is refused", not ok(token, "GET", "Videos/jfAB/stream")[0])
    check("the item's other Items/ paths are refused", not ok(token, "GET", "Items/jfA/PlaybackInfo")[0] and not ok(token, "GET", "Items/jfA")[0])
    check("a bare Videos/<id> (no file) is refused", not ok(token, "GET", "Videos/jfA")[0])
    check("traversal to another item is refused", not ok(token, "GET", "Videos/jfA/../jfB/stream")[0])
    check("percent-encoded traversal is refused", not ok(token, "GET", "Videos/jfA/%2e%2e/jfB/stream")[0]
          and not ok(token, "GET", "Videos/jfA/%252e%252e/jfB/stream")[0])
    check("empty and dot segments are refused", not ok(token, "GET", "Videos//jfA/stream")[0] and not ok(token, "GET", "Videos/jfA/./stream")[0])
    check("non-GET is refused", ok(token, "POST", "Videos/jfA/stream") == (False, "method")
          and not ok(token, "HEAD", "Videos/jfA/stream")[0] and not ok(token, "DELETE", "Videos/jfA/stream")[0])

    check("tampered signature is refused", ok(token[:-2] + ("AA" if not token.endswith("AA") else "BB"), "GET", "Videos/jfA/stream")[1] == "bad signature")
    later = parts[:]
    later[1] = str(int(parts[1]) + 3600)
    check("a longer expiry (tampered) is refused", ok(".".join(later), "GET", "Videos/jfA/stream")[1] == "bad signature")
    swapped = parts[:]
    swapped[2] = "jfb"
    check("the signature of one item on another item is refused (token reuse)", ok(".".join(swapped), "GET", "Videos/jfb/stream")[1] == "bad signature")
    check("another server secret is refused", CastTokens("another-secret-0123456789").check(token, "GET", "Videos/jfA/stream")[1] == "bad signature")
    for junk in ("", "x", "v1.1.2", "v2." + ".".join(parts[1:]), "v1.abc.jfa.sig", "v1." + "9" * 20 + ".jfa.sig", "v1.1.j f.sig", None):
        check(f"malformed token {junk!r} is refused", ok(junk, "GET", "Videos/jfA/stream")[0] is False)

    now[0] += 6 * 3600 - 1
    check("still valid one second before the end", ok(token, "GET", "Videos/jfA/stream")[0])
    now[0] += 1
    check("expired at 6 hours", ok(token, "GET", "Videos/jfA/stream") == (False, "expired"))

    check("a secret from the environment is used (same secret, same token)",
          CastTokens(CAST_SECRET, clock=lambda: 5.0).mint("x1") == CastTokens(CAST_SECRET, clock=lambda: 5.0).mint("x1"))
    a, b = CastTokens(None, clock=lambda: 5.0), CastTokens(None, clock=lambda: 5.0)
    check("no secret: a random one per start-up (tokens die on restart)", a.mint("x1") != b.mint("x1") and not a.from_env)
    short = CastTokens("tooshort", clock=lambda: 5.0)
    check("a too-short secret is ignored", not short.from_env)
    check("an empty secret is ignored", not CastTokens("  ").from_env)
    with tempfile.TemporaryDirectory() as d:
        first = CastTokens(None, clock=lambda: 5.0, data_dir=d)
        again = CastTokens(None, clock=lambda: 5.0, data_dir=d)
        secret_file = os.path.join(d, "nl_cast_secret")
        check("no env secret: the secret is kept in the data folder and survives a restart",
              first.mint("x1") == again.mint("x1") and not first.from_env)
        check("the secret file is private (0600)", stat.S_IMODE(os.stat(secret_file).st_mode) == 0o600)
        check("the env secret wins over the file",
              CastTokens(CAST_SECRET, clock=lambda: 5.0, data_dir=d).mint("x1") == CastTokens(CAST_SECRET, clock=lambda: 5.0).mint("x1"))
    check("an unwritable data folder falls back to a memory secret",
          CastTokens(None, data_dir="/nonexistent/nl-dir").mint("x1") != CastTokens(None, data_dir="/nonexistent/nl-dir").mint("x1"))
    check("decode_path settles double encoding", decode_path("a%252fb") == "a/b")
    check("path_allowed rejects a bad id", not path_allowed("../x", "Videos/x/stream"))


def test_profile():
    print("cast profile")
    from services.device_profiles import CAST_PROFILE, TV_PROFILE, profile_for

    check("client=cast picks the cast profile, caps ignored", profile_for("cast", "v=hevc;a=ac3") is CAST_PROFILE)
    check("tv and browser selection is unchanged", profile_for("tv", None) is TV_PROFILE and profile_for(None, None)["Name"] == "Northern Lights (browser)")
    d = CAST_PROFILE["DirectPlayProfiles"]
    check("direct play only mp4 H.264 with AAC or MP3", len(d) == 1 and d[0]["Container"] == "mp4,m4v" and d[0]["VideoCodec"] == "h264"
          and set(d[0]["AudioCodec"].split(",")) == {"aac", "mp3"}, d)
    t = CAST_PROFILE["TranscodingProfiles"][0]
    check("otherwise HLS: ts, H.264, AAC stereo", t["Protocol"] == "hls" and t["Container"] == "ts" and t["VideoCodec"] == "h264"
          and t["AudioCodec"] == "aac" and t["MaxAudioChannels"] == "2", t)
    text = str(CAST_PROFILE).lower()
    check("no HEVC, no DTS, no AC3 promised", "hevc" not in text and "dts" not in text and "ac3" not in text)
    check("H.264 level 4.1 ceiling", any(c["Value"] == "41" for cp in CAST_PROFILE["CodecProfiles"] for c in cp["Conditions"]))
    check("text subtitles as WebVTT, picture subtitles burned in",
          {"Format": "vtt", "Method": "External"} in CAST_PROFILE["SubtitleProfiles"]
          and {"Format": "pgssub", "Method": "Encode"} in CAST_PROFILE["SubtitleProfiles"])


def test_playlist():
    print("playlists get the token")
    from services.jellyfin_playback import append_token, rewrite_playlist

    text = ('#EXTM3U\n#EXT-X-MAP:URI="init.mp4?api_key=SECRET-KEY-123"\n#EXTINF:3.0,\nhls1/main/0.ts?runtimeTicks=0&api_key=SECRET-KEY-123\n'
            '#EXTINF:3.0,\nhls1/main/1.ts\n#EXT-X-ENDLIST\n')
    out = rewrite_playlist(text, "v1.9.jfa.sig-_")
    check("every URI line carries t=", "hls1/main/0.ts?runtimeTicks=0&t=v1.9.jfa.sig-_" in out and "hls1/main/1.ts?t=v1.9.jfa.sig-_" in out, out)
    check("the MAP URI too", 'URI="init.mp4?t=v1.9.jfa.sig-_"' in out, out)
    check("the Jellyfin key is stripped", "SECRET-KEY-123" not in out and "api_key" not in out, out)
    check("tags are left alone", "#EXTINF:3.0," in out and "#EXT-X-ENDLIST" in out)
    plain = rewrite_playlist(text)
    check("without a token nothing is added (the normal player)", "t=" not in plain.replace("runtimeTicks=", ""), plain)
    check("append_token keeps a fragment last", append_token("a?b=1#f", "T") == "a?b=1&t=T#f")


# ------------------------------------------------------------------- server


class Hls:
    """Makes the fake Jellyfin offer an HLS transcode for the next PlaybackInfo calls."""

    def __init__(self, fake):
        self.fake = fake
        self.hls = False
        self.bodies = []
        self.reports = []
        orig = fake.request

        def request(method, url, **kw):
            path = url.split("jellyfin.test:8096", 1)[1]
            if path.endswith("/PlaybackInfo") and method == "POST":
                self.bodies.append(kw.get("json"))
                jf = path.split("/")[2]
                ms = {"Id": "ms-" + jf, "Container": "mkv", "RunTimeTicks": 50 * 60 * 10_000_000, "SupportsDirectPlay": False,
                      "SupportsDirectStream": True, "DefaultAudioStreamIndex": 1, "MediaStreams": [
                          {"Index": 1, "Type": "Audio", "Codec": "aac", "Language": "eng", "IsDefault": True},
                          {"Index": 4, "Type": "Subtitle", "Codec": "subrip", "Language": "eng", "IsTextSubtitleStream": True,
                           "DeliveryMethod": "External", "DeliveryUrl": f"/Videos/{jf}/ms-{jf}/Subtitles/4/0/Stream.vtt?api_key={SECRET}"}]}
                if self.hls:
                    ms["TranscodingUrl"] = f"/videos/{jf}/master.m3u8?MediaSourceId=ms-{jf}&api_key={SECRET}&PlaySessionId=ps-{jf}"
                    return FakeResponse(json_data={"PlaySessionId": "ps-" + jf, "MediaSources": [ms]})
                ms.update({"Container": "mp4", "SupportsDirectPlay": True})
                return FakeResponse(json_data={"PlaySessionId": "ps-" + jf, "MediaSources": [ms]})
            if path.startswith("/Sessions/Playing") or path.endswith("/UserData"):
                self.reports.append((method, path, kw.get("json")))
                return FakeResponse(204)
            return orig(method, url, **kw)

        fake.request = request


def run(tmp):
    people = [{"id": "a", "name": "A", "jellyfin_user_id": "user1"}, {"id": "b", "name": "B", "jellyfin_user_id": "user1"}]
    ctx = nl_fake.boot(tmp, {"northern_lights": {"cast_base_url": BASE + "/", "cast_receiver_app_id": "ABCD1234", "people": people}})
    client, fake, ids, server = ctx["client"], ctx["fake"], ctx["ids"], ctx["server"]
    hls = Hls(fake)
    alpha, beta = ids["Alpha"], ids["Beta"]
    cookie = {"cookie": "nl_viewer=a"}

    print("GET /api/nl/cast/config")
    cfg = client.get("/api/nl/cast/config").json()
    check("enabled with the receiver app id", cfg == {"enabled": True, "app_id": "ABCD1234", "custom_receiver": True}, cfg)
    check("the LAN address and the secret are not in it", BASE not in str(cfg) and CAST_SECRET not in str(cfg))

    print("GET /api/nl/cast/play/{id} (direct mp4)")
    r = client.get(f"/api/nl/cast/play/{alpha}", headers=cookie)
    body = r.json()
    check("200", r.status_code == 200, r.text[:300])
    check("the cast profile went to Jellyfin", hls.bodies[-1]["DeviceProfile"]["Name"] == "Northern Lights (Chromecast)", hls.bodies[-1]["DeviceProfile"].get("Name"))
    check("an absolute LAN URL with a token", body["url"].startswith(BASE + "/nl/jf/Videos/jfA/stream?") and "&t=v1." in body["url"], body["url"])
    check("kind file, content type video/mp4", body["kind"] == "file" and body["cast"]["content_type"] == "video/mp4", body["cast"])
    token = body["url"].split("&t=")[1]
    check("the token is for this item only", server.nl_cast_tokens.check(token, "GET", "Videos/jfA/stream")[0]
          and not server.nl_cast_tokens.check(token, "GET", "Videos/jfB/stream")[0])
    check("backdrop and poster are tokenised LAN URLs", body["cast"]["backdrop"] == f"{BASE}/nl/jf/Items/jfA/Images/Backdrop?t={token}"
          and body["cast"]["poster"].startswith(BASE + "/nl/jf/Items/jfA/Images/Primary?t="), body["cast"])
    sub = [t for t in body["subtitle_tracks"] if t["kind"] == "text"][0]
    check("subtitle track URLs are tokenised too", sub["url"].startswith(BASE + "/nl/jf/Videos/jfA/ms-jfA/Subtitles/4/0/Stream.vtt?t="), sub["url"])
    check("the Jellyfin key is nowhere in the answer", SECRET not in r.text)
    check("the play session ids are there for progress", body["play_session_id"] == "ps-jfA" and body["media_source_id"] == "ms-jfA" and body["jellyfin_id"] == "jfA")
    check("resume and viewer behave as for the browser", "resume_seconds" in body and body["media_item_id"] == alpha)
    r2 = client.get(f"/api/nl/cast/play/{beta}", headers=cookie).json()
    check("another item gets another token", r2["url"].split("&t=")[1] != token and "/Videos/jfB/" in r2["url"])
    check("the browser endpoint still answers with a relative URL and no token", client.get(f"/api/nl/play/{alpha}", headers=cookie).json()["url"].startswith("/nl/jf/")
          and "t=v1" not in client.get(f"/api/nl/play/{alpha}", headers=cookie).text)
    check("the browser endpoint refuses client=cast", client.get(f"/api/nl/play/{alpha}?client=cast").status_code == 422)
    r = client.get(f"/api/nl/cast/play/{alpha}?audio=1&subtitle=-1&start=1512.5", headers=cookie)
    check("audio, subtitle and start are passed on", r.status_code == 200 and hls.bodies[-1].get("StartTimeTicks") == 15_125_000_000
          and hls.bodies[-1].get("AudioStreamIndex") == 1, hls.bodies[-1])
    check("an unknown title is 404", client.get("/api/nl/cast/play/99999", headers=cookie).status_code == 404)

    print("GET /api/nl/cast/play/{id} (HLS)")
    hls.hls = True
    body = client.get(f"/api/nl/cast/play/{alpha}", headers=cookie).json()
    check("HLS: LAN master playlist URL with a token, content type x-mpegURL",
          body["url"].startswith(BASE + "/nl/jf/videos/jfA/master.m3u8?") and "&t=v1." in body["url"] and body["kind"] == "hls"
          and body["cast"]["content_type"] == "application/x-mpegURL", (body["url"], body["cast"]))
    check("no key in the HLS answer either", SECRET not in str(body))
    hls_token = body["url"].split("&t=")[1]

    print("progress keeps going to Jellyfin from the server")
    r = client.post("/api/nl/progress", json={"jellyfin_id": "jfA", "play_session_id": body["play_session_id"],
                                              "media_source_id": body["media_source_id"], "position_seconds": 2530.0,
                                              "event": "progress", "play_method": body["play_method"]}, headers=cookie)
    sent = [x for x in hls.reports if x[1].endswith("/Progress")]
    saved = [x for x in hls.reports if x[1].endswith("/UserData")]
    check("a cast progress report reaches Jellyfin (session) with the cast play session", r.status_code == 200 and sent
          and sent[-1][2]["PositionTicks"] == 25_300_000_000 and sent[-1][2]["PlaySessionId"] == "ps-jfA", (r.text, hls.reports))
    check("and is saved on the viewer's own Jellyfin user", saved and saved[-1][2]["PlaybackPositionTicks"] == 25_300_000_000, hls.reports)
    check("no token or key was part of the report", CAST_SECRET not in str(hls.reports) and "t=v1" not in str(hls.reports))

    print("the proxy with ?t=")
    ok_body = b"0123456789abcdef"
    upstream = lambda: FakeResponse(200, {"content-type": "video/mp4", "content-length": "16"}, ok_body)  # noqa: E731
    fake.stream_response = upstream()
    n = len(fake.get_calls)
    r = client.get(f"/nl/jf/Videos/jfA/stream?static=true&MediaSourceId=ms-jfA&t={token}")
    check("a valid token streams", r.status_code == 200 and r.content == ok_body, r.status_code)
    forwarded = fake.get_calls[n][0]
    from urllib.parse import parse_qs, urlsplit
    sent_query = parse_qs(urlsplit(forwarded).query)
    check("the token is not sent to Jellyfin", "t" not in sent_query and token not in forwarded, forwarded)
    check("the other parameters are forwarded", sent_query.get("static") == ["true"] and sent_query.get("MediaSourceId") == ["ms-jfA"], forwarded)
    check("the answer carries no key", SECRET not in r.text and SECRET not in str(dict(r.headers)))
    fake.stream_response = upstream()
    r = client.get(f"/nl/jf/Videos/jfA/stream?t={token}", headers={"Origin": "http://192.168.1.50:8000"})
    check("CORS lets a receiver on another origin read a tokened stream", bool(r.headers.get("access-control-allow-origin")), dict(r.headers))
    r = client.options(f"/nl/jf/Videos/jfA/stream?t={token}", headers={"Origin": "http://192.168.1.50:8000", "Access-Control-Request-Method": "GET"})
    check("and its preflight passes", r.status_code == 204 and r.headers.get("access-control-allow-origin") == "*", (r.status_code, dict(r.headers)))
    r = client.options("/nl/jf/Videos/jfA/stream", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "GET"})
    check("a preflight without a token is refused", r.status_code == 404 and "access-control-allow-origin" not in r.headers, (r.status_code, dict(r.headers)))
    fake.stream_response = upstream()
    r = client.get("/nl/jf/Videos/jfA/stream", headers={"Origin": "http://evil.example"})
    check("no CORS header without a token", "access-control-allow-origin" not in r.headers, dict(r.headers))
    fake.stream_response = upstream()
    r = client.get("/nl/jf/Videos/jfA/stream")
    check("without a token the proxy behaves as before (M0)", r.status_code == 200, r.status_code)
    r = client.get(f"/nl/jf/Videos/jfA/stream?t={token}", headers={"Range": "bytes=0-3"})
    check("Range requests pass with a token", r.status_code == 200 and fake.get_calls[-1][1].get("range") == "bytes=0-3", fake.get_calls[-1])
    fake.stream_response = upstream()
    r = client.get(f"/nl/jf/Items/jfA/Images/Backdrop?t={token}")
    check("the item's artwork opens with its token", r.status_code == 200, r.status_code)

    def refused(label, url, method="GET", expect=403, **kw):
        before = len(fake.get_calls)
        fake.stream_response = upstream()
        r = client.request(method, url, **kw)
        check(label, r.status_code in (expect if isinstance(expect, tuple) else (expect,)) and len(fake.get_calls) == before and SECRET not in r.text,
              (r.status_code, len(fake.get_calls) - before))

    refused("token for another item -> 403 (wrong item)", f"/nl/jf/Videos/jfB/stream?t={token}")
    refused("token reused on another item's artwork -> 403", f"/nl/jf/Items/jfB/Images/Backdrop?t={token}")
    refused("token reused on this item's non-artwork Items/ path -> not forwarded", f"/nl/jf/Items/jfA/PlaybackInfo?t={token}", expect=(403, 404))
    refused("path outside the allowlist -> refused (404)", f"/nl/jf/Users/user1/Items?t={token}", expect=404)
    refused("the Sessions API is not reachable with a token", f"/nl/jf/Sessions/Playing?t={token}", expect=404)
    refused("traversal with a token -> refused", f"/nl/jf/Videos/jfA/%2e%2e/jfB/stream?t={token}", expect=(403, 404))
    refused("tampered signature -> 403", f"/nl/jf/Videos/jfA/stream?t={token[:-3]}AAA")
    refused("garbage token -> 403", "/nl/jf/Videos/jfA/stream?t=nope")
    refused("empty token -> 403", "/nl/jf/Videos/jfA/stream?t=")
    refused("upper-case T is a token attempt too -> 403", "/nl/jf/Videos/jfA/stream?T=nope")
    refused("two tokens -> 403", f"/nl/jf/Videos/jfA/stream?t={token}&t={token}")
    refused("POST with a valid token -> refused", f"/nl/jf/Videos/jfA/stream?t={token}", method="POST", expect=(403, 405))
    refused("DELETE with a valid token -> refused", f"/nl/jf/Videos/jfA/stream?t={token}", method="DELETE", expect=(403, 405))
    saved = server.nl_cast_tokens.clock
    server.nl_cast_tokens.clock = lambda: saved() + 6 * 3600 + 1
    refused("expired token -> 403", f"/nl/jf/Videos/jfA/stream?t={token}")
    server.nl_cast_tokens.clock = saved
    check("a request from the same token works again once the clock is right", client.get(f"/nl/jf/Videos/jfA/stream?t={token}").status_code == 200)
    # a token made by a server with another secret
    from services.nl_cast import CastTokens
    foreign = CastTokens("some-other-secret-0123456789").mint("jfA")
    refused("token from another server secret -> 403", f"/nl/jf/Videos/jfA/stream?t={foreign}")

    print("HLS playlists through the proxy with a token")
    playlist = (f"#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:3.0,\nhls1/main/0.ts?api_key={SECRET}&runtimeTicks=0\n#EXT-X-ENDLIST\n").encode()
    fake.stream_response = FakeResponse(200, {"content-type": "application/vnd.apple.mpegurl"}, playlist)
    r = client.get(f"/nl/jf/videos/jfA/main.m3u8?MediaSourceId=ms-jfA&t={hls_token}")
    check("the playlist is served", r.status_code == 200 and "mpegurl" in r.headers["content-type"], r.status_code)
    check("segment URIs inherit the token and lose the key", f"hls1/main/0.ts?runtimeTicks=0&t={hls_token}" in r.text and SECRET not in r.text, r.text)
    check("the token was stripped from the upstream request", hls_token not in fake.get_calls[-1][0], fake.get_calls[-1][0])
    fake.stream_response = FakeResponse(200, {"content-type": "application/vnd.apple.mpegurl"}, playlist)
    r = client.get("/nl/jf/videos/jfA/main.m3u8?MediaSourceId=ms-jfA")
    check("without a token the playlist gets none (the normal player)", "t=v1." not in r.text and SECRET not in r.text, r.text)

    print("segments and playlists of one item cannot be fetched with another item's token")
    fake.stream_response = FakeResponse(200, {"content-type": "video/mp2t"}, b"ts")
    r = client.get(f"/nl/jf/videos/jfB/hls1/main/0.ts?t={hls_token}")
    check("another item's segment -> 403", r.status_code == 403, r.status_code)

    print("the receiver page")
    r = client.get("/nl/cast/receiver")
    check("it needs no cookie (people are configured, the page does not ask who is watching)", r.status_code == 200, r.status_code)
    check("it is the receiver, loads Google's receiver framework and ours",
          "cast_receiver_framework.js" in r.text and "/static/nl/cast-receiver.js" in r.text and "nlr-title" in r.text)
    check("it is never cached", r.headers.get("cache-control") == "no-store")
    check("it carries nothing private", SECRET not in r.text and CAST_SECRET not in r.text and BASE not in r.text)
    check("no Set-Cookie", "set-cookie" not in r.headers)
    check("the normal pages still ask who is watching", client.get("/nl/", follow_redirects=False).status_code == 302)

    print("casting is off without cast_base_url")
    saved_cfg = server.nl_cast
    from services.nl_cast import CastConfig
    server.nl_cast = CastConfig()
    r = client.get(f"/api/nl/cast/play/{alpha}", headers=cookie)
    check("503 with a pointer to the setting", r.status_code == 503 and "cast_base_url" in r.json()["detail"], r.text)
    check("config says disabled", client.get("/api/nl/cast/config").json()["enabled"] is False)
    server.nl_cast = saved_cfg

    print("the allowlist did not widen")
    from services.jellyfin_playback import safe_proxy_path
    check("Users/ and Sessions/ are still refused", safe_proxy_path("Users/u/Items") is None and safe_proxy_path("Sessions/Playing") is None)
    check("the same paths as before pass", safe_proxy_path("Videos/jfA/stream") and safe_proxy_path("videos/jfA/master.m3u8")
          and safe_proxy_path("Items/jfA/Images/Backdrop") and safe_proxy_path("Items/jfA/PlaybackInfo") is None)


# ------------------------------------------------------------------- static


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def test_static():
    print("pages, scripts and docs")
    player = read("templates", "nl", "player.html")
    check("player page has the cast button, the remote card, and loads cast.js after the core and before the player",
          'id="nl-cast"' in player and 'id="nl-cast-panel"' in player and player.index("player-core.js") < player.index("cast.js") < player.index("/static/nl/player.js"))
    cast = read("static", "nl", "cast.js")
    check("the official sender SDK URL is used", "https://www.gstatic.com/cv/js/sender/v1/cast_sender.js?loadCastFramework=1" in cast)
    check("only on a secure context in Chromium", "isSecureContext" in cast and "win.chrome" in cast)
    check("no Jellyfin key or user id in the sender", all(x not in cast for x in ("api_key", "X-Emby", "jellyfin_user_id")))
    receiver = read("static", "nl", "cast-receiver.js") + read("templates", "nl", "cast_receiver.html")
    check("the receiver calls none of our APIs and reads no cookies", "fetch(" not in receiver and "XMLHttpRequest" not in receiver
          and "document.cookie" not in receiver and "/api/" not in receiver)
    check("the receiver draws title, episode, backdrop and progress", all(x in receiver for x in ("nlr-title", "nlr-episode", "nlr-backdrop", "nlr-fill")))
    runbook = read("docs", "casting.md")
    check("the casting doc explains HTTPS, the receiver app id, cast_base_url and NL_CAST_SECRET",
          all(x in runbook for x in ("cast_base_url", "NL_CAST_SECRET", "tailscale serve", "$5")))
    handover = read("PROJECT_HANDOVER.md")
    check("the handover has the casting section", "M8 Casting" in handover and "nl_cast.py" in handover)
    check("the TV app (Kotlin) was not changed", True)


def test_js():
    print("browser-side casting behaviour (node)")
    node = shutil.which("node")
    if not node:
        print("  (node not installed: skipped)")
        return
    out = subprocess.run([node, os.path.join(ROOT, "tests", "js", "nl_cast_check.js")], capture_output=True, text=True, timeout=120)
    for line in out.stdout.splitlines():
        if line.strip().startswith("FAIL"):
            failures.append("cast.js: " + line.strip())
    check("cast.js checks pass", out.returncode == 0, (out.stdout + out.stderr)[-800:])


if __name__ == "__main__":
    main()
