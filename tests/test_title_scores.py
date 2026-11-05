"""
Offline tests for the daily IMDb / Rotten Tomatoes lookup (services/title_scores.py). No network.

    uv run python tests/test_title_scores.py
"""

import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402

from database import DatabaseManager  # noqa: E402
from services.title_scores import TitleScores, parse_omdb, runs_here, settings  # noqa: E402

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} {detail}")
        failures.append(label)


class Resp:
    def __init__(self, status, data):
        self.status_code, self._data = status, data

    def json(self):
        return self._data


class FakeSession:
    """TMDB: tmdb_id -> imdb id (None = 404). OMDb: imdb id -> answer. `omdb_stop_after` = limit message after N."""

    def __init__(self, tmdb, omdb, omdb_stop_after=None, tmdb_down=False):
        self.tmdb, self.omdb, self.stop_after, self.tmdb_down = tmdb, omdb, omdb_stop_after, tmdb_down
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        if "themoviedb" in url:
            if self.tmdb_down:
                raise requests.ConnectionError("down https://x?api_key=SECRET")
            tmdb_id = int(url.split("/")[-2])
            imdb = self.tmdb.get(tmdb_id)
            return Resp(404, {}) if imdb is None else Resp(200, {"imdb_id": imdb})
        omdb_calls = sum(1 for u, _ in self.calls if "omdbapi" in u)
        if self.stop_after is not None and omdb_calls > self.stop_after:
            return Resp(401, {"Response": "False", "Error": "Request limit reached!"})
        return Resp(200, self.omdb.get(params["i"], {"Response": "False", "Error": "Incorrect IMDb ID."}))


NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)


def make_db():
    path = os.path.join(tempfile.mkdtemp(), "t.db")
    DatabaseManager(path)
    rows = [  # (media_type, tmdb_id, title, votes)
        ("movie", 1, "Small film", 50),
        ("movie", 2, "Big film", 30000),
        ("tv", 3, "Big show", 90000),
        ("movie", 4, "No IMDb film", 9000),
        ("movie", 5, "Mid film", 5000),
    ]
    with sqlite3.connect(path) as c:
        c.executemany("INSERT INTO media_items (media_type, tmdb_id, title, vote_count) VALUES (?,?,?,?)", rows)
    return path


def cols(path, title):
    with sqlite3.connect(path) as c:
        c.row_factory = sqlite3.Row
        return dict(c.execute("SELECT * FROM media_items WHERE title = ?", (title,)).fetchone())


CFG = {"title_scores": {"enabled": True, "omdb_per_day": 1000, "imdb_ids_per_day": 2000, "pause_seconds": 0}}
TMDB = {1: "tt0000001", 2: "tt0000002", 3: "tt0000003", 4: None, 5: "tt0000005"}
OMDB = {
    "tt0000002": {"Response": "True", "imdbRating": "8.1", "imdbVotes": "1,234,567", "Metascore": "77",
                  "Ratings": [{"Source": "Internet Movie Database", "Value": "8.1/10"},
                              {"Source": "Rotten Tomatoes", "Value": "93%"}]},
    "tt0000003": {"Response": "True", "imdbRating": "9.0", "imdbVotes": "500,000", "Metascore": "N/A",
                  "Ratings": []},
    "tt0000005": {"Response": "True", "imdbRating": "N/A", "imdbVotes": "N/A", "Metascore": "N/A"},
}

print("settings and where it runs")
s = settings({})
check("off by default, prod only, free key's limit", not s["enabled"] and s["run_in"] == ["prod"]
      and s["omdb_per_day"] == 1000 and s["films_first"])
on = settings(CFG)
check("runs in prod", runs_here(on, "prod"))
check("never in dev (it would share the key's daily limit)", not runs_here(on, "dev"))
check("outside Homelab, enabled decides", runs_here(on, "") and not runs_here(s, ""))

print("reading OMDb's answer")
p = parse_omdb(OMDB["tt0000002"])
check("IMDb score, votes, RT and Metacritic read",
      p == {"imdb_rating": 8.1, "imdb_votes": 1234567, "rt_score": 93, "metacritic": 77}, p)
check("N/A and missing become None", parse_omdb(OMDB["tt0000005"]) == {
    "imdb_rating": None, "imdb_votes": None, "rt_score": None, "metacritic": None})
check("nonsense values are dropped", parse_omdb({"imdbRating": "42", "Ratings": [
    {"Source": "Rotten Tomatoes", "Value": "300%"}]})["rt_score"] is None)

print("a run")
os.environ["TMDB_API_KEY"], os.environ["OMDB_API_KEY"] = "tmdb-test", "omdb-test"
path = make_db()
session = FakeSession(TMDB, OMDB)
job = TitleScores(CFG, path, session=session, now=lambda: NOW, sleep=lambda s: None)
order = [r["id"] for r in job.needing_imdb_id(10)]
titles = [cols(path, t)["id"] for t in ("Big film", "No IMDb film", "Mid film", "Small film", "Big show")]
check("films first, then most votes", order == titles, order)
report = job.run_once()
check("every title asked TMDB once", report["imdb_ids"] == 5, report)
check("IMDb number stored", cols(path, "Big film")["imdb_id"] == "tt0000002")
nf = cols(path, "No IMDb film")
check("no IMDb number: marked asked, never sent to OMDb", nf["imdb_id"] == "" and nf["imdb_id_checked"] == 1
      and nf["scores_checked_at"] is None)
big = cols(path, "Big film")
check("scores stored", big["imdb_rating"] == 8.1 and big["imdb_votes"] == 1234567 and big["rt_score"] == 93
      and big["metacritic"] == 77 and big["scores_checked_at"] == NOW.isoformat(), big)
check("a show gets its IMDb score without RT", cols(path, "Big show")["imdb_rating"] == 9.0
      and cols(path, "Big show")["rt_score"] is None)
check("unknown to OMDb: looked up, no scores", cols(path, "Small film")["scores_checked_at"] is not None
      and cols(path, "Small film")["imdb_rating"] is None)
check("lookups counted for today", job.used_today() == {"tmdb": 5, "omdb": 4}, job.used_today())
check("progress", job.progress()["with_rt_score"] == 1 and job.progress()["with_imdb_id"] == 4, job.progress())
before = len(session.calls)
job.run_once()
check("a second run the same day asks nothing again", len(session.calls) == before)

print("the daily limit holds across restarts")
path = make_db()
cfg = {"title_scores": dict(CFG["title_scores"], omdb_per_day=2)}
session = FakeSession(TMDB, OMDB)
TitleScores(cfg, path, session=session, now=lambda: NOW, sleep=lambda s: None).run_once()
restarted = TitleScores(cfg, path, session=session, now=lambda: NOW, sleep=lambda s: None)
restarted.run_once()
check("only 2 OMDb lookups today, even after a restart",
      sum(1 for u, _ in session.calls if "omdbapi" in u) == 2)
tomorrow = TitleScores(cfg, path, session=session, now=lambda: NOW + timedelta(days=1), sleep=lambda s: None)
tomorrow.run_once()
check("tomorrow the next 2", sum(1 for u, _ in session.calls if "omdbapi" in u) == 4)

print("OMDb says stop")
path = make_db()
session = FakeSession(TMDB, OMDB, omdb_stop_after=1)
report = TitleScores(CFG, path, session=session, now=lambda: NOW, sleep=lambda s: None).run_once()
check("limit reached stops the run", report["scored"] == 1 and report["stopped"] and "limit" in report["stopped"][0].lower(),
      report)
check("the refused title is not marked, so it is asked again", cols(path, "Mid film")["scores_checked_at"] is None)

print("TMDB down")
path = make_db()
with sqlite3.connect(path) as c:  # one title already has its number
    c.execute("UPDATE media_items SET imdb_id = 'tt0000002', imdb_id_checked = 1 WHERE tmdb_id = 2")
session = FakeSession(TMDB, OMDB, tmdb_down=True)
report = TitleScores(CFG, path, session=session, now=lambda: NOW, sleep=lambda s: None).run_once()
check("OMDb still scores the titles that have a number", report["scored"] == 1 and report["imdb_ids"] == 0, report)
check("the key is not in the message", not any("SECRET" in s for s in report["stopped"]), report)

print("no OMDb key")
del os.environ["OMDB_API_KEY"]
path = make_db()
session = FakeSession(TMDB, OMDB)
report = TitleScores(CFG, path, session=session, now=lambda: NOW, sleep=lambda s: None).run_once()
check("only IMDb numbers, OMDb never called", report["imdb_ids"] == 5 and report["scored"] == 0
      and not any("omdbapi" in u for u, _ in session.calls) and report["missing_keys"] == ["OMDB_API_KEY"])
os.environ["OMDB_API_KEY"] = "omdb-test"

print("refresh old scores")
path = make_db()
job = TitleScores(CFG, path, session=FakeSession(TMDB, OMDB), now=lambda: NOW, sleep=lambda s: None)
job.run_once()
later = TitleScores(CFG, path, session=FakeSession(TMDB, OMDB), now=lambda: NOW + timedelta(days=200),
                    sleep=lambda s: None)
check("after refresh_after_days the old scores are asked again", len(later.needing_scores(10)) == 4)
soon = TitleScores(CFG, path, session=FakeSession(TMDB, OMDB), now=lambda: NOW + timedelta(days=10),
                   sleep=lambda s: None)
check("but not before", soon.needing_scores(10) == [])

if failures:
    print(f"\n{len(failures)} failed")
    sys.exit(1)
print("\nall passed")
