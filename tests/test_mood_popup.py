"""
Mood session tests (tests/test_mood_popup.py)

No DB, no model; a tiny synthetic pool is enough because the belief engine does
not look at films yet.

    uv run python tests/test_mood_popup.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import mood_popup as mp  # noqa: E402
from services import mood_questions as mq  # noqa: E402
from services import vibe_moods  # noqa: E402

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} — got: {detail}")
        failures.append(label)


def pool(n=8):
    return [{"id": i + 1, "title": f"Film {i + 1}", "media_type": "movie", "recommendation_score": 8.0 - i * 0.1,
             "genres": "Comedy", "tier2_keywords": "christmas"} for i in range(n)]


def new(**kw):
    return mp.MoodSession(1, {}, pool(), **kw)


def play(s, picks, limit=20):
    """Answer with picks[qid] (default: first option) until the session stops; returns asked ids."""
    for _ in range(limit):
        q = s.next_question()
        if q is None:
            break
        s.answer(q["id"], picks.get(q["id"], q["options"][0]["id"]))
        if s.should_stop():
            break
    return [a for a, _ in s.asked]


def test_bank():
    print("Bank")
    check("bank validates against vibe_moods", mq.validate() and vibe_moods.validate())
    names = {g for q in mq.BANK for o in q["options"] for g in o.get("groups", {})}
    moods = {m for q in mq.BANK for o in q["options"] for m in o.get("moods", {})}
    check("every group name exists", names <= set(vibe_moods.GROUPS), names - set(vibe_moods.GROUPS))
    check("every mood id exists", moods <= set(vibe_moods.BY_ID), moods - set(vibe_moods.BY_ID))
    check("every group has a tier 2 question",
          {q["group"] for q in mq.BANK if q["tier"] == 2} == set(vibe_moods.GROUPS))
    bad = [dict(mq.BANK[1], options=[dict(mq.BANK[1]["options"][0], groups={"funny": 1.0}), mq.BANK[1]["options"][1]])]
    try:
        old, mq.BANK[:] = list(mq.BANK), mq.BANK + bad
        mq.BANK[-1]["id"] = "ZZ"
        raised = False
        try:
            mq.validate()
        except ValueError:
            raised = True
        check("validate() catches an unknown group", raised)
    finally:
        mq.BANK[:] = old
    s = new()
    check("missing group counts 0.3",
          abs(mq.likelihood(mq.BY_ID["G1"]["options"][0], "laugh_out_loud") - 1.0) < 1e-9
          and abs(mq.likelihood(mq.BY_ID["G1"]["options"][1], "laugh_out_loud") - 0.3) < 1e-9)
    check("missing mood counts 0.3",
          mq.likelihood(mq.BY_ID["L1"]["options"][0], "good_cry") == 0.3)
    check("starts even", abs(max(s.belief.values()) - min(s.belief.values())) < 1e-12
          and abs(sum(s.belief.values()) - 1) < 1e-9)


def test_questions():
    print("Questions")
    s = new()
    q = s.next_question()
    check("opener is A3", q["id"] == "A3", q["id"])
    pub = s.public_question()
    check("public shape", set(pub) == {"id", "text", "options", "step", "max_steps"} and pub["step"] == 1
          and pub["max_steps"] == 6 and all(set(o) == {"id", "emoji", "label", "examples"} and o["examples"] == []
                                            for o in pub["options"]), pub)
    for bad, label in (("G1", "wrong question"),):
        try:
            s.answer(bad, "smiling")
            check(f"ValueError on {label}", False)
        except ValueError:
            check(f"ValueError on {label}", True)
    try:
        s.answer("A3", "nope")
        check("ValueError on unknown option", False)
    except ValueError:
        check("ValueError on unknown option", True)

    for oid in ("wrung", "bored", "stressed", "great"):
        t = new()
        t.next_question()
        t.answer("A3", oid)
        check(f"A3 is never asked twice ({oid})", "A3" not in [(t.next_question() or {}).get("id")])

    a = play(new(), {})
    b = play(new(), {})
    check("same answers give the same questions", a == b and len(a) >= 2, (a, b))
    check("opener first in a full run", a[0] == "A3")

    for oid in ("wrung", "bored", "stressed", "great"):
        s = new()
        s.next_question()
        s.answer("A3", oid)
        s.next_question()
        check(f"second question follows A3 ({oid})", s.current is not None and s.current["id"] != "A3")


def test_update():
    print("Belief")
    s = new()
    s.next_question()
    s.answer("A3", "great")
    start = dict(s.belief)
    s.current = None
    s.current = {"id": "G1", "text": "", "options": mq.BY_ID["G1"]["options"]}
    s.answer("G1", "racing")
    b = s.belief
    check("sums to 1", abs(sum(b.values()) - 1) < 1e-9)
    for m in ("romance_warm", "date_night", "cosy_comfort"):
        check(f"G1 'Heart still racing' lowers {m}", b[m] < start[m], (b[m], start[m]))
    check("...but never to 0", min(b.values()) > 0.001, min(b.values()))
    check("raises edge_of_seat", b["edge_of_seat"] > start["edge_of_seat"])
    # repeated strong no's still leave every mood above zero
    t = new()
    for _ in range(40):
        t.belief = mp.update(t.belief, mq.BY_ID["G1"]["options"][2])
    check("40 hard no's leave all moods > 0", min(t.belief.values()) > 0, min(t.belief.values()))

    k = new()
    k.next_question()
    k.answer("A3", "wrung")
    before = dict(k.belief)
    k.next_question()
    qid = k.current["id"]
    k.answer(qid, "skip")
    check("skip changes nothing", k.belief == before)
    check("skip counts as asked", ("skip" == k.asked[-1][1]) and k.answered_count == 2)

    p = new(prior={"good_cry": 3.0, "romance_warm": 1.0})
    check("prior hook sets the start", p.belief["good_cry"] > p.belief["romance_warm"] > p.belief["edge_of_seat"])


def test_eligibility():
    print("Eligibility")
    check("only A3 before the opener", [q["id"] for q in new()._eligible()] == ["A3"])

    def force(s, qid, oid):
        s.current = {"id": qid, "text": "", "options": mq.BY_ID[qid]["options"]}
        s.answer(qid, oid)

    s = new()
    for qid, oid in (("A3", "bored"), ("G1", "racing"), ("G2", "all"), ("G4", "bringit")):
        force(s, qid, oid)
    light = s.group_mass("light")
    check("a strong no-comedy path takes Light below 0.15", light < mp.GROUP_MIN, light)
    ids = [q["id"] for q in s._eligible()]
    check("'What kind of funny?' (L1) and L2 are not eligible", "L1" not in ids and "L2" not in ids, ids)
    check("thrill question is still eligible", "T1" in ids, ids)

    # driving the session itself: once Light is below 0.15, L1/L2 are never asked
    for pick in ("racing", "haunted", "talking"):
        t = new()
        for _ in range(6):
            q = t.next_question()
            if q is None:
                break
            if q["id"] in ("L1", "L2"):
                check(f"L1/L2 asked only while Light >= 0.15 ({pick})", t.group_mass("light") >= mp.GROUP_MIN,
                      t.group_mass("light"))
            t.answer(q["id"], {"A3": "bored", "G1": pick, "G2": "all", "G4": "bringit"}.get(q["id"], q["options"][0]["id"]))

    # tie-breaker only when the top two are close
    t = new()
    t.asked = [("A3", "great")]
    t.belief = {m: (0.5 if m == "good_cry" else 0.1 if m == "romance_warm" else 0.4 / 28) for m in mp.MOOD_IDS}
    check("H1 not eligible with a clear leader", "H1" not in [q["id"] for q in t._eligible()])
    t.belief = {m: (0.3 if m in ("good_cry", "romance_warm") else 0.4 / 28) for m in mp.MOOD_IDS}
    check("H1 eligible when top two are within 0.1", "H1" in [q["id"] for q in t._eligible()])


def test_stop():
    print("Stopping")
    s = new()
    n = 0
    while True:
        q = s.next_question()
        if q is None:
            break
        s.answer(q["id"], "skip" if n else q["options"][0]["id"])
        n += 1
        if s.should_stop():
            break
    check("stops by 6 answers", n <= 6 and s.should_stop(), n)
    check("no 7th question", s.next_question() is None)

    s = new()
    s.belief = {m: (0.9 if m == "good_cry" else 0.1 / 29) for m in mp.MOOD_IDS}
    s.asked = [("A3", "great")]
    check("never stops before 2 answers", not s.should_stop())
    s.asked.append(("G1", "teary"))
    check("stops on top >= 0.55", s.should_stop())
    s.belief = {m: (0.42 if m in ("good_cry", "romance_warm") else 0.16 / 28) for m in mp.MOOD_IDS}
    check("stops on top two >= 0.80", s.should_stop())
    s.belief = {m: 1 / 30 for m in mp.MOOD_IDS}
    check("keeps going on a flat belief", not s.should_stop())
    s.done = True
    check("done (Just show me) stops and ends questions", s.should_stop() and s.next_question() is None)

    # skip all the way: still bounded
    s = new()
    for _ in range(10):
        q = s.next_question()
        if q is None:
            break
        s.answer(q["id"], "skip")
    check("all skips end at 6", s.answered_count == 6, s.answered_count)


def test_interface():
    print("Interface")
    s = new()
    s.next_question()
    s.answer("A3", "stressed")
    f = s.log_fields()
    check("log_fields keys", set(f) == {"answers_json", "asked_order", "belief_json"}, set(f))
    check("log_fields JSON", json.loads(f["answers_json"]) == {"A3": "stressed"}
          and json.loads(f["asked_order"]) == ["A3"] and set(json.loads(f["belief_json"])) == set(mp.MOOD_IDS))
    r = s.ranked()
    check("ranked keeps pool order and adds popup_boost", [x["id"] for x in r] == list(range(1, 9))
          and all(x["popup_boost"] == 0 and "_popup_base" not in x for x in r))
    check("scored is (score, item)", s.scored()[0][1]["id"] == 1)
    for attr in ("id", "request", "done", "current", "belief", "asked"):
        check(f"has .{attr}", hasattr(s, attr))


if __name__ == "__main__":
    test_bank()
    test_questions()
    test_update()
    test_eligibility()
    test_stop()
    test_interface()
    print(f"\n{'ALL PASSED' if not failures else f'{len(failures)} FAILED'}")
    sys.exit(1 if failures else 0)
