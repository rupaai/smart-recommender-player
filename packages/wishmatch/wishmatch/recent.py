"""
A wish made from what people watched (or played, read, ate) lately, for picks with nothing typed.

Each recent item has a weight: how recent it is (halved every `half_life_days`) times how much the
person liked it (1 + reaction, so loved counts about twice, an unrated item once). The wish then holds:

- the traits the liked recent items agree on (a small spread), at their weighted mean value;
- the tags found on at least `min_tag_share` of them (by weight), one phrase each;
- as tags to avoid: tags of items the person disliked lately that the liked ones don't share.

`closest(item, recent)` names the recent item a pick is most like, for "because you watched X".
Settings: the `recent` section (settings.py).
"""

from __future__ import annotations

from math import sqrt
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .types import Item, Wish


def recency_weight(days_ago: float, half_life_days: float) -> float:
    return 0.5 ** (max(0.0, float(days_ago)) / max(0.1, float(half_life_days)))


def wish_from_recent(recent: Sequence[Tuple[Item, float, Optional[float]]], settings: Dict[str, Any]) -> Wish:
    """`recent` is (item, days ago, reaction -1..1 or None). Returns an empty Wish when nothing fits."""
    cfg = settings["recent"]
    liked: List[Tuple[Item, float]] = []
    disliked: List[Item] = []
    for item, days, reaction in recent:
        r = 0.0 if reaction is None else max(-1.0, min(1.0, float(reaction)))
        if reaction is not None and r < float(cfg["dislike_below"]):
            disliked.append(item)
            continue
        liked.append((item, recency_weight(days, cfg["half_life_days"]) * (1.0 + r)))
    liked = [(it, w) for it, w in liked if w > 0]
    if not liked:
        return Wish(text="", source="recent")

    traits: List[Tuple[float, str, float]] = []
    names = {t for it, _ in liked for t in it.traits}
    for name in names:
        vals = [(it.traits[name], w) for it, w in liked if name in it.traits]
        total = sum(w for _, w in vals)
        if len(vals) < max(1, len(liked) // 2) or total <= 0:
            continue
        mean = sum(v * w for v, w in vals) / total
        spread = sqrt(sum(w * (v - mean) ** 2 for v, w in vals) / total)
        if spread <= float(cfg["max_spread"]):
            # Ends of the scale say more than the middle: a strong lean is kept first.
            traits.append((abs(mean - 0.5) - spread, name, mean))
    traits.sort(reverse=True)
    chosen_traits = {name: round(mean, 3) for _, name, mean in traits[: int(cfg["max_traits"])]}

    total = sum(w for _, w in liked)
    share: Dict[str, float] = {}
    for it, w in liked:
        for t in it.tags:
            share[t] = share.get(t, 0.0) + w / total
    tags = [t for t, s in sorted(share.items(), key=lambda kv: (-kv[1], kv[0])) if s >= float(cfg["min_tag_share"])]
    tags = tags[: int(cfg["max_tags"])]
    liked_tags = {t for it, _ in liked for t in it.tags}
    avoid = {t for it in disliked for t in it.tags} - liked_tags
    return Wish(text="", traits=chosen_traits, tags=set(tags), tag_sets=[{t} for t in tags],
                avoid_tags=avoid, words_used=[], source="recent")


def closest(item: Item, recent: Sequence[Item]) -> Optional[Any]:
    """The id of the recent item most like this one (shared traits and tags), or None."""
    best, best_score = None, -1.0
    for r in recent:
        if r.id == item.id:
            continue
        common = set(item.traits) & set(r.traits)
        trait_fit = (sum(1.0 - abs(item.traits[t] - r.traits[t]) for t in common) / len(common)) if common else 0.0
        union = item.tags | r.tags
        tag_fit = len(item.tags & r.tags) / len(union) if union else 0.0
        score = 0.5 * trait_fit + 0.5 * tag_fit
        if score > best_score:
            best, best_score = r.id, score
    return best
