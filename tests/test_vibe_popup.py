"""
Questions pop-up tests (tests/test_vibe_popup.py)

A synthetic pool with known facets, tags and clusters; no DB, no model.

    uv run python tests/test_vibe_popup.py
"""

import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import services.vibe_popup as vp  # noqa: E402
from services.vibe_questions import ABOUT_YOU, AVOID, BANK, BY_ID  # noqa: E402
from services.vibe_vocab import DIAL_NAMES, FEELS, GOOD_FOR  # noqa: E402

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} — got: {detail}")
        failures.append(label)


def make_pool(n=120, seed=1, facets=True):
    rng = random.Random(seed)
    pool = []
    for i in range(n):
        f = {d: round(rng.random(), 2) for d in DIAL_NAMES}
        tags = {"feels": rng.sample(FEELS, 3), "good_for": rng.sample(GOOD_FOR, 2), "flags": [],
                "palette": [rng.choice(["pastel", "cold", "bw"])]}
        if i % 9 == 0:
            tags["flags"] = ["jump_scares"]
        pool.append({
            "id": i + 1, "title": f"Film {i + 1}", "media_type": "movie",
            "recommendation_score": 8.0 - i * 0.01, "rating": 6 + rng.random() * 3, "vote_count": rng.randint(5, 5000),
            "runtime": rng.choice([88, 100, 118, 135, 160]), "release_year": rng.choice([1960, 1985, 2005, 2020]),
            "original_language": rng.choice(["en", "en", "en", "ko", "fr"]),
            "genres": rng.choice(["Comedy", "Drama", "Horror, Thriller", "Action", "Animation, Family"]),
            "tier2_keywords": rng.choice(["heist, con artist", "serial killer, investigation", "christmas, winter",
                                          "jump scare, haunted house", "road trip"]),
            "facets": json.dumps(f) if facets else None, "vibe_tags": json.dumps(tags) if facets else None,
            "vibe_cluster_id": i % 7 + 1,
        })
    return pool


def test_bank():
    print("Bank")
    ids = [q["id"] for q in BANK]
    check("31 questions encoded (C4, H4 wait for Phase 5)", len(ids) == 31 and "C4" not in ids and "H4" not in ids, len(ids))
    feels_used = {t for q in BANK for o in q["options"] for t in o["fx"].get("feels+", [])}
    unknown = {t for t in feels_used if t not in FEELS and t not in GOOD_FOR}
    check("most feels+ tags are in the closed vocabulary (rest match keywords)", len(unknown) <= 15, unknown)
    check("every option has an emoji and a label", all(o["emoji"] and o["label"] for q in BANK for o in q["options"]))


def test_scoring():
    print("Belief and scoring")
    pool = make_pool()
    b = vp.apply_effects(vp.new_belief(), {"set": {"energy": 0.1}})
    top = [it for _, it in vp.score_pool(pool, b)[:10]]
    avg = sum(vp.features(it)["facets"]["energy"] for it in top) / 10
    check("a calm answer pulls calm films up", avg < 0.35, avg)
    b = vp.apply_effects(vp.new_belief(), {"max": {"darkness": 0.3}})
    kept = vp.score_pool(pool, b)
    check("a dial cap removes films above it", all(vp.features(it)["facets"]["darkness"] <= 0.3 for _, it in kept), len(kept))
    b = vp.apply_effects(vp.new_belief(), {"max": {"darkness": 0.01}})
    check("an impossible cap becomes a penalty, not an empty list", len(vp.score_pool(pool, b)) == len(pool))
    b = vp.apply_effects(vp.new_belief(), {"kwx": ["jump scare"]})
    check("content flags and keywords both exclude",
          not any("jump scare" in it["tier2_keywords"] or "jump_scares" in it["vibe_tags"] for _, it in vp.score_pool(pool, b)))
    b2 = vp.apply_effects(b, {"shift": {"energy": 0.2}})
    check("shift moves from 0.5 with growing confidence", b2["dials"]["energy"] == [0.7, 0.2])


def test_session():
    print("Session")
    pool = make_pool()
    s = vp.PopupSession(1, {"query": "something"}, pool, {c: f"cluster {c}" for c in range(1, 8)})
    q = s.next_question()
    check("first question is about you", q and q["id"] in ABOUT_YOU, q and q["id"])
    pq = s.public_question()
    check("options carry two example films", all(len(o["examples"]) == 2 for o in pq["options"] if o["id"] != "newdish"), pq)
    before = vp.top_ids(s.scored(), 10)
    s.answer(q["id"], q["options"][0]["id"])
    check("an answer changes the top 10", vp.top_ids(s.scored(), 10) != before)
    asked = [q["id"]]
    while not s.should_stop():
        q = s.next_question()
        if not q:
            break
        asked.append(q["id"])
        s.answer(q["id"], q["options"][-1]["id"])
    check("at most 6 questions", len(asked) <= 6, asked)
    check("an avoid question by question 4", any(a in AVOID for a in asked[:4]) or len(asked) < 4, asked)
    check("no alt siblings asked together", not ({"B1", "B5"} <= set(asked) or {"H1", "H2"} <= set(asked)), asked)
    check("ranked list carries the pop-up boost", "popup_boost" in s.ranked()[0])

    s = vp.PopupSession(2, {"query": "something fun with friends"}, make_pool(), {})
    check("text that says 'friends' answers D1", "D1" in s.asked_ids and s.belief["profile"] == "together")
    for _ in range(6):
        q = s.next_question()
        if not q:
            break
        check(f"D1 not asked again ({q['id']})", q["id"] != "D1")
        s.answer(q["id"], "skip")

    s = vp.PopupSession(3, {"query": "x"}, make_pool(facets=False), {})
    q = s.next_question()
    check("without facets or tags, dial questions are skipped", q is None or q["id"] in ("A4", "F1", "F4", "D1", "A1", "A3"),
          q and q["id"])

    s = vp.PopupSession(4, {"query": "x"}, make_pool(), {c: f"cluster {c}" for c in range(1, 8)})
    s.asked = [("A3", "skip"), ("D1", "skip"), ("H1", "skip"), ("C1", "skip")]
    opts = s._generated_options(BY_ID["I1"], s.scored())
    check("I1 offers 3 real clusters plus none", len(opts) == 4 and opts[-1]["id"] == "none", [o["label"] for o in opts])

    try:
        s.answer("Z9", "x")
        check("answering a question that isn't open is rejected", False)
    except ValueError:
        check("answering a question that isn't open is rejected", True)


def test_auto():
    print("When it appears")
    pool = make_pool()
    check("short text asks", vp.should_auto_ask("cozy", pool))
    check("long specific text over a tight pool doesn't",
          not vp.should_auto_ask("slow korean revenge thriller with a devastating ending please", pool[:3]))
    check("long text over a scattered pool still asks",
          vp.should_auto_ask("slow korean revenge thriller with a devastating ending please", pool))

    s = vp.PopupSession(5, {"query": "slow korean revenge thriller with a devastating ending please"},
                        make_pool(), {c: f"cluster {c}" for c in range(1, 8)})
    q = s.next_question(force=True)
    check("always asks a first question", q is not None and len(q["options"]) >= 2, q and q["id"])

    s = vp.PopupSession(6, {"query": "x"}, make_pool(), {})
    vp_min = vp.MIN_VALUE
    vp.MIN_VALUE = 99.0           # nothing passes: the forced pick falls back to the best below the bar
    try:
        check("without force, nothing below the bar is asked", s.next_question() is None)
        q = s.next_question(force=True)
        check("with force, the best question below the bar is asked", q is not None and q["id"] in ABOUT_YOU,
              q and q["id"])
    finally:
        vp.MIN_VALUE = vp_min


def test_skip_reason():
    print("Why it didn't appear")
    pool, bare = make_pool(), make_pool(facets=False)
    check("asked: no reason", vp.skip_reason("cozy", pool, True) is None)
    check("no results", vp.skip_reason("cozy", [], False) == "no_results")
    check("no facets or tags: no_mood_data", vp.skip_reason("cozy", bare, False) == "no_mood_data")
    check("bare pool never asks a question", vp.PopupSession(1, {}, bare).next_question() is None)
    check("specific request", vp.skip_reason("slow korean revenge thriller with a devastating ending please",
                                             pool[:3], False) == "specific_request")
    check("otherwise nothing worth asking", vp.skip_reason("cozy", pool, False) == "nothing_to_ask")


if __name__ == "__main__":
    test_bank()
    test_scoring()
    test_session()
    test_auto()
    test_skip_reason()
    print(f"\n{'ALL PASSED' if not failures else f'{len(failures)} FAILED'}")
    sys.exit(1 if failures else 0)
