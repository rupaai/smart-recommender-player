"""
How well one item fits a wish, from 0 (not at all) to 1 (exactly).

- Each trait the wish names scores 1 when the item sits on the wanted value and
  falls to 0 once it is `trait_reach` away (0.6 on the 0..1 scale).
- Each phrase the wish names ("cozy", "make me laugh") scores 0.6 when the item has one
  of the phrase's tags, rising to 1 when it has them all.
- Each tag the wish avoids takes points off when the item has it.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from .types import Item, Vocabulary, Wish, phrase_key

TRAIT_REACH = 0.6
PHRASE_MET = 0.6      # a phrase with one of its tags scores this; with all of them, 1


def match(item: Item, wish: Wish, settings: Dict, vocab: Vocabulary) -> Tuple[float, List[str]]:
    """0..1, and the reason words for the fit (tags first, then trait ends)."""
    if wish.is_empty():
        return 0.0, []
    w = settings["weights"]
    parts: List[Tuple[float, float]] = []
    reasons: List[str] = []

    if wish.traits:
        fits = []
        for trait, target in wish.traits.items():
            wt = wish.weights.get(trait, 1.0)
            x = item.traits.get(trait)
            if x is None:
                fits.append((wt, 0.5))
                continue
            fit = max(0.0, 1.0 - abs(x - target) / TRAIT_REACH)
            fits.append((wt, fit))
            if fit >= 0.75:
                ends = vocab.traits.get(trait) or {}
                word = ends.get("high") if target >= 0.5 else ends.get("low")
                if word:
                    reasons.append(str(word).split(",")[0].strip())
        total = sum(wt for wt, _ in fits)
        parts.append((float(w["trait"]) * max(wt for wt, _ in fits), sum(wt * f for wt, f in fits) / total))

    if wish.tags:
        # Each understood phrase counts once: "christmas" (wintry, winter, cozy) is met by any
        # of its tags, so a phrase with many tags doesn't outweigh "cozy" on its own.
        sets = wish.tag_sets or [{t} for t in wish.tags]
        met = []
        for ts in sets:
            have = len(ts & item.tags)
            wt = wish.weights.get(phrase_key(ts), 1.0)
            met.append((wt, PHRASE_MET + (1 - PHRASE_MET) * have / len(ts) if have else 0.0))
        hits = [t for t in sorted(wish.tags) if t in item.tags]
        total = sum(wt for wt, _ in met)
        parts.append((float(w["tag"]) * max(wt for wt, _ in met), sum(wt * m for wt, m in met) / total))
        reasons = hits + reasons

    score = sum(wt * v for wt, v in parts) / sum(wt for wt, _ in parts) if parts else 0.0
    if wish.avoid_tags:
        bad = len(wish.avoid_tags & item.tags) / len(wish.avoid_tags)
        score -= float(w["avoid_tag"]) * bad
    return max(0.0, min(1.0, score)), reasons
