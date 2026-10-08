"""
Offline tests for the LLM mood tagger (tests/test_mood_tag.py)

A fake Batch API client stands in for Anthropic: no network, no API key,
no spend. Throwaway SQLite DB in a tempdir.

    uv run python tests/test_mood_tag.py
"""

import json
import os
import shutil
import sqlite3
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import DatabaseManager  # noqa: E402
from services.vibe_vocab import CONTENT_FLAGS, DIAL_NAMES, FEELS  # noqa: E402
import utils.mood_tag as mt  # noqa: E402

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} — got: {detail}")
        failures.append(label)


def good_answer(**over):
    ans = {
        "known": "well",
        "dials": {d: 5 for d in DIAL_NAMES},
        "flags": ["animal_harm"],
        "flags_unsure": ["animal_harm", "suicide"],
        "feels": ["warm", "cozy", "warm", "not-a-tag"],
        "good_for": ["winding down", "date night"],
        "setting": ["city"],
        "season_weather": [],
        "time_of_day": "mostly-day",
        "palette": ["pastel"],
        "story_era": "2010s",
        "vibe": "  A gentle,   warm caper. ",
    }
    ans.update(over)
    return ans


class FakeBatches:
    def __init__(self):
        self.created = []
        self.answers = {}

    def create(self, requests):
        bid = f"msgbatch_{len(self.created)}"
        self.created.append((bid, requests))
        return SimpleNamespace(id=bid)

    def retrieve(self, bid):
        counts = SimpleNamespace(succeeded=1, errored=0, expired=0, canceled=0, processing=0)
        return SimpleNamespace(processing_status="ended", request_counts=counts)

    def results(self, bid):
        reqs = dict(self.created)[bid]
        for req in reqs:
            cid = req["custom_id"]
            kind, payload = self.answers.get(cid, ("ok", good_answer()))
            if kind == "errored":
                yield SimpleNamespace(custom_id=cid, result=SimpleNamespace(type="errored"))
                continue
            usage = SimpleNamespace(input_tokens=400, cache_creation_input_tokens=0,
                                    cache_read_input_tokens=1700, output_tokens=250)
            msg = SimpleNamespace(
                usage=usage,
                stop_reason="max_tokens" if kind == "truncated" else "end_turn",
                content=[SimpleNamespace(type="text", text=payload if isinstance(payload, str) else json.dumps(payload))],
            )
            yield SimpleNamespace(custom_id=cid, result=SimpleNamespace(type="succeeded", message=msg))


def seed(db_path):
    db = DatabaseManager(db_path)
    with db.get_connection() as conn:
        for i in range(12):
            conn.execute(
                "INSERT INTO media_items (media_type, tmdb_id, title, release_year, overview, genres, vote_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("tv" if i >= 10 else "movie", 100 + i, f"Film {i}", 2000 + i, f"Plot {i}",
                 "Comedy" if i % 2 else "Drama", 5000 - i * 100),
            )
        conn.execute("INSERT INTO media_items (media_type, tmdb_id, title, overview) VALUES ('movie', 999, 'No plot', '')")
        conn.execute("INSERT INTO watch_history (media_item_id, user_rating, profile_id, status) VALUES (5, 9, 1, 'seen')")
    return db


def test_schema_and_prompt():
    print("Schema and prompt")
    schema = mt.output_schema()
    check("every dial is required", set(schema["properties"]["dials"]["required"]) == set(DIAL_NAMES))
    check("feels enum is the closed list of 37", len(schema["properties"]["feels"]["items"]["enum"]) == 37)
    check("schema uses no unsupported numeric constraints", "minimum" not in json.dumps(schema))
    prompt = mt.build_system_prompt()
    check("rubric names every dial", all(f"- {d}:" in prompt for d in DIAL_NAMES))
    check("prompt is long enough to cache (>512 tokens)", len(prompt) / mt.CHARS_PER_TOKEN > 600)
    params = mt.build_params({"id": 1, "title": "X", "overview": "y" * 5000}, prompt, schema)
    check("Haiku 5.5, thinking off, low effort", params["model"] == "claude-haiku-5-5"
          and params["thinking"] == {"type": "disabled"} and params["output_config"]["effort"] == "low")
    check("no sampling params (Haiku 5.5 rejects them)", not {"temperature", "top_p", "top_k"} & set(params))
    check("system prompt is cached", params["system"][0]["cache_control"] == {"type": "ephemeral"})
    check("long overview clipped", len(params["messages"][0]["content"]) < 1000)


def test_normalise():
    print("Normalise")
    t = mt.normalise_tags(good_answer(dials={**{d: 5 for d in DIAL_NAMES}, "energy": 14, "gore": -3}))
    check("dials scaled to 0-1 and clamped", t["dials"]["energy"] == 1.0 and t["dials"]["gore"] == 0.0
          and t["dials"]["tension"] == 0.5, t["dials"])
    check("unknown and duplicate tags dropped", t["feels"] == ["warm", "cozy"], t["feels"])
    check("a flag can't be both sure and unsure", t["flags"] == ["animal_harm"] and t["flags_unsure"] == ["suicide"])
    check("vibe whitespace collapsed", t["vibe"] == "A gentle, warm caper.", t["vibe"])
    check("bad enum falls back", mt.normalise_tags(good_answer(time_of_day="dusk"))["time_of_day"] == "mixed")


def test_select_and_collect(tmp):
    print("Select, submit, collect")
    path = os.path.join(tmp, "mt.db")
    seed(path)
    conn = sqlite3.connect(path)
    pilot = mt.select_pilot(conn, 8)
    ids = [p["id"] for p in pilot]
    check("pilot has the requested size", len(pilot) == 8, len(pilot))
    check("your rated film comes first", ids[0] == 5, ids)
    check("titles without an overview are skipped", all(p["overview"] for p in pilot))
    check("series are included", any(p["media_type"] == "tv" for p in pilot))

    fake = FakeBatches()
    mt.client = lambda: SimpleNamespace(messages=SimpleNamespace(batches=fake))
    fake.answers = {
        f"item-{ids[1]}": ("errored", None),
        f"item-{ids[2]}": ("truncated", good_answer()),
        f"item-{ids[3]}": ("ok", "{not json"),
    }
    bids = mt.submit(conn, pilot, "pilot")
    check("one batch row recorded", conn.execute("SELECT COUNT(*) FROM mood_tag_batches").fetchone()[0] == 1)
    again = mt.select_pilot(conn, 8)
    check("items in a pending batch aren't picked twice", not set(ids) & {a["id"] for a in again})

    tagged = mt.collect(conn, bids)
    check("good results stored, failures left untagged", len(tagged) == 5, tagged)
    row = conn.execute("SELECT status, succeeded, failed, cost_usd, usage_json FROM mood_tag_batches").fetchone()
    check("batch marked collected with counts", row[:3] == ("collected", 5, 3), row)
    # 7 responses had usage (errored has none): 7*(400*.05 + 1700*.005 + 250*.25)/1e6
    expected = 7 * (400 * 0.05 + 1700 * 0.005 + 250 * 0.25) / 1e6
    check("real cost computed from usage", abs(row[3] - expected) < 1e-9, row[3])
    stored = json.loads(conn.execute("SELECT vibe_tags FROM media_items WHERE id = ?", (ids[0],)).fetchone()[0])
    check("stored tags are normalised", stored["dials"]["energy"] == 0.5 and stored["model"] == "claude-haiku-5-5")
    left = {r["id"] for r in mt.select_remaining(conn)}
    check("failed items are picked up by the next run", {ids[1], ids[2], ids[3]} <= left, left)
    check("measured cost per item", abs(mt.measured_cost_per_item(conn) - row[3] / 8) < 1e-12)

    report = os.path.join(tmp, "reports", "pilot.md")
    mt.write_report(conn, tagged, "Pilot", report)
    text = open(report).read()
    other = conn.execute("SELECT title FROM media_items WHERE id = ?", (tagged[-1],)).fetchone()[0]
    check("report lists your rated film first", text.index("Film 5") < text.index(other))
    conn.close()


if __name__ == "__main__":
    tmp = tempfile.mkdtemp()
    try:
        test_schema_and_prompt()
        test_normalise()
        test_select_and_collect(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{'ALL PASSED' if not failures else f'{len(failures)} FAILED'}")
    sys.exit(1 if failures else 0)
