"""
Taste profiles built from reactions.

A person's taste has two parts, both measured against the candidates as a whole
(the "population"):
- traits: which way their liked items lean on each scale (more humour, less gore),
  minus the way their disliked items lean;
- tags: tags they pick more often than the population, and tags on the items they
  rated above their own average.
Both shrink towards zero while there are few reactions.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

from .types import Item, Reaction


@dataclass
class Population:
    """Trait means and tag frequencies of the candidates."""
    trait_mean: Dict[str, float] = field(default_factory=dict)
    tag_freq: Dict[str, float] = field(default_factory=dict)

    @classmethod
    def of(cls, items: Iterable[Item]) -> "Population":
        sums: Dict[str, float] = {}
        counts: Dict[str, int] = {}
        tags: Dict[str, int] = {}
        n = 0
        for it in items:
            n += 1
            for t, v in it.traits.items():
                sums[t] = sums.get(t, 0.0) + v
                counts[t] = counts.get(t, 0) + 1
            for g in it.tags:
                tags[g] = tags.get(g, 0) + 1
        return cls({t: sums[t] / counts[t] for t in sums}, {g: c / n for g, c in tags.items()} if n else {})


@dataclass
class Taste:
    traits: Dict[str, float] = field(default_factory=dict)   # trait -> lean (-1..1 after shrink)
    tags: Dict[str, float] = field(default_factory=dict)     # tag -> affinity (-1..1)
    count: int = 0
    trait_weight: float = 0.5

    def score(self, item: Item, pop: Population) -> Tuple[float, List[str]]:
        """-1..1 for how well the item fits, and the liked tags it carries (best first)."""
        if not self.count and not self.tags:
            return 0.0, []
        dot = norm_d = norm_x = 0.0
        for t, d in self.traits.items():
            x = item.traits.get(t)
            if x is None:
                continue
            dx = x - pop.trait_mean.get(t, 0.5)
            dot += d * dx
            norm_d += d * d
            norm_x += dx * dx
        trait_part = dot / (math.sqrt(norm_d * norm_x) + 1e-9) if norm_d and norm_x else 0.0
        trait_part *= min(1.0, math.sqrt(norm_d) * 4)   # a faint lean counts less than a clear one
        known = [(g, self.tags[g]) for g in item.tags if g in self.tags]
        tag_part = max(-1.0, min(1.0, 2.0 * sum(a for _, a in known) / len(known))) if known else 0.0
        w = self.trait_weight
        reasons = [g for g, a in sorted(known, key=lambda p: -p[1]) if a > 0.05]
        return max(-1.0, min(1.0, w * trait_part + (1 - w) * tag_part)), reasons


def _declare(taste: Taste, declared: Optional[Dict], cfg: Dict) -> Taste:
    """Lay the person's written likes / dislikes over the tag affinities (a like adds, a dislike subtracts)."""
    w = float(cfg.get("declared_weight", 0.0))
    if not declared or not w:
        return taste
    for g in declared.get("likes") or []:
        taste.tags[g] = max(-1.0, min(1.0, taste.tags.get(g, 0.0) + w))
    for g in declared.get("dislikes") or []:
        taste.tags[g] = max(-1.0, min(1.0, taste.tags.get(g, 0.0) - w))
    return taste


def build_taste(reactions: List[Reaction], described: Dict, pop: Population, settings: Dict,
                declared: Optional[Dict] = None) -> Taste:
    """A person's taste from their reactions and the described items they reacted to.

    `declared` ({"likes": [tags], "dislikes": [tags]}) is what the person (or a written profile) says they
    like and avoid; it moves those tags by settings["taste"]["declared_weight"], even with no reactions."""
    cfg = settings["taste"]
    pairs = [(r.score, described[r.item_id]) for r in reactions if r.item_id in described]
    if not pairs:
        return _declare(Taste(trait_weight=float(cfg["trait_weight"])), declared, cfg)
    n = len(pairs)
    shrink = n / (n + float(cfg["shrink"]))
    mean_s = sum(s for s, _ in pairs) / n

    lean: Dict[str, float] = {}
    for s, it in pairs:
        for t, x in it.traits.items():
            lean[t] = lean.get(t, 0.0) + s * (x - pop.trait_mean.get(t, 0.5))
    traits = {t: shrink * v / n for t, v in lean.items()}

    tag_scores: Dict[str, List[float]] = {}
    for s, it in pairs:
        for g in it.tags:
            tag_scores.setdefault(g, []).append(s)
    k = float(cfg["tag_shrink"])
    tags: Dict[str, float] = {}
    for g, scores in tag_scores.items():
        c = len(scores)
        above_average = (sum(scores) / c - mean_s) * c / (c + k)
        picked_more = (c / n - pop.tag_freq.get(g, 0.0)) * (1.0 if sum(scores) >= 0 else -1.0)
        tags[g] = max(-1.0, min(1.0, shrink * (above_average + picked_more)))
    return _declare(Taste(traits=traits, tags=tags, count=n, trait_weight=float(cfg["trait_weight"])), declared, cfg)


def combine(scores: List[float], together: bool, settings: Dict) -> float:
    """One taste score for the people at the screen. scores[0] is the viewer.

    Alone: the viewer's taste plus the others' at `other_weight`.
    Together: everyone equally, pushed down by how much the least keen person dislikes it."""
    if not scores:
        return 0.0
    cfg = settings["people"]
    if together:
        avg = sum(scores) / len(scores)
        return avg - float(cfg["together_dislike"]) * max(0.0, -min(scores))
    w = float(cfg["other_weight"])
    others = scores[1:]
    if not others:
        return scores[0]
    return (scores[0] + w * sum(others) / len(others)) / (1.0 + w)
