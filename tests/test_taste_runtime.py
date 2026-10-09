"""
Offline tests for the taste model's database side (tests/test_taste_runtime.py)

Throwaway SQLite DB in a tempdir: gate, off switch, ranking effect, cache,
impression logging.

    uv run python tests/test_taste_runtime.py
"""

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import DatabaseManager  # noqa: E402
from services import taste_model as tm  # noqa: E402
from services.taste_runtime import TasteRuntime  # noqa: E402

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} — got: {detail}")
        failures.append(label)


def make_db(tmp, rated=14, name="t.db"):
    db = DatabaseManager(os.path.join(tmp, name))
    with db.get_connection() as conn:
        for i in range(1, 41):
            slow = i % 2 == 0
            facets = {"pace": 0.2 if slow else 0.85, "humour": 0.5, "source": "llm"}
            conn.execute(
                "INSERT INTO media_items (id, media_type, tmdb_id, title, genres, rating, vote_count, facets, vibe_cluster_id) "
                "VALUES (?, 'movie', ?, ?, 'Drama', 7.0, 5000, ?, ?)",
                (i, i, f"Film {i}", json.dumps(facets), 1 if slow else 2),
            )
        for i in range(1, rated + 1):
            verdict, score = ("loved", 0.6) if i % 2 == 0 else ("not_for_me", -1.0)
            conn.execute("INSERT INTO watch_history (media_item_id, profile_id, status, user_rating) VALUES (?, 1, 'seen', ?)",
                         (i, 9.0 if i % 2 == 0 else 3.0))
            conn.execute("INSERT INTO rating_details (profile_id, media_item_id, verdict, taste_score, inferred) VALUES (1, ?, ?, ?, 0)",
                         (i, verdict, score))
    return db


def ranked_items(db):
    with db.get_connection() as conn:
        return [dict(r) | {"recommendation_score": 7.0} for r in conn.execute("SELECT * FROM media_items WHERE id > 20")]


def test_runtime(tmp):
    print("Runtime")
    db = make_db(tmp)
    off = TasteRuntime(db, {"enabled": False})
    items = off.apply(ranked_items(db), "me")
    check("disabled: items untouched", all("facet_fit" not in i and i["recommendation_score"] == 7.0 for i in items))

    on = TasteRuntime(db, {"enabled": True, "min_verdicts": 10})
    items = on.apply(ranked_items(db), "me")
    check("enabled: items carry facet_fit and cluster_affinity", all("facet_fit" in i and "cluster_affinity" in i for i in items))
    top = items[0]
    check("films like the loved ones (slow, cluster 1) rank first", top["vibe_cluster_id"] == 1 and top["facet_fit"] > 0, top)
    check("fast films like the disliked ones rank last", items[-1]["vibe_cluster_id"] == 2 and items[-1]["facet_fit"] < 0)
    check("score moved by weight x term", abs(top["recommendation_score"] - round(7.0 + top["facet_fit"] + 0.8 * top["cluster_affinity"], 2)) < 0.011)
    check("badge texts present on a good match", isinstance(top["taste_reasons"], list))

    gated = TasteRuntime(db, {"enabled": True, "min_verdicts": 50})
    items = gated.apply(ranked_items(db), "me")
    check("below the evidence gate: nothing changes", all("facet_fit" not in i and i["recommendation_score"] == 7.0 for i in items))

    # Partner has no verdicts: his own view only borrows my model at its 0.25 seat, together at 0.6.
    me_top = on.apply(ranked_items(db), "me")[0]["facet_fit"]
    par_top = on.apply(ranked_items(db), "partner")[0]["facet_fit"]
    tog_top = on.apply(ranked_items(db), "together")[0]["facet_fit"]
    check("partner view leans lightly on my model", 0 < par_top < tog_top < me_top, (par_top, tog_top, me_top))

    # Cache follows ratings.
    before = on.models()[1]["n_verdicts"]
    with db.get_connection() as conn:
        conn.execute("INSERT INTO watch_history (media_item_id, profile_id, status, user_rating) VALUES (15, 1, 'seen', 9.0)")
        conn.execute("INSERT INTO rating_details (profile_id, media_item_id, verdict, taste_score, inferred) VALUES (1, 15, 'loved', 0.6, 0)")
    check("model rebuilds when a verdict is added", on.models()[1]["n_verdicts"] == before + 1)

    # Older database without the table: quietly off.
    empty = DatabaseManager(os.path.join(tmp, "e.db"))
    with empty.get_connection() as conn:
        conn.execute("DROP TABLE rating_details")
    check("no rating_details table: no crash, no change", all("facet_fit" not in i for i in TasteRuntime(empty, {"enabled": True}).apply([{"id": 1, "recommendation_score": 1.0}], "me")))


def test_logging(tmp):
    print("Impression logging")
    db = make_db(tmp, name="log.db")
    on = TasteRuntime(db, {"enabled": True, "min_verdicts": 10})
    items = on.apply(ranked_items(db), "me")[:3]
    sid = db.create_discover_session(profile="me", media_type="movie", query_text=None, request={}, engine="legacy")
    db.log_impressions(sid, items, 1)
    with db.get_connection() as conn:
        row = conn.execute("SELECT score_json FROM discover_impressions WHERE session_id = ? ORDER BY rank LIMIT 1", (sid,)).fetchone()
    parts = json.loads(row[0])
    check("score_json holds facet_fit and cluster_affinity", "facet_fit" in parts and "cluster_affinity" in parts, parts)


def test_selectivity():
    print("Selectivity")
    pop = {"pace": (0.5, 0.2), "ending_valence": (0.5, 0.2)}
    rated = [{"taste_score": 0.6, "facets": {"pace": 0.7, "ending_valence": 0.9}} for _ in range(9)]
    rated += [{"taste_score": 0.6, "facets": {"pace": 0.3, "ending_valence": 0.9}} for _ in range(9)]
    m = tm.build_taste_model(rated, {"min_verdicts": 5}, pop)
    check("a dial that sits at the usual level and varies gets little weight",
          m["dials"]["pace"]["weight"] < 0.5 * m["dials"]["ending_valence"]["weight"], (m["dials"]["pace"], m["dials"]["ending_valence"]))
    check("population stats need enough films", tm.population_stats([{"pace": 0.5}] * 5) == {})


if __name__ == "__main__":
    tmp = tempfile.mkdtemp()
    try:
        test_runtime(tmp)
        test_logging(tmp)
        test_selectivity()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print()
    if failures:
        print(f"{len(failures)} check(s) failed")
        sys.exit(1)
    print("All taste runtime checks passed")
