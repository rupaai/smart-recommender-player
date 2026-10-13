"""
Offline tests for Northern Lights M7b, "browse polish" (tests/test_nl_polish_b.py): Recently Added and Recently
Watched rows, "Unwatched only", "Hide from home" (per viewer), Cast & crew, Trailer, Tech details and "More like this".

Jellyfin and TMDB are faked (a stand-in requests session and a stand-in TMDB reader); nothing leaves the process.

    uv run python tests/test_nl_polish_b.py
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nl_fake  # noqa: E402
import requests  # noqa: E402
from nl_fake import ALPHA, BETA, EPS, SECRET, TICKS, FakeResponse  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAB = "a" * 32
PAR = "b" * 32
TMDB_KEY = "TMDB-SECRET-9"
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


def played(jid, path, kind, when):
    return {"Id": jid, "Path": path, "Type": kind, "Name": jid,
            "UserData": {"Played": True, "PlaybackPositionTicks": 0, "LastPlayedDate": when}}


PERSON_IMG = "c" * 32
DETAILS_ALPHA = {
    "Id": "jfA", "Type": "Movie", "UserData": nl_fake.ud(40), "RunTimeTicks": 100 * 60 * TICKS,
    "People": [
        {"Name": "Ann Actor", "Id": PERSON_IMG, "Role": "Lead", "Type": "Actor", "PrimaryImageTag": "t"},
        {"Name": "Bo Noimage", "Id": "d" * 32, "Role": "Friend", "Type": "Actor"},
        {"Name": "Dee Rector", "Id": "e" * 32, "Type": "Director", "PrimaryImageTag": "t"},
        {"Name": "Ann Actor", "Id": PERSON_IMG, "Role": "Lead", "Type": "Actor", "PrimaryImageTag": "t"},
        {"Name": "Gus Gaffer", "Id": "f" * 32, "Type": "Gaffer"},
    ],
    "RemoteTrailers": [{"Url": "https://www.youtube.com/watch?v=jfTrailer01", "Name": "From Jellyfin"}],
    "MediaSources": [{
        "Container": "mkv,webm", "Size": 4509715660, "Bitrate": 8123456,
        "MediaStreams": [
            {"Type": "Video", "Codec": "hevc", "Width": 1920, "Height": 800, "VideoRangeType": "HDR10", "RealFrameRate": 23.976},
            {"Type": "Audio", "Codec": "ac3", "DisplayLanguage": "English", "ChannelLayout": "5.1", "IsDefault": True},
            {"Type": "Audio", "Codec": "aac", "Language": "spa", "Channels": 2},
            {"Type": "Subtitle", "Language": "eng", "DisplayLanguage": "English"},
            {"Type": "Subtitle", "Language": "spa", "DisplayLanguage": "Spanish"},
            {"Type": "Subtitle", "Language": "eng", "DisplayLanguage": "English"},
        ],
        "Path": "/media/Movies/Alpha (2020)/Alpha.mp4",
    }],
}
DETAILS_SERIES = {
    "Id": "series1", "Type": "Series",
    "People": [{"Name": "Sia Star", "Id": "9" * 32, "Role": "Captain", "Type": "Actor", "PrimaryImageTag": "t"}],
    "RemoteTrailers": [],
}


class PolishJellyfin(nl_fake.FakeJellyfin):
    """The usual fake plus: the played lists of each user, item details with People / MediaSources / RemoteTrailers."""

    def __init__(self):
        super().__init__()
        self.played_by = {"user1": [
            played("jfS1E1", EPS[(1, 1)], "Episode", "2026-10-12T20:00:00.0000000Z"),
            played("jfB", BETA, "Movie", "2026-10-01T20:00:00.0000000Z"),
            played("zzz", "/media/elsewhere/unknown.mkv", "Movie", "2026-10-11T20:00:00.0000000Z"),
        ]}
        self.details = {"jfA": dict(DETAILS_ALPHA), "series1": dict(DETAILS_SERIES)}
        self.played_calls = []

    def request(self, method, url, **kw):
        path = url.split("jellyfin.test:8096", 1)[1]
        params = kw.get("params") or {}
        if path.startswith("/Users/") and path.endswith("/Items") and params.get("Filters") == "IsPlayed":
            if self.down:
                raise requests.ConnectionError("down")
            uid = path.split("/")[2]
            self.played_calls.append((uid, params))
            return FakeResponse(json_data={"Items": self.played_by.get(uid, [])})
        if path.startswith("/Users/") and "/Items/" in path and path.rsplit("/", 1)[1] in self.details:
            if self.down:
                raise requests.ConnectionError("down")
            self.calls.append((method, path, params))
            return FakeResponse(json_data=self.details[path.rsplit("/", 1)[1]])
        return super().request(method, url, **kw)


class FakeVibe:
    """Stands in for the recommender's VibeIndex."""

    def __init__(self, ready=True, sims=None, fail=False):
        self.ready, self.sims, self.fail = ready, sims or {}, fail

    def is_ready(self):
        if self.fail:
            raise RuntimeError("boom")
        return self.ready

    def item_vector(self, item_id):
        return [1.0] if item_id in self.sims.get("_has", [item_id]) else None

    def similarities(self, vec, ids):
        return {i: s for i, s in self.sims.items() if i != "_has" and i in set(ids)}


def main():
    tmp = tempfile.mkdtemp(prefix="nl_polish_b_test_")
    try:
        test_pure()
        test_migration(tmp)
        run(tmp)
        test_static()
        test_js_behaviour()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if failures:
        print(f"\n{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("\nAll Northern Lights browse polish (M7b) tests passed")


# ---------------------------------------------------------------------- pure helpers


def test_pure():
    from services import nl_browse as nb
    from services import nl_polish_b as pb

    print("trailers")
    check("youtube_key: bare key, watch, youtu.be, embed, shorts",
          pb.youtube_key("abcDEF_123") == "abcDEF_123" and pb.youtube_key("https://www.youtube.com/watch?v=abcDEF_123") == "abcDEF_123"
          and pb.youtube_key("https://youtu.be/abcDEF_123?t=4") == "abcDEF_123" and pb.youtube_key("https://www.youtube.com/embed/abcDEF_123") == "abcDEF_123"
          and pb.youtube_key("https://www.youtube.com/watch?feature=x&v=abcDEF_123") == "abcDEF_123")
    check("youtube_key: nothing else", pb.youtube_key("javascript:alert(1)") is None and pb.youtube_key("") is None and pb.youtube_key(None) is None
          and pb.youtube_key("abc") is None and pb.youtube_key('abcDEF"><script>') is None and pb.youtube_key("https://vimeo.com/123456789") is None)
    vids = {"results": [
        {"site": "YouTube", "type": "Teaser", "key": "teaserKey01", "official": True},
        {"site": "Vimeo", "type": "Trailer", "key": "vimeoKey001", "official": True},
        {"site": "YouTube", "type": "Featurette", "key": "featureKey1", "official": True},
        {"site": "YouTube", "type": "Trailer", "key": "plainKey001", "official": False, "name": "Plain"},
        {"site": "YouTube", "type": "Trailer", "key": "officialKey1", "official": True, "name": "Official Trailer"},
        {"site": "YouTube", "type": "Trailer", "key": "bad key!", "official": True},
    ]}
    t = pb.pick_tmdb_trailer(vids)
    check("TMDB: the official YouTube trailer wins, with our own shape", t == {"key": "officialKey1", "name": "Official Trailer", "url": "https://www.youtube.com/watch?v=officialKey1"}, t)
    check("TMDB: no official one, any trailer; then a teaser", pb.pick_tmdb_trailer({"results": vids["results"][3:4]})["key"] == "plainKey001"
          and pb.pick_tmdb_trailer({"results": vids["results"][:1]})["key"] == "teaserKey01")
    check("TMDB: junk gives none", pb.pick_tmdb_trailer(None) is None and pb.pick_tmdb_trailer({"results": "x"}) is None and pb.pick_tmdb_trailer({"results": [1, None]}) is None
          and pb.pick_tmdb_trailer({"results": [{"site": "Vimeo", "type": "Trailer", "key": "vimeoKey001"}]}) is None)
    check("Jellyfin RemoteTrailers: first YouTube address", pb.jellyfin_trailer(DETAILS_ALPHA)["key"] == "jfTrailer01"
          and pb.jellyfin_trailer({"RemoteTrailers": [{"Url": "https://vimeo.com/1"}, {"Url": "https://youtu.be/zzzzzz1234"}]})["key"] == "zzzzzz1234"
          and pb.jellyfin_trailer({"RemoteTrailers": "x"}) is None and pb.jellyfin_trailer(None) is None and pb.jellyfin_trailer({}) is None)

    print("cast, crew, tech")
    people = pb.parse_people(DETAILS_ALPHA)
    check("cast: actors only, once each, photo through our proxy only when there is one",
          [(p["name"], p["role"]) for p in people["cast"]] == [("Ann Actor", "Lead"), ("Bo Noimage", "Friend")]
          and people["cast"][0]["image"] == f"/nl/jf/Items/{PERSON_IMG}/Images/Primary?maxWidth=200&quality=80" and people["cast"][1]["image"] is None, people)
    check("crew: the director with the job as the role, other jobs left out", [(p["name"], p["role"]) for p in people["crew"]] == [("Dee Rector", "Director")], people["crew"])
    check("people: junk is harmless", pb.parse_people(None) == {"cast": [], "crew": []} and pb.parse_people({"People": "x"}) == {"cast": [], "crew": []}
          and pb.parse_people({"People": [None, {"Type": "Actor"}, {"Name": "A", "Type": "Actor", "Id": "../x", "PrimaryImageTag": "t"}]})["cast"][0]["image"] is None)
    many = {"People": [{"Name": f"P{i}", "Type": "Actor"} for i in range(50)] + [{"Name": f"C{i}", "Type": "Writer"} for i in range(50)]}
    check("lists are capped", len(pb.parse_people(many)["cast"]) == pb.CAST_MAX and len(pb.parse_people(many)["crew"]) == pb.CREW_MAX)
    rows = {r["label"]: r["value"] for r in pb.tech_rows(DETAILS_ALPHA)}
    check("tech: container, size, bitrate", rows["Container"] == "MKV" and rows["File size"] == "4.2 GB" and rows["Bitrate"] == "8.1 Mbps", rows)
    check("tech: video line", rows["Video"] == "HEVC (H.265) · 1080p (1920×800) · HDR10 · 23.976 fps", rows.get("Video"))
    check("tech: one row per audio track, default marked", rows["Audio 1"] == "English · AC3 · 5.1 · default" and rows["Audio 2"] == "spa · AAC · stereo", rows)
    check("tech: subtitle languages once each", rows["Subtitles"] == "English, Spanish", rows.get("Subtitles"))
    check("tech: no file path anywhere", "/media" not in json.dumps(pb.tech_rows(DETAILS_ALPHA)))
    fallback = pb.tech_rows({}, {"file_path": "/media/Movies/Alpha (2020)/Alpha.mp4", "file_size": 3 * 1024 ** 3})
    check("tech: without Jellyfin only the file type and size, never the path", fallback == [{"label": "Container", "value": "MP4"}, {"label": "File size", "value": "3.0 GB"}], fallback)
    check("tech: nothing known, nothing shown", pb.tech_rows({}, None) == [] and pb.tech_rows(None, {"file_path": "/x/y", "file_size": 0}) == [])
    check("size and bitrate formats", pb.human_size(0) == "" and pb.human_size("x") == "" and pb.human_size(1536) == "2 KB" and pb.human_rate(640000) == "640 kbps" and pb.human_rate(None) == "")
    check("resolution labels", pb.resolution_label(3840, 2160).startswith("4K") and pb.resolution_label(1280, 720).startswith("720p") and pb.resolution_label(720, 480).startswith("480p") and pb.resolution_label(0, 0) == "")

    print("more like this: ranking")
    base = {"drama", "thriller"}
    cands = [
        {"id": 1, "tmdb_id": 101, "genres": "Sci-Fi, Drama", "rating": 7.9},
        {"id": 2, "tmdb_id": 102, "genres": "Comedy", "rating": 6.9},
        {"id": 3, "tmdb_id": 103, "genres": "Drama, Thriller", "rating": 7.2},
        {"id": 4, "tmdb_id": None, "genres": "Western", "rating": 9.9},
        {"id": 5, "tmdb_id": 105, "genres": "Sci-Fi, Drama", "rating": 7.9},
    ]
    r = pb.rank_candidates(base, cands, {101: 0, 102: 5}, {5: 0.9})
    check("on TMDB's list, close in the vibe index, or half the genres; nothing else", sorted(x["id"] for x in r) == [1, 2, 3, 5] and r[0]["id"] == 1, [x["id"] for x in r])
    check("a title with no reason (id 4, a well rated Western) is left out however high its rating", 4 not in [x["id"] for x in r])
    by = {x["id"]: x for x in r}
    check("reasons are truthful", by[1]["why"].startswith("Similar on TMDB") and by[5]["why"] == "Close in feel · Same genres: Drama" and by[3]["why"] == "Same genres: Drama, Thriller", {k: v["why"] for k, v in by.items()})
    check("signals say what contributed", by[1]["signals"][0] == "tmdb" and by[5]["signals"] == ["recommender", "genres"] and by[3]["signals"] == ["genres"])
    check("nothing to go on gives nothing", pb.rank_candidates(base, cands, {}, {}, limit=3)[0]["id"] == 3 and pb.rank_candidates(set(), [cands[3]], {}, {}) == [])

    print("watched state")
    by_path = {ALPHA: {"media_item_id": 1}, BETA: {"media_item_id": 2}, EPS[(1, 1)]: {"media_item_id": 3}, EPS[(1, 2)]: {"media_item_id": 3}}
    data = {"Items": [
        played("a", EPS[(1, 2)], "Episode", "2026-10-12"), played("b", BETA, "Movie", "2026-10-10"), played("c", EPS[(1, 1)], "Episode", "2026-10-09"),
        played("d", "/nowhere", "Movie", "2026-10-08"), {"Id": "e", "Path": ALPHA, "UserData": {"Played": False}},
    ]}
    pl = nb.parse_played(data, by_path)
    check("parse_played: one entry per title, newest first, unknown files and unplayed items dropped", pl == [{"media_item_id": 3, "played_at": "2026-10-12"}, {"media_item_id": 2, "played_at": "2026-10-10"}], pl)
    check("parse_played: junk", nb.parse_played(None, by_path) == [] and nb.parse_played({"Items": []}, by_path) == [])
    merged = nb.merge_played([[{"media_item_id": 2, "played_at": "2026-10-01"}], [{"media_item_id": 3, "played_at": "2026-10-05"}, {"media_item_id": 2, "played_at": "2026-10-09"}]])
    check("merge_played: either person counts, latest time, newest first", merged == [{"media_item_id": 2, "played_at": "2026-10-09"}, {"media_item_id": 3, "played_at": "2026-10-05"}], merged)


# ------------------------------------------------------------------------- migration


def test_migration(tmp):
    from database import DatabaseManager

    print("schema v16")
    fresh = DatabaseManager(os.path.join(tmp, "fresh.db"))
    check("fresh DB is at schema 16 or newer", fresh.get_schema_version() >= 16, fresh.get_schema_version())
    path = os.path.join(tmp, "v15.db")
    old = DatabaseManager(path)
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE nl_hidden_titles")
        conn.execute("PRAGMA user_version = 15")
    check("a v15 file really lacks the table", "nl_hidden_titles" not in {r[0] for r in sqlite3.connect(path).execute("SELECT name FROM sqlite_master")})
    up = DatabaseManager(path)
    check("it upgrades to 16 and the table is there",
          up.get_schema_version() >= 16 and "nl_hidden_titles" in {r[0] for r in sqlite3.connect(path).execute("SELECT name FROM sqlite_master")})
    check("running again is harmless", DatabaseManager(path).get_schema_version() >= 16)
    up.register_source_file("/media/Movies/X (2020)/X.mkv", "X", 2020, "movie")
    with up.get_connection() as conn:
        x = conn.execute("SELECT id FROM media_items WHERE title = 'X'").fetchone()["id"]
    check("hiding is per viewer key and repeatable", up.set_hidden("labiba", x, True) and up.set_hidden("labiba", x, True) and up.get_hidden_ids("labiba") == {x} and up.get_hidden_ids("partner") == set())
    up.set_hidden("labiba", x, False)
    check("showing again removes it", up.get_hidden_ids("labiba") == set())
    try:
        up.set_hidden("labiba", 99999, True)
        check("an unknown title is refused", False, "no error")
    except ValueError:
        check("an unknown title is refused", True)
    up.set_hidden("labiba", x, True)
    with up.get_connection() as conn:
        conn.execute("DELETE FROM media_items WHERE id = ?", (x,))
    check("deleting a title removes its hidden rows", up.get_hidden_ids("labiba") == set())


# ----------------------------------------------------------------------------- API


def run(tmp):
    ctx = nl_fake.boot(tmp)
    server, client, ids, db = ctx["server"], ctx["client"], ctx["ids"], ctx["db"]
    client.follow_redirects = False
    from services import nl_people as np

    nobody = server.nl_people
    fake = PolishJellyfin()
    server.jellyfin_service.session = fake
    server.jellyfin_playback._ids = {}
    server.jellyfin_playback._last_refresh = None

    # More movies on the shelf, with TMDB ids and genres.
    extra = {"Delta": (80, "Comedy", 7.8, 104), "Epsilon": (150, "Drama, Thriller", 7.2, 105), "Zeta": (118, "Sci-Fi, Drama", 7.9, 103), "Arrival": (116, "Sci-Fi, Drama", 7.9, 106)}
    for title in extra:
        db.register_source_file(f"/media/Movies/{title} (2015)/{title}.mkv", title, 2015, "movie")
    with db.get_connection() as conn:
        for title, (rt, genres, rating, tmdb) in extra.items():
            conn.execute("UPDATE media_items SET runtime = ?, genres = ?, rating = ?, tmdb_id = ? WHERE title = ?", (rt, genres, rating, tmdb, title))
        conn.execute("UPDATE media_items SET tmdb_id = 101, genres = 'Drama, Thriller', rating = 8.0 WHERE title = 'Alpha'")
        conn.execute("UPDATE media_items SET tmdb_id = 102, genres = 'Comedy' WHERE title = 'Beta'")
        conn.execute("UPDATE media_items SET tmdb_id = 201, media_type = 'tv' WHERE title = 'Sigma'")
        conn.execute("UPDATE media_sources SET created_at = '2026-09-01 10:00:00' WHERE file_path LIKE '%/Delta%'")
        conn.execute("UPDATE media_sources SET created_at = '2026-09-20 10:00:00' WHERE file_path LIKE '%/Zeta%'")
        conn.execute("UPDATE media_sources SET created_at = '2026-08-01 10:00:00' WHERE file_path LIKE '%/Epsilon%'")
        conn.execute("UPDATE media_sources SET created_at = '2026-07-01 10:00:00' WHERE file_path LIKE '%/Arrival%'")
        conn.execute("UPDATE media_sources SET created_at = '2026-10-10 10:00:00' WHERE file_path = ?", (BETA,))
        conn.execute("UPDATE media_sources SET created_at = '2026-10-12 10:00:00' WHERE file_path LIKE '%/Sigma%'")
        ids2 = {r["title"]: r["id"] for r in conn.execute("SELECT id, title FROM media_items")}
    ids.update(ids2)
    alpha, beta, sigma, delta, epsilon, zeta, arrival, gamma = (ids[t] for t in ("Alpha", "Beta", "Sigma", "Delta", "Epsilon", "Zeta", "Arrival", "Gamma Ray"))

    # TMDB stand-in behind the real wiring: server._nl_tmdb -> server._tmdb_get(api_key, path, params).
    tmdb_calls = []
    tmdb_data = {
        "/movie/101/videos": {"results": [
            {"site": "YouTube", "type": "Teaser", "key": "teaserKey01", "official": True},
            {"site": "YouTube", "type": "Trailer", "key": "officialKey1", "official": True, "name": "Official Trailer"},
        ]},
        "/movie/101/similar": {"results": [{"id": 103}, {"id": 999}, {"id": 102}]},
        "/movie/101/recommendations": {"results": [{"id": 104}]},
    }

    def fake_tmdb(key, path, params=None):
        tmdb_calls.append((key, path))
        return tmdb_data.get(path)

    server._tmdb_get = fake_tmdb
    server.api_key = TMDB_KEY
    server.nl_extras._vibe = lambda: FakeVibe(ready=False)

    def get(path, who=None):
        return client.get(path, headers=cookie(who) if who else None)

    print("home: Recently Added, Recently Watched, nothing hidden yet")
    h = get("/api/nl/home").json()
    check("home has the new rows", "recently_added" in h and "recently_watched" in h, list(h))
    ra = [c["title"] for c in h["recently_added"]]
    check("Recently Added: newest file first, movies and shows together", ra[:3] == ["Sigma", "Beta", "Zeta"] and "Alpha" in ra, ra)
    check("Recently Added: cards are the usual cards", all({"id", "href", "title", "poster_url"} <= set(c) for c in h["recently_added"]) and h["recently_added"][0]["href"] == f"/nl/show/{sigma}")
    rw = [c["title"] for c in h["recently_watched"]]
    check("Recently Watched: last played first, a show once, files we don't have dropped", rw == ["Sigma", "Beta"], rw)
    uid, params = fake.played_calls[0]
    check("Recently Watched asks Jellyfin for played items, newest first, as the viewer",
          uid == "user1" and params["Filters"] == "IsPlayed" and params["SortBy"] == "DatePlayed" and params["SortOrder"] == "Descending", fake.played_calls[0])
    check("Continue Watching, Movies and Shows rows still there", len(h["continue_watching"]) == 2 and [c["title"] for c in h["movies"]][:1] == ["Beta"] and h["movies_total"] == 6 and h["shows_total"] == 1)
    fake.down = True
    hd = get("/api/nl/home").json()
    check("Jellyfin down: the home still builds, Recently Watched is just empty", hd["recently_watched"] == [] and len(hd["recently_added"]) > 0 and hd["continue_watching"] == [])
    fake.down = False

    print("Unwatched only")
    lib = get("/api/nl/library?type=movie").json()
    check("library without the filter: everything, flags say not asked", len(lib["items"]) == 6 and lib["unwatched"] is False and lib["unwatched_applied"] is False)
    lib = get("/api/nl/library?type=movie&unwatched=1").json()
    titles = [c["title"] for c in lib["items"]]
    check("movies: the watched one (Beta) is gone, the half-watched one (Alpha) stays", "Beta" not in titles and "Alpha" in titles and len(titles) == 5 and lib["unwatched_applied"] is True, titles)
    lib = get("/api/nl/library?type=tv&unwatched=1").json()
    check("shows: a show with a watched episode counts as watched", lib["items"] == [] and lib["unwatched_applied"] is True, lib)
    lib = get("/api/nl/library?type=tv").json()
    check("shows without the filter: Sigma", [c["title"] for c in lib["items"]] == ["Sigma"])
    fake.down = True
    lib = get("/api/nl/library?type=movie&unwatched=1").json()
    check("Jellyfin down: everything is listed and the page is told the filter could not be applied", len(lib["items"]) == 6 and lib["unwatched"] is True and lib["unwatched_applied"] is False)
    fake.down = False
    check("a bad type is still refused", get("/api/nl/library?type=music").status_code == 422)

    print("Hide from home (implicit viewer)")
    r = client.put(f"/api/nl/hidden/{beta}", json={"hidden": True})
    check("PUT hidden", r.status_code == 200 and r.json() == {"hidden": True}, r.text)
    check("GET hidden", get(f"/api/nl/hidden/{beta}").json() == {"hidden": True} and get(f"/api/nl/hidden/{alpha}").json() == {"hidden": False})
    h = get("/api/nl/home").json()
    check("Beta is off Recently Added, Recently Watched and Movies", all("Beta" not in [c["title"] for c in h[k]] for k in ("recently_added", "recently_watched", "movies")), {k: [c["title"] for c in h[k]] for k in ("recently_added", "recently_watched", "movies")})
    check("the other rows are not shortened by it", [c["title"] for c in h["recently_watched"]] == ["Sigma"] and h["movies_total"] == 6)
    client.put(f"/api/nl/hidden/{alpha}", json={"hidden": True})
    h = get("/api/nl/home").json()
    check("Continue Watching keeps a hidden title that is part way through", any(c["title"] == "Alpha" for c in h["continue_watching"]))
    check("Alpha is off the poster rows", all("Alpha" not in [c["title"] for c in h[k]] for k in ("recently_added", "movies")))
    lib = get("/api/nl/library?type=movie").json()
    check("Movies page still lists hidden titles", {"Alpha", "Beta"} <= {c["title"] for c in lib["items"]})
    t = get(f"/api/nl/title/{beta}").json()
    check("the details page says it is hidden", t["hidden"] is True and get(f"/api/nl/title/{delta}").json()["hidden"] is False)
    check("search is not filtered by it", all("hidden" not in str(c).lower() for c in get("/api/nl/search?q=be").json()["results"]))
    r = client.put(f"/api/nl/hidden/{beta}", json={"hidden": False})
    h = get("/api/nl/home").json()
    check("showing again brings it back", r.json() == {"hidden": False} and "Beta" in [c["title"] for c in h["recently_added"]] and "Beta" in [c["title"] for c in h["recently_watched"]])
    client.put(f"/api/nl/hidden/{alpha}", json={"hidden": False})
    check("unknown title: 404, bad body: 422, GET only and PUT only",
          client.put("/api/nl/hidden/99999", json={"hidden": True}).status_code == 404 and client.put(f"/api/nl/hidden/{beta}", json={"hidden": "yes please"}).status_code == 422
          and client.put(f"/api/nl/hidden/{beta}", json={}).status_code == 422 and client.post(f"/api/nl/hidden/{beta}", json={"hidden": True}).status_code == 405
          and client.delete(f"/api/nl/hidden/{beta}").status_code == 405)

    print("Hide from home also keeps a title out of the picks row")

    class Rec:
        def recommend(self, **kw):
            return {"pool": [
                {"id": beta, "title": "Beta", "genres": "Comedy", "rating": 6.9, "recommendation_score": 9.0},
                {"id": delta, "title": "Delta", "genres": "Comedy", "rating": 7.8, "recommendation_score": 8.0},
            ]}

    real_rec = server.recommender_service.recommend
    server.recommender_service.recommend = Rec().recommend
    p = get("/api/nl/picks").json()
    check("before hiding, Beta is a pick", beta in [i["id"] for i in p["items"]], [i["id"] for i in p["items"]])
    client.put(f"/api/nl/hidden/{beta}", json={"hidden": True})
    p = get("/api/nl/picks").json()
    check("after hiding, it is not", beta not in [i["id"] for i in p["items"]] and delta in [i["id"] for i in p["items"]], [i["id"] for i in p["items"]])
    client.put(f"/api/nl/hidden/{beta}", json={"hidden": False})
    server.recommender_service.recommend = real_rec

    print("two people: hiding and watched state are per person; Both has its own")
    server.nl_people = np.People(np.parse_people([
        {"id": "labiba", "name": "Labiba", "jellyfin_user_id": LAB, "profile_id": 1},
        {"id": "partner", "name": "Sam", "jellyfin_user_id": PAR, "profile_id": 2},
    ]))
    fake.played_by = {
        LAB: [played("jfB", BETA, "Movie", "2026-10-05T20:00:00Z")],
        PAR: [played("jfS1E1", EPS[(1, 1)], "Episode", "2026-10-09T20:00:00Z")],
    }
    fake.played_calls.clear()
    client.put(f"/api/nl/hidden/{delta}", json={"hidden": True}, headers=cookie("labiba"))
    titles = lambda who, key: [c["title"] for c in get("/api/nl/home", who).json()[key]]  # noqa: E731
    check("Labiba hid Delta: gone for her, still there for Sam and for Both of us",
          "Delta" not in titles("labiba", "movies") and "Delta" in titles("partner", "movies") and "Delta" in titles("both", "movies"))
    check("hidden state is read per person", get(f"/api/nl/hidden/{delta}", "labiba").json()["hidden"] is True and get(f"/api/nl/hidden/{delta}", "partner").json()["hidden"] is False
          and get(f"/api/nl/hidden/{delta}", "both").json()["hidden"] is False)
    client.put(f"/api/nl/hidden/{zeta}", json={"hidden": True}, headers=cookie("both"))
    check("Both of us has its own list", "Zeta" not in titles("both", "movies") and "Zeta" in titles("labiba", "movies"))
    check("Recently Watched is each person's own: Labiba watched Beta, Sam the show", titles("labiba", "recently_watched") == ["Beta"] and titles("partner", "recently_watched") == ["Sigma"])
    check("Both of us: either person's watching counts, latest first", titles("both", "recently_watched") == ["Sigma", "Beta"], titles("both", "recently_watched"))
    users_asked = sorted({u for u, _ in fake.played_calls})
    check("each person's own Jellyfin user was asked", users_asked == sorted([LAB, PAR]), users_asked)
    un = lambda who: [c["title"] for c in get("/api/nl/library?type=movie&unwatched=1", who).json()["items"]]  # noqa: E731
    check("Unwatched only: Labiba has seen Beta, Sam has not, Both of us: nobody may have seen it", "Beta" not in un("labiba") and "Beta" in un("partner") and "Beta" not in un("both"))
    check("Unwatched shows: Sam saw Sigma", get("/api/nl/library?type=tv&unwatched=1", "partner").json()["items"] == [] and len(get("/api/nl/library?type=tv&unwatched=1", "labiba").json()["items"]) == 1)
    server.nl_people = nobody

    print("title extras: cast & crew, trailer, tech details")
    tmdb_calls.clear()
    r = get(f"/api/nl/title/{alpha}/extras")
    x = r.json()
    check("200 with the usual keys", r.status_code == 200 and {"cast", "crew", "trailer", "tech", "jellyfin"} <= set(x), x)
    check("cast and crew come from Jellyfin in our shape", [p["name"] for p in x["cast"]] == ["Ann Actor", "Bo Noimage"] and x["crew"][0]["role"] == "Director" and x["jellyfin"] is True)
    check("photos go through our image proxy", x["cast"][0]["image"].startswith("/nl/jf/Items/") and x["cast"][1]["image"] is None)
    check("the trailer is TMDB's official YouTube trailer", x["trailer"] == {"key": "officialKey1", "name": "Official Trailer", "url": "https://www.youtube.com/watch?v=officialKey1"}, x["trailer"])
    check("TMDB was asked with the server's key, for the movie id", tmdb_calls == [(TMDB_KEY, "/movie/101/videos")], tmdb_calls)
    check("tech details rows are ready to show", dict((r_["label"], r_["value"]) for r_ in x["tech"])["Container"] == "MKV" and any(r_["label"] == "Video" for r_ in x["tech"]))
    body = r.text
    check("no key, no file path, no Jellyfin field name in the answer", TMDB_KEY not in body and SECRET not in body and "/media/" not in body and "MediaSources" not in body and "PrimaryImageTag" not in body)
    get(f"/api/nl/title/{alpha}/extras")
    check("TMDB answers are cached (one call for two page views)", len(tmdb_calls) == 1, tmdb_calls)

    server.nl_extras._cache.clear()
    tmdb_data.pop("/movie/101/videos")
    x = get(f"/api/nl/title/{alpha}/extras").json()
    check("TMDB has no video: Jellyfin's RemoteTrailers", x["trailer"]["key"] == "jfTrailer01", x["trailer"])
    server.nl_extras._cache.clear()
    server.api_key = ""
    tmdb_calls.clear()
    x = get(f"/api/nl/title/{alpha}/extras").json()
    check("no TMDB key: TMDB is not called and the page still answers", tmdb_calls == [] and x["trailer"]["key"] == "jfTrailer01" and x["cast"])
    server.api_key = TMDB_KEY
    fake.details["jfA"] = {**DETAILS_ALPHA, "RemoteTrailers": []}
    server.nl_extras._cache.clear()
    x = get(f"/api/nl/title/{alpha}/extras").json()
    check("no trailer anywhere: null", x["trailer"] is None and x["cast"])
    fake.down = True
    server.nl_extras._cache.clear()
    r = get(f"/api/nl/title/{alpha}/extras")
    x = r.json()
    check("Jellyfin down: 200, no cast, tech from our own database (type and size only)", r.status_code == 200 and x["cast"] == [] and x["crew"] == [] and x["jellyfin"] is False
          and x["tech"] == [{"label": "Container", "value": "MP4"}], x)
    fake.down = False
    fake.details["jfA"] = dict(DETAILS_ALPHA)
    server.nl_extras._cache.clear()
    tmdb_data["/movie/101/videos"] = {"results": []}
    fake.details["jfA"] = {**DETAILS_ALPHA, "RemoteTrailers": [{"Url": "javascript:alert(1)"}]}
    x = get(f"/api/nl/title/{alpha}/extras").json()
    check("a non-YouTube trailer address is never passed on", x["trailer"] is None, x["trailer"])
    fake.details["jfA"] = dict(DETAILS_ALPHA)
    x = get(f"/api/nl/title/{sigma}/extras").json()
    check("a show: cast from its series, no tech details", [p["name"] for p in x["cast"]] == ["Sia Star"] and x["tech"] is None, x)
    check("unknown title: 404 for both routes", get("/api/nl/title/99999/extras").status_code == 404 and get("/api/nl/title/99999/more").status_code == 404
          and get(f"/api/nl/title/{gamma}/extras").status_code == 404)

    print("more like this")
    server.nl_extras._cache.clear()
    tmdb_data["/movie/101/videos"] = {"results": []}
    server.nl_extras._vibe = lambda: FakeVibe(ready=False)
    m = get(f"/api/nl/title/{alpha}/more").json()
    got = [c["title"] for c in m["items"]]
    check("TMDB similar then recommendations, then shared genres; never itself, never a title we don't have", got == ["Zeta", "Delta", "Beta", "Epsilon"], got)
    check("engines say what was used", m["engines"] == ["tmdb", "genres"], m["engines"])
    check("cards carry a truthful reason and the usual fields",
          m["items"][0]["reason"].startswith("Similar on TMDB") and m["items"][3]["reason"] == "Same genres: Drama, Thriller" and m["items"][0]["href"] == f"/nl/title/{zeta}")
    server.nl_extras._vibe = lambda: FakeVibe(sims={arrival: 0.91, epsilon: 0.8})
    m = get(f"/api/nl/title/{alpha}/more").json()
    by = {c["title"]: c for c in m["items"]}
    check("with the recommender's vibe index, close titles join in", "Arrival" in by and by["Arrival"]["reason"].startswith("Close in feel") and m["engines"] == ["tmdb", "recommender", "genres"], (list(by), m["engines"]))
    server.nl_extras._vibe = lambda: FakeVibe(fail=True)
    m = get(f"/api/nl/title/{alpha}/more").json()
    check("a failing vibe index only removes its part", [c["title"] for c in m["items"]] == ["Zeta", "Delta", "Beta", "Epsilon"] and "recommender" not in m["engines"])
    server.nl_extras._vibe = None
    server.nl_extras._cache.clear()
    tmdb_data_backup = dict(tmdb_data)
    tmdb_data.clear()
    m = get(f"/api/nl/title/{alpha}/more").json()
    check("TMDB empty: genres alone", [c["title"] for c in m["items"]] == ["Epsilon"] and m["engines"] == ["genres"], m)
    server.nl_extras._cache.clear()
    server.api_key = ""
    m = get(f"/api/nl/title/{alpha}/more").json()
    check("no TMDB key either: same, without a call", [c["title"] for c in m["items"]] == ["Epsilon"])
    r = get(f"/api/nl/title/{sigma}/more")
    check("a show with nothing to go on: 200 with an empty row", r.status_code == 200 and r.json()["items"] == [] and r.json()["engines"] == [], r.text)
    r = get(f"/api/nl/title/{beta}/more")
    check("a title that only shares a genre gets just that", r.status_code == 200 and [c["title"] for c in r.json()["items"]] == ["Delta"], r.text)
    server.nl_extras._tmdb_raw = lambda path, params=None: (_ for _ in ()).throw(RuntimeError("tmdb exploded"))
    server.nl_extras._cache.clear()
    server.api_key = TMDB_KEY
    m = get(f"/api/nl/title/{alpha}/more")
    check("a TMDB reader that raises does not break the route", m.status_code == 200, m.text)
    tmdb_data.update(tmdb_data_backup)

    print("the proxy is unchanged by M7b")
    from services.jellyfin_playback import safe_proxy_path

    check("a person's photo is an ordinary image path", safe_proxy_path(f"Items/{PERSON_IMG}/Images/Primary") == f"Items/{PERSON_IMG}/Images/Primary")
    for refused in ("Users/user1/Items", f"Items/{PERSON_IMG}", f"Items/{PERSON_IMG}/People", "MediaSegments/jfA", "Sessions/Playing", "System/Info", "Plugins",
                    f"Items/{PERSON_IMG}/PlaybackInfo", f"Items/{PERSON_IMG}/Images/Primary/../../../Users", "Items/jfA/Similar", "Persons",
                    f"Items/{PERSON_IMG}/Images/Primary%2f..%2fUsers"):
        check(f"still refused: {refused}", safe_proxy_path(refused) is None, safe_proxy_path(refused))
    for method in ("post", "put", "delete", "patch"):
        r = getattr(client, method)(f"/nl/jf/Items/{PERSON_IMG}/Images/Primary")
        check(f"{method.upper()} on the proxy is refused", r.status_code == 405, r.status_code)
    proxy_src = read("services/jellyfin_playback.py")
    check("this milestone added nothing to the allowlist", "Similar" not in proxy_src and "People" not in proxy_src.split("def safe_proxy_path")[1].split("def ")[0])
    nl_src = read("services/nl_polish_b.py") + read("services/nl_browse.py")
    check("no Jellyfin key is read or sent by the new code", "api_key" not in read("services/nl_polish_b.py") and "X-Emby" not in nl_src)

    print("pages")
    for url, needle in (("/nl/", "nl-recent-added"), ("/nl/movies", "nl-filters"), ("/nl/shows", "nl-filters")):
        r = get(url)
        check(f"{url} has {needle}", r.status_code == 200 and needle in r.text and "/static/nl/polish.js" in r.text, r.status_code)
    r = get("/nl/tv/")
    check("TV home has the new rows, the switch and polish.js", r.status_code == 200 and "tv-recent-added" in r.text and "tv-filters" in r.text and "/static/nl/polish.js" in r.text)
    check("TV title page loads polish.js", "/static/nl/polish.js" in get(f"/nl/tv/title/{alpha}").text)
    check("the page cannot see a key in any of the new pages or scripts",
          all(SECRET not in get(u).text and TMDB_KEY not in get(u).text for u in ("/nl/", "/nl/movies", "/nl/tv/", f"/nl/title/{alpha}")))


# ------------------------------------------------------------------------- statics


def test_static():
    print("static files")
    js = read("static/nl/polish.js")
    tv = read("static/nl/tv/tv.js")
    nl = read("static/nl/nl.js")
    home = read("static/nl/home.js")
    browse = read("static/nl/browse.js")
    title = read("static/nl/title.js")
    css = read("static/nl/nl.css")
    tvcss = read("static/nl/tv/tv.css")
    check("polish.js: text only via textContent, no innerHTML, no eval", "innerHTML" not in js and "eval(" not in js and "document.write" not in js)
    check("polish.js: the Trailer link is only built from a checked YouTube address, in a new tab with noopener",
          "trailerHref(x.trailer)" in js and 'a.target = "_blank"' in js and "noopener noreferrer" in js and "youtube\\.com\\/watch" in js)
    check("polish.js: the TV gets a note, never a link", "not available on the TV" in js and "!tv && actions" in js)
    check("polish.js: storage access is wrapped", "try { return window.localStorage.getItem" in js and "try { window.localStorage.setItem" in js)
    check("nl.js: cards get the hide button only when a home row marks them", "item.hideable" in nl and "NLPolish.hideButton" in nl)
    check("home.js: Recently added / watched rows and hideable cards", "recently_added" in home and "recently_watched" in home and "it.hideable = true" in home)
    check("browse.js: Unwatched only goes to the server and says when it could not apply", "unwatched=1" in browse and "unwatched_applied" in browse and "NLPolish.unwatchedSwitch" in browse)
    check("title.js: hide toggle and the extras", "hideToggle" in title and "titleExtras" in title)
    check("tv.js: Unwatched pill, hide button in the actions row, trailer note, extras",
          "unwatchedSwitch" in tv and "hideToggle" in tv and "trailerNote" in tv and "titleExtras" in tv and "tv-recent-added" in tv)
    check("tv.js: the hide button is in the existing actions row (no new navigation rule)", 'actions.appendChild(window.NLPolish.hideToggle' in tv)
    check("css: web and TV styles exist", ".nl-hide-icon" in css and ".nl-fold-head" in css and ".nl-people" in css and ".nl-fold-head" in tvcss and ".tv-trailer-note" in tvcss)
    check("the old ticket-look UI is untouched", "nl_hidden" not in read("templates/index.html") and "api/nl" not in read("static/app.js"))
    kt = read("tv-app/app/src/main/java/app/northernlights/tv/MainActivity.kt")
    check("no TV navigation rule changed: off-origin links are still dropped", "return ServerConfig.originOf(request.url.toString()) != serverOrigin" in kt)


def test_js_behaviour():
    print("browser-side behaviour (node)")
    node = shutil.which("node")
    if not node:
        print("  (node not installed: skipped)")
        return
    out = subprocess.run([node, os.path.join(ROOT, "tests", "js", "nl_polish_b_check.js")], capture_output=True, text=True, timeout=60)
    check("trailer guard, folds, switch, hide, extras on web and TV all behave", out.returncode == 0, (out.stdout + out.stderr)[-800:])


if __name__ == "__main__":
    main()
