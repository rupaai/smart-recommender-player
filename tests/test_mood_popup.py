"""
Mood session tests (tests/test_mood_popup.py)

No DB, no model; a tiny synthetic pool is enough. The ranking tests use a pool of
thrillers, rom-coms, dramas, comedies and animation (with or without facets).

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
    kw.setdefault("mood_weight", 3.0)
    return mp.MoodSession(1, {}, pool(), **kw)


KINDS = {
    "thriller": ("Thriller, Action", {"tension": 0.9, "energy": 0.8, "pace": 0.8, "darkness": 0.6, "humour": 0.1,
                                      "weight": 0.5, "ending_valence": 0.4}),
    "romcom": ("Romance, Comedy", {"tension": 0.15, "energy": 0.4, "pace": 0.5, "darkness": 0.1, "humour": 0.5,
                                   "weight": 0.2, "ending_valence": 0.9}),
    "drama": ("Drama", {"tension": 0.4, "energy": 0.3, "pace": 0.3, "darkness": 0.5, "humour": 0.1,
                        "weight": 0.8, "ending_valence": 0.4}),
    "comedy": ("Comedy", {"tension": 0.1, "energy": 0.6, "pace": 0.7, "darkness": 0.1, "humour": 0.95,
                          "weight": 0.1, "ending_valence": 0.8}),
    "family": ("Family, Animation", {"tension": 0.1, "energy": 0.4, "pace": 0.5, "darkness": 0.05, "humour": 0.6,
                                     "weight": 0.2, "ending_valence": 0.95}),
}


def mood_pool(facets=True, per=3):
    """per films of each kind, all with the same base score so only the moods separate them."""
    out, i = [], 0
    for kind, (genres, dials) in KINDS.items():
        for _ in range(per):
            i += 1
            out.append({"id": i, "title": f"{kind} {i}", "kind": kind, "media_type": "movie",
                        "recommendation_score": 7.0, "genres": genres,
                        "facets": json.dumps(dials) if facets else None})
    return out


def ids_of(s, kind):
    return [x["id"] for x in s.ranked() if x["kind"] == kind]


def answered(facets=True, **answers):
    s = mp.MoodSession(1, {}, mood_pool(facets), mood_weight=3.0)
    for qid, oid in answers.items():
        s.current = {"id": qid, "options": mq.BY_ID[qid]["options"]}
        s.answer(qid, oid)
    return s


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
    check("ranked adds popup_boost and hides _popup keys", sorted(x["id"] for x in r) == list(range(1, 9))
          and all("_popup_base" not in x and "_popup_moodfit" not in x for x in r))
    check("scored is (score, item)", isinstance(s.scored()[0][0], float) and "id" in s.scored()[0][1])
    for attr in ("id", "request", "done", "current", "belief", "asked"):
        check(f"has .{attr}", hasattr(s, attr))


def test_ranking():
    print("Ranking")
    s0 = answered()
    r0 = s0.ranked()
    check("even belief: same keys as before plus mood_fit and top_moods",
          all({"mood_fit", "top_moods", "popup_boost", "recommendation_score", "kind"} <= set(x) for x in r0))
    check("no _popup keys leak", all(not any(k.startswith("_popup") for k in x) for x in r0))
    check("score = pool score + mood_fit", all(abs(x["recommendation_score"] - (7.0 + x["mood_fit"])) < 0.01
                                               and abs(x["popup_boost"] - x["mood_fit"]) < 0.01 for x in r0))
    check("top_moods are 3 distinct mood ids", all(len(x["top_moods"]) == 3 and len(set(x["top_moods"])) == 3
                                                  and set(x["top_moods"]) <= set(mp.MOOD_IDS) for x in r0))
    check("mood_weight 0 leaves the pool score",
          all(x["mood_fit"] == 0 and x["recommendation_score"] == 7.0
              for x in mp.MoodSession(1, {}, mood_pool(), mood_weight=0).ranked()))

    # Thrill answers
    s = answered(G1="racing", G3="loud")
    r = s.ranked()
    pos = {x["id"]: i for i, x in enumerate(r)}
    th, rc = ids_of(s, "thriller"), ids_of(s, "romcom")
    before = {x["id"]: i for i, x in enumerate(r0)}
    check("after Thrill answers thrillers lead", {x["kind"] for x in r[:3]} == {"thriller"}, [x["kind"] for x in r[:4]])
    check("thrillers rose against the even belief", min(pos[i] for i in th) < min(before[i] for i in th)
          or all(pos[i] <= before[i] for i in th), (pos, before))
    check("thriller top_moods lean thrill",
          any(m in vibe_moods.GROUPS["thrill"] for m in r[0]["top_moods"]), r[0]["top_moods"])

    # Not-Light answer
    s = answered(G4="bringit")
    r = s.ranked()
    pos = {x["id"]: i for i, x in enumerate(r)}
    rc = ids_of(s, "romcom")
    base = {x["id"]: i for i, x in enumerate(r0)}
    fits0 = {x["id"]: x["mood_fit"] for x in r0}
    check("rom-com score drops after a not-Light answer",
          all(x["mood_fit"] < fits0[x["id"]] for x in r if x["kind"] == "romcom"))
    dr = ids_of(s, "drama")
    check("drama passes the rom-com", max(pos[i] for i in dr) < min(pos[i] for i in rc)
          and not max(base[i] for i in dr) < min(base[i] for i in rc), (pos, base))
    check("rom-com is lowered but not last", max(pos[i] for i in rc) < len(r) - 1
          and any(x["kind"] != "romcom" for x in r[pos[rc[-1]] + 1:]), [x["kind"] for x in r])

    # No facets: genres alone still move the order
    n0 = answered(facets=False)
    n1 = answered(facets=False, G1="racing", G3="loud")
    o0 = [x["id"] for x in n0.ranked()]
    o1 = [x["id"] for x in n1.ranked()]
    check("no facets: answers still change the order", o0 != o1)
    check("no facets: thrillers lead on genres", {x["kind"] for x in n1.ranked()[:3]} == {"thriller"},
          [x["kind"] for x in n1.ranked()[:4]])
    check("no facets: every film still gets a mood_fit", all(x["mood_fit"] > 0 for x in n1.ranked()))

    # Cache: fits are computed once per film
    calls = []
    real = vibe_moods.fit
    vibe_moods.fit = lambda *a, **k: (calls.append(1), real(*a, **k))[1]
    try:
        c = answered()
        c.ranked()
        first = len(calls)
        c.current = {"id": "G1", "options": mq.BY_ID["G1"]["options"]}
        c.answer("G1", "racing")
        c.ranked()
    finally:
        vibe_moods.fit = real
    check("fits are cached across answers", first == len(c.pool) * len(vibe_moods.MOODS) and len(calls) == first,
          (first, len(calls)))

    # Determinism
    a = [(x["id"], x["recommendation_score"], x["mood_fit"], x["top_moods"])
         for x in answered(G1="racing", G3="loud", T1="even").ranked()]
    b = [(x["id"], x["recommendation_score"], x["mood_fit"], x["top_moods"])
         for x in answered(G1="racing", G3="loud", T1="even").ranked()]
    check("ranking is deterministic", a == b)
    check("equal scores keep pool order", [x["id"] for x in sorted(
        mp.MoodSession(1, {}, [dict(id=i, recommendation_score=5.0) for i in range(5)], mood_weight=3.0).ranked(),
        key=lambda x: -x["recommendation_score"])] == list(range(5)))


def test_weight():
    print("Mood weight")
    check("constructor argument wins", new(mood_weight=1.5).mood_weight == 1.5)
    check("missing file falls back to 3.0", mp.load_mood_weight("/nonexistent/config.yaml") == 3.0)
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        for text, want in (("recommender:\n  mood_weight: 2.5\n", 2.5), ("recommender:\n  x: 1\n", 3.0),
                           ("recommender: {mood_weight: oops}\n", 3.0), ("", 3.0)):
            path = os.path.join(d, "c.yaml")
            with open(path, "w") as f:
                f.write(text)
            check(f"config {text.strip()[:30]!r} -> {want}", mp.load_mood_weight(path) == want)

def test_grownup_avoid():
    print("\n'Grown-up' pushes animation down")
    s = answered(L2="grownup")
    order = [x["kind"] for x in s.ranked()]
    check("animated films rank below every other kind", order[-3:] == ["family"] * 3, order)
    check("a cartoon gets a negative mood score", min(x["mood_fit"] for x in s.ranked() if x["kind"] == "family") < 0)
    plain = answered(L2="dontmind")
    check("'Don't mind' pushes nothing down", not plain.avoid, plain.avoid)


if __name__ == "__main__":
    test_grownup_avoid()
    test_bank()
    test_questions()
    test_update()
    test_eligibility()
    test_stop()
    test_interface()
    test_ranking()
    test_weight()
    print(f"\n{'ALL PASSED' if not failures else f'{len(failures)} FAILED'}")
    sys.exit(1 if failures else 0)
