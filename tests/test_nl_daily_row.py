"""
"Picked for you today" in the player app (static/nl/daily-picks.js) and its "Get it" button
(POST /api/nl/daily-picks/download, which uses the New & popular film copy picker).

    uv run python tests/test_nl_daily_row.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


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
    check("Get it picks the copy like films on New & popular", 'state.nl_new_episodes.pick' in route and '"movie")' in route)


if __name__ == "__main__":
    test_pages()
    if failures:
        print(f"\n{len(failures)} failed")
        sys.exit(1)
    print("\nall passed")
