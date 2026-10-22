"""
Choosing the candidates: the best-known items, in a separate pool for each kind.

One shared pool would let the bigger kind crowd the smaller one out (5,802 well-known
films against 1,292 well-known shows), so each kind gets its own share of the total.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional

from .types import Item


def pool_sizes(kinds: List[str], total: int, shares: Dict[str, float]) -> Dict[str, int]:
    """How many candidates each kind gets. One kind takes the whole total."""
    if len(kinds) == 1:
        return {kinds[0]: total}
    weights = {k: float(shares.get(k, 0.0)) for k in kinds}
    if sum(weights.values()) <= 0:
        weights = {k: 1.0 for k in kinds}
    norm = sum(weights.values())
    return {k: int(round(total * w / norm)) for k, w in weights.items()}


def choose(items: Iterable[Item], settings: Dict, kinds: Optional[List[str]] = None,
           hidden_gems: bool = False, only_ids: bool = False) -> Dict[str, List[Item]]:
    """kind -> candidates, best known first.

    only_ids: the caller already limited the items (a library shelf), so nothing is left out."""
    cfg = settings["candidates"]
    known = set(cfg["hidden_gem_known"] if hidden_gems else cfg["known"])
    min_pop = cfg.get("min_popularity") or {}

    by_kind: Dict[str, List[Item]] = {}
    for item in items:
        if kinds and item.kind not in kinds:
            continue
        if not only_ids:
            if item.known not in known:
                continue
            if item.popularity < float(min_pop.get(item.known, 0) or 0):
                continue
        by_kind.setdefault(item.kind, []).append(item)
    for pool in by_kind.values():
        pool.sort(key=lambda it: it.popularity, reverse=True)
    if only_ids:
        return by_kind

    wanted = kinds or list(cfg.get("kinds") or {}) or list(by_kind)
    sizes = pool_sizes([k for k in wanted if k in by_kind] or list(by_kind), int(cfg["total"]), cfg.get("kinds") or {})
    out = {k: by_kind[k][:n] for k, n in sizes.items()}
    # A kind with fewer items than its share leaves room: give it to the others.
    spare = sum(n - len(out[k]) for k, n in sizes.items())
    for k in sorted(sizes, key=lambda k: -sizes[k]):
        if spare <= 0:
            break
        extra = by_kind[k][len(out[k]):len(out[k]) + spare]
        out[k] = out[k] + extra
        spare -= len(extra)
    return out
