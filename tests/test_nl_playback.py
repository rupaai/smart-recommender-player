"""
Offline tests for the Northern Lights M0 playback path (tests/test_nl_playback.py)

Jellyfin is faked (a stand-in requests session); nothing leaves the process.

    uv run python tests/test_nl_playback.py
"""

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402

failures = []
SECRET = "SECRET-KEY-123"


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} — got: {detail}")
        failures.append(label)


class FakeResponse:
    def __init__(self, status=200, headers=None, body=b"", json_data=None):
        self.status_code = status
        self.headers = headers or {}
        self._body = body
        self._json = json_data
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def json(self):
        return self._json

    def iter_content(self, chunk_size=1):
        for i in range(0, len(self._body), 4):
            yield self._body[i:i + 4]

    def close(self):
        self.closed = True


class FakeJellyfin:
    """Stands in for JellyfinService.session."""

    def __init__(self):
        self.items = [{"Id": "jf1", "Path": "/media/Movies/A (2020)/A.mp4"}]
        self.items_calls = 0
        self.playback = {}
        self.down = False
        self.get_calls = []
        self.last_get = None
        self.stream_response = None

    def request(self, method, url, **kw):
        if self.down:
            raise requests.ConnectionError("down")
        if url.endswith("/Items") and method == "GET":
            self.items_calls += 1
            return FakeResponse(json_data={"Items": list(self.items)})
        if "/PlaybackInfo" in url and method == "POST":
            self.last_playback_body = kw.get("json")
            return FakeResponse(json_data=self.playback)
        return FakeResponse(404, json_data={})

    def get(self, url, headers=None, **kw):
        if self.down:
            raise requests.ConnectionError("down")
        self.get_calls.append((url, dict(headers or {})))
        return self.stream_response


def main():
    tmp = tempfile.mkdtemp(prefix="nl_playback_test_")
    try:
        run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if failures:
        print(f"\n{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("\nAll Northern Lights playback tests passed")


def run(tmp):
    cfg_file = os.path.join(tmp, "config.yaml")
    with open(cfg_file, "w", encoding="utf-8") as f:
        json.dump({
            "media_folders": [os.path.join(tmp, "Movies"), os.path.join(tmp, "Shows")],
            "jellyfin_library": {"movie_dir": os.path.join(tmp, "Movies"), "tv_dir": os.path.join(tmp, "Shows")},
            "torrent": {"download_dir": os.path.join(tmp, "downloads")},
        }, f)  # JSON is valid YAML
    os.environ["CONFIG_PATH"] = cfg_file
    os.environ["DATABASE_PATH"] = os.path.join(tmp, "nl.db")
    import server
    from fastapi.testclient import TestClient
    from services import jellyfin_playback as jp

    # ---- pure helpers
    print("strip_api_key / rewrite_playlist")
    check("strips api_key and ApiKey, keeps other params",
          jp.strip_api_key("/videos/1/a.ts?x=1&api_key=ZZ&ApiKey=YY&y=2") == "/videos/1/a.ts?x=1&y=2",
          jp.strip_api_key("/videos/1/a.ts?x=1&api_key=ZZ&ApiKey=YY&y=2"))
    check("drops the ? when only a key was there", jp.strip_api_key("/a.ts?api_key=ZZ") == "/a.ts")
    check("no query is untouched", jp.strip_api_key("segment0.ts") == "segment0.ts")
    playlist = (
        "#EXTM3U\n#EXT-X-VERSION:3\n"
        '#EXT-X-MAP:URI="init.mp4?api_key=ZZ&n=1"\n'
        "#EXTINF:6.0,\nseg0.ts?x=1&ApiKey=ZZ\n#EXT-X-ENDLIST\n"
    )
    out = jp.rewrite_playlist(playlist)
    check("playlist has no key left", "ZZ" not in out and "api_key" not in out.lower(), out)
    check("playlist URI line and attribute keep other params",
          "seg0.ts?x=1\n" in out and 'URI="init.mp4?n=1"' in out, out)
    check("playlist #EXT lines untouched",
          "#EXTM3U" in out and "#EXT-X-VERSION:3" in out and "#EXTINF:6.0," in out and "#EXT-X-ENDLIST" in out, out)

    print("safe_proxy_path")
    for good in ("videos/a/stream", "Videos/a/master.m3u8"):
        check(f"allows {good}", jp.safe_proxy_path(good) == good)
    for bad in ("System/Info", "Users", "Users/x/Items", "videos/../System/Info", "../videos/a",
                "videos/%2e%2e/Users", "videos/%252e%252e/Users", "videos/..%2fUsers", "",
                "videosX/a", "videos/a\\..\\b", "videos//a", "Items/videos/a"):
        check(f"refuses {bad!r}", jp.safe_proxy_path(bad) is None)

    # ---- service with a fake Jellyfin
    fake = FakeJellyfin()
    svc = server.jellyfin_service
    svc.base_url = "http://jellyfin.test:8096"
    svc.api_key = SECRET
    svc.user_id = "user1"
    svc.session = fake
    playback = server.jellyfin_playback
    playback._ids = {}

    print("path lookup")
    check("finds the id", playback.find_item_id("/media/Movies/A (2020)/A.mp4") == "jf1")
    check("one fetch so far", fake.items_calls == 1, fake.items_calls)
    check("cache hit does not refetch", playback.find_item_id("/media/Movies/A (2020)/A.mp4") == "jf1" and fake.items_calls == 1)
    fake.items.append({"Id": "jf2", "Path": "/media/Movies/B (2021)/B.mkv"})
    check("miss refreshes once and finds new item", playback.find_item_id("/media/Movies/B (2021)/B.mkv") == "jf2"
          and fake.items_calls == 2, fake.items_calls)
    check("unknown path: one refresh, then None", playback.find_item_id("/nope.mp4") is None and fake.items_calls == 3,
          fake.items_calls)

    print("stream choice")
    item = {"title": "A", "sources": [{"file_path": "/media/Movies/A (2020)/A.mp4"}]}
    fake.playback = {"PlaySessionId": "ps1", "MediaSources": [{
        "Id": "ms1", "Container": "mp4", "SupportsDirectPlay": True, "SupportsDirectStream": True,
        "RunTimeTicks": 72_000_000_000}]}
    s = playback.stream_for(item)
    check("direct play when SupportsDirectPlay", s["kind"] == "file" and s["play_method"] == "DirectPlay", s)
    check("direct url is proxied with ids", s["url"] == "/nl/jf/Videos/jf1/stream?static=true&MediaSourceId=ms1&PlaySessionId=ps1", s["url"])
    check("duration and title", s["duration_seconds"] == 7200 and s["title"] == "A", s)
    check("profile and bitrate sent",
          fake.last_playback_body["DeviceProfile"]["Name"].startswith("Northern Lights")
          and fake.last_playback_body["MaxStreamingBitrate"] == 20_000_000
          and fake.last_playback_body["EnableTranscoding"] is True, fake.last_playback_body)
    fake.playback = {"PlaySessionId": "ps2", "MediaSources": [{
        "Id": "ms2", "Container": "mkv", "SupportsDirectPlay": False, "SupportsDirectStream": False,
        "TranscodingUrl": f"/videos/jf1/master.m3u8?MediaSourceId=ms2&api_key={SECRET}&PlaySessionId=ps2"}]}
    s = playback.stream_for(item)
    check("HLS otherwise", s["kind"] == "hls" and s["play_method"] == "Transcode", s)
    check("transcoding url is proxied and key stripped",
          s["url"] == "/nl/jf/videos/jf1/master.m3u8?MediaSourceId=ms2&PlaySessionId=ps2" and SECRET not in s["url"], s["url"])

    client = TestClient(server.app)

    print("GET /api/nl/play/{id}")
    server.db.register_source_file("/media/Movies/A (2020)/A.mp4", "A", 2020)
    item_id = server.db.get_item_by_id(1)["id"]
    fake.playback = {"PlaySessionId": "ps1", "MediaSources": [{
        "Id": "ms1", "Container": "mp4", "SupportsDirectPlay": True, "RunTimeTicks": 10_000_000}]}
    r = client.get(f"/api/nl/play/{item_id}")
    check("200 with stream info", r.status_code == 200 and r.json()["kind"] == "file", r.text)
    check("no API key in the JSON", SECRET not in r.text)
    check("404 for unknown item", client.get("/api/nl/play/9999").status_code == 404)
    server.db.register_source_file("/media/Movies/Z (2022)/Z.mp4", "Z", 2022)
    r = client.get("/api/nl/play/2")
    check("404 when Jellyfin does not know the file", r.status_code == 404, r.text)
    fake.down = True
    playback._ids = {}
    r = client.get(f"/api/nl/play/{item_id}")
    check("503 (not 500) when Jellyfin is down", r.status_code == 503, f"{r.status_code} {r.text}")
    check("503 body has a clear message and no key", "Jellyfin" in r.json().get("detail", "") and SECRET not in r.text, r.text)
    fake.down = False
    svc.api_key = None
    r = client.get(f"/api/nl/play/{item_id}")
    check("503 when Jellyfin is not configured", r.status_code == 503, r.status_code)
    svc.api_key = SECRET

    print("GET /nl/jf/ proxy")
    for path in ("System/Info", "Users", "Items", "videos/../System/Info", "videos/%2e%2e/System/Info",
                 "videos/%2E%2E/Users", "videos/%252e%252e/Users", "Videos/..%2fUsers"):
        fake.get_calls.clear()
        r = client.get(f"/nl/jf/{path}")
        check(f"refuses {path}", r.status_code == 404 and not fake.get_calls, f"{r.status_code} calls={len(fake.get_calls)}")

    resp = FakeResponse(206, {"content-type": "video/mp4", "content-length": "12",
                              "content-range": "bytes 0-11/100", "accept-ranges": "bytes",
                              "x-secret-header": SECRET, "set-cookie": "a=b"}, body=b"0123456789ab")
    fake.stream_response = resp
    r = client.get(f"/nl/jf/Videos/jf1/stream?static=true&api_key={SECRET}&ApiKey={SECRET}",
                   headers={"Range": "bytes=0-11", "Authorization": "Bearer mine"})
    check("passes Jellyfin's 206 through", r.status_code == 206, r.status_code)
    check("body is streamed through", r.content == b"0123456789ab", r.content)
    check("range headers returned", r.headers.get("content-range") == "bytes 0-11/100"
          and r.headers.get("accept-ranges") == "bytes" and r.headers.get("content-type") == "video/mp4", dict(r.headers))
    check("other upstream headers not leaked", "x-secret-header" not in r.headers and "set-cookie" not in r.headers)
    url, hdrs = fake.get_calls[-1]
    check("Range forwarded upstream", hdrs.get("range") == "bytes=0-11", hdrs)
    check("upstream url ok and client keys removed",
          url == "http://jellyfin.test:8096/Videos/jf1/stream?static=true" and SECRET not in url, url)
    check("client Authorization not forwarded", "authorization" not in {k.lower() for k in hdrs}, hdrs)
    check("API key not in response headers or body", SECRET not in str(dict(r.headers)) and SECRET.encode() not in r.content)
    check("upstream response closed", resp.closed)

    fake.stream_response = FakeResponse(200, {"content-type": "video/mp4"}, body=b"abc")
    r = client.get("/nl/jf/videos/jf1/stream")
    check("200 passes through", r.status_code == 200 and r.content == b"abc", r.status_code)

    fake.stream_response = FakeResponse(404, {"content-type": "text/plain"}, body=b"nope")
    r = client.get("/nl/jf/videos/jf1/missing.ts")
    check("upstream 404 passes through", r.status_code == 404, r.status_code)

    m3u8 = (f"#EXTM3U\n#EXT-X-MAP:URI=\"init.mp4?api_key={SECRET}\"\n#EXTINF:6.0,\n"
            f"hls1/main/0.ts?runtimeTicks=0&ApiKey={SECRET}\n#EXT-X-ENDLIST\n").encode()
    pl = FakeResponse(200, {"content-type": "application/x-mpegURL"}, body=m3u8)
    fake.stream_response = pl
    r = client.get("/nl/jf/videos/jf1/master.m3u8?api_key=" + SECRET)
    check("playlist returned as mpegurl", r.status_code == 200 and "mpegurl" in r.headers["content-type"].lower(),
          f"{r.status_code} {r.headers.get('content-type')}")
    check("playlist rewritten, no key anywhere", SECRET not in r.text and "api_key" not in r.text.lower(), r.text)
    check("playlist keeps other params", "hls1/main/0.ts?runtimeTicks=0" in r.text, r.text)
    check("playlist upstream closed", pl.closed)

    fake.stream_response = FakeResponse(200, {"content-type": "application/octet-stream"}, body=b"#EXTM3U\nx.ts?api_key=" + SECRET.encode())
    r = client.get("/nl/jf/videos/jf1/live.m3u8")
    check("playlist detected by .m3u8 extension", SECRET not in r.text and "x.ts" in r.text, r.text)

    fake.down = True
    r = client.get("/nl/jf/videos/jf1/stream")
    check("502 when Jellyfin is unreachable", r.status_code == 502, r.status_code)
    fake.down = False

    print("GET /nl/play/{id}")
    r = client.get("/nl/play/1")
    check("player page served", r.status_code == 200 and "<video" in r.text and "/static/nl/player.js" in r.text, r.status_code)
    check("player page has no key", SECRET not in r.text)
    check("static player files served",
          client.get("/static/nl/player-core.js").status_code == 200 and client.get("/static/nl/player.js").status_code == 200)


if __name__ == "__main__":
    main()
