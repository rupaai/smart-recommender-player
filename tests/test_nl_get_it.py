"""
Offline tests for Northern Lights "Get it" (tests/test_nl_get_it.py): search a title, every season of a show on
one page, and "Find a copy" links that open it. TMDB and the torrent search are faked; nothing leaves the process.

    uv run python tests/test_nl_get_it.py
"""

import os
import shutil
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nl_fake  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TODAY = date(2026, 11, 14)
failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} — got: {detail}")
        failures.append(label)


SIGMA = {"id": 500, "name": "Sigma", "first_air_date": "2019-01-10", "vote_average": 8.1, "vote_count": 900,
         "overview": "About Sigma.", "networks": [{"name": "Net"}], "poster_path": "/s.jpg",
         "last_episode_to_air": {"season_number": 3, "episode_number": 2, "air_date": "2026-11-10"},
         "next_episode_to_air": {"season_number": 3, "episode_number": 3, "air_date": "2026-11-20"},
         "seasons": [{"season_number": 0, "episode_count": 2, "name": "Specials"},
                     {"season_number": 1, "episode_count": 3, "name": "Season 1"},
                     {"season_number": 2, "episode_count": 4, "name": "Season 2"},
                     {"season_number": 3, "episode_count": 8, "name": "Season 3"},
                     {"season_number": 4, "episode_count": 8, "name": "Season 4"}]}
SIGMA_NEW = {"id": 900, "name": "Sigma", "first_air_date": "2026-02-01", "vote_average": 7.0, "vote_count": 40, "seasons": []}
FILM = {"id": 700, "title": "Bright Film", "release_date": "2026-08-01", "vote_average": 7.8, "vote_count": 900,
        "overview": "About it.", "runtime": 118, "genres": [{"id": 1, "name": "Drama"}], "poster_path": "/m.jpg"}


class FakeTMDB:
    def __init__(self):
        self.calls = []
        self.down = False

    def __call__(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        if self.down:
            return None
        if path == "/search/multi":
            return {"results": [
                {"media_type": "tv", "id": 500, "name": "Sigma", "first_air_date": "2019-01-10", "poster_path": "/s.jpg", "vote_average": 8.1},
                {"media_type": "tv", "id": 900, "name": "Sigma", "first_air_date": "2026-02-01"},
                {"media_type": "person", "id": 1, "name": "Sigma Person"},
                {"media_type": "movie", "id": 700, "title": "Bright Film", "release_date": "2026-08-01"},
            ]}
        if path == "/tv/500":
            return SIGMA
        if path == "/tv/900":
            return SIGMA_NEW
        if path == "/movie/700":
            return FILM
        return None


def test_pure():
    from services import nl_get_it as gi
    print("season lines")
    check("complete", gi.season_state({"aired": 3, "have": 3}) == "complete")
    check("partial", gi.season_state({"aired": 4, "have": 1}) == "partial")
    check("none", gi.season_state({"aired": 4, "have": 0}) == "none")
    check("upcoming", gi.season_state({"aired": 0, "have": 0}) == "upcoming")
    check("partial line says what's missing", gi.season_line({"aired": 4, "have": 1, "episodes": 4}) == "On the server · 1 of 4 · 3 missing")
    check("airing season line", gi.season_line({"aired": 2, "have": 0, "episodes": 8}) == "Not on the server · 2 of 8 out")
    check("Find a copy link", gi.get_href(12) == "/nl/get?item=12" and gi.get_href(None) == "/nl/get")


def run(tmp):
    from services import nl_new_episodes as ne
    from services.nl_get_it import GetIt

    ctx = nl_fake.boot(tmp)
    client, state, db, ids = ctx["client"], ctx["state"], ctx["db"], ctx["ids"]
    sigma = ids["Sigma"]
    with db.get_connection() as conn:
        conn.execute("UPDATE media_items SET tmdb_id = 500 WHERE id = ?", (sigma,))
    tmdb = FakeTMDB()
    svc = ne.NewEpisodes(db, tmdb, {}, searcher=None, today=lambda: TODAY)
    state.nl_new_episodes = svc
    state.nl_get_it = GetIt(svc)
    state.api_key = "test-key"

    print("search")
    r = client.get("/api/nl/get/search", params={"q": "sigma"})
    rows = r.json()["results"]
    check("films and shows, no people", [(x["kind"], x["tmdb_id"]) for x in rows] == [("tv", 500), ("tv", 900), ("movie", 700)], rows)
    first = rows[0]
    check("the show on the server says how many seasons are here", first["in_library"] and first["have_label"] == "2 seasons here"
          and first["media_item_id"] == sigma and first["year"] == 2019, first)
    check("two shows named Sigma are flagged so the year is read", rows[0]["same_name"] and rows[1]["same_name"] and not rows[2]["same_name"])
    check("a title not here says so", rows[1]["have_label"] == "Not here" and not rows[1]["in_library"])
    r = client.get("/api/nl/get/search", params={"q": "sigma", "kind": "movie"})
    check("kind=movie keeps only films", [x["kind"] for x in r.json()["results"]] == ["movie"])
    check("one letter searches nothing", client.get("/api/nl/get/search", params={"q": "s"}).json()["results"] == [])
    tmdb.down = True
    r = client.get("/api/nl/get/search", params={"q": "sigma"})
    check("TMDB down: a plain 503", r.status_code == 503 and "TMDB" in r.json()["detail"], r.text)
    tmdb.down = False

    print("a show: every season on one page")
    r = client.get("/api/nl/get/tv/500")
    show = r.json()
    seasons = {s["season"]: s for s in show["seasons"]}
    check("specials are left out", sorted(seasons) == [1, 2, 3, 4], sorted(seasons))
    check("season 1 is complete", seasons[1]["state"] == "complete" and not seasons[1]["can_get_season"], seasons[1])
    check("season 2 is partial with 3 missing and can be got whole", seasons[2]["state"] == "partial" and seasons[2]["missing"] == 3
          and seasons[2]["can_get_season"], seasons[2])
    check("season 3 is still airing: single episodes only", seasons[3]["state"] == "none" and not seasons[3]["can_get_season"]
          and seasons[3]["aired"] == 2, seasons[3])
    check("season 4 is not out", seasons[4]["state"] == "upcoming", seasons[4])
    check("counts for the header", show["season_count"] == 4 and show["complete_seasons"] == 1 and show["href"] == f"/nl/show/{sigma}", show)
    check("not downloading", show["downloading"] is False)
    check("unknown kind is a 404", client.get("/api/nl/get/person/1").status_code == 404)
    check("a show TMDB doesn't know is a 404", client.get("/api/nl/get/tv/4242").status_code == 404)

    print("a film")
    film = client.get("/api/nl/get/movie/700").json()
    check("a film not here", film["kind"] == "movie" and film["title"] == "Bright Film" and not film["in_library"] and film["href"] is None, film)

    print("Find a copy opens Get it")
    gamma = ids.get("Gamma Ray")
    with db.get_connection() as conn:
        gamma = gamma or conn.execute("SELECT id FROM media_items WHERE title = 'Gamma Ray'").fetchone()[0]
        stub = db.get_or_create_media_item(title="Odd Stub", media_type="tv")
    r = client.get(f"/api/nl/get/item/{gamma}")
    check("a catalogue title with a TMDB id opens its page", r.json() == {"kind": "movie", "tmdb_id": 999, "title": "Gamma Ray"}, r.text)
    r = client.get(f"/api/nl/get/item/{stub}")
    check("a title without one searches its name", r.json()["tmdb_id"] is None and r.json()["query"] == "Odd Stub", r.text)
    check("an unknown title is a 404", client.get("/api/nl/get/item/999999").status_code == 404)

    print("the pages")
    for path in ("/nl/get", "/nl/tv/get", "/player/get"):
        page = client.get(path)
        check(f"{path} loads the Get it script", page.status_code == 200 and "/static/nl/get-it.js" in page.text, page.status_code)
    check("the web nav has Get it", 'href="/nl/get"' in client.get("/nl/").text)
    check("the TV bar has Get it", 'get">Get it</a>' in client.get("/nl/tv/").text)


def test_static():
    print("wiring")
    js = open(os.path.join(ROOT, "static/nl/get-it.js"), encoding="utf-8").read()
    tvjs = open(os.path.join(ROOT, "static/nl/tv/tv.js"), encoding="utf-8").read()
    check("get-it.js: text only via textContent, no innerHTML or eval", "innerHTML" not in js and "eval(" not in js)
    check("get-it.js: never handles a magnet link, only copy_id", "magnet" not in js and "copy_id" in js)
    check("get-it.js: reuses the New & popular copies and download calls",
          "/api/nl/new-episodes/copies" in js and "/api/nl/new-episodes/download" in js)
    check("get-it.js: remote-focusable rows for results, seasons, copies and episodes",
          'setAttribute("data-nl-focus", "")' in js and '"res-"' in js and '"season-"' in js and '"ep-"' in js)
    check("get-it.js: best pick is shown and taken first", "Get best pick" in js and "best_pick" in js)
    check("tv.js starts the page and maps /nl/get links", 'page === "get"' in tvjs and "NLGet.mount" in tvjs
          and 'href.indexOf("/nl/get") === 0' in tvjs)


def main():
    test_pure()
    tmp = tempfile.mkdtemp(prefix="nl_get_test_")
    try:
        run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    test_static()
    if failures:
        print(f"\n{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("\nAll Northern Lights Get it tests passed")


if __name__ == "__main__":
    main()
