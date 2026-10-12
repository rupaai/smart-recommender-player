"""Shared stand-ins for the Northern Lights M2 tests (not a test itself).

A fake Jellyfin (the object that replaces JellyfinService.session) and a small library
in a throw-away database: two movies, one show with 4 episodes, one title we do not have.
"""

import json
import os

import requests

SECRET = "SECRET-KEY-123"
ALPHA = "/media/Movies/Alpha (2020)/Alpha.mp4"
BETA = "/media/Movies/Beta (2021)/Beta.mkv"
EPS = {
    (1, 1): "/media/Shows/Sigma/Season 1/Sigma S01E01.mkv",
    (1, 2): "/media/Shows/Sigma/Season 1/Sigma S01E02.mkv",
    (1, 3): "/media/Shows/Sigma/Season 1/Sigma S01E03.mkv",
    (2, 1): "/media/Shows/Sigma/Season 2/Sigma S02E01.mkv",
}
TICKS = 10_000_000


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
        if self._json is None:
            raise ValueError("no body")
        return self._json

    def iter_content(self, chunk_size=1):
        for i in range(0, len(self._body), 4):
            yield self._body[i:i + 4]

    def close(self):
        self.closed = True


def ud(minutes_in=0, run_minutes=50, played=False):
    return {"PlaybackPositionTicks": int(minutes_in * 60 * TICKS), "Played": played}


class FakeJellyfin:
    """Stands in for JellyfinService.session."""

    def __init__(self):
        self.down = False
        self.calls = []        # (method, path, params)
        self.get_calls = []    # proxy GETs: (url, headers)
        self.stream_response = None
        self.scans = 0
        self.items = {ALPHA: "jfA", BETA: "jfB"}
        for (s, e), path in EPS.items():
            self.items[path] = f"jfS{s}E{e}"
        # Alpha is 40 minutes into a 100 minute film; Sigma S1E1 is done, S1E2 half way.
        self.resume = [
            {"Id": "jfS1E2", "Path": EPS[(1, 2)], "Type": "Episode", "Name": "Second",
             "RunTimeTicks": 50 * 60 * TICKS, "UserData": ud(25), "ImageTags": {"Primary": "t"}},
            {"Id": "jfA", "Path": ALPHA, "Type": "Movie", "Name": "Alpha",
             "RunTimeTicks": 100 * 60 * TICKS, "UserData": ud(40), "BackdropImageTags": ["b"]},
            {"Id": "jfS1E3", "Path": EPS[(1, 3)], "Type": "Episode", "Name": "Third",
             "RunTimeTicks": 50 * 60 * TICKS, "UserData": ud(5)},
            {"Id": "zzz", "Path": "/media/elsewhere/unknown.mkv", "Type": "Movie", "Name": "Unknown",
             "RunTimeTicks": 100 * 60 * TICKS, "UserData": ud(10)},
        ]
        self.episodes = [
            self._ep((1, 1), "First", played=True),
            self._ep((1, 2), "Second", minutes=25),
            self._ep((1, 3), "Third"),
            self._ep((2, 1), "Premiere"),
        ]
        self.next_up = [self.episodes[1]]

    @staticmethod
    def _ep(key, name, played=False, minutes=0):
        return {"Id": f"jfS{key[0]}E{key[1]}", "Path": EPS[key], "Type": "Episode", "Name": name,
                "Overview": f"{name} overview", "RunTimeTicks": 50 * 60 * TICKS,
                "UserData": ud(minutes, played=played), "ImageTags": {"Primary": "t"}}

    def request(self, method, url, **kw):
        if self.down:
            raise requests.ConnectionError("down")
        path = url.split("jellyfin.test:8096", 1)[1]
        params = kw.get("params") or {}
        self.calls.append((method, path, params))
        if path == "/Users":
            return FakeResponse(json_data=[{"Name": "User1", "Id": "user1"}])
        if path == "/Items":
            self.scans += 1
            return FakeResponse(json_data={"Items": [{"Id": i, "Path": p} for p, i in self.items.items()]})
        if path.endswith("/PlaybackInfo") and method == "POST":
            jf = path.split("/")[2]
            return FakeResponse(json_data={"PlaySessionId": "ps-" + jf, "MediaSources": [{
                "Id": "ms-" + jf, "Container": "mp4", "RunTimeTicks": 50 * 60 * TICKS, "SupportsDirectPlay": True,
                "SupportsDirectStream": True, "DefaultAudioStreamIndex": 1, "MediaStreams": []}]})
        if path == "/Users/user1/Items/Resume":
            return FakeResponse(json_data={"Items": self.resume})
        if path == "/Shows/NextUp":
            return FakeResponse(json_data={"Items": self.next_up})
        if path == "/Shows/series1/Episodes":
            return FakeResponse(json_data={"Items": self.episodes})
        if path == "/Users/user1/Items" and params.get("SearchTerm"):
            term = params["SearchTerm"].lower()
            return FakeResponse(json_data={"Items": [e for e in self.episodes if term in e["Name"].lower()]})
        if path.startswith("/Users/user1/Items/jfS"):
            return FakeResponse(json_data={"Id": path.rsplit("/", 1)[1], "SeriesId": "series1", "Type": "Episode"})
        if path == "/Users/user1/Items/jfA":
            return FakeResponse(json_data={"Id": "jfA", "Type": "Movie", "UserData": ud(40), "RunTimeTicks": 100 * 60 * TICKS})
        if path.startswith("/Users/user1/Items/"):
            return FakeResponse(json_data={"Id": path.rsplit("/", 1)[1], "Type": "Movie", "UserData": {}})
        return FakeResponse(404, json_data={})

    def get(self, url, headers=None, **kw):
        if self.down:
            raise requests.ConnectionError("down")
        self.get_calls.append((url, dict(headers or {})))
        return self.stream_response


def boot(tmp):
    """Imports server against a fresh DB in tmp, loads the library, wires the fake. Returns a dict."""
    from services.jellyfin_playback import JellyfinPlayback
    JellyfinPlayback.file_exists = staticmethod(lambda path: True)
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

    db = server.db
    db.register_source_file(ALPHA, "Alpha", 2020, "movie")
    db.register_source_file(BETA, "Beta", 2021, "movie")
    for (s, e), path in EPS.items():
        db.register_source_file(path, "Sigma", 2019, "tv", season_number=s, episode_number=e)
    db.upsert_catalogue_items([{"media_type": "movie", "tmdb_id": 999, "title": "Gamma Ray", "release_year": 2018,
                                "poster_url": "https://img.test/gamma.jpg"}])
    with db.get_connection() as conn:
        conn.execute("UPDATE media_items SET poster_url = 'https://img.test/alpha.jpg', runtime = 100, genres = 'Drama, Thriller', "
                     "overview = 'About Alpha.' WHERE title = 'Alpha'")
        conn.execute("UPDATE media_items SET runtime = 95 WHERE title = 'Beta'")
        conn.execute("UPDATE media_items SET poster_url = 'https://img.test/sigma.jpg', genres = 'Sci-Fi', overview = 'About Sigma.' "
                     "WHERE title = 'Sigma'")
        # Alpha arrived long ago; Beta and Sigma are fresh.
        conn.execute("UPDATE media_sources SET created_at = '2020-01-01 10:00:00' WHERE file_path = ?", (ALPHA,))

    fake = FakeJellyfin()
    svc = server.jellyfin_service
    svc.base_url = "http://jellyfin.test:8096"
    svc.api_key = SECRET
    svc.user_id = "user1"
    svc.session = fake
    server.jellyfin_playback._ids = {}
    server.jellyfin_playback._resolved_user = None
    server.jellyfin_playback._last_refresh = 0.0
    ids = {}
    with db.get_connection() as conn:
        for r in conn.execute("SELECT id, title FROM media_items"):
            ids[r["title"]] = r["id"]
        ids["sources"] = {r["file_path"]: r["id"] for r in conn.execute("SELECT id, file_path FROM media_sources")}
    return {"server": server, "client": TestClient(server.app), "fake": fake, "ids": ids, "db": db}
