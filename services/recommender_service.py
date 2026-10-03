"""
Recommender Service (services/recommender_service.py)
Production-grade multi-stage candidate scoring, vector similarity, 
Bayesian rating weighting, MMR diversity pruning, and pagination.
"""

from __future__ import annotations

import os
import random
import re
import math
import time
from typing import Any, Dict, List, Optional, Tuple, Set

import requests
import numpy as np
from database import DatabaseManager


# ===========================================================================
# Helpers & Math Utilities
# ===========================================================================
def _split_lower(csv: Optional[str]) -> List[str]:
    """Splits a comma-separated string into lowercase stripped tokens."""
    if not csv:
        return []
    return [t.strip().lower() for t in str(csv).split(",") if t.strip()]


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """Calculates cosine similarity between two float vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    va, vb = np.array(a), np.array(b)
    norm_a, norm_b = np.linalg.norm(va), np.linalg.norm(vb)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(va, vb) / (norm_a * norm_b))


def jaccard_similarity(set_a: Set[str], set_b: Set[str]) -> float:
    """Measures set overlap between genre/keyword sets for MMR diversity."""
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a.intersection(set_b))
    union = len(set_a.union(set_b))
    return intersection / union if union > 0 else 0.0


def calculate_weighted_rating(
    rating: float,
    num_votes: int,
    min_votes: int = 1000,
    global_mean: float = 6.5,
) -> float:
    """Applies IMDb's Bayesian Weighted Rating formula to eliminate 1-vote 10/10 noise."""
    if num_votes <= 0:
        return global_mean
    return (num_votes / (num_votes + min_votes)) * rating + (
        min_votes / (num_votes + min_votes)
    ) * global_mean


# ===========================================================================
# Mood ↔ Genre Mapping & Config
# ===========================================================================
MOOD_MAPPINGS: Dict[int, Dict[str, Any]] = {
    1: {"name": "😊 Happy / Feel-Good", "vibe": "light, funny, uplifting", "genres": ["Comedy", "Family", "Animation", "Music"]},
    2: {"name": "😢 Sad / Emotional", "vibe": "moving, heartfelt, tearjerker", "genres": ["Drama", "Romance"]},
    3: {"name": "😱 Thrilling / On the Edge", "vibe": "intense, gripping, high-stakes", "genres": ["Action", "Thriller", "Adventure"]},
    4: {"name": "👻 Scary / Spooky", "vibe": "dark, tense, hair-raising", "genres": ["Horror", "Mystery"]},
    5: {"name": "❤️ Romantic", "vibe": "warm, swoony, tender", "genres": ["Romance"]},
    6: {"name": "🤯 Mind-Bending / Thoughtful", "vibe": "clever, cerebral, twisty", "genres": ["Science Fiction", "Sci-Fi", "Mystery"]},
    7: {"name": "😌 Relaxing / Chill", "vibe": "gentle, cozy, low-stakes", "genres": ["Documentary", "Animation", "Family"]},
    8: {"name": "🎲 Surprise Me!", "vibe": "anything goes", "genres": []},
}

GUILTY_PLEASURE_KEYWORDS = frozenset({
    "guilty pleasure", "so bad it's good", "so bad its good",
    "cringe", "b-movie", "b movie", "trash", "campy", "cheesy"
})

TV_GENRE_ALIASES: Dict[str, List[str]] = {
    "action": ["Action & Adventure", "Action"],
    "adventure": ["Action & Adventure", "Adventure"],
    "science fiction": ["Sci-Fi & Fantasy", "Science Fiction"],
    "sci-fi": ["Sci-Fi & Fantasy", "Sci-Fi"],
    "fantasy": ["Sci-Fi & Fantasy", "Fantasy"],
    "family": ["Family", "Kids"],
}


# ===========================================================================
# Vector Embedding Adaptor
# ===========================================================================
def get_vector_embeddings(text: str) -> Optional[List[float]]:
    """Encodes text using ONNX Runtime if available."""
    model_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "models", "model.onnx"
    )
    if not os.path.exists(model_path) or not text.strip():
        return None

    try:
        import onnxruntime as ort
        from transformers import AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained("BAAI/bge-small-en-v1.5")
        encoded = tokenizer(
            text, padding="max_length", truncation=True, max_length=128, return_tensors="np"
        )
        session = ort.InferenceSession(model_path)
        inputs = {
            "input_ids": encoded["input_ids"],
            "attention_mask": encoded["attention_mask"],
            "token_type_ids": encoded.get("token_type_ids", encoded["input_ids"] * 0),
        }
        output = session.run(None, inputs)
        hidden = output[0]
        mask = encoded["attention_mask"]
        pooled = (hidden * mask[:, :, None]).sum(axis=1) / mask.sum(axis=1, keepdims=True)
        return pooled[0].tolist()
    except Exception as e:
        print(f"[Recommender] Vector embedding error: {e}", flush=True)
        return None


# ===========================================================================
# Recommender Service Engine
# ===========================================================================
class RecommenderService:
    def __init__(
        self,
        db_manager: DatabaseManager,
        api_key: Optional[str] = None,
        enable_tier2: bool = True,
        shortlist_size: int = 250,
        final_pool: int = 50,
    ):
        self.db = db_manager
        self.api_key = api_key
        self.enable_tier2 = enable_tier2
        self.shortlist_size = shortlist_size   # Funnel 2: quality-cut pool size
        self.final_pool = final_pool           # Funnel 4: diverse pool for pagination
        self.http = requests.Session()

    def rank_candidates(
        self,
        candidates: List[Dict[str, Any]],
        taste_profile: Dict[str, Any],
        query: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Multi-faceted scoring combining Bayesian rating, taste profile, and vector similarity."""
        preferred_genres = taste_profile.get("preferred_genres", {})
        preferred_keywords = taste_profile.get("preferred_keywords", {})

        # Compute query vector embedding if model is deployed
        query_vector = get_vector_embeddings(query) if query else None

        scored: List[Tuple[float, Dict[str, Any]]] = []
        for item in candidates:
            raw_rating = float(item.get("rating") or 6.0)
            num_votes = int(item.get("num_votes") or item.get("vote_count") or 100)

            # 1. Base Score: Bayesian Weighted Rating
            base_score = calculate_weighted_rating(raw_rating, num_votes)

            # 2. Taste Affinity Score
            taste_bonus = 0.0
            item_genres = set(_split_lower(item.get("genres")))
            item_keywords = set(_split_lower(item.get("tier2_keywords")))

            for g in item_genres:
                if g in preferred_genres:
                    taste_bonus += preferred_genres[g] * 0.2
            for k in item_keywords:
                if k in preferred_keywords:
                    taste_bonus += preferred_keywords[k] * 0.15

            taste_bonus = min(taste_bonus, 2.0)

            # 3. Vector Semantic Relevance vs Term Matching Boost
            relevance_boost = 0.0
            if query_vector and item.get("embedding"):
                # Fast Cosine Similarity using stored JSON vector
                similarity = cosine_similarity(query_vector, item["embedding"])
                relevance_boost = similarity * 3.0
            elif query:
                # Text fall-back match
                q_words = set(re.findall(r"\w+", query.lower()))
                item_text = f"{item.get('title')} {item.get('overview')} {item.get('tier2_keywords')}".lower()
                matches = sum(1 for w in q_words if len(w) > 3 and w in item_text)
                relevance_boost = min(matches * 0.3, 2.0)

            final_score = base_score + taste_bonus + relevance_boost

            item["recommendation_score"] = round(final_score, 2)
            item["base_weighted_rating"] = round(base_score, 2)
            item["taste_bonus"] = round(taste_bonus, 2)
            item["relevance_boost"] = round(relevance_boost, 2)

            scored.append((final_score, item))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored]

    def apply_mmr_diversity(
        self, candidates: List[Dict[str, Any]], top_n: int, lambda_param: float = 0.7
    ) -> List[Dict[str, Any]]:
        """Maximal Marginal Relevance (MMR) re-ranking to maximize diversity and remove duplicates."""
        if not candidates or top_n <= 0:
            return []

        selected: List[Dict[str, Any]] = []
        unselected = list(candidates)

        while unselected and len(selected) < top_n:
            best_item = None
            best_mmr_score = -float("inf")

            for item in unselected:
                rel = item.get("recommendation_score", 0.0)

                # Similarity against already selected set
                max_sim = 0.0
                item_genres = set(_split_lower(item.get("genres")))

                for sel in selected:
                    sel_genres = set(_split_lower(sel.get("genres")))
                    sim = jaccard_similarity(item_genres, sel_genres)
                    # Deduplicate franchises/same main title stems
                    if item.get("title", "").split(":")[0] == sel.get("title", "").split(":")[0]:
                        sim += 0.5
                    if sim > max_sim:
                        max_sim = sim

                mmr_score = lambda_param * rel - (1 - lambda_param) * max_sim
                if mmr_score > best_mmr_score:
                    best_mmr_score = mmr_score
                    best_item = item

            if best_item:
                selected.append(best_item)
                unselected.remove(best_item)

        return selected

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
    ) -> Dict[str, Any]:
        """Executes multi-stage filtering, scoring, MMR diversity pruning, and pagination."""
        mood_info = MOOD_MAPPINGS.get(mood_id, MOOD_MAPPINGS[8])

        # Resolve Genres
        if genres_override:
            genres = genres_override
        else:
            genres = mood_info["genres"]

        if media_type == "tv" and genres:
            expanded = list(genres)
            for g in genres:
                expanded.extend(TV_GENRE_ALIASES.get(g.strip().lower(), []))
            genres = list(dict.fromkeys(expanded))

        # Baseline Minimum Rating Floor
        min_rating = 6.0 if min_rating_override is None else min_rating_override
        if query and any(kw in query.lower() for kw in GUILTY_PLEASURE_KEYWORDS):
            min_rating = 0.0

        # Stage 1: Quality-cut candidate funnel — hard filters + Bayesian
        # audience-weighted ordering computed in SQL (real vote counts), cut
        # to the shortlist so Python scoring never touches the full catalogue.
        candidates = self.db.get_top_candidates(
            media_type=media_type,
            genres=genres if genres else None,
            exclude_watched=exclude_watched,
            min_rating=min_rating,
            era=era_preference,
            duration=duration_preference if media_type == "movie" else None,
            limit=self.shortlist_size,
        )

        if not candidates:
            return {"total_results": 0, "page": page, "page_size": page_size, "items": []}

        # Era & Runtime Filters
        if era_preference == "modern":
            candidates = [c for c in candidates if (c.get("release_year") or 0) >= 2015] or candidates
        elif era_preference == "2000s":
            candidates = [c for c in candidates if 2000 <= (c.get("release_year") or 0) < 2015] or candidates
        elif era_preference == "classic":
            candidates = [c for c in candidates if (c.get("release_year") or 0) < 2000] or candidates

        if media_type == "movie" and duration_preference:
            if duration_preference == "short":
                candidates = [c for c in candidates if 0 < (c.get("runtime") or 0) < 105] or candidates
            elif duration_preference == "epic":
                candidates = [c for c in candidates if (c.get("runtime") or 0) > 140] or candidates

        # Stage 2: Personal Taste & Vector Scoring
        taste = self.db.get_user_taste_profile(min_rating=7.0)
        ranked = self.rank_candidates(candidates, taste, query=query)

        # Stage 3: Candidate Diversity Pruning (MMR)
        # Process the shortlist through MMR to build the diverse final pool.
        pool_size = min(len(ranked), self.final_pool)
        diverse_candidates = self.apply_mmr_diversity(ranked[:pool_size], top_n=pool_size)

        # Cold-start variety (opt-in): seeded shuffle that rotates every 10
        # minutes — repeat requests surface different picks, but the order is
        # stable within a bucket so pagination stays coherent while browsing.
        if variety and len(diverse_candidates) > page_size:
            random.Random(int(time.time() // 600)).shuffle(diverse_candidates)

        # Stage 4: Pagination Slice
        total_results = len(diverse_candidates)
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        paginated_items = diverse_candidates[start_idx:end_idx]

        return {
            "total_results": total_results,
            "page": page,
            "page_size": page_size,
            "has_more": end_idx < total_results,
            "items": paginated_items,
        }