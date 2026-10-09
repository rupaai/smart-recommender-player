"""
Verdicts (services/verdicts.py)

The Rate tab's five verdicts, the "why" chips, the vibe-check questions, and
the maths that turns them into one `taste_score` per film (-1..+1) for the
recommender. Plan: the "Better ratings" doc; the 1-10 stars bunched on 7 and 8
(76 % of 74 films), so a raw rating said little about taste.

Pure functions only, no database access, so the tests need no fixtures.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

# --- Verdicts ---------------------------------------------------------------
# key -> (label, taste_score, legacy user_rating written to watch_history).
# The legacy rating keeps older readers (>= 7 liked, >= 6 for TIGER history)
# right: "liked" passes both, "meh" and "not for me" fail both.
VERDICTS: Dict[str, Dict[str, Any]] = {
    "rewatch": {"label": "Would rewatch", "score": 1.0, "rating": 10.0},
    "loved": {"label": "Loved it", "score": 0.6, "rating": 9.0},
    "liked": {"label": "Liked it", "score": 0.2, "rating": 7.0},
    "meh": {"label": "Meh", "score": -0.3, "rating": 5.0},
    "not_for_me": {"label": "Not for me", "score": -1.0, "rating": 3.0},
}
VERDICT_KEYS: List[str] = list(VERDICTS)
# Where "Why?" opens by itself after the tap.
EXTREME_VERDICTS = ("rewatch", "not_for_me")

# "Didn't finish" caps the score: stopping is a stronger dislike than a shrug.
DID_NOT_FINISH_CAP = -0.5
DID_NOT_FINISH_RATING = 2.0

# --- Chips: the same list for "worked" and "didn't" --------------------------
CHIPS: List[Dict[str, str]] = [
    {"id": "story", "label": "Story"},
    {"id": "characters", "label": "Characters"},
    {"id": "acting", "label": "Acting"},
    {"id": "visuals", "label": "Visuals"},
    {"id": "music", "label": "Music"},
    {"id": "vibe", "label": "Vibe"},
    {"id": "pace", "label": "Pace"},
    {"id": "humour", "label": "Humour"},
    {"id": "ending", "label": "Ending"},
    {"id": "feeling", "label": "Made me feel something"},
]
CHIP_IDS = [c["id"] for c in CHIPS]

# --- Vibe-check questions ----------------------------------------------------
# One tap each, 2-4 options. `dial` names the mood dial the answer corrects or
# confirms (None = no dial). `dynamic` options come from the film's own tags.
VIBE_QUESTIONS: List[Dict[str, Any]] = [
    {"id": "leaves", "text": "How did it leave you? (up to 3)", "dial": "weight", "always": True,
     "multi": True, "max": 3, "exclusive": "nothing",
     "options": [("lifted", "Lifted"), ("warm", "Warm"), ("thoughtful", "Thoughtful"), ("inspired", "Inspired"),
                 ("pumped", "Pumped up"), ("wrecked", "Wrecked"), ("unsettled", "Unsettled"), ("haunted", "Haunted"),
                 ("restless", "Restless"), ("nothing", "Nothing much")]},
    # Id stays "darker" so answers saved earlier keep their meaning.
    {"id": "darker", "text": "Different from what you expected? (up to 3)", "dial": "darkness", "always": True,
     "multi": True, "max": 3, "exclusive": "expected",
     "options": [("darker", "Darker"), ("lighter", "Lighter"), ("funnier", "Funnier"), ("sadder", "Sadder"),
                 ("scarier", "Scarier"), ("slower", "Slower"), ("faster", "Faster"), ("weirder", "Weirder"),
                 ("sweeter", "Sweeter"), ("expected", "About as expected")]},
    {"id": "felt", "text": "Felt like… (pick up to 2)", "dial": None, "multi": True, "dynamic": "feels",
     "options": []},
    {"id": "pace", "text": "The pace?", "dial": "pace",
     "options": [("dragged", "Dragged"), ("right", "Just right"), ("rushed", "Rushed")]},
    {"id": "brain", "text": "How much brain did it need?", "dial": "attention",
     "options": [("half", "Could half-watch"), ("focus", "Full focus"), ("lost", "Lost me")]},
    {"id": "tension", "text": "The tension?", "dial": "tension",
     "options": [("toomuch", "Too much"), ("right", "Just right"), ("wantmore", "Wanted more edge")]},
    {"id": "funny", "text": "Funny?", "dial": "humour",
     "options": [("funnier", "Funnier than expected"), ("expected", "As expected"), ("notreally", "Not really")]},
    {"id": "ending", "text": "The ending?", "dial": "ending_valence",
     "options": [("nailed", "Nailed it"), ("fine", "Fine"), ("ruined", "Ruined it")]},
    {"id": "weird", "text": "Weirdness?", "dial": "strangeness",
     "options": [("tooweird", "Too weird"), ("right", "Just right"), ("ordinary", "Too ordinary")]},
    {"id": "bestfor", "text": "Best for…", "dial": None, "multi": True, "dynamic": "good_for",
     "options": []},
]
_QUESTION_BY_ID = {q["id"]: q for q in VIBE_QUESTIONS}
QUESTIONS_SHOWN = 3
DEFAULT_MAX_PICKS = 2
# A film with no extreme dials still gets something useful to answer.
_BASELINE_EXTREMENESS = {"felt": 0.34, "bestfor": 0.30}
_FALLBACK_FEELS = ["cozy", "tense", "warm", "dreamy", "funny", "haunting"]
_FALLBACK_GOOD_FOR = ["winding down", "date night", "friends over", "a long free evening"]


def validate_verdict(verdict: Optional[str]) -> Optional[str]:
    if verdict is None:
        return None
    if verdict not in VERDICTS:
        raise ValueError(f"invalid verdict: {verdict!r}")
    return verdict


def clean_chips(chips: Optional[Sequence[str]]) -> List[str]:
    """Known chip ids only, in list order, no duplicates."""
    seen: List[str] = []
    for c in chips or []:
        if c in CHIP_IDS and c not in seen:
            seen.append(c)
    return seen


def clean_vibe_check(answers: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Keeps answers that match a known question and option. Multi-pick
    questions keep up to their `max` picks (an "exclusive" option such as
    "Nothing much" drops the others), and an old single string answer becomes a
    one-item list. Unknown ids are dropped, never an error: the question list
    can change between versions."""
    out: Dict[str, Any] = {}
    for qid, answer in (answers or {}).items():
        q = _QUESTION_BY_ID.get(qid)
        if not q:
            continue
        if q.get("multi"):
            raw = answer if isinstance(answer, list) else [answer]
            picks: List[str] = []
            valid = {o[0] for o in q["options"]}
            for a in raw:
                a = str(a) if a else ""
                if not a or a in picks:
                    continue
                if q["options"] and a not in valid:
                    continue
                picks.append(a)
            exclusive = q.get("exclusive")
            if exclusive and exclusive in picks:
                picks = [exclusive] if len(picks) == 1 or picks[-1] == exclusive else [x for x in picks if x != exclusive]
            picks = picks[: int(q.get("max", DEFAULT_MAX_PICKS))]
            if picks:
                out[qid] = picks
        else:
            valid = {o[0] for o in q["options"]}
            if answer in valid:
                out[qid] = answer
    return out


def taste_score(verdict: Optional[str], did_not_finish: bool = False) -> Optional[float]:
    """-1..+1 for a confirmed verdict; None when there is no verdict."""
    if verdict is None:
        return None
    score = float(VERDICTS[verdict]["score"])
    if did_not_finish:
        score = min(score, DID_NOT_FINISH_CAP)
    return round(score, 3)


def legacy_rating(verdict: str, did_not_finish: bool = False) -> float:
    """The 1-10 number kept in watch_history.user_rating for older readers."""
    if did_not_finish:
        return DID_NOT_FINISH_RATING
    return float(VERDICTS[verdict]["rating"])


# --- Inferred verdicts for ratings given before verdicts existed -------------
LOVED_PERCENTILE = 0.85
LIKED_PERCENTILE = 0.25


def percentile_of(rating: float, all_ratings: Sequence[float]) -> float:
    """Mid-rank percentile (0..1) of `rating` in this person's own ratings, so
    ties share the middle of their block rather than the top."""
    n = len(all_ratings)
    if n == 0:
        return 0.5
    below = sum(1 for r in all_ratings if r < rating)
    equal = sum(1 for r in all_ratings if r == rating)
    return (below + equal / 2.0) / n


def infer_verdict(rating: float, all_ratings: Sequence[float]) -> Dict[str, Any]:
    """A provisional verdict for an old 1-10 rating, read against the person's
    own spread. Never guesses `rewatch` or `not_for_me`: only they can say.

    Inside "liked" the score leans on the percentile, so a high 8 sits above a
    low 7 instead of tying.
    """
    pct = percentile_of(rating, all_ratings)
    if pct >= LOVED_PERCENTILE:
        verdict, score = "loved", VERDICTS["loved"]["score"]
    elif pct >= LIKED_PERCENTILE:
        span = LOVED_PERCENTILE - LIKED_PERCENTILE
        score = 0.1 + 0.2 * max(0.0, min(1.0, (pct - LIKED_PERCENTILE) / span))
        verdict = "liked"
    else:
        verdict, score = "meh", VERDICTS["meh"]["score"]
    return {"verdict": verdict, "taste_score": round(float(score), 3), "percentile": round(pct, 3)}


# --- Choosing which vibe-check questions to show for a film -------------------
def pick_vibe_questions(facets: Optional[Dict[str, Any]], vibe_tags: Optional[Dict[str, Any]],
                        shown: int = QUESTIONS_SHOWN) -> Dict[str, Any]:
    """The questions to show up front for one film, and the rest for "More".

    "How did it leave you?" always leads. The other slots go to the questions
    whose dial sits furthest from the middle for this film, since that's where
    a person's reaction is most informative. Feels / good-for options are the
    film's own tags.
    """
    facets = facets or {}
    tags = vibe_tags or {}

    def extremeness(q: Dict[str, Any]) -> float:
        dial = q.get("dial")
        value = facets.get(dial) if dial else None
        if isinstance(value, (int, float)):
            return abs(float(value) - 0.5)
        return _BASELINE_EXTREMENESS.get(q["id"], 0.0)

    def render(q: Dict[str, Any]) -> Dict[str, Any]:
        options = [{"id": o[0], "label": o[1]} for o in q["options"]]
        if q.get("dynamic") == "feels":
            words = [w for w in (tags.get("feels") or []) if isinstance(w, str)][:4]
            options = [{"id": w, "label": w} for w in (words or _FALLBACK_FEELS[:4])]
        elif q.get("dynamic") == "good_for":
            words = [w for w in (tags.get("good_for") or []) if isinstance(w, str)][:4]
            options = [{"id": w, "label": w} for w in (words or _FALLBACK_GOOD_FOR)]
        return {
            "id": q["id"], "text": q["text"], "multi": bool(q.get("multi")),
            "max": int(q.get("max", DEFAULT_MAX_PICKS)), "exclusive": q.get("exclusive"),
            "options": options,
        }

    lead = [q for q in VIBE_QUESTIONS if q["id"] == "leaves"]
    rest = sorted((q for q in VIBE_QUESTIONS if q["id"] != "leaves"), key=extremeness, reverse=True)
    ordered = lead + rest
    return {
        "shown": [render(q) for q in ordered[:shown]],
        "more": [render(q) for q in ordered[shown:]],
    }
