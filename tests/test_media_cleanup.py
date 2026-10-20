"""
Offline tests for the watched cleanup (services/media_cleanup.py)

    uv run python tests/test_media_cleanup.py
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.media_cleanup import MediaCleanup, delete_files, parse_date, pick, settings  # noqa: E402

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} {detail}")
        failures.append(label)


NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


def jf_date(days_ago):
    return (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S.1234567Z")


def item(item_id, days_ago, path="/media/Movies/A (2020)/A (2020).mkv", favorite=False, kind="Movie", series=None,
         played=True):
    return {"Id": item_id, "Type": kind, "Name": item_id, "Path": path, "SeriesId": series, "SeriesName": "Show",
            "ParentIndexNumber": 1, "IndexNumber": 2,
            "UserData": {"Played": played, "IsFavorite": favorite,
                         "LastPlayedDate": jf_date(days_ago) if days_ago is not None else None}}


def ids(chosen):
    return sorted(c["id"] for c in chosen)


print("dates and settings")
check("Jellyfin's 7-digit fractions are read", parse_date("2026-10-13T20:00:00.1234567Z") is not None)
check("a missing date is None", parse_date(None) is None and parse_date("nonsense") is None)
check("off unless switched on, dry run by default", settings({}) == {"enabled": False, "days": 7.0, "dry_run": True})
check("days below 1 are raised to 1", settings({"media_cleanup": {"days_after_watched": 0}})["days"] == 1.0)

print("choosing")
check("watched by both, 8 days ago: deleted", ids(pick([[item("a", 8)], [item("a", 9)]], set(), NOW, 7)) == ["a"])
check("6 days ago: kept", pick([[item("a", 6)], [item("a", 6)]], set(), NOW, 7) == [])
check("counted from the later of the two dates",
      pick([[item("a", 30)], [item("a", 3)]], set(), NOW, 7) == [])
check("watched by only one person: kept", pick([[item("a", 30)], [item("b", 30)]], set(), NOW, 7) == [])
check("marked unplayed for one person: kept", pick([[item("a", 30)], [item("a", 30, played=False)]], set(), NOW, 7) == [])
check("favorite for either person: kept", pick([[item("a", 30)], [item("a", 30, favorite=True)]], set(), NOW, 7) == [])
check("episode of a favorite show: kept",
      pick([[item("e", 30, kind="Episode", series="s1")], [item("e", 30, kind="Episode", series="s1")]], {"s1"}, NOW, 7) == [])
ep = pick([[item("e", 30, kind="Episode", series="s1")]], set(), NOW, 7)
check("episodes go one by one, named Show S01E02", ids(ep) == ["e"] and ep[0]["name"] == "Show S01E02", ep)
check("no watched date: kept", pick([[item("a", None)]], set(), NOW, 7) == [])
check("nobody configured: nothing", pick([], set(), NOW, 7) == [])

print("deleting files")
root = tempfile.mkdtemp()
movies, shows = os.path.join(root, "Movies"), os.path.join(root, "Shows")
film_dir = os.path.join(movies, "A (2020)")
os.makedirs(film_dir)
for name in ("A (2020).mkv", "A (2020).en.srt", "A (2020) - copy.mkv"):
    open(os.path.join(film_dir, name), "w").close()
removed = delete_files(os.path.join(film_dir, "A (2020).mkv"), [movies, shows])
check("video and its subtitle go", sorted(os.path.basename(p) for p in removed) == ["A (2020).en.srt", "A (2020).mkv"], removed)
check("another version in the same folder stays", os.path.exists(os.path.join(film_dir, "A (2020) - copy.mkv")))
delete_files(os.path.join(film_dir, "A (2020) - copy.mkv"), [movies, shows])
check("the empty movie folder goes, Movies stays", not os.path.exists(film_dir) and os.path.isdir(movies))

season = os.path.join(shows, "Show (2019)", "Season 01")
os.makedirs(season)
open(os.path.join(season, "Show - S01E01.mkv"), "w").close()
open(os.path.join(season, "Show - S01E02.mkv"), "w").close()
delete_files(os.path.join(season, "Show - S01E01.mkv"), [movies, shows])
check("one episode goes, the season stays", os.path.exists(os.path.join(season, "Show - S01E02.mkv")))
delete_files(os.path.join(season, "Show - S01E02.mkv"), [movies, shows])
check("last episode: season and show folders go", not os.path.exists(os.path.join(shows, "Show (2019)")) and os.path.isdir(shows))

outside = os.path.join(root, "downloads", "x.mkv")
os.makedirs(os.path.dirname(outside))
open(outside, "w").close()
check("outside Movies and Shows: refused", delete_files(outside, [movies, shows]) == [] and os.path.exists(outside))
check("a path that climbs out with ..: refused",
      delete_files(os.path.join(movies, "..", "downloads", "x.mkv"), [movies, shows]) == [] and os.path.exists(outside))
link = os.path.join(movies, "link.mkv")
os.symlink(outside, link)
check("a symlink to elsewhere: refused", delete_files(link, [movies, shows]) == [] and os.path.exists(outside))

print("a full run")


class FakePlayback:
    def __init__(self, played):
        self.played = played

    def resolve_user(self, raw, label=""):
        return raw

    def _user_id(self):
        return "default"

    def _request(self, method, path, params=None):
        uid = path.split("/")[2]
        if params["Filters"] == "IsFavorite":
            return {"Items": []}
        return {"Items": self.played.get(uid, [])}


class FakeDB:
    def __init__(self):
        self.removed = []

    def remove_source_files(self, paths):
        self.removed.extend(paths)


def make_film():
    d = os.path.join(movies, "B (2021)")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "B (2021).mkv")
    open(path, "w").close()
    return path


film = make_film()
played = {"u1": [item("b", 10, path=film)], "u2": [item("b", 9, path=film)]}
rescans = []
db = FakeDB()
cfg = {"media_cleanup": {"enabled": True, "days_after_watched": 7, "dry_run": True}}
job = MediaCleanup(cfg, FakePlayback(played), lambda: ["u1", "u2"], db, [movies, shows], lambda: rescans.append(1))
rep = job.run_once(NOW)
check("dry run lists it and keeps the file", len(rep["items"]) == 1 and not rep["items"][0]["deleted"] and os.path.exists(film), rep)
check("dry run: no rescan, nothing removed from the catalogue", rescans == [] and db.removed == [])
cfg["media_cleanup"]["dry_run"] = False
rep = job.run_once(NOW)
check("real run deletes it", rep["items"][0]["deleted"] and not os.path.exists(film), rep)
check("real run rescans Jellyfin and drops the catalogue row", rescans == [1] and db.removed == [film], (rescans, db.removed))
check("the admin page sees the last run", job.public()["last"] is rep)

film = make_film()
job = MediaCleanup(cfg, FakePlayback({"u1": [item("b", 10, path=film)]}), lambda: ["u1", "u2"], db, [movies, shows],
                   lambda: None)
check("second person never watched: kept", job.run_once(NOW)["items"] == [] and os.path.exists(film))

job = MediaCleanup(cfg, FakePlayback({"default": [item("b", 10, path=film)]}), lambda: [], db, [movies, shows],
                   lambda: None)
check("no people configured: the default viewer decides", len(job.run_once(NOW)["items"]) == 1 and not os.path.exists(film))

film = make_film()
elsewhere = {"u1": [item("c", 10, path="/somewhere/else.mkv")]}
job = MediaCleanup(cfg, FakePlayback(elsewhere), lambda: ["u1"], db, [movies, shows], lambda: None)
check("Jellyfin path outside Movies/Shows: skipped", job.run_once(NOW)["items"] == [])


class Down(FakePlayback):
    def _request(self, *a, **k):
        raise RuntimeError("Jellyfin could not be reached")


job = MediaCleanup(cfg, Down({}), lambda: ["u1"], db, [movies, shows], lambda: None)
rep = job.run_once(NOW)
check("Jellyfin down: no crash, error reported, nothing deleted", rep["error"] and os.path.exists(film), rep)
job = MediaCleanup(cfg, FakePlayback(played), lambda: ["u1"], db, [os.path.join(root, "missing"), shows], lambda: None)
check("Movies folder missing: nothing runs", job.run_once(NOW)["error"])

if failures:
    print(f"\n{len(failures)} failed")
    sys.exit(1)
print("\nall passed")
