"""
Wishmatch: read the wish, choose candidates, score them, explain each pick.

    engine = Wishmatch(adapter)
    result = engine.recommend(Request(text="relaxing, make us laugh", people=[1, 2], together=True))
    for pick in result["picks"]:
        print(pick.item.id, pick.score, pick.reasons)

score = weights.match x fit to the wish
      + weights.taste x fit to the people's taste
      + weights.quality x the crowd's opinion
      + weights.crowd x liked by fans of what the people liked (crowd.py)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import candidates as cand
from .explain import reasons as explain
from .crowd import CrowdScores, crowd_scores, extra_candidates
from .matcher import match
from .profiles import Population, Taste, build_taste, combine
from .settings import merged
from .types import Item, Pick, Reaction, Request, Wish
from .wish import read_wish


def mix_kinds(ranked: List[Pick], shares: Dict[str, float]) -> List[Pick]:
    """Reorder so each kind keeps roughly its share of every stretch of the list.

    Each next slot goes to the kind furthest below its share so far (ties: the
    better score). Kinds without a share only fill in when the others run out."""
    by_kind: Dict[str, List[Pick]] = {}
    for p in ranked:
        by_kind.setdefault(p.item.kind, []).append(p)
    active = {k: float(v) for k, v in shares.items() if k in by_kind and float(v) > 0}
    if len(active) < 2:
        return ranked
    norm = sum(active.values())
    active = {k: v / norm for k, v in active.items()}
    out: List[Pick] = []
    taken = {k: 0 for k in by_kind}
    while any(taken[k] < len(by_kind[k]) for k in active):
        n = len(out) + 1
        open_kinds = [k for k in active if taken[k] < len(by_kind[k])]
        k = max(open_kinds, key=lambda k: (active[k] * n - taken[k], by_kind[k][taken[k]].score))
        out.append(by_kind[k][taken[k]])
        taken[k] += 1
    rest = [p for k, ps in by_kind.items() for p in ps[taken[k]:]]
    return out + sorted(rest, key=lambda p: -p.score)


class Wishmatch:
    def __init__(self, adapter: Any, reader: Any = None):
        """reader: an optional model-based wish reader (llm.LLMWishReader); without one, or when it
        fails, wishes are read with the plain word lookup."""
        self.adapter = adapter
        self.reader = reader

    def settings(self) -> Dict[str, Any]:
        return merged(self.adapter.rules())

    def read(self, text: str, settings: Optional[Dict[str, Any]] = None) -> Wish:
        settings = settings or self.settings()
        vocab = self.adapter.vocabulary()
        lookup = read_wish(text, vocab, settings["lexicon"])
        if self.reader is None or not (text or "").strip():
            return lookup
        try:
            return self.reader.read(text, vocab, lookup)
        except Exception as e:  # noqa: BLE001 - the model is optional; the lookup always answers
            print(f"[Wishmatch] Model wish reader failed ({e}); using the word lookup.", flush=True)
            return lookup

    def tastes(self, people: List[Any], pop: Population, settings: Dict[str, Any],
               reactions: Optional[Dict[Any, List[Reaction]]] = None) -> List[Taste]:
        out = []
        for person in people:
            person_reactions = (reactions or {}).get(person)
            if person_reactions is None:
                person_reactions = self.adapter.reactions(person)
            described = self.adapter.describe([r.item_id for r in person_reactions]) if person_reactions else {}
            declare = getattr(self.adapter, "profile", None)
            declared = declare(person) if callable(declare) else None
            out.append(build_taste(person_reactions, described, pop, settings, declared))
        return out

    def crowd(self, people: List[Any], reactions: Dict[Any, List[Reaction]], settings: Dict[str, Any]) -> List[CrowdScores]:
        """Each person's crowd scores (empty when the adapter has no crowd data or the weight is 0)."""
        if not float(settings["weights"]["crowd"]):
            return []
        reacted = sorted({r.item_id for p in people for r in reactions.get(p, [])}, key=str)
        neighbours = self.adapter.crowd(reacted) if reacted else {}
        if not neighbours:
            return []
        shrink = float(settings["crowd"]["shrink"])
        return [crowd_scores(reactions.get(p, []), neighbours, shrink) for p in people]

    def recommend(self, request: Request, wish: Optional[Wish] = None) -> Dict[str, Any]:
        """{"picks": [Pick, ...], "wish": Wish, "candidates": {kind: count}}.

        Picks are best first within each kind; with several kinds and a `mix` setting they are
        interleaved so each kind keeps its share (see mix_kinds). The caller slices the list."""
        settings = self.settings()
        vocab = self.adapter.vocabulary()
        wish = wish or self.read(request.text, settings)
        only = request.only_ids is not None
        available = list(self.adapter.items(request))
        pools = cand.choose(available, settings, kinds=request.kinds, hidden_gems=wish.hidden_gems, only_ids=only)

        reactions = {p: self.adapter.reactions(p) for p in request.people}
        # Together: something one of you has already seen is pushed down, not removed
        # (the adapter removes what everyone has seen, and what the viewer has seen when alone).
        seen: Dict[Any, int] = {}
        if request.together and len(request.people) > 1 and hasattr(self.adapter, "seen_by"):
            for person in request.people:
                for item_id in self.adapter.seen_by(person) or ():
                    seen[item_id] = seen.get(item_id, 0) + 1
        seen_penalty = float(settings["people"].get("seen_penalty", 0.0))
        crowds = self.crowd(request.people, reactions, settings)
        crowd_added = 0
        if crowds and not only:
            # The crowd's strongest suggestions join the pool even when they missed the popularity cut.
            cfg = settings["crowd"]
            chosen = {it.id for pool in pools.values() for it in pool}
            known = set(settings["candidates"]["hidden_gem_known" if wish.hidden_gems else "known"])
            by_id = {it.id: it for it in available}
            for item_id in extra_candidates(crowds, int(cfg["extra_candidates"]), float(cfg["min_score"])):
                it = by_id.get(item_id)
                if it is None or item_id in chosen or it.known not in known:
                    continue
                if request.kinds and it.kind not in request.kinds:
                    continue
                pools.setdefault(it.kind, []).append(it)
                chosen.add(item_id)
                crowd_added += 1

        items: List[Item] = [it for pool in pools.values() for it in pool]
        if not items:
            return {"picks": [], "wish": wish, "candidates": {}, "crowd_added": 0}

        pop = Population.of(items)
        tastes = self.tastes(request.people, pop, settings, reactions)
        w = settings["weights"]
        max_reasons = int(settings["explain"]["max_reasons"])
        hidden = {t for g in settings["explain"].get("hide_groups") or [] for t in vocab.tags.get(g, [])}

        picks: List[Pick] = []
        for it in items:
            fit, match_words = match(it, wish, settings, vocab)
            scored = [t.score(it, pop) for t in tastes]
            taste = combine([s for s, _ in scored], request.together, settings)
            taste_words = scored[0][1] if scored and not request.together else \
                [g for g in (scored[0][1] if scored else []) if all(g in words for _, words in scored)]
            crowd_parts = [c.get(it.id) for c in crowds]
            crowd = combine([v for v, _ in crowd_parts], request.together, settings) if crowd_parts else 0.0
            because = crowd_parts[0][1] if crowd_parts and crowd > 0 else None
            score = (float(w["match"]) * fit + float(w["taste"]) * taste
                     + float(w["quality"]) * it.quality + float(w["crowd"]) * crowd)
            if it.id in seen:
                score -= seen_penalty * seen[it.id] / len(request.people)
            picks.append(Pick(item=it, score=round(score, 4), match=round(fit, 3), taste=round(taste, 3),
                              crowd=round(crowd, 3), because=because,
                              reasons=explain([x for x in match_words if x not in hidden],
                                              [x for x in taste_words if x not in hidden], max_reasons)))
        picks.sort(key=lambda p: -p.score)
        if len(pools) > 1 and not request.kinds:
            picks = mix_kinds(picks, settings.get("mix") or {})
        return {"picks": picks, "wish": wish, "candidates": {k: len(v) for k, v in pools.items()},
                "crowd_added": crowd_added}
