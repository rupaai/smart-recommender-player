"""
Offline tests for Northern Lights "New & popular" (tests/test_nl_new_episodes.py): new episodes of your shows,
popular shows still airing, and one-tap download of the best matching copy.

TMDB, the torrent search and the downloader are faked; nothing leaves the process.

    uv run python tests/test_nl_new_episodes.py
"""

import os
import shutil
import sys
import tempfile
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nl_fake  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TODAY = date(2026, 11, 5)
failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} — got: {detail}")
        failures.append(label)


def day(n):
    return str(TODAY + timedelta(days=n))


def show(tmdb_id, name, *, last=None, nxt=None, seasons=None, rating=8.0, votes=900, genres=("Drama",), lang="en"):
    return {
        "id": tmdb_id, "name": name, "first_air_date": "2022-01-10", "overview": f"About {name}.",
        "vote_average": rating, "vote_count": votes, "original_language": lang,
        "genres": [{"id": i, "name": g} for i, g in enumerate(genres)],
        "networks": [{"name": "Net"}], "poster_path": f"/{tmdb_id}.jpg", "backdrop_path": f"/{tmdb_id}b.jpg",
        "last_episode_to_air": last, "next_episode_to_air": nxt,
        "seasons": [{"season_number": s, "episode_count": c} for s, c in (seasons or {}).items()],
    }


def ep(season, episode, when, name="An episode"):
    return {"season_number": season, "episode_number": episode, "air_date": when, "name": name}


SHOWS = {
    500: show(500, "Sigma", last=ep(2, 3, day(-2)), nxt=ep(2, 4, day(5)), seasons={1: 3, 2: 8}),
    501: show(501, "Old Show", last=ep(4, 10, day(-200)), seasons={4: 10}),
    502: show(502, "Disliked", last=ep(1, 2, day(-1)), seasons={1: 8}),
    503: show(503, "Later Show", last=ep(1, 6, day(-3)), seasons={1: 6}),
    600: show(600, "The Bright Shore", last=ep(3, 8, day(-10)), seasons={1: 8, 2: 8, 3: 8}),
    601: show(601, "Love Villa", last=ep(5, 2, day(-1)), genres=("Reality",)),
    602: show(602, "So So", last=ep(1, 2, day(-1)), rating=6.0),
    603: show(603, "Seoul Nights", last=ep(1, 4, day(-1)), lang="ko", rating=7.5, votes=300),
    604: show(604, "Not For You", last=ep(2, 1, day(-4)), nxt=ep(2, 2, day(3)), seasons={2: 10}),
    605: show(605, "Just Right", last=ep(1, 5, day(-6)), nxt=ep(1, 6, day(1)), seasons={1: 10}),
    606: show(606, "Long Gone", last=ep(1, 5, day(-300)), seasons={1: 5}),
}
TRENDING = [600, 601, 602, 500, 603, 604, 606, 605]


class FakeTMDB:
    def __init__(self):
        self.calls = []
        self.down = False

    def __call__(self, path, params=None):
        self.calls.append(path)
        if self.down:
            return None
        if path == "/trending/tv/week":
            return {"results": [{"id": i} for i in TRENDING]} if (params or {}).get("page") == 1 else {"results": []}
        if path.startswith("/tv/") and "/season/" in path:
            tid, n = int(path.split("/")[2]), int(path.split("/")[4])
            if tid != 500 or n not in (1, 2):
                return None
            eps = 3 if n == 1 else 8
            return {"name": f"Season {n}", "episodes": [
                {"episode_number": e, "name": f"Ep {e}", "air_date": day(-30 + e) if n == 1 else day(-3 + e), "runtime": 50}
                for e in range(1, eps + 1)]}
        if path.startswith("/tv/"):
            return SHOWS.get(int(path.split("/")[2]))
        return None


class FakeSearcher:
    def __init__(self):
        self.queries = []

    def search(self, query, media_type="movie", sort=None, sort_by_size=True):
        self.queries.append((query, media_type, sort))
        rows = [
            ("Sigma.S02E03.1080p.WEB.x264", 95),      # a later episode: not the one asked for
            ("Other.Show.S02E02.1080p.WEB", 90),      # another show
            ("Sigma.S02E02.720p.HDTV", 60),
            ("Sigma.S02E02.1080p.WEB-DL", 80),
            ("Sigma S02 Complete 1080p", 70),         # a season pack when an episode was asked for
            ("The.Bright.Shore.S03.1080p.WEB", 85),
            ("The.Bright.Shore.S01-S03.1080p", 99),   # a several-season pack: too much
            ("The.Bright.Shore.S03E08.1080p", 75),
        ]
        out = [{"name": n, "quality": "1080p", "size_display": "1 GB", "seeds": 20, "score": s,
                "magnet": f"magnet:?xt=urn:btih:{i:040d}&dn={n}"} for i, (n, s) in enumerate(rows)]
        out.sort(key=lambda r: -r["score"])
        return out


def main():
    test_pure()
    tmp = tempfile.mkdtemp(prefix="nl_new_test_")
    try:
        run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    test_static()
    if failures:
        print(f"\n{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("\nAll Northern Lights New & popular tests passed")


def test_pure():
    from services import nl_new_episodes as ne

    print("what to offer for a show")
    o = ne.latest_offer(SHOWS[500], {(1, 1), (1, 2), (1, 3), (2, 1)}, TODAY, 45)
    check("airing season: the first missing episode", o and o["kind"] == "episode" and o["season"] == 2 and o["episode"] == 2, o)
    check("missing episodes listed", o and o["missing"] == [2, 3], o)
    check("status line says what came out, the next one and how many are new",
          o and o["status_line"] == "S02E03 out 3 Nov · next 10 Nov · 2 to watch", o and o["status_line"])
    check("nothing when you have them all", ne.latest_offer(SHOWS[500], {(2, 1), (2, 2), (2, 3)}, TODAY, 45) is None)
    check("nothing when the latest episode is old", ne.latest_offer(SHOWS[501], set(), TODAY, 45) is None)
    o = ne.latest_offer(SHOWS[600], set(), TODAY, 45)
    check("finished season, watched elsewhere: its first episode, never a whole season by default",
          o and o["kind"] == "episode" and o["season"] == 3 and o["episode"] == 1 and o["label"] == "Get S03E01"
          and o["status_line"] == "Season 3 finished 26 Oct · 8 to watch", o)
    o = ne.latest_offer(SHOWS[600], set(), TODAY, 45, "beginning")
    check("a show you never had starts from S1E1, even when it is on season 3",
          o and (o["season"], o["episode"]) == (1, 1) and o["status_line"] == "Season 3 finished 26 Oct · start from S01E01"
          and o["to_get"] == 24, o)
    o = ne.latest_offer(SHOWS[500], {(1, 1), (1, 3), (2, 1)}, TODAY, 45, "continue")
    check("with files: the first gap in watching order (S01E02 before S02E02)", o and (o["season"], o["episode"]) == (1, 2), o)
    o = ne.latest_offer(SHOWS[500], {(2, 1)}, TODAY, 45, "continue")
    check("with files from season 2 only: on from there, not back to S1E1", o and (o["season"], o["episode"]) == (2, 2), o)
    check("aired episodes in order, latest season only up to the last aired one",
          ne.aired_episodes(SHOWS[500]) == [(1, 1), (1, 2), (1, 3), (2, 1), (2, 2), (2, 3)], ne.aired_episodes(SHOWS[500]))
    o = ne.latest_offer(SHOWS[600], {(3, e) for e in range(1, 8)}, TODAY, 45)
    check("finished season, one missing: that episode", o and o["kind"] == "episode" and o["episode"] == 8, o)
    check("no air date or a future one: nothing",
          ne.latest_offer(show(1, "x", last=ep(1, 1, None)), set(), TODAY, 45) is None
          and ne.latest_offer(show(1, "x", last=ep(1, 1, day(2))), set(), TODAY, 45) is None)
    check("specials (season 0) are ignored", ne.latest_offer(show(1, "x", last=ep(0, 1, day(-1))), set(), TODAY, 45) is None)

    print("does a copy match")
    m = ne.copy_matches
    check("the right episode", m("Sigma.S02E02.1080p.WEB", "Sigma", 2, 2))
    check("a double episode that holds it", m("Sigma.S02E01-E02.1080p", "Sigma", 2, 2))
    check("not another episode", not m("Sigma.S02E03.1080p", "Sigma", 2, 2))
    check("not another show", not m("Other.Show.S02E02.1080p", "Sigma", 2, 2))
    check("not a pack when an episode was asked for", not m("Sigma S02 Complete", "Sigma", 2, 2))
    check("a season pack", m("The.Bright.Shore.S03.1080p.WEB", "The Bright Shore", 3, None)
          and m("The Bright Shore Season 3 1080p", "The Bright Shore", 3, None))
    check("not another season, a several-season pack or an episode",
          not m("The.Bright.Shore.S02.1080p", "The Bright Shore", 3, None)
          and not m("The.Bright.Shore.S01-S03.1080p", "The Bright Shore", 3, None)
          and not m("The.Bright.Shore.S03E08.1080p", "The Bright Shore", 3, None))
    check("1x02 style", m("Sigma 2x02 HDTV", "Sigma", 2, 2))

    print("settings")
    cfg = ne.settings_from({"new_episodes": {"recent_days": 10, "min_rating": None}})
    check("config wins, missing keys keep defaults", cfg["recent_days"] == 10 and cfg["min_rating"] == ne.DEFAULTS["min_rating"], cfg)

    print("popular order")
    cards = [{"_rank": 0, "taste": None}, {"_rank": 1, "taste": 0.9}, {"_rank": 2, "taste": None}]
    check("a strong taste fit moves up", ne.popular_order(cards, 0.6)[0]["taste"] == 0.9)
    check("taste weight 0 is trending order", [c["_rank"] for c in ne.popular_order(cards, 0.0)] == [0, 1, 2])
    gaps = [{"_rank": 3, "taste": None}, {"_rank": 40, "taste": None}]
    check("ranks with gaps (filtered shows) still order by trending", [c["_rank"] for c in ne.popular_order(gaps, 0.6)] == [3, 40])


def run(tmp):
    from services import nl_new_episodes as ne

    ctx = nl_fake.boot(tmp)
    client, state, db, ids = ctx["client"], ctx["state"], ctx["db"], ctx["ids"]
    sigma = ids["Sigma"]
    tmdb, searcher = FakeTMDB(), FakeSearcher()
    with db.get_connection() as conn:
        conn.execute("UPDATE media_items SET tmdb_id = 500 WHERE id = ?", (sigma,))
        profile = conn.execute("SELECT id FROM profiles ORDER BY id LIMIT 1").fetchone()[0]
    db.upsert_catalogue_items([
        {"media_type": "tv", "tmdb_id": 501, "title": "Old Show"},
        {"media_type": "tv", "tmdb_id": 502, "title": "Disliked"},
        {"media_type": "tv", "tmdb_id": 503, "title": "Later Show"},
        {"media_type": "tv", "tmdb_id": 604, "title": "Not For You"},
        {"media_type": "tv", "tmdb_id": 605, "title": "Just Right"},
    ])
    with db.get_connection() as conn:
        tid = {r[0]: r[1] for r in conn.execute("SELECT tmdb_id, id FROM media_items WHERE media_type = 'tv'")}
        conn.execute("INSERT INTO watch_history (media_item_id, profile_id, status, user_rating) VALUES (?, ?, 'seen', 8)", (tid[501], profile))
        conn.execute("INSERT INTO watch_history (media_item_id, profile_id, status, user_rating) VALUES (?, ?, 'seen', 3)", (tid[502], profile))
        conn.execute("INSERT INTO watch_later (media_item_id, profile_id) VALUES (?, ?)", (tid[503], profile))

    taste_calls = []

    def taste(item_ids, people):
        taste_calls.append((sorted(item_ids), list(people)))
        return {tid[604]: -0.5, tid[605]: 0.9}

    svc = ne.NewEpisodes(db, tmdb, {"new_episodes": {"min_rating": 7.0}}, taste=taste, searcher=searcher,
                         foreign_gate=lambda row: row["original_language"] == "en" or float(row["rating"]) >= 7.8,
                         today=lambda: TODAY)
    state.nl_new_episodes = svc

    print("your shows")
    mine = {r["title"] for r in svc.your_shows([profile])}
    check("files, seen and saved-for-later shows count; a disliked one does not",
          mine == {"Sigma", "Old Show", "Later Show"}, mine)
    data = svc.lists([profile])
    yours = {c["title"]: c for c in data["yours"]}
    check("only shows with something new for you", set(yours) == {"Sigma", "Later Show"}, list(yours))
    check("Sigma offers S02E02 and links to its show page",
          yours["Sigma"]["offer"]["label"] == "Get S02E02" and yours["Sigma"]["href"] == f"/nl/show/{sigma}"
          and yours["Sigma"]["in_library"], yours["Sigma"])
    check("a Watch later show without files has no show page link and starts from S1E1",
          yours["Later Show"]["href"] is None and yours["Later Show"]["offer"]["label"] == "Get S01E01"
          and yours["Later Show"]["why"] == "On your Watch later list", yours["Later Show"])
    check("newest first", [c["title"] for c in data["yours"]] == ["Sigma", "Later Show"])

    print("popular right now")
    titles = [c["title"] for c in data["popular"]]
    check("airing, well-rated, allowed shows only (not yours, reality, low-rated, foreign below the bar, old)",
          set(titles) == {"The Bright Shore", "Just Right"}, titles)
    check("a show that clearly doesn't fit your taste is left out", "Not For You" not in titles)
    check("a good taste fit ranks first and says why", titles[0] == "Just Right"
          and data["popular"][0]["why"] == "Trending, and your kind of show", data["popular"][0])
    check("taste asked for the viewer", taste_calls and taste_calls[0][1] == [profile], taste_calls)
    bright = [c for c in data["popular"] if c["title"] == "The Bright Shore"][0]
    check("a popular show on season 3 offers S01E01", bright["offer"]["label"] == "Get S01E01", bright["offer"])
    check("TMDB images, no keys", data["popular"][0]["poster_url"].startswith("https://image.tmdb.org/t/p/w342/"))
    check("no internal fields leak", all("_rank" not in c for c in data["popular"]))

    print("caching")
    before = len(tmdb.calls)
    svc.lists([profile])
    check("the built page is reused", len(tmdb.calls) == before)
    svc.lists([profile], fresh=True)
    check("fresh rebuilds from cached TMDB answers", len(tmdb.calls) == before, tmdb.calls[before:])
    tmdb.down = True
    svc._details = {k: (0.0, v[1]) for k, v in svc._details.items()}   # all stale
    svc._trending = (0.0, svc._trending[1])
    again = svc.lists([profile], fresh=True)
    check("TMDB down: the last answers are still used", {c["title"] for c in again["yours"]} == {"Sigma", "Later Show"}, again)
    tmdb.down = False

    real = ne.wishmatch_taste(state.wishmatch_movies.engine, [tid[604], tid[605]], [profile], False)
    check("the real Wishmatch taste hook runs (untagged titles: no score)", real == {}, real)

    print("the endpoints")
    state.api_key = ""
    r = client.get("/api/nl/new-episodes")
    check("no TMDB key: a plain note, no error", r.status_code == 200 and not r.json()["enabled"] and "TMDB" in r.json()["note"], r.text)
    state.api_key = "test-key"
    r = client.get("/api/nl/new-episodes")
    check("the lists come back with a downloading flag", r.status_code == 200 and all("downloading" in c for c in r.json()["popular"]), r.text)

    r = client.get("/api/nl/new-episodes/copies", params={"tmdb_id": 500, "season": 2, "episode": 2})
    copies = r.json().get("copies") or []
    check("copies: only Sigma S02E02, best first", [c["name"] for c in copies] == ["Sigma.S02E02.1080p.WEB-DL", "Sigma.S02E02.720p.HDTV"], copies)
    check("copies never carry a magnet link", "magnet" not in r.text)
    check("the search asked for the episode as a show", searcher.queries[-1] == ("Sigma S02E02", "tv", "best"), searcher.queries[-1])

    started = []

    async def fake_download(magnet, download_id, movie_id, title, media_type="movie"):
        started.append((magnet, movie_id, title, media_type))

    state.torrent_downloader.download = fake_download
    state.downloads_store.clear()
    r = client.post("/api/nl/new-episodes/download", json={"tmdb_id": 500, "season": 2, "episode": 2})
    check("one tap starts the best matching copy", r.status_code == 200 and started and "Sigma.S02E02.1080p.WEB-DL" in started[-1][0]
          and started[-1][1] == sigma and started[-1][2] == "Sigma" and started[-1][3] == "tv", (r.text, started))
    check("the reply names the release, not the link", "magnet" not in r.text and r.json()["release"] == "Sigma.S02E02.1080p.WEB-DL")
    r = client.get("/api/nl/new-episodes")
    sig = [c for c in r.json()["yours"] if c["title"] == "Sigma"]
    check("the card now says it is downloading", sig and sig[0]["downloading"], sig)

    copies = client.get("/api/nl/new-episodes/copies", params={"tmdb_id": 500, "season": 2, "episode": 2}).json()["copies"]
    r = client.post("/api/nl/new-episodes/download", json={"tmdb_id": 500, "season": 2, "episode": 2, "copy_id": copies[1]["copy_id"]})
    check("a chosen copy is the one started", r.status_code == 200 and "720p.HDTV" in started[-1][0], started[-1])
    r = client.post("/api/nl/new-episodes/download", json={"tmdb_id": 500, "season": 2, "episode": 2, "copy_id": "nope"})
    check("an unknown copy is a plain 404", r.status_code == 404 and "no longer in the list" in r.json()["detail"], r.text)
    r = client.post("/api/nl/new-episodes/download", json={"tmdb_id": 600, "season": 3, "episode": 2, "copy_id": copies[0]["copy_id"]})
    check("a copy of another show can't be used", r.status_code == 404, r.text)

    with db.get_connection() as conn:
        had = conn.execute("SELECT id FROM media_items WHERE media_type = 'tv' AND tmdb_id = 600").fetchone()
    check("the new show is not in the catalogue yet", had is None)
    r = client.post("/api/nl/new-episodes/download", json={"tmdb_id": 600, "season": 3})
    with db.get_connection() as conn:
        row = conn.execute("SELECT id, title FROM media_items WHERE media_type = 'tv' AND tmdb_id = 600").fetchone()
    check("a whole season: the single-season pack, and the show is added to the catalogue",
          r.status_code == 200 and "The.Bright.Shore.S03.1080p.WEB" in started[-1][0] and row and started[-1][1] == row[0]
          and row[1] == "The Bright Shore", (r.text, started[-1], row))
    r = client.post("/api/nl/new-episodes/download", json={"tmdb_id": 605, "season": 1, "episode": 6})
    check("no copy yet: a plain sentence", r.status_code == 404 and "few hours after they air" in r.json()["detail"], r.text)
    r = client.post("/api/nl/new-episodes/download", json={"tmdb_id": 999999, "season": 1})
    check("an unknown show: a plain 404", r.status_code == 404, r.text)
    r = client.post("/api/nl/new-episodes/download", json={"tmdb_id": 500, "season": 0})
    check("bad numbers are refused", r.status_code == 422, r.text)

    print("the season picker")
    r = client.get("/api/nl/new-episodes/show/500/seasons")
    seasons = r.json().get("seasons") or []
    check("seasons with aired and had counts", r.status_code == 200 and [(x["season"], x["aired"], x["have"]) for x in seasons]
          == [(1, 3, 3), (2, 3, 1)], seasons)
    r = client.get("/api/nl/new-episodes/show/500/season/2")
    eps = r.json().get("episodes") or []
    check("a season's episodes say aired and had", r.status_code == 200 and len(eps) == 8 and eps[0]["have"] and not eps[1]["have"]
          and eps[2]["aired"] and not eps[3]["aired"] and eps[1]["code"] == "S02E02", eps[:4])
    check("an unfinished season can't be fetched whole", r.json().get("can_get_season") is False, r.json().get("can_get_season"))
    r = client.get("/api/nl/new-episodes/show/500/season/1")
    check("a finished season you have all of can't be fetched again", r.json().get("can_get_season") is False)
    check("an unknown season is a plain 404", client.get("/api/nl/new-episodes/show/500/season/9").status_code == 404)
    check("an unknown show is a plain 404", client.get("/api/nl/new-episodes/show/999999/seasons").status_code == 404)

    print("the pages")
    for url in ("/nl/new", "/nl/tv/new", "/player/new"):
        r = client.get(url)
        check(f"{url} loads with the page script", r.status_code == 200 and "/static/nl/new-episodes.js" in r.text, r.status_code)
    tv = client.get("/nl/tv/new").text
    check("the TV page is a TV page (tv.js starts it)", 'data-page="new"' in tv and "/static/nl/tv/tv.js" in tv)
    check("the laptop page links stay under /player/", 'href="/player/new"' in client.get("/player/new").text)
    check("the TV nav has New & popular", 'href="/nl/tv/new"' in client.get("/nl/tv/").text)
    check("the web nav has New", 'href="/nl/new"' in client.get("/nl/").text)


def test_static():
    print("wiring")
    cfg = open(os.path.join(ROOT, "config.yaml"), encoding="utf-8").read()
    check("config.yaml has the new_episodes: settings", "new_episodes:" in cfg and "recent_days:" in cfg)
    route = open(os.path.join(ROOT, "web/routes/nl_new.py"), encoding="utf-8").read()
    check("the route reuses the Download tab's start_download", "from web.routes.torrents import start_download" in route)
    js = open(os.path.join(ROOT, "static/nl/new-episodes.js"), encoding="utf-8").read()
    tvjs = open(os.path.join(ROOT, "static/nl/tv/tv.js"), encoding="utf-8").read()
    home = open(os.path.join(ROOT, "static/nl/home.js"), encoding="utf-8").read()
    check("new-episodes.js: text only via textContent, no innerHTML or eval", "innerHTML" not in js and "eval(" not in js)
    check("new-episodes.js: never handles a magnet link, only copy_id", "magnet" not in js and "copy_id" in js)
    check("new-episodes.js: buttons and copies are remote-focusable rows on the TV",
          'setAttribute("data-nl-focus", "")' in js and '"data-nl-row", "new-"' in js and '"data-nl-row", "copies-"' in js)
    check("new-episodes.js: a season picker with its own focus rows on the TV",
          "/seasons" in js and "/season/" in js and '"data-nl-row", "seasons-"' in js and '"data-nl-row", "ep-"' in js
          and "Get whole season" in js)
    check("tv.js starts the page and the home row", 'page === "new"' in tvjs and "NLNew.mount" in tvjs and "NLNew.homeRow" in tvjs)
    check("home.js has the New episodes row", "NLNew.homeRow" in home)
    tvhome = open(os.path.join(ROOT, "templates/nl/tv/home.html"), encoding="utf-8").read()
    check("TV home has a focusable New episodes row", 'data-nl-row="new-episodes"' in tvhome)


if __name__ == "__main__":
    main()
