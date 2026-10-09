"""
Generative Retrieval Engine — TIGER / Semantic IDs (services/generative_retriever.py)

A parallel recommendation engine implementing the TIGER architecture
("Recommender Systems with Generative Retrieval", arXiv:2305.05065):

    Catalog text -> dense embeddings -> RQ-VAE semantic IDs
        -> sequence predictor (ONNX INT8) -> prefix trie lookup -> item IDs

It runs BESIDE the existing RecommenderService (which remains untouched) and
is selected through the `recommender.engine_mode` feature flag in config.yaml:

    "fallback"        generative first; legacy if artifacts/history missing
    "legacy_only"     existing recommender always (kill-switch)
    "generative_only" generative primary; legacy only on unexpected failure

Runtime artifacts (deployed under models/tiger/ — see training/README.md):
    predictor_int8.onnx   INT8-quantized sequence predictor (<15MB, <5ms CPU)
    semantic_ids.json     item_id -> [c1, c2, c3(, collision_leaf)]
    prefix_trie.json      nested-dict trie: semantic ID tuple -> item ids
    item_map.json         item_id -> catalog text used for embedding
    config.json           predictor hyper-parameters (dims, seq len, ...)

The legacy engine stays the automatic safety net: every failure path here
(artifact missing, onnxruntime absent, short history, empty trie hits)
raises/falls through to the existing SQL + taste-scoring pipeline.
"""

from __future__ import annotations

import json
import os
import random
import re
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from database import DatabaseManager
from services.recommender_service import (  # noqa: E402  (shared scoring helpers)
    _split_lower,
    apply_series_order,
    calculate_weighted_rating,
    parse_origin_intent,
    parse_query_intent,
    TV_GENRE_ALIASES,
)

# ===========================================================================
# Constants & paths
# ===========================================================================
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_ARTIFACT_DIR = os.path.join(_PROJECT_ROOT, "models", "tiger")

NUM_LEVELS = 3          # predicted codebook levels (the collision leaf is metadata)
CODEBOOK_SIZE = 256     # K per level

ARTIFACT_FILES = {
    "model": "predictor_int8.onnx",
    "semantic_ids": "semantic_ids.json",
    "trie": "prefix_trie.json",
    "item_map": "item_map.json",
    "config": "config.json",
}


class InsufficientHistoryError(RuntimeError):
    """Raised when the user's history cannot feed the generative engine.

    The router treats this as a signal to fall back to the legacy engine.
    """


# ===========================================================================
# JSON / trie helpers
# ===========================================================================
def _load_json(path: str) -> Optional[Any]:
    """Loads a JSON artifact, returning None on any failure."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def load_prefix_trie(semantic_ids: Dict[str, Any]) -> Dict[str, Any]:
    """Builds the static prefix trie mapping semantic ID tuples to item IDs.

    Keyed on the 3 *predicted* codebook levels. Items that collided on the
    same tuple (disambiguated by a collision leaf token offline) all live in
    the same leaf list, so a runtime lookup can never hallucinate an item.
    Accepts either the full semantic_ids.json document
    ({"items": {...}}) or a bare {item_id: codes} mapping.
    """
    trie: Dict[str, Any] = {}
    if not isinstance(semantic_ids, dict):
        return trie
    items = semantic_ids.get("items", semantic_ids)
    for item_id, codes in items.items():
        node = trie
        for code in list(codes)[:NUM_LEVELS]:
            node = node.setdefault(str(int(code)), {})
        leaf = node.setdefault("__items__", [])
        leaf.append(int(item_id))
    return trie


def trie_lookup(trie: Dict[str, Any], codes: Sequence[int]) -> List[int]:
    """Resolves a semantic ID tuple to concrete item IDs via the prefix trie.

    Returns [] for any tuple absent from the trie (0% hallucinated lookups).
    """
    node: Any = trie
    for code in list(codes)[:NUM_LEVELS]:
        if not isinstance(node, dict):
            return []
        node = node.get(str(int(code)))
        if node is None:
            return []
    if isinstance(node, dict):
        items = node.get("__items__", [])
        return [int(i) for i in items]
    return []


# ===========================================================================
# ONNX inference helpers
# ===========================================================================
def build_onnx_session(model_path: str):
    """Creates a CPU onnxruntime session (2 threads — Raspberry Pi friendly)."""
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    return ort.InferenceSession(model_path, options, providers=["CPUExecutionProvider"])


def _softmax(logits: np.ndarray) -> np.ndarray:
    e = np.exp(logits - np.max(logits))
    return e / e.sum()


def beam_decode_top_tuples(
    logits: np.ndarray,
    top_k: int = 10,
    beam_width: int = 64,
    candidates_per_level: int = 64,
) -> List[Tuple[Tuple[int, int, int], float]]:
    """Beam search over the 3 codebook logits -> top-K semantic ID tuples.

    logits: [NUM_LEVELS, CODEBOOK_SIZE] raw logits for the predicted next item.
    Returns [((c1, c2, c3), probability), ...] sorted by probability desc.
    """
    log_probs = [np.log(_softmax(np.asarray(logits[level], dtype=np.float64)) + 1e-12) for level in range(NUM_LEVELS)]
    beam: List[Tuple[Tuple[int, ...], float]] = [((), 0.0)]
    for level in range(NUM_LEVELS):
        top_idx = np.argsort(log_probs[level])[-candidates_per_level:][::-1]
        expanded: List[Tuple[Tuple[int, ...], float]] = []
        for prefix, score in beam:
            for code in top_idx:
                expanded.append((prefix + (int(code),), score + float(log_probs[level][int(code)])))
        expanded.sort(key=lambda x: x[1], reverse=True)
        beam = expanded[:beam_width]
    if not beam:
        return []
    # Convert joint log-probs to relative probabilities in [0, 1]
    best = beam[0][1]
    results: List[Tuple[Tuple[int, int, int], float]] = []
    for prefix, score in beam[:top_k]:
        rel = float(np.exp(score - best)) if score < best else 1.0
        results.append((tuple(prefix), min(1.0, max(0.0, rel))))
    return results


# ===========================================================================
# Generative Retriever (TIGER runtime engine)
# ===========================================================================
class GenerativeRetriever:
    """ONNX-backed generative retrieval engine (parallel to RecommenderService).

    Fully fault-tolerant at load time: missing artifacts or a missing
    onnxruntime install simply leave the engine non-ready and the router
    serves the legacy recommender instead.
    """

    def __init__(self, db_manager: DatabaseManager, config: Optional[Dict[str, Any]] = None,
                 taste_runtime: Optional[Any] = None):
        self.taste_runtime = taste_runtime   # services.taste_runtime.TasteRuntime, optional
        self.db = db_manager
        cfg = (config or {}).get("recommender", {}) or {}
        gen_cfg = cfg.get("generative", {}) or {}
        self.engine_mode = str(cfg.get("engine_mode", "fallback")).strip().lower()
        self.history_threshold = int(gen_cfg.get("history_threshold", 3))
        self.top_k = int(gen_cfg.get("top_k", 10))
        self.max_seq_len = int(gen_cfg.get("max_seq_len", 10))
        self.candidate_pool = int(gen_cfg.get("candidate_pool", 60))
        # Ratings below this are not "liked" and never enter the model's history.
        self.history_min_rating = float(gen_cfg.get("history_min_rating", 6.0))
        self.artifact_dir = (
            os.environ.get("TIGER_MODEL_DIR")
            or gen_cfg.get("model_dir")
            or DEFAULT_ARTIFACT_DIR
        )
        # Lazily-populated artifacts
        self._loaded = False
        self._session: Any = None
        self._input_names: List[str] = []
        self._semantic_ids: Dict[str, List[int]] = {}
        self._trie: Dict[str, Any] = {}
        self._predictor_cfg: Dict[str, Any] = {}

    # -- artifact loading ---------------------------------------------------
    def is_ready(self) -> bool:
        """True when all artifacts are present and the ONNX session loads."""
        try:
            return self._ensure_loaded()
        except Exception as e:
            print(f"[GenerativeRetriever] readiness check failed: {e}", flush=True)
            return False

    def _ensure_loaded(self) -> bool:
        if self._loaded:
            return self._session is not None
        self._loaded = True
        model_path = os.path.join(self.artifact_dir, ARTIFACT_FILES["model"])
        semantic = _load_json(os.path.join(self.artifact_dir, ARTIFACT_FILES["semantic_ids"]))
        trie = _load_json(os.path.join(self.artifact_dir, ARTIFACT_FILES["trie"]))
        pcfg = _load_json(os.path.join(self.artifact_dir, ARTIFACT_FILES["config"])) or {}
        if semantic is None or trie is None or not os.path.exists(model_path):
            print(
                f"[GenerativeRetriever] artifacts missing in {self.artifact_dir} — engine disabled.",
                flush=True,
            )
            return False
        self._semantic_ids = semantic.get("items", {}) if isinstance(semantic, dict) else {}
        self._trie = trie
        self._predictor_cfg = pcfg
        # Trust the predictor config's sequence length if present
        self.max_seq_len = int(pcfg.get("max_seq_len", self.max_seq_len))
        self._session = build_onnx_session(model_path)
        self._input_names = [i.name for i in self._session.get_inputs()]
        self._validate_against_db()
        print(
            f"[GenerativeRetriever] loaded TIGER artifacts "
            f"({len(self._semantic_ids)} semantic IDs) from {self.artifact_dir}",
            flush=True,
        )
        return True

    def _validate_against_db(self) -> None:
        """Sanity-check that artifact item IDs overlap the plugged database.

        Artifacts and the active catalogue must be built from the same DB
        (see training/README.md re-training cadence). A mismatch is not fatal
        — the router simply falls back — but it is loudly logged.
        """
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id FROM media_items;")
                db_ids = {int(r[0]) for r in cursor.fetchall()}
            artifact_ids = {int(k) for k in self._semantic_ids}
            overlap = len(db_ids & artifact_ids)
            if db_ids and overlap == 0:
                print(
                    "[GenerativeRetriever] ⚠️  WARNING: 0% item-ID overlap between "
                    "TIGER artifacts and the active database — the artifacts were "
                    "built from a different catalogue.db. Rebuild artifacts "
                    "(training/README.md) or set DATABASE_PATH to the matching DB; "
                    "the router will serve the legacy recommender meanwhile.",
                    flush=True,
                )
            elif db_ids:
                coverage = overlap / max(len(artifact_ids), 1)
                print(
                    f"[GenerativeRetriever] artifact/DB coverage: {overlap} of "
                    f"{len(artifact_ids)} semantic IDs present in the active DB "
                    f"({coverage:.1%})",
                    flush=True,
                )
        except Exception as e:
            print(f"[GenerativeRetriever] DB sanity check skipped ({e})", flush=True)



    # -- history / sequence -------------------------------------------------
    def _profile_ids(self) -> Tuple[int, int]:
        """(primary, other) profile ids — same rule as the legacy engine
        (lowest id is "me"), instead of hard-coding 1 and 2."""
        ids: List[int] = []
        if hasattr(self.db, "get_profiles"):
            try:
                ids = sorted(int(p["id"]) for p in self.db.get_profiles())
            except Exception:
                ids = []
        if not ids:
            return 1, 2
        return ids[0], (ids[1] if len(ids) > 1 else ids[0])

    def get_history_item_ids(self, limit: Optional[int] = None, profile: str = "together") -> List[int]:
        """The films this request's viewer(s) watched AND liked, newest
        `limit`, oldest-first (the order the sequence model expects).

        Previously this read the OLDEST 100 rows of watch_history, so nothing
        rated after the first 100 entries ever reached the model; it also fed
        "we haven't seen it" answers in as watches and treated a 3/10 like a
        10/10, mixing both people into one sequence.
        """
        n = int(limit or max(self.max_seq_len * 3, 30))
        if hasattr(self.db, "get_recent_liked_history"):
            primary, other = self._profile_ids()
            if profile == "me":
                pids = [primary]
            elif profile == "partner":
                pids = [other]
            else:
                pids = sorted({primary, other})
            return self.db.get_recent_liked_history(
                pids, limit=n, min_rating=self.history_min_rating
            )
        # Legacy/test databases without the method.
        history = self.db.get_watch_history_chronological(limit=n)
        ids: List[int] = []
        for row in history:
            item_id = int(row.get("media_item_id") or 0)
            if item_id and (not ids or ids[-1] != item_id):
                ids.append(item_id)
        return ids

    def history_to_semantic_sequence(self, item_ids: Sequence[int]) -> List[List[int]]:
        """Converts watched item IDs to their semantic ID triplets ([c1, c2, c3])."""
        seq: List[List[int]] = []
        for item_id in item_ids:
            codes = self._semantic_ids.get(str(int(item_id)))
            if codes:
                seq.append([int(c) for c in list(codes)[:NUM_LEVELS]])
        return seq

    # -- inference ----------------------------------------------------------
    def predict_top_tuples(self, seq: List[List[int]]) -> List[Tuple[Tuple[int, int, int], float]]:
        """Runs the INT8 ONNX predictor and beam-decodes the top-K next tuples."""
        if not seq or self._session is None:
            return []
        tokens = np.zeros((1, self.max_seq_len, NUM_LEVELS), dtype=np.int64)
        mask = np.zeros((1, self.max_seq_len), dtype=np.float32)
        trimmed = seq[-self.max_seq_len:]
        for pos, codes in enumerate(trimmed):
            for level in range(NUM_LEVELS):
                tokens[0, pos, level] = int(codes[level]) % CODEBOOK_SIZE
            mask[0, pos] = 1.0
        feeds = {self._input_names[0]: tokens, self._input_names[1]: mask}
        output = self._session.run(None, feeds)[0]
        last_pos = len(trimmed) - 1
        last_logits = np.asarray(output)[0, last_pos]  # [NUM_LEVELS, CODEBOOK_SIZE]
        return beam_decode_top_tuples(last_logits, top_k=self.top_k)

    # -- main entry (mirrors RecommenderService.recommend) -------------------
    def recommend(
        self,
        mood_id: int = 8,
        exclude_watched: bool = True,
        era_preference: Optional[str] = None,
        duration_preference: Optional[str] = None,
        page: int = 1,
        page_size: int = 10,
        sub_mood: Optional[str] = None,
        query: Optional[str] = None,
        media_type: str = "movie",
        genres_override: Optional[List[str]] = None,
        min_rating_override: Optional[float] = None,
        variety: bool = False,
        profile: str = "together",
    ) -> Dict[str, Any]:
        """Generative retrieval pipeline producing the *unified* legacy payload shape.

        Raises InsufficientHistoryError whenever the request should be routed
        to the legacy engine instead (short history / no artifacts / no hits).
        """
        if not self._ensure_loaded():
            raise InsufficientHistoryError("TIGER artifacts not available")

        item_ids = self.get_history_item_ids(profile=profile)
        seq = self.history_to_semantic_sequence(item_ids)
        if len(seq) < self.history_threshold:
            raise InsufficientHistoryError(
                f"usable history ({len(seq)}) below threshold ({self.history_threshold})"
            )
        seq = seq[-self.max_seq_len:]

        scored_tuples = self.predict_top_tuples(seq)
        if not scored_tuples:
            raise InsufficientHistoryError("predictor produced no semantic ID tuples")

        rows = self.db.get_candidates(
            media_type=media_type, genres=None, exclude_watched=exclude_watched, min_rating=0.0
        )
        by_id = {int(r["id"]): r for r in rows}

        # Profile-aware taste. This engine previously read only the blended
        # single-profile taste, so "just me" and "his picks" produced identical
        # results here even after the legacy engine was fixed. Mirror the mix
        # the legacy path uses.
        from services.recommender_service import PROFILE_MIX
        me_id, partner_id = self._profile_ids()
        if profile == "partner":
            primary_id, other_id = partner_id, me_id
            own_w, other_w = PROFILE_MIX["partner"]
        elif profile == "me":
            primary_id, other_id = me_id, partner_id
            own_w, other_w = PROFILE_MIX["me"]
        else:
            primary_id, other_id = me_id, partner_id
            own_w, other_w = PROFILE_MIX["together"]
        all_tastes = self.db.get_all_profile_tastes()
        taste = dict(all_tastes.get(primary_id) or {})
        other_taste = all_tastes.get(other_id) or {}
        preferred_genres = {
            g: (own_w * (taste.get("preferred_genres", {}).get(g, 0.0))
                + other_w * (other_taste.get("preferred_genres", {}).get(g, 0.0)))
            for g in set(taste.get("preferred_genres", {}))
            | set(other_taste.get("preferred_genres", {}))
        }
        preferred_keywords = {
            k: (own_w * (taste.get("preferred_keywords", {}).get(k, 0.0))
                + other_w * (other_taste.get("preferred_keywords", {}).get(k, 0.0)))
            for k in set(taste.get("preferred_keywords", {}))
            | set(other_taste.get("preferred_keywords", {}))
        }
        request_genres = {g.strip().lower() for g in (genres_override or []) if g}
        if media_type == "tv":
            expanded = set(request_genres)
            for g in request_genres:
                expanded.update(x.lower() for x in TV_GENRE_ALIASES.get(g, []))
            request_genres = expanded
        query_words = {w for w in re.findall(r"\w+", (query or "").lower()) if len(w) > 3}

        # Genre list for SQL candidate backfill (TV aliases included, legacy-style)
        genre_query_list = [g for g in (genres_override or []) if g]
        if media_type == "tv":
            for g in list(genre_query_list):
                genre_query_list.extend(TV_GENRE_ALIASES.get(g.strip().lower(), []))

        # Trie resolution — unknown tuples resolve to nothing (no hallucinations)
        best: Dict[int, Tuple[float, Dict[str, Any]]] = {}
        top_prob = max((p for _, p in scored_tuples), default=1e-9) or 1e-9
        for rank, (codes, prob) in enumerate(scored_tuples):
            for item_id in trie_lookup(self._trie, codes):
                item = by_id.get(int(item_id))
                if item is None:
                    continue
                raw_rating = float(item.get("rating") or 6.0)
                num_votes = int(item.get("num_votes") or item.get("vote_count") or 100)
                base_score = calculate_weighted_rating(raw_rating, num_votes)

                taste_bonus = 0.0
                for g in _split_lower(item.get("genres")):
                    if g in preferred_genres:
                        taste_bonus += preferred_genres[g] * 0.2
                for kw in _split_lower(item.get("tier2_keywords")):
                    if kw in preferred_keywords:
                        taste_bonus += preferred_keywords[kw] * 0.15
                taste_bonus = min(taste_bonus, 2.0)

                # Strong genre affinity for the requested mood (hard filter applied post-ranking)
                genre_boost = 0.0
                if request_genres and request_genres.intersection(_split_lower(item.get("genres"))):
                    genre_boost = 1.5

                # Free-text query relevance (word matches in title/overview/keywords)
                query_boost = 0.0
                if query_words:
                    item_text = f"{item.get('title')} {item.get('overview')} {item.get('tier2_keywords')}".lower()
                    query_boost = min(sum(1 for w in query_words if w in item_text) * 0.4, 2.0)

                model_boost = 2.0 * (prob / top_prob)
                final_score = base_score + taste_bonus + genre_boost + query_boost + model_boost

                item["recommendation_score"] = round(final_score, 2)
                item["base_weighted_rating"] = round(base_score, 2)
                item["taste_bonus"] = round(taste_bonus, 2)
                item["query_boost"] = round(query_boost, 2)
                item["relevance_boost"] = round(model_boost, 2)
                item["semantic_id"] = list(codes)
                item["generative_rank"] = rank

                prev = best.get(int(item_id))
                if prev is None or final_score > prev[0]:
                    best[int(item_id)] = (final_score, item)
                if len(best) >= self.candidate_pool:
                    break
            if len(best) >= self.candidate_pool:
                break

        if not best:
            raise InsufficientHistoryError("no trie hits for the predicted semantic IDs")

        ranked = sorted(best.values(), key=lambda x: x[0], reverse=True)
        ranked_items = [item for _, item in ranked]

        # Mood/genre intent: the predictor pool is often small, so when a mood
        # (or genre chips) narrows the request, backfill it with the best
        # genre-matched catalogue items — scored identically minus the
        # sequence-model boost — so the mood genuinely changes the results.
        if request_genres and genre_query_list:
            backfill_rows = self.db.get_candidates(
                media_type=media_type,
                genres=genre_query_list,
                exclude_watched=exclude_watched,
                min_rating=0.0,
            )
            seen_ids = set(best.keys())
            backfill: List[Tuple[float, Dict[str, Any]]] = []
            for r in backfill_rows:
                if int(r["id"]) in seen_ids:
                    continue
                item = dict(r)
                raw_rating = float(item.get("rating") or 6.0)
                num_votes = int(item.get("num_votes") or item.get("vote_count") or 100)
                base_score = calculate_weighted_rating(raw_rating, num_votes)

                taste_bonus = 0.0
                for g in _split_lower(item.get("genres")):
                    if g in preferred_genres:
                        taste_bonus += preferred_genres[g] * 0.2
                for kw in _split_lower(item.get("tier2_keywords")):
                    if kw in preferred_keywords:
                        taste_bonus += preferred_keywords[kw] * 0.15
                taste_bonus = min(taste_bonus, 2.0)

                query_boost = 0.0
                if query_words:
                    item_text = f"{item.get('title')} {item.get('overview')} {item.get('tier2_keywords')}".lower()
                    query_boost = min(sum(1 for w in query_words if w in item_text) * 0.4, 2.0)

                final_score = base_score + taste_bonus + 1.5 + query_boost
                item["recommendation_score"] = round(final_score, 2)
                item["base_weighted_rating"] = round(base_score, 2)
                item["taste_bonus"] = round(taste_bonus, 2)
                item["query_boost"] = round(query_boost, 2)
                item["relevance_boost"] = 0.0
                backfill.append((final_score, item))

            backfill.sort(key=lambda x: x[0], reverse=True)
            ranked_items = ranked_items + [item for _, item in backfill[: self.candidate_pool]]
            ranked_items.sort(key=lambda it: it.get("recommendation_score", 0.0), reverse=True)

        # Learned taste (facet_fit, cluster_affinity); a no-op when off or too thin.
        if self.taste_runtime is not None:
            ranked_items = self.taste_runtime.apply(ranked_items, profile)

        # Genre hard-filter when the filtered pool stays useful.
        if request_genres:
            genre_matched = [
                it for it in ranked_items
                if request_genres.intersection(_split_lower(it.get("genres")))
            ]
            if len(genre_matched) >= max(page_size * 2, 6):
                ranked_items = genre_matched

        # Era & runtime preferences (same semantics as the legacy engine)
        if era_preference == "modern":
            ranked_items = [it for it in ranked_items if (it.get("release_year") or 0) >= 2015] or ranked_items
        elif era_preference == "2000s":
            ranked_items = [it for it in ranked_items if 2000 <= (it.get("release_year") or 0) < 2015] or ranked_items
        elif era_preference == "classic":
            ranked_items = [it for it in ranked_items if (it.get("release_year") or 0) < 2000] or ranked_items

        if media_type == "movie" and duration_preference:
            if duration_preference == "short":
                ranked_items = [it for it in ranked_items if 0 < (it.get("runtime") or 0) < 105] or ranked_items
            elif duration_preference == "epic":
                ranked_items = [it for it in ranked_items if (it.get("runtime") or 0) > 140] or ranked_items

        # Series order: swap sequels for the earliest unseen entry, one per series.
        ranked_items = apply_series_order(ranked_items, self.db)

        # Cold-start variety (opt-in): same seeded 10-minute rotation as the
        # legacy engine, so repeat requests surface different picks.
        if variety and len(ranked_items) > page_size:
            random.Random(int(time.time() // 600)).shuffle(ranked_items)

        total_results = len(ranked_items)
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size

        return {
            "total_results": total_results,
            "page": page,
            "page_size": page_size,
            "has_more": end_idx < total_results,
            "items": ranked_items[start_idx:end_idx],
            "generative": {
                "history_len": len(seq),
                "predicted_tuples": [list(codes) for codes, _ in scored_tuples[:5]],
            },
        }


# ===========================================================================
# Feature-flag router (unified API payload)
# ===========================================================================
def route_recommend(
    legacy_recommender: Any,
    generative_retriever: "GenerativeRetriever",
    engine_mode: Optional[str],
    **kwargs: Any,
) -> Dict[str, Any]:
    """Routes a recommendation request between the two engines.

    - "legacy_only":     legacy engine always (kill-switch).
    - "fallback":        generative if ready, legacy otherwise (pre-checked,
                         and any runtime failure still falls back).
    - "generative_only": generative primary; legacy only on unexpected failure.

    The legacy engine is NEVER modified — it is only ever called as-is.
    """
    mode = str(engine_mode or "fallback").strip().lower()

    def _legacy() -> Dict[str, Any]:
        result = legacy_recommender.recommend(**kwargs)
        result["engine"] = "legacy"
        return result

    if mode == "legacy_only":
        return _legacy()

    # A request naming a country of origin ("South Asian movies") cannot be
    # served here: the predictor generates semantic IDs from watch history and
    # has no concept of a film's country, so it happily returns Hollywood. The
    # legacy engine filters on origin_country/original_language in SQL. Route
    # those requests to legacy rather than returning confidently wrong results.
    if parse_origin_intent(kwargs.get("query")):
        result = _legacy()
        result["origin_routed_to"] = "legacy"
        return result

    # Same reasoning for a request about a film's IDEAS. The predictor knows
    # nothing of concept, theme or subtext — it ranks by similarity to your
    # watch history — so "something like They Live, chill but with a deep
    # concept" returned a Mario cartoon and a Taylor Swift concert film. Any
    # request that names a concept the legacy theme layer can evaluate goes to
    # legacy, which matches the actual premise of each film.
    if parse_query_intent(kwargs.get("query")).get("concept_requested"):
        result = _legacy()
        result["concept_routed_to"] = "legacy"
        return result

    # A typed request is about how the film should FEEL; the predictor only
    # knows your watch history. When the vibe index is built, the legacy engine
    # answers it with text-to-film similarity.
    vibe_ready = getattr(legacy_recommender, "vibe_ready", None)
    if vibe_ready and vibe_ready(kwargs.get("query")):
        result = _legacy()
        result["vibe_routed_to"] = "legacy"
        return result

    if mode == "fallback" and not generative_retriever.is_ready():
        return _legacy()

    try:
        result = generative_retriever.recommend(**kwargs)
        result["engine"] = "generative"
        return result
    except Exception as e:
        print(
            f"[Router] generative engine unavailable ({e}) — falling back to legacy recommender.",
            flush=True,
        )
        return _legacy()


