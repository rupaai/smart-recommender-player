"""
Questions that fit the request: asked only where the top candidates really differ.

After the wish is read and the candidates scored, look at the top `look_at` picks and
find the split that tells them apart best, on something the wish didn't already say:
- a trait: the picks spread widely on it (some very funny, some not at all), so ask
  which end; the answer sets the wanted value near that end;
- a tag group: two or three tags of one group (silly / witty) that each cover a fair
  share of the picks without overlapping much, so ask which; the answer adds that tag.
When the best split is too weak (the picks already agree), ask nothing. Each question
carries example items for each answer, taken from the picks.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Set

from .types import Pick, Vocabulary, Wish, phrase_key

LOW, HIGH = 0.2, 0.8   # the wanted value an answer sets for a trait


def _spread(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))


def trait_question(picks: List[Pick], wish: Wish, vocab: Vocabulary, cfg: Dict[str, Any],
                   skip: Set[str]) -> Optional[Dict[str, Any]]:
    best = None
    min_share = float(cfg["min_share"])
    for trait, ends in vocab.traits.items():
        if trait in wish.traits or f"trait:{trait}" in skip:
            continue
        values = [p.item.traits[trait] for p in picks if trait in p.item.traits]
        if len(values) < 4:
            continue
        low = [v for v in values if v <= 0.35]
        high = [v for v in values if v >= 0.65]
        if min(len(low), len(high)) < min_share * len(values):
            continue
        spread = _spread(values)
        if best is None or spread > best[0]:
            best = (spread, trait, ends)
    if best is None or best[0] < float(cfg["min_spread"]):
        return None
    spread, trait, ends = best
    low_word = str(ends.get("low", "")).split(",")[0].strip()
    high_word = str(ends.get("high", "")).split(",")[0].strip()
    lows = [p for p in picks if p.item.traits.get(trait, 0.5) <= 0.35]
    highs = [p for p in picks if p.item.traits.get(trait, 0.5) >= 0.65]
    return {
        "id": f"trait:{trait}", "kind": "trait", "about": f"{low_word} or {high_word}", "strength": round(spread, 3),
        "options": [
            {"id": "low", "words": [low_word], "set": {"traits": {trait: LOW}}, "examples": [p.item.id for p in lows[:2]]},
            {"id": "high", "words": [high_word], "set": {"traits": {trait: HIGH}}, "examples": [p.item.id for p in highs[:2]]},
        ],
    }


def tag_question(picks: List[Pick], wish: Wish, vocab: Vocabulary, cfg: Dict[str, Any],
                 skip: Set[str]) -> Optional[Dict[str, Any]]:
    """The 2-3 tags of one group that split the picks best: each on a fair share, little overlap."""
    n = len(picks)
    if n < 4:
        return None
    lo, hi = float(cfg["min_share"]), 1.0 - float(cfg["min_share"])
    best = None
    for group in cfg.get("tag_groups") or list(vocab.tags):
        if f"tags:{group}" in skip:
            continue
        cover = {t: {i for i, p in enumerate(picks) if t in p.item.tags} for t in vocab.tags.get(group, [])}
        cover = {t: s for t, s in cover.items()
                 if lo * n <= len(s) <= hi * n and t not in wish.tags and t not in wish.avoid_tags}
        chosen: List[str] = []
        for t in sorted(cover, key=lambda t: -len(cover[t])):
            if all(len(cover[t] & cover[c]) <= 0.34 * min(len(cover[t]), len(cover[c])) for c in chosen):
                chosen.append(t)
            if len(chosen) == 3:
                break
        if len(chosen) < 2:
            continue
        shares = [len(cover[t]) / n for t in chosen]
        strength = -sum(s * math.log(s) for s in shares)          # higher = a more even, wider split
        if best is None or strength > best[0]:
            best = (strength, group, chosen, cover)
    if best is None or best[0] < float(cfg["min_tag_strength"]):
        return None
    strength, group, chosen, cover = best
    return {
        "id": f"tags:{group}", "kind": "tags", "about": " or ".join(chosen), "strength": round(strength, 3),
        "options": [{"id": t, "words": [t], "set": {"tags": [t]},
                     "examples": [picks[i].item.id for i in sorted(cover[t])[:2]]} for t in chosen],
    }


def next_question(picks: List[Pick], wish: Wish, vocab: Vocabulary, settings: Dict[str, Any],
                  asked: Set[str]) -> Optional[Dict[str, Any]]:
    """The best question for these picks, or None when they already agree or the limit is reached."""
    cfg = settings["questions"]
    if len(asked) >= int(cfg["max"]):
        return None
    top = picks[:int(cfg["look_at"])]
    options = [q for q in (trait_question(top, wish, vocab, cfg, asked), tag_question(top, wish, vocab, cfg, asked)) if q]
    if not options:
        return None
    # A trait split and a tag split are measured differently; prefer the trait unless its spread is small.
    trait = next((q for q in options if q["kind"] == "trait"), None)
    if trait and trait["strength"] >= float(cfg["prefer_trait_spread"]):
        return trait
    return next((q for q in options if q["kind"] == "tags"), trait)


def apply_answer(wish: Wish, question: Dict[str, Any], option_id: str, weight: float = 0.5) -> Wish:
    """The wish with this answer added ("skip" changes nothing). An answer counts `weight` as much
    as a typed word, so it narrows the picks without drowning out what was asked for."""
    if option_id == "skip":
        return wish
    opt = next((o for o in question["options"] if o["id"] == option_id), None)
    if opt is None:
        raise ValueError("Unknown option.")
    out = Wish(text=wish.text, traits=dict(wish.traits), tags=set(wish.tags), tag_sets=[set(s) for s in wish.tag_sets],
               avoid_tags=set(wish.avoid_tags), words_used=list(wish.words_used),
               words_unknown=list(wish.words_unknown), hidden_gems=wish.hidden_gems, source=wish.source,
               weights=dict(wish.weights))
    for trait, value in (opt["set"].get("traits") or {}).items():
        out.traits[trait] = value
        out.weights[trait] = weight
    for t in opt["set"].get("tags") or []:
        out.tags.add(t)
        out.tag_sets.append({t})
        out.weights[phrase_key({t})] = weight
    out.words_used.append(f"answer: {option_id}")
    return out
