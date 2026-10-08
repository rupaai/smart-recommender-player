"""
Offline tests for the TMDB keywords / reviews / tagline backfill
(tests/test_vibe_backfill.py). No network; throwaway SQLite DB.

    uv run python tests/test_vibe_backfill.py
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import DatabaseManager  # noqa: E402
from utils.backfill_tmdb_vibe import clean_review, parse_details  # noqa: E402

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} — got: {detail}")
        failures.append(label)


def test_parse():
    print("Parse TMDB details")
    movie = {
        "tagline": "  Be kind.  ",
        "keywords": {"keywords": [{"name": "bear"}, {"name": "kindness"}]},
        "reviews": {"results": [
            {"content": "Long plot retelling. " * 40, "author_details": {}},
            {"content": "**Pure joy.** Made me smile. See https://x.y", "author_details": {"rating": 9}},
        ]},
    }
    kw, reviews, tagline = parse_details(movie, "movie")
    check("movie keywords joined", kw == "bear, kindness", kw)
    check("tagline trimmed", tagline == "Be kind.", tagline)
    check("rated review first, markup and links stripped", reviews.startswith('"Pure joy. Made me smile. See"'), reviews)
    check("long review cut at a sentence", '"Long plot retelling.' in reviews and len(reviews) < 400, len(reviews))
    tv = parse_details({"keywords": {"results": [{"name": "sitcom"}]}}, "tv")
    check("TV keywords use 'results'; empty reviews/tagline are None", tv == ("sitcom", None, None), tv)
    check("short review unchanged", clean_review("Lovely.") == "Lovely.")


def test_db(tmp):
    print("DB round trip")
    db = DatabaseManager(os.path.join(tmp, "b.db"))
    with db.get_connection() as conn:
        conn.execute("INSERT INTO media_items (media_type, tmdb_id, title, vote_count) VALUES ('movie', 1, 'A', 10)")
        conn.execute("INSERT INTO media_items (media_type, tmdb_id, title, vote_count) VALUES ('movie', 2, 'B', 900)")
        conn.execute("INSERT INTO media_items (media_type, tmdb_id, title, vote_count, tier2_keywords) "
                     "VALUES ('tv', 3, 'C', 50, 'old')")
    pending = db.get_items_missing_tier2()
    check("most-voted first", [p["title"] for p in pending] == ["B", "C", "A"], pending)
    db.set_tier2_vibe(pending[0]["id"], "k1", '"r1"', "t1")
    db.set_tier2_vibe(pending[1]["id"], None, None, None, checked=-1)
    left = [p["title"] for p in db.get_items_missing_tier2()]
    check("fetched and 404 titles aren't fetched again", left == ["A"], left)
    with db.get_connection() as conn:
        c = conn.execute("SELECT tier2_keywords, tier2_checked FROM media_items WHERE title='C'").fetchone()
    check("a 404 keeps keywords we already had", tuple(c) == ("old", -1), tuple(c))


if __name__ == "__main__":
    tmp = tempfile.mkdtemp()
    try:
        test_parse()
        test_db(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{'ALL PASSED' if not failures else f'{len(failures)} FAILED'}")
    sys.exit(1 if failures else 0)
