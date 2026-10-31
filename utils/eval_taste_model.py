"""
Offline check of the taste model (utils/eval_taste_model.py)

Free and local: no API calls, no network. Run it on a COPY of the catalogue
(the script only reads the file you point it at):

    uv run python utils/eval_taste_model.py --db catalogue_full.db --vibe-dir models/vibe

1. Leave-one-out over the films a profile rated: for each rated film, build the
   model from all the others and score the held-out one. Reported against the
   current ordering (Bayesian quality + genre/keyword taste bonus built the
   same leave-one-out way), by rank correlation with the real taste_score and
   by where held-out liked films land among random unrated films.
2. Typical Discover requests: top 10 with the taste terms off and on.

Verdicts guessed from old 1-10 ratings (inferred) are derived from the
rating itself, so part of check 1 is circular for them; the report says how
many held-out films were confirmed.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from typing import Any, Dict, List, Sequence, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import DatabaseManager  # noqa: E402
from services import taste_model as tm  # noqa: E402
from services.recommender_service import RecommenderService, calculate_weighted_rating  # noqa: E402
from services.taste_runtime import TasteRuntime  # noqa: E402

REQUESTS: List[Dict[str, Any]] = [
    {"label": "Surprise me (movies, together)", "query": None, "mood_id": 8, "media_type": "movie", "profile": "together"},
    {"label": "cozy and funny", "query": "cozy and funny, something warm", "mood_id": 8, "media_type": "movie", "profile": "together"},
    {"label": "tense thriller", "query": "a tense edge-of-your-seat thriller", "mood_id": 8, "media_type": "movie", "profile": "me"},
    {"label": "slow and thoughtful", "query": "slow, thoughtful, makes you think", "mood_id": 8, "media_type": "movie", "profile": "me"},
    {"label": "light TV for a weeknight", "query": "easy light show to unwind", "mood_id": 8, "media_type": "tv", "profile": "together"},
    {"label": "something like Coco", "query": "something like Coco", "mood_id": 8, "media_type": "movie", "profile": "together"},
]


def ranks(values: Sequence[float]) -> List[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            out[order[k]] = (i + j) / 2.0 + 1
        i = j + 1
    return out


def spearman(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) < 3:
        return float("nan")
    ra, rb = ranks(a), ranks(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** 0.5
    return num / den if den else float("nan")


def auc(pos: Sequence[float], neg: Sequence[float]) -> float:
    if not pos or not neg:
        return float("nan")
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def split(csv: Any) -> List[str]:
    return [x.strip().lower() for x in (csv or "").split(",") if x.strip()]


class GenreTaste:
    """The current genre / keyword taste bonus, built from a list of rows."""

    def __init__(self, rows: Sequence[Dict[str, Any]]):
        self.g: Dict[str, float] = {}
        self.k: Dict[str, float] = {}
        for r in rows:
            if r["taste_score"] > 0:
                w = max(0.1, 0.5 + 0.5 * r["taste_score"])
                for x in split(r.get("genres")):
                    self.g[x] = self.g.get(x, 0.0) + w
                for x in split(r.get("tier2_keywords")):
                    self.k[x] = self.k.get(x, 0.0) + w

    def bonus(self, item: Dict[str, Any]) -> float:
        t = sum(self.g.get(x, 0.0) * 0.2 for x in split(item.get("genres")))
        t += sum(self.k.get(x, 0.0) * 0.15 for x in split(item.get("tier2_keywords")))
        return min(t, 2.0)


def load_rated(db: DatabaseManager, profile_id: int) -> List[Dict[str, Any]]:
    with db.get_connection() as conn:
        rows = conn.execute(
            """SELECT rd.media_item_id AS id, rd.taste_score, rd.inferred, rd.worked, rd.didnt, rd.vibe_check,
                      mi.title, mi.media_type, mi.genres, mi.tier2_keywords, mi.rating, mi.vote_count,
                      mi.facets, mi.vibe_tags, mi.vibe_cluster_id, mi.release_year
               FROM rating_details rd JOIN media_items mi ON mi.id = rd.media_item_id
               WHERE rd.profile_id = ? ORDER BY rd.media_item_id;""",
            (profile_id,),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for k, default in (("worked", []), ("didnt", []), ("vibe_check", {})):
            d[k] = json.loads(d[k]) if d.get(k) else default
        out.append(d)
    return out


def loo(db: DatabaseManager, profile_id: int, cfg: Dict[str, Any], distractors: int, seed: int) -> Dict[str, Any]:
    rated = load_rated(db, profile_id)
    population = TasteRuntime(db, cfg).population()
    c = tm.config_with_defaults(cfg)
    w = c["weights"]
    rng = random.Random(seed)
    rated_ids = {r["id"] for r in rated}
    with db.get_connection() as conn:
        pool = [dict(r) for r in conn.execute(
            "SELECT id, media_type, genres, tier2_keywords, rating, vote_count, facets, vibe_tags, vibe_cluster_id "
            "FROM media_items WHERE media_type='movie' AND rating >= 7.0 AND vote_count >= 3000 AND facets IS NOT NULL;")]
    pool = [p for p in pool if p["id"] not in rated_ids]
    sample = rng.sample(pool, min(distractors, len(pool)))

    rows = []
    for i, held in enumerate(rated):
        rest = rated[:i] + rated[i + 1:]
        model = tm.build_taste_model(rest, cfg, population)
        genre = GenreTaste(rest)

        def scores(item: Dict[str, Any]) -> Dict[str, float]:
            base = calculate_weighted_rating(float(item.get("rating") or 6.0), int(item.get("vote_count") or 100)) + genre.bonus(item)
            s = tm.blend([(1.0, model)], item)
            return {"base": base, "ff": s["facet_fit"], "ca": s["cluster_affinity"],
                    "both": base + w["facet_fit"] * s["facet_fit"] + w["cluster_affinity"] * s["cluster_affinity"],
                    "ff_only": base + w["facet_fit"] * s["facet_fit"],
                    "ca_only": base + w["cluster_affinity"] * s["cluster_affinity"]}

        h = scores(held)
        dist = [scores(d) for d in sample]
        pct = {}
        for key in ("base", "both", "ff_only", "ca_only"):
            below = sum(1 for d in dist if d[key] < h[key]) + 0.5 * sum(1 for d in dist if d[key] == h[key])
            pct[key] = below / len(dist)
        rows.append({"title": held["title"], "taste_score": held["taste_score"], "inferred": held["inferred"],
                     "active": model["active"], **h, **{f"pct_{k}": v for k, v in pct.items()}})
    return {"rows": rows, "n_distractors": len(sample)}


def summarize_loo(res: Dict[str, Any]) -> str:
    rows = [r for r in res["rows"] if r["active"]]
    ts = [r["taste_score"] for r in rows]
    liked = [r for r in rows if r["taste_score"] > 0]
    not_liked = [r for r in rows if r["taste_score"] <= 0]
    conf = [r for r in rows if not r["inferred"]]
    lines = [f"Held-out films scored: {len(rows)} ({len(conf)} confirmed, {len(rows) - len(conf)} inferred); "
             f"{len(liked)} liked, {len(not_liked)} meh or worse; {res['n_distractors']} random unrated films (rated 7+, 3,000+ votes, so quality alone doesn't decide it) as distractors.", ""]
    lines.append("| Ordering | Spearman vs taste_score | AUC liked vs not | Liked film's mean percentile among distractors | Liked in top 10 % |")
    lines.append("|---|---|---|---|---|")
    labels = [("base", "Current (quality + genre taste)", "base"), ("ca_only", "+ cluster_affinity", "ca_only"),
              ("ff_only", "+ facet_fit", "ff_only"), ("both", "+ both", "both")]
    for key, label, pk in labels:
        sp = spearman([r[key] for r in rows], ts)
        a = auc([r[key] for r in liked], [r[key] for r in not_liked])
        pcts = [r[f"pct_{pk}"] for r in liked]
        top = sum(1 for p in pcts if p >= 0.9) / len(pcts) if pcts else float("nan")
        lines.append(f"| {label} | {sp:+.3f} | {a:.3f} | {sum(pcts) / len(pcts):.3f} | {top:.0%} |")
    lines.append("")
    lines.append("The terms alone (no quality or genre bonus), Spearman vs taste_score: "
                 f"facet_fit {spearman([r['ff'] for r in rows], ts):+.3f}, cluster_affinity {spearman([r['ca'] for r in rows], ts):+.3f}.")
    return "\n".join(lines)


def headline(res: Dict[str, Any]) -> Dict[str, float]:
    """The numbers the nightly gate compares (higher is better): the full ordering's rank
    correlation and AUC against the real taste_score, over films the model could score."""
    rows = [r for r in res["rows"] if r["active"]]
    ts = [r["taste_score"] for r in rows]
    liked = [r["both"] for r in rows if r["taste_score"] > 0]
    not_liked = [r["both"] for r in rows if r["taste_score"] <= 0]
    out = {"taste_spearman": spearman([r["both"] for r in rows], ts), "taste_auc": auc(liked, not_liked)}
    return {k: round(v, 4) for k, v in out.items() if v == v}   # drop NaN


def discover(db_path: str, vibe_dir: str, profile_ids: Sequence[int], cfg: Dict[str, Any]) -> str:
    from services.vibe_index import VibeIndex

    db = DatabaseManager(db_path)
    runtime = TasteRuntime(db, {**cfg, "enabled": False})
    service = RecommenderService(db, enable_tier2=False, vibe_index=VibeIndex(vibe_dir), taste_runtime=runtime)
    out: List[str] = []
    for req in REQUESTS:
        kw = dict(mood_id=req["mood_id"], query=req["query"], media_type=req["media_type"], profile=req["profile"],
                  page_size=10, variety=False, exclude_watched=True)
        runtime.cfg["enabled"] = False
        off = service.recommend(**kw)["items"]
        runtime.cfg["enabled"] = True
        on = service.recommend(**kw)["items"]
        off_rank = {i["id"]: n + 1 for n, i in enumerate(off)}
        out.append(f"#### {req['label']} (profile: {req['profile']}, {req['media_type']})\n")
        out.append("| # | Off | On | Why (on) |")
        out.append("|---|---|---|---|")
        for n in range(10):
            a = off[n] if n < len(off) else None
            b = on[n] if n < len(on) else None
            bt = ""
            if b:
                moved = off_rank.get(b["id"])
                bt = f"{b['title']} ({b.get('release_year') or '?'})" + (" **new**" if moved is None else (f" (was {moved})" if moved != n + 1 else ""))
            reasons = "; ".join(b.get("taste_reasons") or []) if b else ""
            out.append(f"| {n + 1} | {a['title'] + ' (' + str(a.get('release_year') or '?') + ')' if a else ''} | {bt} | {reasons} |")
        overlap = len({i['id'] for i in off} & {i['id'] for i in on})
        out.append(f"\nTop-10 overlap: {overlap}/10.\n")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--vibe-dir", default="models/vibe")
    ap.add_argument("--profile-id", type=int, default=1)
    ap.add_argument("--distractors", type=int, default=300)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--skip-discover", action="store_true")
    ap.add_argument("--json", help="also write the headline numbers to this file (used by the nightly job)")
    args = ap.parse_args()

    from services.app_config import load_config
    cfg = (load_config().get("recommender") or {}).get("taste_model") or {}

    db = DatabaseManager(args.db)
    print("## Leave-one-out over rated films\n")
    res = loo(db, args.profile_id, cfg, args.distractors, args.seed)
    print(summarize_loo(res))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(headline(res), f)
    if not args.skip_discover:
        print("\n## Discover requests, taste terms off vs on\n")
        print(discover(args.db, args.vibe_dir, [args.profile_id], cfg))


if __name__ == "__main__":
    main()
