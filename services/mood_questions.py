"""
Mood question bank (services/mood_questions.py)

The new question bank from the vibe-questions plan (T2), as data. It replaces
services/vibe_questions.py in the pop-up once MoodSession is wired in (T4); the
old bank stays in the repo.

Each question:
    id        "A3", "G1", "L1", "H1" ...
    text      what she is asked
    tier      0 opener (always first), 1 group question, 2 inside one group,
              3 tie-breaker (only when the top two moods are close)
    group     tier 2 only: the question is eligible while this group holds
              enough of the belief
    options   [{id, emoji, label, groups | moods, default?}]

An option points either at mood GROUPS ({group: weight 0..1}) or at single MOODS
({mood_id: weight 0..1}). Anything it doesn't mention counts DEFAULT_WEIGHT (0.3),
a mild "not this"; `default` on an option overrides that for the unmentioned ones
(the tie-breaker uses 1.0 so it only pushes the named moods down). Group and mood
names are exactly those of services/vibe_moods.py; validate() checks them.
"""

from __future__ import annotations

from typing import Any, Dict, List

from services import vibe_moods

DEFAULT_WEIGHT = 0.3


def _o(id: str, emoji: str, label: str, groups: Dict[str, float] | None = None,
       moods: Dict[str, float] | None = None, default: float = DEFAULT_WEIGHT) -> Dict[str, Any]:
    o: Dict[str, Any] = {"id": id, "emoji": emoji, "label": label, "default": default}
    if groups is not None:
        o["groups"] = groups
    if moods is not None:
        o["moods"] = moods
    return o


def _all(w: float) -> Dict[str, float]:
    return {g: w for g in vibe_moods.GROUPS}


BANK: List[Dict[str, Any]] = [
    # --- Tier 0: the opener (wording and emoji as A3 in vibe_questions.py) ---------
    {"id": "A3", "tier": 0, "text": "How did today end?", "options": [
        _o("wrung", "🫠", "Wrung out, too many people",
           {"light": 1.0, "drift": 0.8, "heart": 0.6, "dark": 0.1, "mind": 0.2}),
        _o("bored", "🥱", "Bored, nothing happened", {"thrill": 1.0, "mind": 0.8, "dark": 0.6}),
        _o("stressed", "🌀", "Stressed, head still buzzing",
           {"light": 1.0, "heart": 0.7, "drift": 0.6, "thrill": 0.3, "dark": 0.1}),
        _o("great", "✨", "Great day, keep it going", {"thrill": 0.9, "light": 0.8, "heart": 0.8, "mind": 0.6}),
    ]},

    # --- Tier 1: which groups ------------------------------------------------------
    {"id": "G1", "tier": 1, "text": "When the credits roll, you want to be…", "options": [
        _o("smiling", "😊", "Smiling", {"light": 1.0}),
        _o("teary", "🥹", "Teary in a good way", {"heart": 1.0}),
        _o("racing", "💓", "Heart still racing", {"thrill": 1.0}),
        _o("haunted", "🌑", "Quiet, a bit haunted", {"dark": 1.0}),
        _o("talking", "💬", "Talking about it for an hour", {"mind": 1.0}),
        _o("faraway", "🌅", "Somewhere far away", {"drift": 1.0}),
    ]},
    {"id": "G2", "tier": 1, "text": "How much brain is on the table?", "options": [
        _o("none", "🫥", "Almost none", {"light": 1.0, "drift": 0.8, "mind": 0.0}),
        _o("some", "🙂", "Some", _all(0.7)),
        _o("all", "🧠", "All of it", {"mind": 1.0, "dark": 0.7, "light": 0.3}),
    ]},
    {"id": "G3", "tier": 1, "text": "Calm or charged?", "options": [
        _o("calm", "🌿", "Slow and calm", {"drift": 1.0, "heart": 0.8, "thrill": 0.1}),
        _o("steady", "🚶", "Steady", _all(0.7)),
        _o("loud", "🔊", "Fast and loud", {"thrill": 1.0, "light": 0.6, "drift": 0.1}),
    ]},
    {"id": "G4", "tier": 1, "text": "How heavy can it get?", "options": [
        _o("light", "🪶", "Keep it light", {"light": 1.0, "heart": 0.6, "dark": 0.05}),
        _o("bit", "⚖️", "A bit of weight is fine", {"heart": 1.0, "mind": 0.8}),
        _o("bringit", "🪨", "Bring it", {"dark": 1.0, "mind": 0.8, "heart": 0.7}),
    ]},

    # --- Tier 2: which mood inside a group -----------------------------------------
    {"id": "L1", "tier": 2, "group": "light", "text": "What kind of funny?", "options": [
        _o("silly", "🤪", "Big and silly", moods={"laugh_out_loud": 1.0}),
        _o("cringe", "😬", "Awkward and cringey", moods={"cringe_comedy": 1.0}),
        _o("gentle", "🫖", "Warm and gentle", moods={"cosy_comfort": 1.0, "sweet_small": 1.0}),
        _o("cosy", "🛋️", "Not funny, just cosy", moods={"cosy_comfort": 1.0, "switch_off": 1.0}),
    ]},
    {"id": "L2", "tier": 2, "group": "light", "text": "Grown-up, or fine for anyone?", "options": [
        _o("grownup", "🍷", "Grown-up", moods={"family_all": 0.1, "animated_any": 0.3}, default=1.0),
        _o("anyone", "👨‍👩‍👧", "For anyone", moods={"family_all": 1.0, "animated_any": 1.0}),
        _o("dontmind", "🤷", "Don't mind", moods={}, default=1.0),
    ]},
    {"id": "H0", "tier": 2, "group": "heart", "text": "Which kind of heart?", "options": [
        _o("love", "💘", "Falling in love", moods={"romance_warm": 1.0, "date_night": 1.0}),
        _o("cry", "😭", "A big cry", moods={"good_cry": 1.0}),
        _o("winning", "🏆", "Real people winning", moods={"feel_good_true": 1.0}),
        _o("back", "📼", "Looking back", moods={"nostalgic": 1.0}),
        _o("music", "🎶", "Music", moods={"music_dance": 1.0}),
    ]},
    {"id": "T1", "tier": 2, "group": "thrill", "text": "What gets your pulse up?", "options": [
        _o("plan", "🧩", "A clever plan", moods={"heist_caper": 1.0}),
        _o("chase", "⏱️", "A chase against the clock", moods={"edge_of_seat": 1.0}),
        _o("even", "🗡️", "Getting even", moods={"revenge_thrill": 1.0}),
        _o("lurking", "👀", "Something lurking", moods={"scary_fun": 1.0}),
        _o("explode", "💥", "Things exploding", moods={"big_spectacle": 1.0}),
    ]},
    {"id": "D1", "tier": 2, "group": "dark", "text": "Which kind of dark?", "options": [
        _o("gritty", "🚬", "Gritty but real", moods={"dark_gritty": 1.0}),
        _o("bleak", "🌧️", "Bleak but brilliant", moods={"feel_bad_good": 1.0}),
        _o("scary", "👻", "Properly scary", moods={"proper_horror": 1.0}),
        _o("history", "🪖", "History at its worst", moods={"war_history": 1.0}),
    ]},
    {"id": "M1", "tier": 2, "group": "mind", "text": "What should your brain do?", "options": [
        _o("solve", "🔍", "Solve it", moods={"brainy_puzzle": 1.0, "whodunit": 1.0}),
        _o("ideas", "🚀", "Chew on big ideas", moods={"sci_fi_ideas": 1.0}),
        _o("talk", "🗣️", "Listen to great talk", moods={"talky_smart": 1.0}),
        _o("confused", "🌀", "Be gloriously confused", moods={"weird_wonderful": 1.0}),
    ]},
    {"id": "R1", "tier": 2, "group": "drift", "text": "Where should it take you?", "options": [
        _o("beautiful", "🌄", "Somewhere beautiful and slow", moods={"slow_dreamy": 1.0}),
        _o("adventure", "🗺️", "On a big adventure", moods={"adventure_escape": 1.0}),
        _o("nowhere", "📻", "Nowhere, it's company in the background", moods={"background": 1.0}),
    ]},

    # --- Tier 3: tie-breaker (H1 of the old bank), a soft push-down on what would ruin it ----
    {"id": "H1", "tier": 3, "text": "What would ruin tonight?", "options": [
        _o("sob", "😭", "Someone dies and I sob",
           moods={"good_cry": 0.05, "feel_bad_good": 0.1, "war_history": 0.3}, default=1.0),
        _o("jump", "👻", "A jump scare", moods={"proper_horror": 0.05, "scary_fun": 0.3}, default=1.0),
        _o("lost", "🤯", "Feeling lost or stupid",
           moods={"weird_wonderful": 0.05, "brainy_puzzle": 0.2, "sci_fi_ideas": 0.3}, default=1.0),
        _o("cruel", "😣", "People being cruel to each other",
           moods={"dark_gritty": 0.1, "revenge_thrill": 0.2, "feel_bad_good": 0.1, "cringe_comedy": 0.2},
           default=1.0),
        _o("bored", "😴", "Being bored",
           moods={"slow_dreamy": 0.1, "background": 0.1, "talky_smart": 0.3}, default=1.0),
    ]},
]
BY_ID: Dict[str, Dict[str, Any]] = {q["id"]: q for q in BANK}


def likelihood(option: Dict[str, Any], mood_id: str) -> float:
    """How strongly the option points at one mood, 0..1 (before the 0.05 floor)."""
    default = float(option.get("default", DEFAULT_WEIGHT))
    if "moods" in option:
        return float(option["moods"].get(mood_id, default))
    ms = vibe_moods.memberships(mood_id)
    return sum(share * float(option["groups"].get(g, default)) for g, share in ms.items())


def validate() -> bool:
    """Raise ValueError listing every problem in BANK; True when clean."""
    problems: List[str] = []
    ids = [q["id"] for q in BANK]
    if len(set(ids)) != len(ids):
        problems.append("duplicate question ids")
    if [q["id"] for q in BANK if q["tier"] == 0] != ["A3"]:
        problems.append("the only tier 0 question must be A3")
    for q in BANK:
        qid = q["id"]
        if q["tier"] not in (0, 1, 2, 3):
            problems.append(f"{qid}: bad tier {q['tier']!r}")
        if q["tier"] == 2 and q.get("group") not in vibe_moods.GROUPS:
            problems.append(f"{qid}: tier 2 needs a known group, got {q.get('group')!r}")
        oids = [o["id"] for o in q["options"]]
        if len(set(oids)) != len(oids) or "skip" in oids:
            problems.append(f"{qid}: duplicate option ids or an option named skip")
        if len(oids) < 2:
            problems.append(f"{qid}: needs at least two options")
        for o in q["options"]:
            where = f"{qid}/{o['id']}"
            if ("groups" in o) == ("moods" in o):
                problems.append(f"{where}: needs exactly one of groups or moods")
                continue
            if not (o.get("emoji") and o.get("label")):
                problems.append(f"{where}: needs emoji and label")
            for g, w in o.get("groups", {}).items():
                if g not in vibe_moods.GROUPS:
                    problems.append(f"{where}: unknown group {g!r}")
                if not 0.0 <= w <= 1.0:
                    problems.append(f"{where}: weight {w} outside 0..1")
            for m, w in o.get("moods", {}).items():
                if m not in vibe_moods.BY_ID:
                    problems.append(f"{where}: unknown mood {m!r}")
                if not 0.0 <= w <= 1.0:
                    problems.append(f"{where}: weight {w} outside 0..1")
            if not 0.0 <= float(o.get("default", DEFAULT_WEIGHT)) <= 1.0:
                problems.append(f"{where}: default outside 0..1")
    if problems:
        raise ValueError("mood_questions: " + "; ".join(problems))
    return True
