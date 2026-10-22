"""
Wishmatch: read the wish, choose candidates, score them, explain each pick.

    engine = Wishmatch(adapter)
    result = engine.recommend(Request(text="relaxing, make us laugh", people=[1, 2], together=True))
    for pick in result["picks"]:
        print(pick.item.id, pick.score, pick.reasons)

score = weights.match x fit to the wish
      + weights.taste x fit to the people's taste
      + weights.quality x the crowd's opinion
      + weights.crowd x liked by similar people
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import candidates as cand
from .explain import reasons as explain
from .matcher import match
from .profiles import Population, Taste, build_taste, combine
from .settings import merged
from .types import Item, Pick, Request, Wish
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
    def __init__(self, adapter: Any):
        self.adapter = adapter

    def settings(self) -> Dict[str, Any]:
        return merged(self.adapter.rules())

    def read(self, text: str, settings: Optional[Dict[str, Any]] = None) -> Wish:
        settings = settings or self.settings()
        return read_wish(text, self.adapter.vocabulary(), settings["lexicon"])

    def tastes(self, people: List[Any], pop: Population, settings: Dict[str, Any]) -> List[Taste]:
        out = []
        for person in people:
            reactions = self.adapter.reactions(person)
            described = self.adapter.describe([r.item_id for r in reactions]) if reactions else {}
            out.append(build_taste(reactions, described, pop, settings))
        return out

    def recommend(self, request: Request, wish: Optional[Wish] = None) -> Dict[str, Any]:
        """{"picks": [Pick, ...], "wish": Wish, "candidates": {kind: count}}.

        Picks are best first within each kind; with several kinds and a `mix` setting they are
        interleaved so each kind keeps its share (see mix_kinds). The caller slices the list."""
        settings = self.settings()
        vocab = self.adapter.vocabulary()
        wish = wish or read_wish(request.text, vocab, settings["lexicon"])
        pools = cand.choose(self.adapter.items(request), settings, kinds=request.kinds,
                            hidden_gems=wish.hidden_gems, only_ids=request.only_ids is not None)
        items: List[Item] = [it for pool in pools.values() for it in pool]
        if not items:
            return {"picks": [], "wish": wish, "candidates": {}}

        pop = Population.of(items)
        tastes = self.tastes(request.people, pop, settings)
        w = settings["weights"]
        crowd = self.adapter.crowd([it.id for it in items]) if float(w["crowd"]) else {}
        max_reasons = int(settings["explain"]["max_reasons"])
        hidden = {t for g in settings["explain"].get("hide_groups") or [] for t in vocab.tags.get(g, [])}

        picks: List[Pick] = []
        for it in items:
            fit, match_words = match(it, wish, settings, vocab)
            scored = [t.score(it, pop) for t in tastes]
            taste = combine([s for s, _ in scored], request.together, settings)
            taste_words = scored[0][1] if scored and not request.together else \
                [g for g in (scored[0][1] if scored else []) if all(g in words for _, words in scored)]
            score = (float(w["match"]) * fit + float(w["taste"]) * taste
                     + float(w["quality"]) * it.quality + float(w["crowd"]) * float(crowd.get(it.id, 0.0)))
            picks.append(Pick(item=it, score=round(score, 4), match=round(fit, 3), taste=round(taste, 3),
                              reasons=explain([x for x in match_words if x not in hidden],
                                              [x for x in taste_words if x not in hidden], max_reasons)))
        picks.sort(key=lambda p: -p.score)
        if len(pools) > 1 and not request.kinds:
            picks = mix_kinds(picks, settings.get("mix") or {})
        return {"picks": picks, "wish": wish, "candidates": {k: len(v) for k, v in pools.items()}}
