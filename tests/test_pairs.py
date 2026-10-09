"""
Offline tests for "This or that" pairs (tests/test_pairs.py)

    uv run python tests/test_pairs.py
"""

import json
import os
import random
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import DatabaseManager  # noqa: E402
from services import pairs as pr  # noqa: E402
from services import verdicts as vd  # noqa: E402

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} — got: {detail}")
        failures.append(label)


def film(i, genres="Drama", score=0.2, cluster=None, comparisons=0):
    return {"id": i, "genres": genres, "taste_score": score, "vibe_cluster_id": cluster, "comparisons": comparisons}


def test_picker():
    print("Picker rules")
    rng = lambda: random.Random(1)
    out = pr.pick_pairs([film(1), film(2, "Comedy")], rng=rng())
    check("no shared genre, no pair", out == [], out)
    out = pr.pick_pairs([film(1, score=1.0), film(2, score=0.2)], rng=rng())
    check("scores further than 0.3 apart are not paired", out == [], out)
    out = pr.pick_pairs([film(1, score=0.6), film(2, score=0.3)], rng=rng())
    check("scores within 0.3 are paired", len(out) == 1 and out[0]["shared_genre"] == "Drama", out)
    out = pr.pick_pairs([film(1, "Drama, Crime"), film(2, "Crime, Thriller")], rng=rng())
    check("any shared genre counts", len(out) == 1 and out[0]["shared_genre"] == "Crime", out)
    out = pr.pick_pairs([film(1), film(2)], answered=[(2, 1)], rng=rng())
    check("an answered pair (either order) never comes back", out == [], out)
    out = pr.pick_pairs([film(1, score=None), film(2)], rng=rng())
    check("films without a score are ignored", out == [], out)

    films = [film(1, cluster=5), film(2, cluster=9), film(3, cluster=5), film(4, cluster=9)]
    out = pr.pick_pairs(films, limit=1, rng=rng())
    ids = {out[0]["a"]["id"], out[0]["b"]["id"]}
    check("same vibe cluster is preferred", ids in ({1, 3}, {2, 4}), ids)

    films = [film(1, comparisons=5), film(2, comparisons=5), film(3, comparisons=0), film(4, comparisons=0)]
    out = pr.pick_pairs(films, limit=1, rng=rng())
    ids = {out[0]["a"]["id"], out[0]["b"]["id"]}
    check("fewest comparisons first", ids == {3, 4}, ids)

    films = [film(i) for i in range(1, 9)]
    out = pr.pick_pairs(films, limit=10, rng=rng())
    seen = [f["id"] for p in out for f in (p["a"], p["b"])]
    check("a film appears once per round", len(seen) == len(set(seen)) and len(out) == 4, out)
    check("round size is capped", len(pr.pick_pairs([film(i) for i in range(1, 40)], limit=10, rng=rng())) == 10)


def test_scores():
    print("Score maths")
    check("no answers, no scores", pr.pair_scores([]) == {})
    s = pr.pair_scores([(1, 2)])
    check("winner above loser, symmetric", s[1] > 0 > s[2] and abs(s[1] + s[2]) < 1e-6, s)
    s5 = pr.pair_scores([(1, 2)] * 5)
    check("more evidence moves further from zero", s5[1] > s[1], (s5, s))
    check("one comparison is shrunk toward zero", s[1] < 0.5, s)
    s = pr.pair_scores([(1, 2), (2, 3)])
    check("transitive order 1 > 2 > 3", s[1] > s[2] > s[3], s)
    s = pr.pair_scores([(1, 2), (2, 1)])
    check("a split result stays near zero", abs(s[1]) < 1e-6, s)
    check("nudge never exceeds the cap", abs(pr.nudge_for(50)) <= pr.MAX_NUDGE and abs(pr.nudge_for(-50)) <= pr.MAX_NUDGE)
    check("nudge is monotone", pr.nudge_for(0.2) < pr.nudge_for(0.8))


def test_nudge_bounds():
    print("Nudge cap and verdict boundaries")
    sc = {k: v["score"] for k, v in vd.VERDICTS.items()}
    check("small nudge applied", abs(pr.apply_nudge(sc["liked"], 0.1) - 0.3) < 1e-9)
    check("capped at +0.15 even if asked for more", abs(pr.apply_nudge(sc["liked"], 0.9) - 0.35) < 1e-9)
    check("capped at -0.15", abs(pr.apply_nudge(sc["liked"], -0.9) - 0.05) < 1e-9)
    ok = True
    for key, base in sc.items():
        for n in (-1, -0.15, 0, 0.15, 1):
            f = pr.apply_nudge(base, n)
            ok &= abs(f - base) <= pr.MAX_NUDGE + 1e-9 and -1 <= f <= 1
    check("never more than 0.15 from the verdict, always inside -1..+1", ok)
    ordered = sorted(sc.items(), key=lambda kv: kv[1])
    ok = True
    for (lo_k, lo), (hi_k, hi), in zip(ordered, ordered[1:]):
        ok &= pr.apply_nudge(lo, 1) < hi and pr.apply_nudge(hi, -1) > lo
    check("a verdict never crosses its neighbour (best 'meh' < worst 'liked')", ok)
    # A tight pair of verdicts: liked 0.2 vs meh -0.3 leaves room, so force a case by DNF score
    check("didn't-finish score stays below Meh", pr.apply_nudge(vd.DID_NOT_FINISH_CAP, 1) < sc["meh"])
    check("rewatch can't exceed 1.0", pr.apply_nudge(1.0, 1) == 1.0)
    check("not for me can't go below -1.0", pr.apply_nudge(-1.0, -1) == -1.0)


def test_dials():
    print("Dial differences")
    d = pr.dial_differences({"humour": 0.9, "pace": 0.5, "energy": 0.1, "source": "llm"},
                            {"humour": 0.2, "pace": 0.55, "energy": 0.7, "source": "anchor"}, top=2)
    check("biggest differences first, signed winner minus loser",
          [r["dial"] for r in d] == ["humour", "energy"] and d[0]["diff"] > 0 > d[1]["diff"], d)
    check("no facets, nothing stored", pr.dial_differences(None, {"a": 1}) == [])


def make_db(tmp):
    db = DatabaseManager(os.path.join(tmp, "p.db"))
    with db.get_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO profiles (id, name) VALUES (1, 'Me');")
        for i in range(1, 7):
            conn.execute(
                "INSERT INTO media_items (id, media_type, tmdb_id, title, genres, rating, vote_count, facets) "
                "VALUES (?, 'movie', ?, ?, ?, 7.0, 5000, ?)",
                (i, i, f"Film {i}", "Thriller, Drama" if i <= 4 else "Comedy",
                 json.dumps({"humour": i / 10, "energy": 1 - i / 10, "pace": 0.5})),
            )
    for i in range(1, 7):
        db.rate_item(i, profile_id=1, verdict="liked")
    return db


def test_database():
    print("Database")
    tmp = tempfile.mkdtemp()
    try:
        db = make_db(tmp)
        with db.get_connection() as c:
            check("schema is v9", c.execute("PRAGMA user_version;").fetchone()[0] >= 9)
        rnd = db.get_pair_round(1, "movie", 10, seed=3)
        ids = {frozenset((p["a"]["id"], p["b"]["id"])) for p in rnd["pairs"]}
        check("round only pairs films that share a genre", all(max(i) <= 4 or min(i) >= 5 for i in ids), ids)
        check("round has pairs", len(rnd["pairs"]) == 3, rnd["pairs"])

        r = db.record_pair_answer(1, 1, 2, "a")
        check("answer saved with the shared genre", r["saved"] and r["winner_id"] == 1 and r["shared_genre"] == "Thriller", r)
        check("dial differences stored", r["dial_diffs"] and r["dial_diffs"][0]["dial"] in ("humour", "energy"), r["dial_diffs"])
        d1, d2 = db.get_rating_details(1, 1), db.get_rating_details(2, 1)
        check("winner nudged up, loser down", d1["pair_nudge"] > 0 > d2["pair_nudge"], (d1["pair_nudge"], d2["pair_nudge"]))
        check("verdict score untouched", d1["taste_score"] == 0.2 and d2["taste_score"] == 0.2)
        check("final = verdict + nudge", abs(d1["final_taste_score"] - (0.2 + d1["pair_nudge"])) < 1e-9)
        check("the same pair never comes back", all(
            frozenset((p["a"]["id"], p["b"]["id"])) != frozenset((1, 2)) for p in db.get_pair_round(1, "movie", 10)["pairs"]))
        again = db.record_pair_answer(1, 2, 1, "b")
        check("answering twice keeps the first answer", again["saved"] is False)

        r = db.record_pair_answer(1, 3, 4, "same")
        check("'about the same' stores same=1", r["saved"] and r["same"] is True)
        d3, d4 = db.get_rating_details(3, 1), db.get_rating_details(4, 1)
        check("'about the same' counts for neither", d3["pair_nudge"] is None and d4["pair_nudge"] is None)
        with db.get_connection() as c:
            row = c.execute("SELECT same FROM rating_pairs WHERE winner_id IN (3,4)").fetchone()
        check("row has same = 1", row["same"] == 1)

        for bad in ((5, 1, "a"), (1, 1, "a"), (1, 2, "maybe")):
            try:
                db.record_pair_answer(1, *bad)
                check(f"bad answer {bad} rejected", False)
            except ValueError:
                check(f"bad answer {bad} rejected", True)

        # Many wins can never push a Liked film past Loved, or a Meh below Not for me.
        for k in range(3):
            pass
        db.rate_item(2, profile_id=1, verdict="meh")
        d2 = db.get_rating_details(2, 1)
        check("changing the verdict keeps the stored nudge, re-bounded", d2["final_taste_score"] < vd.VERDICTS["liked"]["score"])
        finals = db.get_final_taste_scores(1)
        check("final scores readable per profile", finals[1] > 0.2 and set(finals) == set(range(1, 7)), finals)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_picker()
    test_scores()
    test_nudge_bounds()
    test_dials()
    test_database()
    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("All pair tests passed.")
