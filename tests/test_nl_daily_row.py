"""
"Picked for you today" in the player app (static/nl/daily-picks.js) and its "Get it" button
(POST /api/nl/daily-picks/download, services/nl_film_copies.py).

The torrent search is faked; nothing leaves the process.

    uv run python tests/test_nl_daily_row.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.nl_film_copies import best_film_copy, film_copies, film_matches  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} — got: {detail}")
        failures.append(label)


def read(path):
    return open(os.path.join(ROOT, path), encoding="utf-8").read()


class FakeSearcher:
    def __init__(self, results):
        self.results = results
        self.asked = []

    def search(self, query, media_type="movie", sort=None):
        self.asked.append((query, media_type, sort))
        return self.results


def test_matching():
    print("which releases are this film")
    check("title and year", film_matches("Paddington.2.2017.1080p.BluRay.REMUX", "Paddington 2", 2017))
    check("one year off is fine", film_matches("Paddington 2 (2018) 2160p", "Paddington 2", 2017))
    check("another year is another film", not film_matches("Dune.1984.1080p", "Dune", 2021))
    check("a missing title word is another film", not film_matches("Paddington.2014.1080p", "Paddington 2", 2017))
    check("an episode is not the film", not film_matches("Fargo.S01E01.2014.1080p", "Fargo", 2014))
    check("a year-titled film still finds its own year", film_matches("1917.2019.1080p.BluRay", "1917", 2019))
    check("apostrophes don't matter", film_matches("Ocean's.Eleven.2001.1080p", "Ocean's Eleven", 2001))
    check("no year known: title is enough", film_matches("Some Film 1080p", "Some Film", None))


def test_best_copy():
    print("best copy")
    s = FakeSearcher([
        {"name": "Dune.1984.1080p", "magnet": "magnet:?a"},
        {"name": "Dune.Part.One.2021.2160p.REMUX", "magnet": "magnet:?b"},
        {"name": "Dune.2021.1080p.WEB", "magnet": "magnet:?c"},
        {"name": "Dune.2021.no.magnet"},
    ])
    found = film_copies(s, "Dune", 2021)
    check("keeps the searcher's order, only this film", [f["magnet"] for f in found] == ["magnet:?b", "magnet:?c"], found)
    check("asks with title and year, Best match, films", s.asked[0] == ("Dune 2021", "movie", "best"), s.asked)
    check("best is the first", best_film_copy(s, "Dune", 2021)["magnet"] == "magnet:?b")
    try:
        best_film_copy(FakeSearcher([]), "Dune", 2021)
        check("nothing found says so", False)
    except LookupError as e:
        check("nothing found says so in plain words", "No copy of Dune" in str(e), e)
    try:
        film_copies(None, "Dune", 2021)
        check("no searcher is a server problem", False)
    except RuntimeError:
        check("no searcher is a server problem", True)


def test_pages():
    print("pages")
    js = read("static/nl/daily-picks.js")
    check("text only via textContent, no innerHTML or eval", "innerHTML" not in js and "eval(" not in js)
    check("never handles a torrent link", "magnet" not in js)
    check("cards are remote-focusable on the TV", 'setAttribute("data-nl-focus", "")' in js)
    check("a busy card keeps its focus (aria-disabled, not disabled)", ".disabled = true" not in js and "aria-disabled" in js)
    check("shows IMDb / RT", "NL.scoreFacts" in js and "imdb_rating" in js and "rt_score" in js)
    web = read("templates/nl/home.html")
    tv = read("templates/nl/tv/home.html")
    check("web home has the row and the script", 'id="nl-daily"' in web and "daily-picks.js" in web)
    check("TV and laptop home has a focusable row", 'data-nl-row="daily-picks"' in tv and "daily-picks.js" in tv)
    check("home.js and tv.js fill it", "NLDaily.homeRow" in read("static/nl/home.js") and "NLDaily.homeRow" in read("static/nl/tv/tv.js"))
    route = read("web/routes/nl_picks.py")
    check("the route marks ready / downloading and has Get it",
          '"ready"' in route and '"downloading"' in route and "/api/nl/daily-picks/download" in route)
    check("Get it reuses the Download tab's start_download", "start_download(body.media_item_id" in route)


if __name__ == "__main__":
    test_matching()
    test_best_copy()
    test_pages()
    if failures:
        print(f"\n{len(failures)} failed")
        sys.exit(1)
    print("\nall passed")
