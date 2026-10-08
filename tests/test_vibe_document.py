"""
Vibe document tests (tests/test_vibe_document.py)

    uv run python tests/test_vibe_document.py
"""

import json
import os
import shutil
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import DatabaseManager  # noqa: E402
from services.vibe_document import MAX_CHARS, build_document, coverage, dial_words, iter_documents  # noqa: E402

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} — got: {detail}")
        failures.append(label)


TAGS = {
    "vibe": "A gentle, warm caper",
    "feels": ["warm", "cozy"],
    "good_for": ["winding down"],
    "dials": {"energy": 0.1, "weight": 0.5, "humour": 0.8},
    "setting": ["city"], "season_weather": [],
}


def test_build():
    print("Build")
    item = {"title": "Paddington 2", "release_year": 2017, "media_type": "movie",
            "tagline": "Bear with me", "overview": "A bear takes odd jobs.", "genres": "Comedy, Family",
            "tier2_keywords": "kindness, family, heist", "tier2_reviews_summary": '"pure joy"',
            "vibe_tags": json.dumps(TAGS)}
    doc = build_document(item, ["Cried a bit", "  "])
    check("title and year lead", doc.startswith("Paddington 2 (2017)."), doc[:40])
    for part in ("Bear with me.", "A bear takes odd jobs.", "Keywords: kindness, family, heist.",
                 "Mood: A gentle, warm caper.", "Feels: warm, cozy.", "Good for: winding down.",
                 'Viewers: "pure joy"', 'Notes: "Cried a bit"', "Setting: city."):
        check(f"has {part!r}", part in doc, doc)
    check("mood comes before reviews", doc.index("Mood:") < doc.index("Viewers:"))
    check("only clear dials become words", dial_words(TAGS["dials"]) == ["calm, gentle", "laugh-out-loud"],
          dial_words(TAGS["dials"]))
    bare = build_document({"title": "X", "media_type": "tv", "overview": "Plot", "vibe_tags": "{bad"})
    check("bare series still gets a document", bare == "X, series. Plot.", bare)
    long = build_document({"title": "Y", "overview": "word " * 400, "tier2_keywords": ", ".join(["k"] * 50),
                           "tier2_reviews_summary": "r " * 900, "vibe_tags": TAGS})
    check("clipped to the embedder budget", len(long) <= MAX_CHARS + 1, len(long))
    check("mood survives clipping", "Mood:" in long)


def test_db(tmp):
    print("From the database")
    path = os.path.join(tmp, "v.db")
    db = DatabaseManager(path)
    with db.get_connection() as conn:
        conn.execute("INSERT INTO media_items (media_type, tmdb_id, title, overview, vibe_tags) "
                     "VALUES ('movie', 1, 'A', 'Plot A', ?)", (json.dumps(TAGS),))
        conn.execute("INSERT INTO media_items (media_type, tmdb_id, title, overview) VALUES ('tv', 2, 'B', 'Plot B')")
        conn.execute("INSERT INTO watch_history (media_item_id, profile_id, user_rating, status, notes) "
                     "VALUES (1, 1, 9, 'seen', 'So good')")
    conn = sqlite3.connect(path)
    docs = dict(iter_documents(conn))
    check("one document per title", set(docs) == {1, 2}, docs)
    check("notes joined in", 'Notes: "So good"' in docs[1], docs[1])
    check("media type filter", list(dict(iter_documents(conn, media_type="tv"))) == [2])
    cov = coverage(conn)
    check("coverage counts mood tags", cov["titles"] == 2 and cov["mood_tags"].startswith("1 "), cov)
    conn.close()


if __name__ == "__main__":
    tmp = tempfile.mkdtemp()
    try:
        test_build()
        test_db(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{'ALL PASSED' if not failures else f'{len(failures)} FAILED'}")
    sys.exit(1 if failures else 0)
