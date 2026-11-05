"""
Card scores: IMDb and Rotten Tomatoes first, TMDB only until IMDb is known, our own score a side note.

    uv run python tests/test_card_scores.py
"""

import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from database import DatabaseManager  # noqa: E402
from services.title_scores import card_scores  # noqa: E402

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} {detail}")
        failures.append(label)


print("card_scores")
check("IMDb and RT", card_scores({"rating": 7.1, "imdb_rating": 7.84, "rt_score": 91}) == {"imdb": 7.8, "rt": 91, "tmdb": None})
check("TMDB until IMDb is known", card_scores({"rating": 7.12}) == {"imdb": None, "rt": None, "tmdb": 7.1})
check("RT 0 is kept", card_scores({"imdb_rating": 5.0, "rt_score": 0})["rt"] == 0)
check("nothing known", card_scores({}) == {"imdb": None, "rt": None, "tmdb": None})

print("the rows cards are built from carry the scores")
path = os.path.join(tempfile.mkdtemp(), "c.db")
db = DatabaseManager(path)
db.upsert_catalogue_items([{"media_type": "movie", "tmdb_id": 1, "title": "Alpha", "rating": 7.1, "vote_count": 900}])
with db.get_connection() as conn:
    conn.execute("UPDATE media_items SET imdb_rating = 7.8, rt_score = 91")
    item_id = conn.execute("SELECT id FROM media_items").fetchone()[0]
row = db.get_items_by_ids([item_id])[item_id]
check("Wishmatch cards (get_items_by_ids)", row["imdb_rating"] == 7.8 and row["rt_score"] == 91, row)
print("browser side (node)")
node = shutil.which("node")
if node:
    out = subprocess.run([node, os.path.join(ROOT, "tests", "js", "card_scores_check.js")], capture_output=True, text=True,
                         timeout=60)
    check("cards and title pages show IMDb and RT first", out.returncode == 0, (out.stdout + out.stderr)[-800:])
else:
    print("  (node not installed: skipped)")

if failures:
    print(f"\n{len(failures)} check(s) failed")
    sys.exit(1)
print("\nall passed")
