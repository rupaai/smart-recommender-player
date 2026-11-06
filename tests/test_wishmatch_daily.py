"""
Today's movie picks from what you watched lately (services/wishmatch_movies/daily.py).

On the small tagged catalogue of tests/test_wishmatch_movies.py: watching funny shows lately gives funny
films, watching tense ones gives tense films; old watches don't count; nothing already seen or played
shows; each card says which recent title it is like; the day's list is kept and remade the next day;
nothing watched gives a plain note. Jellyfin is faked.

    uv run python tests/test_wishmatch_daily.py
"""

import os
import sys
import tempfile
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_wishmatch_movies import catalogue, ids_by_title  # noqa: E402

from services.recommender_service import MOOD_MAPPINGS  # noqa: E402
from services.wishmatch_movies import WishmatchMovies  # noqa: E402
from services.wishmatch_movies.adapter import MovieAdapter  # noqa: E402
from services.wishmatch_movies.daily import DailyPicks, parse_when  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOW = datetime(2026, 11, 6, 9, 0, 0)
failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} — got: {detail}")
        failures.append(label)


def jf(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.0000000Z")


def main():
    db = catalogue()
    ids = ids_by_title(db)
    wm = WishmatchMovies(db, MOOD_MAPPINGS, adapter=MovieAdapter(db, crowd_file=os.path.join(tempfile.mkdtemp(), "c.json")))
    played = {1: [], 2: []}
    day = {"d": date(2026, 11, 6)}
    daily = DailyPicks(wm, lambda pid: played.get(pid), today=lambda: day["d"], now=lambda: NOW)

    print("\n[1] Times")
    check("Jellyfin time", parse_when("2026-11-04T21:10:00.1234567Z") == datetime(2026, 11, 4, 21, 10))
    check("SQLite time", parse_when("2026-11-04 21:10:00") == datetime(2026, 11, 4, 21, 10))
    check("nonsense is None", parse_when("soon") is None and parse_when(None) is None)

    print("\n[2] Nothing watched lately")
    out = daily.build([1, 2], False)
    check("no picks and a plain note", out["picks"] == [] and "Watch something" in out["note"], out)
    played[1] = [{"media_item_id": ids["Tense Show 0"], "played_at": jf(NOW - timedelta(days=90))}]
    check("a watch older than `days` doesn't count", daily.build([1, 2], False)["picks"] == [])

    print("\n[3] Funny shows lately -> funny films")
    played[1] = [{"media_item_id": ids[f"Funny Show {i}"], "played_at": jf(NOW - timedelta(days=i + 1))} for i in range(3)]
    played[1].append({"media_item_id": ids["Funny Film 0"], "played_at": jf(NOW - timedelta(days=2))})
    out = daily.build([1, 2], False)
    names = [c["title"] for c in out["picks"]]
    check("picks are films only", {c["media_type"] for c in out["picks"]} == {"movie"}, names)
    check("the top picks are funny films", all(n.startswith("Funny Film") for n in names[:6]), names)
    check("a film played lately is not picked again", "Funny Film 0" not in names, names)
    check("each card says which recent title it is like",
          all(c["why"].startswith("Because you watched ") and c["because"]["title"] for c in out["picks"]), out["picks"][:1])
    check("cards carry IMDb / RT fields and no huge rows", "imdb_rating" in out["picks"][0] and "vibe_tags" not in out["picks"][0])

    print("\n[4] Gabriele watched tense things; alone his picks are tense")
    played[2] = [{"media_item_id": ids[f"Tense Show {i}"], "played_at": jf(NOW - timedelta(days=1))} for i in range(3)]
    out = daily.build([2, 1], False)
    names = [c["title"] for c in out["picks"]]
    check("his top picks are tense films", all(n.startswith("Tense Film") or "Thriller" in n or n == "Horror Film"
                                               for n in names[:5]), names)

    print("\n[5] Seen marks and ratings in the app count too")
    with db.get_connection() as conn:
        conn.execute("INSERT INTO watch_history (media_item_id, profile_id, status, watched_at) VALUES (?, 2, 'seen', ?)",
                     (ids["Tense Film 0"], (NOW - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")))
    recent, ok = daily.recent(2)
    check("a seen mark from the app is recent", ids["Tense Film 0"] in recent and ok, recent)
    names = [c["title"] for c in daily.build([2, 1], False)["picks"]]
    check("and is never picked", "Tense Film 0" not in names, names)

    print("\n[6] Today's list is kept, and remade tomorrow")
    path = os.path.join(tempfile.mkdtemp(), "daily_picks.json")
    wm.adapter.rules()["daily_picks"] = {"file": path}
    first = daily.today_list([1, 2], False)
    played[1] = [{"media_item_id": ids[f"Tense Show {i}"], "played_at": jf(NOW)} for i in range(4)]
    again = daily.today_list([1, 2], False)
    check("the same list all day", [c["id"] for c in again["picks"]] == [c["id"] for c in first["picks"]])
    check("kept in the file", os.path.exists(path))
    fresh = DailyPicks(wm, lambda pid: played.get(pid), today=lambda: day["d"], now=lambda: NOW)
    check("a restart reads the same list back",
          [c["id"] for c in fresh.today_list([1, 2], False)["picks"]] == [c["id"] for c in first["picks"]])
    day["d"] = date(2026, 11, 7)
    tomorrow = daily.today_list([1, 2], False)
    check("tomorrow follows what was watched since", tomorrow["picks"][0]["title"].startswith("Tense") or
          "Thriller" in tomorrow["picks"][0]["title"], tomorrow["picks"][:2])
    both = daily.today_list([1, 2], True)
    check("both of you get a list of your own", both["together"] and both["picks"], both.get("note"))

    print("\n[7] Wiring")
    rules = open(os.path.join(ROOT, "recommender_rules.yaml"), encoding="utf-8").read()
    check("recommender_rules.yaml has daily_picks: and recent:", "daily_picks:" in rules and "half_life_days:" in rules)
    route = open(os.path.join(ROOT, "web/routes/nl_picks.py"), encoding="utf-8").read()
    check("the route exists", "/api/nl/daily-picks" in route and "state.daily_picks.today_list" in route)

    if failures:
        print(f"\n{len(failures)} failed")
        sys.exit(1)
    print("\nall passed")


if __name__ == "__main__":
    main()
