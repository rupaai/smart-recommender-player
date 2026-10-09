"""
"This or that" pairs (services/pairs.py)

An optional Rate-tab round: two films this person has seen, from the same
genre, and they pick which is more their thing. A verdict says "I like
thrillers"; a pair says which flavour of thriller. Design: docs/RECOMMENDER_V2_DESIGN.md §5
("this one over that one").

Pure functions only, no database access, so the tests need no fixtures.
"""

from __future__ import annotations

import math
import random
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from services import verdicts as vd

ROUND_SIZE = 10
# Films whose taste scores are further apart than this teach nothing
# (a loved film beats a disliked one every time).
MAX_SCORE_GAP = 0.3
# Largest change a film's pair score can make to its verdict score.
MAX_NUDGE = 0.15
# Pull toward zero: a film's log-strength is fitted with this prior, so one
# comparison moves it only a little.
PRIOR_STRENGTH = 1.0
# How many mood dials to remember per answer.
TOP_DIALS = 3
# A nudge never lands closer than this to the neighbouring verdict's score.
BOUNDARY_MARGIN = 0.01


def pair_key(a: int, b: int) -> frozenset:
    return frozenset((int(a), int(b)))


def split_genres(genres: Any) -> List[str]:
    if not genres:
        return []
    if isinstance(genres, (list, tuple)):
        parts = [str(g) for g in genres]
    else:
        parts = str(genres).split(",")
    seen, out = set(), []
    for g in parts:
        g = g.strip()
        if g and g.lower() not in seen:
            seen.add(g.lower())
            out.append(g)
    return out


# --- Picking ------------------------------------------------------------------

def shared_genre(a: Dict[str, Any], b: Dict[str, Any]) -> Optional[str]:
    """The first genre of `a` that `b` also has (a's own order, so the main genre wins)."""
    theirs = {g.lower() for g in split_genres(b.get("genres"))}
    for g in split_genres(a.get("genres")):
        if g.lower() in theirs:
            return g
    return None


def pick_pairs(
    films: Sequence[Dict[str, Any]],
    answered: Iterable[Tuple[int, int]] = (),
    limit: int = ROUND_SIZE,
    rng: Optional[random.Random] = None,
) -> List[Dict[str, Any]]:
    """Choose up to `limit` informative pairs.

    Each film is a dict with `id`, `genres`, `taste_score`, optionally
    `vibe_cluster_id` and `comparisons` (how many pair answers it has so far).
    A pair needs a shared genre, taste scores within MAX_SCORE_GAP, and must
    not have been answered. Pairs from the same vibe cluster rank first, then
    pairs whose films have the fewest comparisons. A film appears once per round.
    Returns [{"a": film, "b": film, "shared_genre": str, "same_cluster": bool}].
    """
    rng = rng or random.Random()
    done: Set[frozenset] = {pair_key(a, b) for a, b in answered}
    usable = [f for f in films if f.get("taste_score") is not None and split_genres(f.get("genres"))]

    candidates = []
    for i, a in enumerate(usable):
        for b in usable[i + 1:]:
            if a["id"] == b["id"] or pair_key(a["id"], b["id"]) in done:
                continue
            if abs(a["taste_score"] - b["taste_score"]) > MAX_SCORE_GAP + 1e-9:
                continue
            genre = shared_genre(a, b)
            if genre is None:
                continue
            ca, cb = a.get("vibe_cluster_id"), b.get("vibe_cluster_id")
            same_cluster = ca is not None and ca == cb
            seen_count = int(a.get("comparisons") or 0) + int(b.get("comparisons") or 0)
            candidates.append((0 if same_cluster else 1, seen_count, rng.random(), a, b, genre, same_cluster))

    candidates.sort(key=lambda c: c[:3])
    used: Set[int] = set()
    out: List[Dict[str, Any]] = []
    for _, _, _, a, b, genre, same_cluster in candidates:
        if len(out) >= limit:
            break
        if a["id"] in used or b["id"] in used:
            continue
        used.update((a["id"], b["id"]))
        # Left/right order is random so the first card is not always the same kind.
        left, right = (a, b) if rng.random() < 0.5 else (b, a)
        out.append({"a": left, "b": right, "shared_genre": genre, "same_cluster": same_cluster})
    return out


# --- Dial differences ---------------------------------------------------------

def dial_differences(winner_facets: Optional[Dict[str, Any]], loser_facets: Optional[Dict[str, Any]],
                     top: int = TOP_DIALS) -> List[Dict[str, Any]]:
    """The mood dials that differed most between winner and loser, biggest first.

    `diff` is winner minus loser, so a positive `diff` on "humour" means the
    winner was funnier. Dials missing from either film are skipped.
    """
    w = {k: float(v) for k, v in (winner_facets or {}).items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
    l = {k: float(v) for k, v in (loser_facets or {}).items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
    rows = [{"dial": k, "winner": round(w[k], 3), "loser": round(l[k], 3), "diff": round(w[k] - l[k], 3)}
            for k in w if k in l]
    rows.sort(key=lambda r: (-abs(r["diff"]), r["dial"]))
    return rows[:top]


# --- Scoring ------------------------------------------------------------------

def pair_scores(answers: Iterable[Tuple[int, int]], iterations: int = 200) -> Dict[int, float]:
    """Bradley-Terry log-strength per film from (winner_id, loser_id) answers.

    "About the same" answers are left out by the caller (they count for
    neither). A zero-mean Gaussian prior (PRIOR_STRENGTH) is fitted along with
    the data, so a film with one comparison scores close to zero and a film
    with many wins moves further. Returns {film_id: strength}, 0 = average.
    """
    games = [(int(w), int(l)) for w, l in answers if int(w) != int(l)]
    if not games:
        return {}
    theta: Dict[int, float] = {}
    for w, l in games:
        theta.setdefault(w, 0.0)
        theta.setdefault(l, 0.0)
    for _ in range(iterations):
        grad = {k: -PRIOR_STRENGTH * v for k, v in theta.items()}
        hess = {k: PRIOR_STRENGTH for k in theta}
        for w, l in games:
            p = 1.0 / (1.0 + math.exp(-(theta[w] - theta[l])))
            grad[w] += 1.0 - p
            grad[l] -= 1.0 - p
            h = p * (1.0 - p)
            hess[w] += h
            hess[l] += h
        step = 0.0
        for k in theta:
            d = grad[k] / hess[k]
            theta[k] += d
            step = max(step, abs(d))
        if step < 1e-7:
            break
    return {k: round(v, 6) for k, v in theta.items()}


def nudge_for(strength: float) -> float:
    """A film's pair strength as a small taste nudge, -MAX_NUDGE..+MAX_NUDGE."""
    return round(MAX_NUDGE * math.tanh(strength), 6)


def _verdict_bounds(base: float) -> Tuple[float, float]:
    """The nearest verdict scores below and above `base`; a nudge stays strictly between."""
    scores = sorted({v["score"] for v in vd.VERDICTS.values()})
    lower = max([s for s in scores if s < base - 1e-9], default=-1.0)
    upper = min([s for s in scores if s > base + 1e-9], default=1.0)
    return lower, upper


def apply_nudge(verdict_score: float, nudge: float) -> float:
    """Verdict score plus nudge, capped at +/-MAX_NUDGE and never past the
    neighbouring verdicts: a "Liked it" stays between Meh and Loved, so pairs
    sort ties inside a verdict and never override it. The extremes stay in -1..+1."""
    nudge = max(-MAX_NUDGE, min(MAX_NUDGE, nudge))
    lower, upper = _verdict_bounds(verdict_score)
    lo = -1.0 if verdict_score <= -1.0 else lower + BOUNDARY_MARGIN
    hi = 1.0 if verdict_score >= 1.0 else upper - BOUNDARY_MARGIN
    return round(max(lo, min(hi, verdict_score + nudge)), 6)


def nudges_from_answers(answers: Iterable[Tuple[int, int]]) -> Dict[int, float]:
    return {k: nudge_for(v) for k, v in pair_scores(answers).items()}
