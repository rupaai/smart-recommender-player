"""
Offline tests for the nightly gate, the settings reader and the catalogue quality floor
(ops/mac/pipeline.py, ops/mac/cc.py)

    uv run python tests/test_pipeline.py
"""

import json
import os
import shutil
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import DatabaseManager  # noqa: E402
from ops.mac import cc, pipeline  # noqa: E402

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} — got: {detail}")
        failures.append(label)


print("gate")
cur = {"taste_spearman": 0.50, "taste_auc": 0.70}
check("same numbers pass", pipeline.gate({"taste_spearman": 0.50, "taste_auc": 0.70}, cur, 1.0) == [])
check("better numbers pass", pipeline.gate({"taste_spearman": 0.6, "taste_auc": 0.8}, cur, 1.0) == [])
check("a 0.8% drop is allowed", pipeline.gate({"taste_spearman": 0.496}, cur, 1.0) == [])
r = pipeline.gate({"taste_spearman": 0.40, "taste_auc": 0.70}, cur, 1.0)
check("a 20% drop is held back, and says which metric", len(r) == 1 and "taste_spearman" in r[0], r)
check("first model (nothing on the server to compare) passes", pipeline.gate({"taste_spearman": 0.1}, {}, 1.0) == [])
check("a metric the server never had is ignored", pipeline.gate({"taste_spearman": 0.5, "taste_auc": 0.1}, {"taste_spearman": 0.5}, 1.0) == [])
check("an old server number (TIGER) is ignored", pipeline.gate({"taste_auc": 0.7}, {"tiger_hit_rate": 0.5, "taste_auc": 0.7}, 1.0) == [])
check("no numbers to compare (too few ratings) is let through", pipeline.gate({"info": {}}, cur, 1.0) == [])
check("a bigger allowed drop lets it through", pipeline.gate({"taste_spearman": 0.40}, cur, 25.0) == [])

print("settings")
check("reads config.yaml", pipeline.setting("nightly.min_vote_count", None) == "200")
check("falls back to the default", pipeline.setting("nightly.nope", "7") == "7")
check("mood tagging ships off", pipeline.setting("nightly.mood_tag_limit", None) == "0")

print("quality floor on new items")
with tempfile.TemporaryDirectory() as tmp:
    before = os.path.join(tmp, "before.db")
    DatabaseManager(before)
    c = sqlite3.connect(before)
    c.execute("INSERT INTO media_items (media_type, tmdb_id, title, rating, vote_count) VALUES ('movie', 1, 'Known', 5.0, 10)")
    c.commit()
    c.close()
    after = os.path.join(tmp, "after.db")
    shutil.copy(before, after)
    c = sqlite3.connect(after)
    c.execute("UPDATE media_items SET rating = 5.5 WHERE tmdb_id = 1")
    for tid, title, rating, votes in ((2, "Good", 7.5, 900), (3, "Few votes", 8.0, 50), (4, "Poor", 4.0, 5000)):
        c.execute("INSERT INTO media_items (media_type, tmdb_id, title, rating, vote_count) VALUES ('movie', ?, ?, ?, ?)",
                  (tid, title, rating, votes))
    c.commit()
    c.close()
    out = os.path.join(tmp, "p.jsonl")
    cc.diff_catalogue(before, after, out, min_votes=200, min_rating=6.0)
    rows = [json.loads(line) for line in open(out)]
    ids = sorted(r["tmdb_id"] for r in rows)
    check("only the good new title gets in", ids == [1, 2], ids)
    check("an existing title below the floor still gets its update",
          [r for r in rows if r["tmdb_id"] == 1][0]["fields"] == {"rating": 5.5})

if failures:
    print(f"\n{len(failures)} failed")
    sys.exit(1)
print("\nall pipeline tests passed")
