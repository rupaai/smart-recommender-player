"""
Offline tests for new episodes that download by themselves (services/nl_auto_episodes.py).

TMDB, Jellyfin and the copy search are faked; nothing leaves the process.

    uv run python tests/test_nl_auto_episodes.py
"""

import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.nl_auto_episodes import (  # noqa: E402
    AutoEpisodes, aired_episodes, next_to_get, ready_after, runs_here, settings_from,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TODAY = date(2026, 11, 6)
failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} — got: {detail}")
        failures.append(label)


def day(n):
    return str(TODAY + timedelta(days=n))


def details(last_season, last_ep, seasons, aired=-2):
    return {"last_episode_to_air": {"season_number": last_season, "episode_number": last_ep, "air_date": day(aired)},
            "seasons": [{"season_number": s, "episode_count": c} for s, c in seasons.items()]}


class FakeNew:
    def __init__(self, shows, tmdb, had, copies):
        self.shows, self.tmdb, self.had, self.copies = shows, tmdb, had, copies
        self.picked = []

    def your_shows(self, profile_ids):
        return self.shows

    def details(self, tmdb_id):
        return self.tmdb.get(tmdb_id)

    def had_episodes(self, item_id):
        return self.had.get(item_id, set())

    def pick(self, tmdb_id, season, episode=None, copy_id=None):
        self.picked.append((tmdb_id, season, episode))
        if episode is None:
            raise AssertionError("asked for a season pack")
        name = self.copies.get((tmdb_id, season, episode))
        if not name:
            raise LookupError("No copy found yet.")
        return {"magnet": f"magnet:?xt={tmdb_id}{season}{episode}", "release": name, "title": "x", "media_item_id": 0}


def row(item_id, tmdb_id, title, active_days_ago=1, in_library=True):
    return {"id": item_id, "tmdb_id": tmdb_id, "title": title, "in_library": in_library,
            "active_at": f"{TODAY - timedelta(days=active_days_ago)} 20:00:00"}


def test_pure():
    print("pure parts")
    d = details(2, 3, {0: 4, 1: 3, 2: 8})
    check("aired: every season in order, latest only up to the last aired, no specials",
          aired_episodes(d, TODAY) == [(1, 1), (1, 2), (1, 3), (2, 1), (2, 2), (2, 3)], aired_episodes(d, TODAY))
    check("aired: nothing when the last episode is in the future", aired_episodes(details(1, 2, {1: 8}, aired=3), TODAY) == [])
    aired = aired_episodes(d, TODAY)
    check("next: the one after the furthest you have", next_to_get(aired, {(1, 1), (1, 2)}) == (1, 3))
    check("next: crosses into the next season", next_to_get(aired, {(1, 3)}) == (2, 1))
    check("next: an old gap is left alone", next_to_get(aired, {(1, 1), (2, 1)}) == (2, 2))
    check("next: none when you have the latest", next_to_get(aired, {(2, 3)}) is None)
    eps = [{"season": 1, "episode": e, "played": e <= 2} for e in range(1, 6)]
    check("ready: files after the last watched", ready_after(eps) == 3, ready_after(eps))
    check("ready: nothing watched = every file", ready_after([{**e, "played": False} for e in eps]) == 5)
    check("ready: order is by season and episode, not list order",
          ready_after([{"season": 2, "episode": 1, "played": False}, {"season": 1, "episode": 9, "played": True}]) == 1)
    cfg = settings_from({"auto_episodes": {"enabled": True}})
    check("runs in prod", runs_here(cfg, "prod"))
    check("not in dev", not runs_here(cfg, "dev"))
    check("off by default", not runs_here(settings_from({}), ""))


def test_plan():
    print("plan and copies")
    shows = [row(1, 100, "Sigma"), row(2, 200, "Busy"), row(3, 300, "Full"), row(4, 400, "Stale", active_days_ago=90),
             row(5, 500, "Only seen", in_library=False), row(6, 600, "No Jellyfin"), row(7, 700, "Caught up"),
             row(8, 800, "No copy")]
    tmdb = {100: details(2, 3, {1: 3, 2: 8}), 700: details(1, 4, {1: 4}), 800: details(1, 4, {1: 4})}
    had = {1: {(1, 1), (1, 2), (1, 3)}, 7: {(1, 4)}, 8: {(1, 1)}}
    ready = {1: 0, 2: 0, 3: 3, 7: 0, 8: 1}
    fake = FakeNew(shows, tmdb, had, {(100, 2, 1): "Sigma.S02E01.1080p"})
    auto = AutoEpisodes(fake, lambda i: ready.get(i), lambda: {2}, lambda: [1, 2],
                        {"auto_episodes": {"enabled": True}}, today=lambda: TODAY)
    plan = auto.plan()
    by = {p["title"]: p for p in plan}
    check("Sigma: gets S02E01 next", by["Sigma"]["action"] == "get" and by["Sigma"]["code"] == "S02E01", by.get("Sigma"))
    check("a show still downloading waits (keeps the order)", by["Busy"]["action"] == "wait")
    check("enough ready: nothing fetched", by["Full"]["action"] == "enough")
    check("a show nobody watched lately is left out", "Stale" not in by)
    check("a show with no files on the server is left out", "Only seen" not in by)
    check("Jellyfin can't say: skipped", by["No Jellyfin"]["action"] == "skip")
    check("caught up: nothing fetched", by["Caught up"]["action"] == "enough")
    found = auto.find_copies(plan)
    check("only single episodes are asked for", all(p[2] is not None for p in fake.picked), fake.picked)
    check("the found copy is kept for starting", [f["release"] for f in found] == ["Sigma.S02E01.1080p"], found)
    check("no copy yet is reported, not started", by["No copy"]["action"] == "no-copy")
    for f in found:
        f["action"] = "started"
    last = auto.finish(plan)
    check("the last run never holds a magnet link", "magnet" not in repr(last))
    check("the last run lists what started", [s["title"] for s in last["started"]] == ["Sigma"])
    check("public() shows the last run", auto.public()["last_run"]["started"][0]["code"] == "S02E01")


def test_static():
    print("wiring")
    for name in ("config.yaml", "homelab/config.yaml"):
        text = open(os.path.join(ROOT, name), encoding="utf-8").read()
        check(f"{name} has auto_episodes:", "auto_episodes:" in text and "episodes_ahead:" in text)
    server = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
    check("server.py starts the job", "start_auto_episodes" in server and "auto_episodes_once" in server)
    route = open(os.path.join(ROOT, "web/routes/nl_new.py"), encoding="utf-8").read()
    check("the route reuses start_download and has a status page",
          "start_download(line[" in route and "/api/nl/auto-episodes" in route)


def main():
    test_pure()
    test_plan()
    test_static()
    if failures:
        print(f"\n{len(failures)} failed")
        sys.exit(1)
    print("\nall passed")


if __name__ == "__main__":
    main()
