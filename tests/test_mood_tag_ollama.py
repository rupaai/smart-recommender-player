"""
Ollama mood tagging tests (tests/test_mood_tag_ollama.py)

With a stand-in for Ollama: new titles get tags in the Haiku format with the model's
name on them, bad answers leave a title untagged, the time limit stops a run, and
the bake-off compares models with Haiku's tags without writing to the catalogue.

    uv run python tests/test_mood_tag_ollama.py
"""

import json
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import DatabaseManager  # noqa: E402
from services.vibe_vocab import DIAL_NAMES  # noqa: E402
from utils import mood_tag_ollama as m  # noqa: E402

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} — got: {detail}")
        failures.append(label)


def answer(humour=5):
    return json.dumps({"known": "well", "dials": {d: (humour if d == "humour" else 5) for d in DIAL_NAMES},
                       "flags": [], "flags_unsure": [], "feels": ["witty", "warm"], "good_for": ["date night"],
                       "setting": ["city"], "season_weather": [], "time_of_day": "mixed", "palette": ["saturated"],
                       "story_era": "2010s", "vibe": "A warm, witty comedy."})


class FakeOllama:
    def __init__(self, model, replies):
        self.model = model
        self.replies = list(replies)

    def status(self):
        return {"has_model": True}

    def generate(self, prompt, schema=None, system=""):
        return self.replies.pop(0) if self.replies else answer()


def catalogue():
    rows = []
    for i in range(30):
        kind = "movie" if i < 18 else "tv"
        rows.append({"media_type": kind, "tmdb_id": 90000 + i, "title": f"Title {i}", "overview": f"About {i}.",
                     "genres": "Comedy", "rating": 7.0, "vote_count": 1000 - i})
    path = tempfile.mktemp(suffix=".db")
    db = DatabaseManager(path)
    db.upsert_catalogue_items(rows)
    haiku = m.normalise_tags(json.loads(answer(humour=8)))   # stored like Haiku's: dials 0..1
    with db.get_connection() as conn:
        for i in range(20):    # the first 20 have Haiku tags; half "well", half "somewhat"
            haiku["known"] = "well" if i % 2 else "somewhat"
            conn.execute("UPDATE media_items SET vibe_tags = ? WHERE tmdb_id = ?", (json.dumps(haiku), 90000 + i))
    return path


def main():
    path = catalogue()
    conn = sqlite3.connect(path)

    print("\n[1] Tagging new titles")
    m.client = lambda model, timeout=300.0: FakeOllama(model, ["not json", answer(), answer()])
    done = m.tag_new(conn, "fake:1b")
    rows = conn.execute("SELECT vibe_tags FROM media_items WHERE tmdb_id >= 90020 ORDER BY tmdb_id").fetchall()
    tags = [json.loads(r[0]) for r in rows if r[0]]
    check("untagged titles get tags; a bad answer leaves one for next time", done == 9 and len(tags) == 9, (done, len(tags)))
    check("tags carry the model's name and Haiku's format",
          all(t["model"] == "fake:1b" and t["dials"]["humour"] == 0.5 and t["feels"] for t in tags), tags[:1])
    check("titles Haiku tagged are left alone",
          json.loads(conn.execute("SELECT vibe_tags FROM media_items WHERE tmdb_id = 90000").fetchone()[0]).get("model")
          != "fake:1b")
    conn.execute("UPDATE media_items SET vibe_tags = NULL WHERE tmdb_id >= 90020")
    check("the time limit stops a run", m.tag_new(conn, "fake:1b", max_minutes=-1) == 0)

    print("\n[2] Bake-off")
    before = conn.execute("SELECT COUNT(*) FROM media_items WHERE vibe_tags IS NOT NULL").fetchone()[0]
    m.client = lambda model, timeout=300.0: FakeOllama(model, [answer(humour=8 if model == "close" else 1)] * 10)
    out = tempfile.mkdtemp()
    report = m.bakeoff(conn, ["far", "close"], 10, out_dir=out)
    check("the model closest to Haiku wins", report["winner"] == "close", report["results"])
    check("dial error is 0 for an exact match", report["results"]["close"]["dial_error"] == 0.0, report["results"])
    check("the report is written", os.path.exists(os.path.join(out, "mood_tag_bakeoff.json")))
    after = conn.execute("SELECT COUNT(*) FROM media_items WHERE vibe_tags IS NOT NULL").fetchone()[0]
    check("the bake-off writes nothing to the catalogue", before == after, (before, after))
    s = m.sample(conn, 10)
    check("the sample mixes films and shows", {r["media_type"] for r in s} == {"movie", "tv"}, [r["media_type"] for r in s])

    if failures:
        print(f"\n{len(failures)} check(s) failed")
        sys.exit(1)
    print("\nall checks passed")


if __name__ == "__main__":
    main()
