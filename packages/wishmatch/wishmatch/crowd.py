"""
The crowd: items that people with similar taste liked.

The adapter supplies neighbours: for an item, the items its fans also rated highly,
with a similarity from 0 to 1 (built offline, for example from MovieLens). A person's
crowd score for a candidate is the similarity-weighted average of their reactions to
the items it is a neighbour of, shrunk while there is little evidence:

    crowd(c) = sum(reaction(r) x sim(r, c)) / (sum(sim(r, c)) + shrink)

So a candidate close to three films you loved scores high, one close to a film you
disliked scores low, and one close to nothing you rated scores 0.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .types import Reaction


@dataclass
class CrowdScores:
    score: Dict[Any, float] = field(default_factory=dict)      # candidate -> -1..1
    because: Dict[Any, Any] = field(default_factory=dict)      # candidate -> the liked item that pulled it in most

    def get(self, item_id: Any) -> Tuple[float, Optional[Any]]:
        return self.score.get(item_id, 0.0), self.because.get(item_id)


def crowd_scores(reactions: List[Reaction], neighbours: Dict[Any, Dict[Any, float]], shrink: float) -> CrowdScores:
    """One person's crowd score for every item that neighbours something they reacted to."""
    num: Dict[Any, float] = {}
    den: Dict[Any, float] = {}
    best: Dict[Any, Tuple[float, Any]] = {}
    for r in reactions:
        for other, sim in (neighbours.get(r.item_id) or {}).items():
            num[other] = num.get(other, 0.0) + r.score * sim
            den[other] = den.get(other, 0.0) + sim
            pull = r.score * sim
            if pull > 0 and pull > best.get(other, (0.0, None))[0]:
                best[other] = (pull, r.item_id)
    out = CrowdScores()
    for item_id, n in num.items():
        out.score[item_id] = max(-1.0, min(1.0, n / (den[item_id] + shrink)))
        if item_id in best:
            out.because[item_id] = best[item_id][1]
    return out


def extra_candidates(scores: List[CrowdScores], limit: int, min_score: float) -> List[Any]:
    """Items the crowd suggests most strongly for anyone at the screen, best first."""
    best: Dict[Any, float] = {}
    for s in scores:
        for item_id, v in s.score.items():
            if v >= min_score and v > best.get(item_id, -1.0):
                best[item_id] = v
    return [i for i, _ in sorted(best.items(), key=lambda p: -p[1])[:max(0, limit)]]
