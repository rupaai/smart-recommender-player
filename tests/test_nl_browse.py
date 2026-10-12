"""
Offline tests for the Northern Lights M2 browse screens (tests/test_nl_browse.py)

Jellyfin is faked (tests/nl_fake.py); nothing leaves the process.

    uv run python tests/test_nl_browse.py
"""

import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nl_fake  # noqa: E402
from nl_fake import ALPHA, BETA, EPS, SECRET, FakeResponse  # noqa: E402

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} — got: {detail}")
        failures.append(label)


def main():
    tmp = tempfile.mkdtemp(prefix="nl_browse_test_")
    try:
        test_pure()
        test_migration(tmp)
        run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if failures:
        print(f"\n{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("\nAll Northern Lights browse tests passed")


def test_pure():
    from services import jellyfin_playback as jp
    from services import nl_browse as nb

    print("pure helpers")
    t = nl_fake.TICKS
    st = nb.progress_of({"RunTimeTicks": 100 * 60 * t, "UserData": {"PlaybackPositionTicks": 25 * 60 * t}})
    check("progress is a percent and seconds", st == {"played": False, "progress": 25, "position_seconds": 1500.0}, st)
    st = nb.progress_of({"RunTimeTicks": 100 * 60 * t, "UserData": {"PlaybackPositionTicks": 90 * 60 * t, "Played": True}})
    check("a played item has no progress", st["played"] and st["progress"] == 0 and st["position_seconds"] == 0, st)
    check("no UserData is fine", nb.progress_of({})["progress"] == 0)
    check("image_url is our proxy, no key", nb.image_url("abc123", "Backdrop", 800) ==
          "/nl/jf/Items/abc123/Images/Backdrop?maxWidth=800&quality=80")
    check("image_url refuses odd ids", nb.image_url("a/../b") is None and nb.image_url("") is None and nb.image_url(None) is None)
    check("episode code", nb.episode_code(1, 4) == "S1 · E4" and nb.episode_code(None, None) == "")

    print("badges")
    now = datetime(2026, 10, 12)
    rows = [{"id": i, "added_at": f"2026-10-{12 - i:02d} 09:00:00"} for i in range(0, 9)]
    rows.append({"id": 99, "added_at": "2020-01-01 00:00:00"})
    rows.append({"id": 98, "added_at": None})
    nb.mark_new(rows, now)
    news = [r["id"] for r in rows if r.get("new")]
    check("New: at most 6, newest first, nothing old", news == [0, 1, 2, 3, 4, 5], news)

    print("Resume / NextUp / Episodes parsing")
    fake = nl_fake.FakeJellyfin()
    by_path = {
        ALPHA: {"source_id": 1, "media_item_id": 10, "media_type": "movie", "title": "Alpha", "year": 2020, "poster_url": "p"},
        EPS[(1, 2)]: {"source_id": 4, "media_item_id": 20, "media_type": "tv", "title": "Sigma", "season_number": 1, "episode_number": 2},
        EPS[(1, 3)]: {"source_id": 5, "media_item_id": 20, "media_type": "tv", "title": "Sigma", "season_number": 1, "episode_number": 3},
    }
    cards = nb.parse_resume({"Items": fake.resume}, by_path)
    check("Resume keeps known files, one card per show", [c["title"] for c in cards] == ["Sigma", "Alpha"], [c["title"] for c in cards])
    ep, mv = cards
    check("episode card: subtitle, progress, resume link",
          ep["subtitle"] == "S1 · E2 · Second" and ep["progress"] == 50 and ep["resume_seconds"] == 1500
          and ep["href"] == "/nl/play/20?source=4&resume=1" and ep["kind"] == "episode", ep)
    check("episode still comes through the proxy", ep["image"] == "/nl/jf/Items/jfS1E2/Images/Primary?maxWidth=480&quality=80", ep["image"])
    check("movie card: backdrop, 40 percent, title page",
          mv["progress"] == 40 and mv["href"] == "/nl/play/10?resume=1" and mv["image"].startswith("/nl/jf/Items/jfA/Images/Backdrop")
          and mv["title_href"] == "/nl/title/10" and mv["subtitle"] == "2020", mv)
    check("played and untouched items are not in Continue Watching", nb.parse_resume(
        {"Items": [{"Id": "x", "Path": ALPHA, "RunTimeTicks": 6000000000, "UserData": {"Played": True}},
                   {"Id": "y", "Path": ALPHA, "RunTimeTicks": 6000000000, "UserData": {}}]}, by_path) == [])
    check("empty and odd replies are fine", nb.parse_resume({}, by_path) == [] and nb.parse_resume(None, by_path) == [])
    eps = nb.parse_episodes({"Items": fake.episodes}, {p: {"source_id": i} for i, p in enumerate(EPS.values(), 1)})
    check("episodes: played, progress, name, runtime", eps[1]["played"] and eps[2]["progress"] == 50 and eps[2]["name"] == "Second"
          and eps[1]["runtime_minutes"] == 50 and eps[3]["progress"] == 0, eps)
    check("NextUp picks the first file we have", nb.parse_next_up({"Items": [{"Path": "/x"}, {"Path": EPS[(1, 3)]}]},
                                                                {EPS[(1, 3)]: {"source_id": 5}}) == 5)
    check("NextUp empty gives None", nb.parse_next_up({"Items": []}, {}) is None)

    print("safe_proxy_path: artwork")
    for good in ("Items/jfA/Images/Primary", "Items/jfA/Images/Backdrop/0", "Items/6f1c-22/Images/Thumb"):
        check(f"allows {good}", jp.safe_proxy_path(good) == good)
    for bad in ("Items/jfA", "Items/jfA/Images", "Items/jfA/Images/Primary/../../Users", "Items/../Users/Images/Primary",
                "Items/%2e%2e/Images/Primary", "Items/jfA/Images/%2e%2e/Primary", "Items/jfA%2fImages/Primary",
                "Items/jfA/Images/Primary%2f..%2f..", "Items/jfA/Images/Primary/", "Items/jfA/Images/Primary?x=1",
                "Items/jfA/Images/Primary/123", "Items/jfA/Images/Primary/..", "Items//Images/Primary",
                "Users/u/Items/jfA/Images/Primary", "Items/jfA/Images/Other", "items/jfA/Images/Primary",
                "Items/jfA/Images/%50rimary", "Items/jfA/Images/Primary\\x"):
        check(f"refuses {bad!r}", jp.safe_proxy_path(bad) is None)


def test_migration(tmp):
    from database import DatabaseManager

    print("migration v12")
    fresh = DatabaseManager(os.path.join(tmp, "fresh.db"))
    with fresh.get_connection() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(media_sources)")}
        prefs = {r[1] for r in conn.execute("PRAGMA table_info(nl_show_prefs)")}
    check("fresh DB is at schema 12", fresh.get_schema_version() == 12, fresh.get_schema_version())
    check("fresh DB has media_sources.jellyfin_id", "jellyfin_id" in cols)
    check("fresh DB has nl_show_prefs", {"media_item_id", "autoplay", "updated_at"} <= prefs, prefs)

    path = os.path.join(tmp, "old.db")
    old = DatabaseManager(path)
    old.register_source_file("/media/Movies/X (2001)/X.mp4", "X", 2001, "movie")
    with sqlite3.connect(path) as conn:  # put it back to how a v11 database looks
        conn.execute("DROP TABLE nl_show_prefs")
        conn.execute("ALTER TABLE media_sources DROP COLUMN jellyfin_id")
        conn.execute("PRAGMA user_version = 11")
    upgraded = DatabaseManager(path)
    with upgraded.get_connection() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(media_sources)")}
        row = conn.execute("SELECT file_path, jellyfin_id FROM media_sources").fetchone()
        has_prefs = conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'nl_show_prefs'").fetchone()
    check("v11 DB upgrades to 12", upgraded.get_schema_version() == 12, upgraded.get_schema_version())
    check("existing rows kept, new column empty", row and row[0].endswith("X.mp4") and row[1] is None, tuple(row) if row else row)
    check("v11 DB gains the column and the table", "jellyfin_id" in cols and has_prefs is not None)
    DatabaseManager(path)
    check("running again is harmless", DatabaseManager(path).get_schema_version() == 12)

    print("jellyfin_id cache and show prefs (database)")
    sid = upgraded.get_library_items("movie")[0]["id"]
    src = upgraded.get_nl_title(sid)["sources"][0]
    check("set_jellyfin_ids writes once", upgraded.set_jellyfin_ids({src["id"]: "jfX"}) == 1
          and upgraded.set_jellyfin_ids({src["id"]: "jfX"}) == 0)
    check("get_sources_by_paths sees it", upgraded.get_sources_by_paths([src["file_path"]])[src["file_path"]]["jellyfin_id"] == "jfX")
    try:
        upgraded.set_show_autoplay(sid, False)
        check("autoplay is for shows only", False)
    except ValueError:
        check("autoplay is for shows only", True)
    upgraded.register_source_file("/media/Shows/S/S01E01.mkv", "S", 2000, "tv", season_number=1, episode_number=1)
    show = [i for i in upgraded.get_library_items("tv")][0]["id"]
    check("autoplay defaults to on", upgraded.get_show_autoplay(show) is True)
    upgraded.set_show_autoplay(show, False)
    check("autoplay can be turned off", upgraded.get_show_autoplay(show) is False)
    upgraded.set_show_autoplay(show, True)
    check("and on again", upgraded.get_show_autoplay(show) is True)


def run(tmp):
    ctx = nl_fake.boot(os.path.join(tmp))
    client, fake, ids, db = ctx["client"], ctx["fake"], ctx["ids"], ctx["db"]
    src = ids["sources"]

    print("GET /api/nl/library")
    r = client.get("/api/nl/library", params={"type": "movie"})
    check("movies listed", r.status_code == 200 and [m["title"] for m in r.json()["items"]] == ["Alpha", "Beta"], r.text)
    alpha, beta = r.json()["items"]
    check("Alpha shows its progress bar, not a badge", alpha["progress"] == 40 and alpha["badge"] == "Resume", alpha)
    check("Beta is New (fresh file), Alpha is not", beta["badge"] == "New" and beta["progress"] is None, beta)
    check("card has poster, year, runtime and a title link",
          alpha["poster_url"] == "https://img.test/alpha.jpg" and alpha["year"] == 2020 and alpha["runtime"] == 100
          and alpha["href"] == f"/nl/title/{ids['Alpha']}", alpha)
    check("one badge at most", all(not (m["progress"] and m["badge"] == "New") for m in r.json()["items"]))
    check("Beta has a Jellyfin artwork fallback after the first lookup",
          beta["image_fallback"] is None or beta["image_fallback"].startswith("/nl/jf/Items/"), beta["image_fallback"])
    r = client.get("/api/nl/library", params={"type": "tv"})
    show = r.json()["items"][0]
    check("shows listed with season count and show link",
          show["title"] == "Sigma" and show["seasons"] == 2 and show["runtime"] is None and show["href"] == f"/nl/show/{ids['Sigma']}", show)
    check("bad type refused", client.get("/api/nl/library", params={"type": "x"}).status_code == 422)
    check("API key nowhere in library output", SECRET not in client.get("/api/nl/library").text)

    print("GET /api/nl/home")
    r = client.get("/api/nl/home")
    data = r.json()
    check("home has Continue Watching, movies and shows", r.status_code == 200 and len(data["continue_watching"]) == 2
          and len(data["movies"]) == 2 and len(data["shows"]) == 1, r.text[:300])
    check("Continue Watching order from Jellyfin (show first)", [c["title"] for c in data["continue_watching"]] == ["Sigma", "Alpha"])
    check("totals for See all", data["movies_total"] == 2 and data["shows_total"] == 1)
    check("the unknown file was dropped", "unknown" not in r.text.lower())
    check("Continue Watching links resume at the right episode",
          data["continue_watching"][0]["href"] == f"/nl/play/{ids['Sigma']}?source={src[EPS[(1, 2)]]}&resume=1", data["continue_watching"][0])
    check("API key nowhere in home output", SECRET not in r.text)
    resume_call = [c for c in fake.calls if c[1] == "/Users/user1/Items/Resume"][-1]
    check("Resume asked for Video with Path", resume_call[2].get("MediaTypes") == "Video" and "Path" in resume_call[2].get("Fields", ""), resume_call)

    print("titles whose files are gone are hidden")
    from services.jellyfin_playback import JellyfinPlayback
    JellyfinPlayback.file_exists = staticmethod(lambda path: "Beta" not in path)  # Beta's file moved away
    try:
        r = client.get("/api/nl/library", params={"type": "movie"})
        check("library drops a movie with no file on disk", [m["title"] for m in r.json()["items"]] == ["Alpha"], r.text[:200])
        data = client.get("/api/nl/home").json()
        check("home row and total drop it too", [m["title"] for m in data["movies"]] == ["Alpha"] and data["movies_total"] == 1,
              data["movies"])
        check("a show with its files still there stays", [s["title"] for s in data["shows"]] == ["Sigma"])
    finally:
        JellyfinPlayback.file_exists = staticmethod(lambda path: True)

    print("Jellyfin ids are cached in the database")
    with db.get_connection() as conn:
        cached = {r["file_path"]: r["jellyfin_id"] for r in conn.execute("SELECT file_path, jellyfin_id FROM media_sources")}
    check("Resume filled jellyfin_id for the files it returned", cached[ALPHA] == "jfA" and cached[EPS[(1, 2)]] == "jfS1E2", cached)

    print("GET /api/nl/title/{id}")
    r = client.get(f"/api/nl/title/{ids['Alpha']}")
    t = r.json()
    check("movie details", r.status_code == 200 and t["title"] == "Alpha" and t["year"] == 2020 and t["runtime"] == 100
          and t["genres"] == ["Drama", "Thriller"] and t["overview"] == "About Alpha.", t)
    check("Resume offered at 40:00", t["can_resume"] and t["resume_seconds"] == 2400 and t["resume_href"] == f"/nl/play/{ids['Alpha']}?resume=1"
          and t["restart_href"].endswith("resume=0") and t["progress"] == 40, t)
    check("backdrop through the proxy", t["backdrop"].startswith("/nl/jf/Items/jfA/Images/Backdrop") and SECRET not in r.text, t["backdrop"])
    r = client.get(f"/api/nl/title/{ids['Beta']}")
    check("an unwatched movie has a plain Play link", r.json()["play_href"] == f"/nl/play/{ids['Beta']}" and not r.json()["can_resume"], r.text)
    r = client.get(f"/api/nl/title/{ids['Sigma']}")
    check("show details list seasons and link to the episodes", r.json()["seasons"][0] == {"number": 1, "label": "Season 1", "episodes": 3}
          and r.json()["show_href"] == f"/nl/show/{ids['Sigma']}" and r.json()["episode_count"] == 4, r.text)
    check("title 404", client.get("/api/nl/title/9999").status_code == 404)
    r = client.get(f"/api/nl/title/{ids['Gamma Ray']}")
    check("a title without a file is 404 (not in the library)", r.status_code == 404, r.status_code)

    print("GET /api/nl/show/{id}")
    r = client.get(f"/api/nl/show/{ids['Sigma']}")
    s = r.json()
    check("show data", r.status_code == 200 and s["title"] == "Sigma" and s["jellyfin"] is True and s["episode_count"] == 4, r.text[:300])
    check("two seasons, episodes in order", [x["number"] for x in s["seasons"]] == [1, 2]
          and [e["episode"] for e in s["seasons"][0]["episodes"]] == [1, 2, 3], s["seasons"])
    e1, e2, e3 = s["seasons"][0]["episodes"]
    check("watched tick and progress", e1["played"] is True and e2["progress"] == 50 and e2["played"] is False and e3["progress"] == 0, (e1, e2, e3))
    check("episode names, still and runtime from Jellyfin", e2["name"] == "Second" and e2["runtime_minutes"] == 50
          and e2["image"].startswith("/nl/jf/Items/jfS1E2/Images/Primary"), e2)
    check("Next up is the half-watched episode, offered as Resume",
          s["next_up"]["code"] == "S1 · E2" and s["next_up"]["label"] == "Resume" and s["next_up"]["text"] == "Resume S1 · E2"
          and s["next_up"]["href"].startswith(f"/nl/play/{ids['Sigma']}?source={src[EPS[(1, 2)]]}"), s["next_up"])
    check("backdrop uses the series id", s["backdrop"].startswith("/nl/jf/Items/series1/Images/Backdrop"), s["backdrop"])
    check("autoplay defaults to on", s["autoplay"] is True)
    nu_call = [c for c in fake.calls if c[1] == "/Shows/NextUp"][-1]
    check("NextUp asked with SeriesId, UserId and Path", nu_call[2].get("SeriesId") == "series1" and nu_call[2].get("UserId") == "user1"
          and "Path" in nu_call[2].get("Fields", ""), nu_call)
    check("API key nowhere in show output", SECRET not in r.text)
    fake.next_up = [fake.episodes[2]]
    s = client.get(f"/api/nl/show/{ids['Sigma']}").json()
    check("a fresh next episode is labelled Next up", s["next_up"]["label"] == "Next up" and s["next_up"]["episode"] == 3, s["next_up"])
    fake.next_up = []
    for e in fake.episodes:
        e["UserData"] = nl_fake.ud(played=True)
    s = client.get(f"/api/nl/show/{ids['Sigma']}").json()
    check("everything watched: Watch again from the first episode",
          s["all_watched"] and s["next_up"]["label"] == "Watch again" and s["next_up"]["episode"] == 1, s["next_up"])
    check("movie id is not a show", client.get(f"/api/nl/show/{ids['Alpha']}").status_code == 404)
    check("missing show 404", client.get("/api/nl/show/9999").status_code == 404)

    print("Jellyfin down: pages still build from our database")
    fake.down = True
    server = ctx["server"]
    server.nl_browse._series.clear()
    r = client.get("/api/nl/home")
    d = r.json()
    check("home renders without Continue Watching", r.status_code == 200 and d["continue_watching"] == []
          and len(d["movies"]) == 2 and len(d["shows"]) == 1, r.text[:200])
    r = client.get("/api/nl/library", params={"type": "movie"})
    check("movies list renders without progress", r.status_code == 200 and all(m["progress"] is None for m in r.json()["items"]), r.text[:200])
    r = client.get(f"/api/nl/title/{ids['Alpha']}")
    check("details render without Jellyfin (Play, no resume)", r.status_code == 200 and r.json()["play_href"].endswith(str(ids["Alpha"]))
          and r.json()["jellyfin"] is False and not r.json()["can_resume"], r.text[:300])
    r = client.get(f"/api/nl/show/{ids['Sigma']}")
    s = r.json()
    check("show page renders without ticks, Next up is the first episode", r.status_code == 200 and s["jellyfin"] is False
          and s["next_up"]["episode"] == 1 and s["next_up"]["label"] == "Play" and not any(e["played"] for sn in s["seasons"] for e in sn["episodes"]),
          r.text[:300])
    r = client.get("/api/nl/search", params={"q": "alpha"})
    check("search still answers from the catalogue", r.status_code == 200 and r.json()["results"][0]["title"] == "Alpha", r.text[:200])
    fake.down = False

    print("GET /api/nl/search")
    fake.items_calls = 0
    r = client.get("/api/nl/search", params={"q": "alpha"})
    res = r.json()["results"]
    check("a library title is Ready with a link", r.status_code == 200 and res[0]["label"] == "Ready" and res[0]["ready"]
          and res[0]["href"] == f"/nl/title/{ids['Alpha']}" and res[0]["kind"] == "movie" and res[0]["download_href"] is None, res[:1])
    r = client.get("/api/nl/search", params={"q": "gamma"})
    res = r.json()["results"]
    check("a catalogue-only title is Not in library yet and links to the Download tab",
          res[0]["label"] == "Not in library yet" and not res[0]["ready"] and res[0]["href"] is None and res[0]["download_href"] == "/#direct", res)
    r = client.get("/api/nl/search", params={"q": "sigma"})
    check("shows are found and link to the show page", r.json()["results"][0]["kind"] == "show"
          and r.json()["results"][0]["href"] == f"/nl/show/{ids['Sigma']}", r.text[:200])
    r = client.get("/api/nl/search", params={"q": "prem"})
    ep = [x for x in r.json()["results"] if x["kind"] == "episode"]
    check("episodes are found through Jellyfin and play directly", len(ep) == 1 and ep[0]["title"] == "Premiere" and ep[0]["label"] == "Ready"
          and ep[0]["subtitle"] == "Sigma · S2 · E1"
          and ep[0]["href"] == f"/nl/play/{ids['Sigma']}?source={src[EPS[(2, 1)]]}", r.text[:300])
    mixed = client.get("/api/nl/search", params={"q": "a"}).json()["results"]
    check("one letter gives no results", mixed == [])
    fake_title = db.upsert_catalogue_items([{"media_type": "movie", "tmdb_id": 1000, "title": "Alpha Centauri", "release_year": 2001}])
    results = client.get("/api/nl/search", params={"q": "alpha"}).json()["results"]
    labels = [x["label"] for x in results]
    check("Ready results come before Not in library yet", labels == sorted(labels, key=lambda x: x != "Ready"), labels)
    check("search output has no key", SECRET not in client.get("/api/nl/search", params={"q": "alpha"}).text and fake_title == 1)

    print("GET /nl/jf/ artwork")
    jf = fake.stream_response = FakeResponse(200, {"content-type": "image/jpeg", "content-length": "4", "x-secret-header": SECRET}, body=b"JPEG")
    fake.get_calls.clear()
    r = client.get("/nl/jf/Items/jfA/Images/Primary?maxWidth=400&quality=80&api_key=" + SECRET)
    url, hdrs = fake.get_calls[-1]
    check("image is proxied", r.status_code == 200 and r.content == b"JPEG" and r.headers["content-type"] == "image/jpeg", r.status_code)
    check("upstream url has no client key", url == "http://jellyfin.test:8096/Items/jfA/Images/Primary?maxWidth=400&quality=80" and SECRET not in url, url)
    check("images are cacheable, other headers not leaked", "max-age" in r.headers.get("cache-control", "") and "x-secret-header" not in r.headers)
    check("key not in the response", SECRET not in str(dict(r.headers)) and SECRET.encode() not in r.content)
    for bad in ("Items/jfA/Images/Primary/../../../Users", "Items/%2e%2e/Images/Primary", "Items/jfA/Images/%2e%2e/%2e%2e/Users",
                "Items/jfA", "Items/jfA/Images", "Users/user1/Items/jfA/Images/Primary", "Items/jfA/Images/Primary%2f..%2fUsers",
                "System/Info", "Items/jfA/Images/Primary/9999"):
        fake.get_calls.clear()
        r = client.get(f"/nl/jf/{bad}")
        check(f"refuses {bad}", r.status_code == 404 and not fake.get_calls, f"{r.status_code} calls={len(fake.get_calls)}")
    for method in ("post", "put", "delete"):
        r = getattr(client, method)("/nl/jf/Items/jfA/Images/Primary")
        check(f"images are read-only: {method.upper()} refused", r.status_code in (404, 405), r.status_code)
    fake.stream_response = FakeResponse(404, {"content-type": "text/plain"}, body=b"none")
    r = client.get("/nl/jf/Items/jfZ/Images/Primary")
    check("missing artwork passes the 404 through, uncached", r.status_code == 404 and "cache-control" not in r.headers, r.status_code)
    check("jf response object closed", jf.closed)

    print("play route: episodes")
    r = client.get(f"/api/nl/play/{ids['Sigma']}", params={"source": src[EPS[(2, 1)]]})
    p = r.json()
    check("the chosen episode is the one played", r.status_code == 200 and p["jellyfin_id"] == "jfS2E1" and p["source_id"] == src[EPS[(2, 1)]]
          and p["media_type"] == "tv" and p["media_item_id"] == ids["Sigma"], r.text[:300])
    p = client.get(f"/api/nl/play/{ids['Sigma']}").json()
    check("without a source a show starts at its first episode", p["jellyfin_id"] == "jfS1E1" and p["source_id"] == src[EPS[(1, 1)]], p.get("jellyfin_id"))
    p = client.get(f"/api/nl/play/{ids['Alpha']}").json()
    check("movies still play as before", p["jellyfin_id"] == "jfA" and p["media_type"] == "movie" and SECRET not in str(p), p.get("jellyfin_id"))
    check("a source from another title is refused", client.get(f"/api/nl/play/{ids['Alpha']}", params={"source": src[EPS[(2, 1)]]}).status_code == 404)
    check("a missing source is refused", client.get(f"/api/nl/play/{ids['Sigma']}", params={"source": 99999}).status_code == 404)

    print("pages")
    for url, needle in (("/nl/", "/static/nl/home.js"), ("/nl/movies", "/static/nl/browse.js"), ("/nl/shows", "/static/nl/browse.js"),
                        (f"/nl/title/{ids['Alpha']}", "/static/nl/title.js"), (f"/nl/show/{ids['Sigma']}", "/static/nl/show.js")):
        r = client.get(url)
        check(f"{url} served", r.status_code == 200 and needle in r.text and 'id="nl-q"' in r.text and SECRET not in r.text, r.status_code)
    check("movies page asks for movies, shows page for shows",
          'data-kind="movie"' in client.get("/nl/movies").text and 'data-kind="tv"' in client.get("/nl/shows").text)
    for f in ("nl.css", "nl.js", "home.js", "browse.js", "title.js", "show.js"):
        check(f"static/nl/{f} served", client.get(f"/static/nl/{f}").status_code == 200)

    print("known_ids: no scan storm")
    pb = ctx["server"].jellyfin_playback
    pb._ids = {}
    pb._last_refresh = None
    fake.scans = 0
    pb.known_ids(["/nope.mkv"])
    pb.known_ids(["/nope.mkv"])
    check("a file Jellyfin lacks causes one scan, not one per call", fake.scans == 1, fake.scans)


if __name__ == "__main__":
    main()
