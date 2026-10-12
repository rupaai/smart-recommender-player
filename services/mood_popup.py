"""
Mood session (services/mood_popup.py)

The mood-map engine from the vibe-questions plan (T2). MoodSession has the same
public interface as vibe_popup.PopupSession (next_question, public_question,
answer, should_stop, ranked, scored, log_fields, done, current, id, request ...)
so server.py can swap it in later (T4). Not wired in yet.

belief  {mood id: P(mood)} over vibe_moods.MOODS, starting from `prior` (even by
        default; the hook for "occasions" later).
answer  P(mood) *= 0.05 + likelihood(option, mood), renormalised. The 0.05 floor
        means no mood ever reaches zero: answers deprioritise, never remove.
next    A3 first; then the eligible question with the largest expected drop in
        entropy of the belief (ties go to bank order, so it is deterministic).
stop    top mood >= 0.55, or top two >= 0.80, or 6 answers; never before 2.
rank    pool order for now; T3 adds ranking by mood mix.
"""

from __future__ import annotations

import json
import math
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from services import mood_questions, vibe_moods
from services.mood_questions import BANK, BY_ID, likelihood
from services.vibe_popup import POOL_TOP, features

MAX_QUESTIONS = 6
MIN_ANSWERS = 2
FLOOR = 0.05
GROUP_MIN = 0.15        # a tier 2 question needs its group to hold this much of the belief
TIE_GAP = 0.1           # tier 3 needs the top two moods this close
STOP_TOP = 0.55
STOP_TOP_TWO = 0.80
MOOD_IDS: List[str] = [m["id"] for m in vibe_moods.MOODS]


def new_belief(prior: Optional[Dict[str, float]] = None) -> Dict[str, float]:
    """Even belief over the moods, or `prior` (missing moods count 0.0 then floored; renormalised)."""
    if prior:
        raw = {m: max(float(prior.get(m, 0.0)), 1e-6) for m in MOOD_IDS}
    else:
        raw = {m: 1.0 for m in MOOD_IDS}
    total = sum(raw.values())
    return {m: v / total for m, v in raw.items()}


def update(belief: Dict[str, float], option: Dict[str, Any]) -> Dict[str, float]:
    """The belief after one answer: P(mood) *= FLOOR + likelihood, renormalised."""
    raw = {m: p * (FLOOR + likelihood(option, m)) for m, p in belief.items()}
    total = sum(raw.values())
    return {m: v / total for m, v in raw.items()}


def entropy(belief: Dict[str, float]) -> float:
    return -sum(p * math.log(p) for p in belief.values() if p > 0)


def group_mass(belief: Dict[str, float], group: str) -> float:
    return sum(belief.get(m, 0.0) * share for m, share in vibe_moods.GROUPS[group].items())


def expected_gain(belief: Dict[str, float], question: Dict[str, Any]) -> float:
    """Expected drop in entropy if this question is asked now.

    P(option) = sum over moods of P(mood) x the option's share of that mood's
    likelihood across the options; the belief after the option uses the real
    update rule."""
    opts = question["options"]
    lik = {m: [FLOOR + likelihood(o, m) for o in opts] for m in belief}
    h_now = entropy(belief)
    expected = 0.0
    for i, o in enumerate(opts):
        p_opt = sum(p * lik[m][i] / sum(lik[m]) for m, p in belief.items())
        if p_opt <= 0:
            continue
        expected += p_opt * entropy(update(belief, o))
    return h_now - expected


class MoodSession:
    def __init__(self, session_id: int, request: Dict[str, Any], pool: List[Dict[str, Any]],
                 cluster_labels: Optional[Dict[int, str]] = None, vibe_index: Any = None,
                 prior: Optional[Dict[str, float]] = None):
        self.id = session_id
        self.request = request
        self.cluster_labels = cluster_labels or {}
        self.vibe_index = vibe_index
        self.prior = prior
        self.belief = new_belief(prior)
        self.asked: List[Tuple[str, str]] = []         # (question id, option id | "skip")
        self.current: Optional[Dict[str, Any]] = None
        self.done = False
        self.updated = time.time()
        self.set_pool(pool)

    # -- pool -----------------------------------------------------------------
    def set_pool(self, pool: List[Dict[str, Any]]) -> None:
        for it in pool:
            it.setdefault("_popup_base", float(it.get("recommendation_score") or 0.0))
            features(it)
        self.pool = pool[:POOL_TOP] if len(pool) > POOL_TOP else pool

    def scored(self, belief: Optional[Dict[str, float]] = None) -> List[Tuple[float, Dict[str, Any]]]:
        """(score, item), best first. For now the pool's own score (T3 adds the mood mix)."""
        out = [(float(it.get("_popup_base") or 0.0), it) for it in self.pool]
        out.sort(key=lambda p: -p[0])
        return out

    def ranked(self) -> List[Dict[str, Any]]:
        """The pool, ready for finalize(). Same shape as PopupSession.ranked()."""
        out = []
        for s, it in self.scored():
            copy_it = {k: v for k, v in it.items() if not k.startswith("_popup")}
            copy_it["recommendation_score"] = round(s, 2)
            copy_it["popup_boost"] = round(s - float(it.get("_popup_base") or 0.0), 2)
            out.append(copy_it)
        return out

    # -- belief ---------------------------------------------------------------
    @property
    def asked_ids(self) -> Set[str]:
        return {q for q, _ in self.asked}

    @property
    def answered_count(self) -> int:
        return len(self.asked)

    def top_moods(self, n: int = 3) -> List[Tuple[str, float]]:
        """The n most likely moods as (id, p), best first (ties in mood list order)."""
        order = {m: i for i, m in enumerate(MOOD_IDS)}
        return sorted(self.belief.items(), key=lambda kv: (-kv[1], order[kv[0]]))[:n]

    def group_mass(self, group: str) -> float:
        return group_mass(self.belief, group)

    # -- questions ------------------------------------------------------------
    def _eligible(self) -> List[Dict[str, Any]]:
        asked = self.asked_ids
        if "A3" not in asked:
            return [BY_ID["A3"]]
        top = self.top_moods(2)
        close = top[0][1] - top[1][1] <= TIE_GAP
        out = []
        for q in BANK:
            if q["id"] in asked or q["tier"] == 0:
                continue
            if q["tier"] == 2 and self.group_mass(q["group"]) < GROUP_MIN:
                continue
            if q["tier"] == 3 and not close:
                continue
            out.append(q)
        return out

    def next_question(self, force: bool = False) -> Optional[Dict[str, Any]]:
        """The most useful next question, or None when done, at 6 answers, or nothing is eligible.
        `force` is accepted for PopupSession compatibility; the opener always comes first anyway."""
        if self.done or self.answered_count >= MAX_QUESTIONS:
            self.current = None
            return None
        best: Optional[Tuple[float, Dict[str, Any]]] = None
        for q in self._eligible():
            gain = round(expected_gain(self.belief, q), 12)
            if best is None or gain > best[0]:
                best = (gain, q)
        if best is None:
            self.current = None
            return None
        q = best[1]
        self.current = {"id": q["id"], "text": q["text"], "tier": q["tier"],
                        "options": [dict(o) for o in q["options"]]}
        return self.current

    def public_question(self) -> Optional[Dict[str, Any]]:
        """The current question as the UI sees it (examples are empty for now)."""
        if not self.current:
            return None
        opts = [{"id": o["id"], "emoji": o["emoji"], "label": o["label"], "examples": []}
                for o in self.current["options"]]
        return {"id": self.current["id"], "text": self.current["text"], "options": opts,
                "step": self.answered_count + 1, "max_steps": MAX_QUESTIONS}

    def answer(self, question_id: str, option_id: str) -> None:
        if not self.current or self.current["id"] != question_id:
            raise ValueError("That question isn't the one being asked.")
        if option_id != "skip":
            opt = next((o for o in self.current["options"] if o["id"] == option_id), None)
            if opt is None:
                raise ValueError("Unknown option.")
            self.belief = update(self.belief, opt)
        self.asked.append((question_id, option_id))
        self.current = None
        self.updated = time.time()

    def should_stop(self) -> bool:
        if self.done or self.answered_count >= MAX_QUESTIONS:
            return True
        if self.answered_count < MIN_ANSWERS:
            return False
        top = self.top_moods(2)
        return top[0][1] >= STOP_TOP or top[0][1] + top[1][1] >= STOP_TOP_TWO

    def log_fields(self) -> Dict[str, Any]:
        answers = {q: a for q, a in self.asked}
        belief = {m: round(p, 6) for m, p in self.belief.items()}
        return {"answers_json": json.dumps(answers), "asked_order": json.dumps([q for q, _ in self.asked]),
                "belief_json": json.dumps(belief)}


# Sanity checks run at import so a bad edit to either bank fails loudly.
vibe_moods.validate()
mood_questions.validate()
