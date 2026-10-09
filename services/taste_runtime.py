"""
Taste runtime (services/taste_runtime.py)

The database side of services/taste_model.py: loads each profile's rated
films, caches the built models until a rating changes, and adds facet_fit and
cluster_affinity to a scored list. Both engines call `apply`.

Everything is off unless `recommender.taste_model.enabled` is true in
config.yaml, and a profile with fewer than `min_verdicts` verdicts adds
nothing, so the ranking falls back to what it did before.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from services import taste_model as tm


class TasteRuntime:
    def __init__(self, db: Any, cfg: Optional[Dict[str, Any]] = None):
        self.db = db
        self.cfg = tm.config_with_defaults(cfg)
        self._models: Dict[int, Dict[str, Any]] = {}
        self._signature: Optional[Tuple[Any, ...]] = None
        self._labels: Optional[Dict[int, str]] = None
        self._population: Optional[Dict[str, Tuple[float, float]]] = None

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.get("enabled"))

    @property
    def weights(self) -> Dict[str, float]:
        return self.cfg["weights"]

    # -- Loading ---------------------------------------------------------------
    def rated_rows(self) -> Dict[int, List[Dict[str, Any]]]:
        """Rated records per profile id, ready for tm.build_taste_model."""
        out: Dict[int, List[Dict[str, Any]]] = {}
        with self.db.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT rd.profile_id, rd.media_item_id, rd.taste_score, rd.inferred,
                       rd.worked, rd.didnt, rd.vibe_check,
                       mi.facets, mi.vibe_tags, mi.vibe_cluster_id
                FROM rating_details rd
                JOIN media_items mi ON mi.id = rd.media_item_id;
                """
            ).fetchall()
        for r in rows:
            rec = dict(r)
            for k in ("worked", "didnt"):
                rec[k] = _json(rec.get(k), [])
            rec["vibe_check"] = _json(rec.get("vibe_check"), {})
            out.setdefault(int(rec["profile_id"]), []).append(rec)
        return out

    def _current_signature(self) -> Tuple[Any, ...]:
        try:
            with self.db.get_connection() as conn:
                row = conn.execute(
                    "SELECT COUNT(*), COALESCE(MAX(updated_at), ''), COALESCE(SUM(taste_score), 0), "
                    "(SELECT COUNT(*) FROM media_items WHERE facets IS NOT NULL) FROM rating_details;"
                ).fetchone()
            return tuple(row)
        except Exception:  # noqa: BLE001  (older schema: no rating_details)
            return ("none",)

    def population(self) -> Dict[str, Tuple[float, float]]:
        """Mean and std of each dial over the acclaimed pool the recommender
        ranks (rated 6.5+, 1,000+ votes), so a dial only gets weight when my
        liked films differ from what is usually on offer."""
        if self._population is None:
            try:
                with self.db.get_connection() as conn:
                    rows = conn.execute(
                        "SELECT facets, vibe_tags FROM media_items WHERE facets IS NOT NULL "
                        "AND vote_count >= 1000 AND rating >= 6.5;"
                    ).fetchall()
                self._population = tm.population_stats(tm.item_features(dict(r))["dials"] for r in rows)
            except Exception:  # noqa: BLE001
                self._population = {}
        return self._population

    def models(self) -> Dict[int, Dict[str, Any]]:
        sig = self._current_signature()
        if sig != self._signature:
            try:
                rated = self.rated_rows() if sig != ("none",) else {}
            except Exception as e:  # noqa: BLE001
                print(f"[Taste] could not load verdicts ({e}); taste terms off.", flush=True)
                rated = {}
            self._models = {pid: tm.build_taste_model(rows, self.cfg, self.population()) for pid, rows in rated.items()}
            self._signature = sig
        return self._models

    def cluster_labels(self) -> Dict[int, str]:
        if self._labels is None:
            try:
                with self.db.get_connection() as conn:
                    self._labels = {r[0]: r[1] for r in conn.execute("SELECT id, label FROM vibe_clusters")}
            except Exception:  # noqa: BLE001
                self._labels = {}
        return self._labels

    # -- Scoring -----------------------------------------------------------------
    def mix_for(self, profile: str) -> List[Tuple[float, int]]:
        """[(weight, profile id)] in the same seats as the genre taste bonus."""
        from services.recommender_service import PROFILE_MIX

        ids = sorted(self.models().keys() | self._profile_ids())
        if not ids:
            return []
        me, partner = ids[0], (ids[1] if len(ids) > 1 else ids[0])
        own, other = (partner, me) if profile == "partner" else (me, partner)
        own_w, other_w = PROFILE_MIX.get(profile, PROFILE_MIX["together"])
        return [(own_w, own)] if own == other else [(own_w, own), (other_w, other)]

    def _profile_ids(self) -> set:
        try:
            with self.db.get_connection() as conn:
                return {r[0] for r in conn.execute("SELECT id FROM profiles")}
        except Exception:  # noqa: BLE001
            return set()

    def score(self, item: Dict[str, Any], profile: str) -> Optional[Dict[str, Any]]:
        models = self.models()
        mix = [(w, models[pid]) for w, pid in self.mix_for(profile) if pid in models]
        if not any(m["active"] for _, m in mix):
            return None
        return tm.blend(mix, item)

    def apply(self, ranked: List[Dict[str, Any]], profile: str = "together") -> List[Dict[str, Any]]:
        """Adds weight x term to recommendation_score and re-sorts. Items get
        facet_fit, cluster_affinity and taste_reasons (badge texts). A no-op
        when disabled or below the evidence gate."""
        if not self.enabled or not ranked:
            return ranked
        try:
            models = self.models()
            mix = [(w, models[pid]) for w, pid in self.mix_for(profile) if pid in models]
            if not any(m["active"] for _, m in mix):
                return ranked
            labels = self.cluster_labels()
            w = self.weights
            for it in ranked:
                s = tm.blend(mix, it)
                it["facet_fit"] = s["facet_fit"]
                it["cluster_affinity"] = s["cluster_affinity"]
                bump = w.get("facet_fit", 0.0) * s["facet_fit"] + w.get("cluster_affinity", 0.0) * s["cluster_affinity"]
                it["recommendation_score"] = round(float(it.get("recommendation_score") or 0.0) + bump, 2)
                it["taste_reasons"] = tm.reasons(
                    s, w, _short_label(labels.get(s.get("cluster_id"))), min_share=self.cfg["reason_min_share"], min_fit=self.cfg["reason_min_fit"])
            ranked.sort(key=lambda it: it.get("recommendation_score") or 0.0, reverse=True)
        except Exception as e:  # noqa: BLE001  (never break a recommendation)
            print(f"[Taste] scoring failed ({e}); using the older ranking.", flush=True)
        return ranked


def _short_label(label: Optional[str]) -> Optional[str]:
    """Cluster labels are 'a, b, c' keyword lists; the badge uses the first three."""
    if not label:
        return None
    return ", ".join(p.strip() for p in label.split(",")[:3] if p.strip()) or None


def _json(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default
