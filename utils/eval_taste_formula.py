"""
Offline check of the taste formula (utils/eval_taste_formula.py)

Compares the old summed taste bonus (capped at +2.0) with the averaged one
(`build_taste_affinity`, recommender.taste_formula: average). Free and local;
it only reads the catalogue you point it at:

    uv run python utils/eval_taste_formula.py --db catalogue_full.db

1. Leave-one-out over each profile's ratings: build the taste from all the
   other ratings, score the held-out film, and report the rank correlation
   with its real taste score and how often a better-liked film outscores a
   worse-liked one.
2. How many distinct taste values the current Discover pool gets (a capped
   formula gives most films the same value, so taste ranks nothing).
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Dict, List, Sequence

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import DatabaseManager  # noqa: E402
from services.recommender_service import (  # noqa: E402
    RecommenderService, _split_lower, averaged_taste, build_taste_affinity,
)


def summed_taste(genres, keywords, rows) -> float:
    """The old formula, built from `rows` the way get_profile_taste_profile does."""
    g_w: Dict[str, float] = {}
    k_w: Dict[str, float] = {}
    for r in rows:
        if r["score"] <= 0:
            continue
        w = max(0.1, 0.5 + 0.5 * r["score"])
        for g in _split_lower(r["genres"]):
            g_w[g] = g_w.get(g, 0.0) + w
        for k in _split_lower(r["tier2_keywords"]):
            k_w[k] = k_w.get(k, 0.0) + w
    total = sum(g_w.get(g, 0.0) * 0.2 for g in genres) + sum(k_w.get(k, 0.0) * 0.15 for k in keywords)
    return min(total, 2.0)


def _ranks(xs: Sequence[float]) -> List[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2.0
        i = j + 1
    return ranks


def spearman(a: Sequence[float], b: Sequence[float]) -> float:
    ra, rb = _ranks(a), _ranks(b)
    n = len(a)
    ma, mb = sum(ra) / n, sum(rb) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va = sum((x - ma) ** 2 for x in ra) ** 0.5
    vb = sum((y - mb) ** 2 for y in rb) ** 0.5
    return cov / (va * vb) if va and vb else 0.0


def pair_accuracy(pred: Sequence[float], truth: Sequence[float]) -> float:
    right = total = 0.0
    for i in range(len(truth)):
        for j in range(len(truth)):
            if truth[i] > truth[j]:
                total += 1
                if pred[i] > pred[j]:
                    right += 1
                elif pred[i] == pred[j]:
                    right += 0.5
    return right / total if total else 0.0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="catalogue_full.db")
    args = ap.parse_args()
    db = DatabaseManager(args.db)

    for pid in sorted(db.get_all_profile_tastes()):
        rows = db.get_profile_taste_rows(pid)
        if len(rows) < 5:
            print(f"Profile {pid}: {len(rows)} ratings, too few to check.")
            continue
        truth, old, new = [], [], []
        for i, r in enumerate(rows):
            others = rows[:i] + rows[i + 1:]
            g, k = set(_split_lower(r["genres"])), set(_split_lower(r["tier2_keywords"]))
            truth.append(r["score"])
            old.append(summed_taste(g, k, others))
            new.append(averaged_taste(g, k, build_taste_affinity(others)))
        print(f"Profile {pid}: {len(rows)} ratings, leave-one-out")
        print(f"  summed (old):   rank corr {spearman(old, truth):+.3f}   pairs right {pair_accuracy(old, truth):.1%}"
              f"   distinct values {len(set(round(x, 3) for x in old))}")
        print(f"  averaged (new): rank corr {spearman(new, truth):+.3f}   pairs right {pair_accuracy(new, truth):.1%}"
              f"   distinct values {len(set(round(x, 3) for x in new))}")
        aff = build_taste_affinity(rows)["genres"]
        top = sorted(aff.items(), key=lambda kv: -kv[1])
        print("  genre affinity: " + ", ".join(f"{g} {a:+.2f}" for g, a in top))

    for formula in ("sum", "average"):
        pool = RecommenderService(db, taste_formula=formula).recommend(
            mood_id=8, profile="me", return_pool=True).get("pool") or []
        values = {round(float(it.get("taste_bonus") or 0.0), 2) for it in pool}
        print(f"Discover pool with taste_formula={formula}: {len(pool)} films, {len(values)} distinct taste values")


if __name__ == "__main__":
    main()
