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
    # "Relaxing" previously mapped to Documentary/Animation/Family only, which
    # returned preschool animation and nature docs — and, once the user added
    # their own genre chips, films like Silence of the Lambs leaked in anyway
    # because the mood contributed nothing to the actual filter. Relaxing is
    # better expressed as a *tone* (gentle pacing, no violence) than as a
    # genre, so these are deliberately the warm/low-stakes genres.
    7: {"name": "😌 Relaxing / Chill", "vibe": "gentle, cozy, low-stakes", "genres": ["Animation", "Family", "Comedy", "Romance", "Music"]},
    8: {"name": "🎲 Surprise Me!", "vibe": "anything goes", "genres": []},
}

# Natural-language intent the free-text query is actually asking for. The old
# scoring substring-matched raw query words against overviews, so a prompt like
# "relaxing but not too complicated, a bit funny" scored every film whose
# blurb happened to contain "funny" — noise, not intent. These are weighted,
# genre-mapped signals so the query steers the *filter*, not just the ranking.
QUERY_INTENT: Dict[str, Dict[str, Any]] = {
    "relaxing": {"genres": ["Animation", "Family", "Comedy", "Romance", "Music"],
                 "exclude_genres": ["Horror", "Thriller", "War", "Crime"],
                 "weight": 0.35},
    "chill": {"genres": ["Animation", "Family", "Comedy", "Romance", "Music"],
              "exclude_genres": ["Horror", "Thriller", "War", "Crime"],
              "weight": 0.35},
    "cozy": {"genres": ["Family", "Animation", "Romance", "Comedy"],
             "exclude_genres": ["Horror", "Thriller", "Crime"], "weight": 0.3},
    "funny": {"genres": ["Comedy"], "weight": 0.3},
    "complicated": {"exclude_genres": ["Horror", "Thriller", "Crime", "War"], "weight": 0.25},
    "simple": {"exclude_genres": ["Horror", "Thriller", "Crime", "War"], "weight": 0.25},
    "dark": {"genres": ["Horror", "Thriller", "Crime"], "weight": 0.3},
    "scary": {"genres": ["Horror"], "weight": 0.3},
    "thoughtful": {"genres": ["Drama", "Science Fiction"], "weight": 0.3},
    "smart": {"genres": ["Science Fiction", "Mystery"], "weight": 0.25},
}

# Words too generic to carry signal — dropped before any matching so they
# don't produce spurious overview hits.
QUERY_STOPWORDS = frozenset({
    "would", "like", "want", "wantto", "wantto", "movie", "film", "watch",
    "something", "high", "quality", "good", "great", "best", "really",
    "should", "have", "that", "this", "with", "but", "not", "too", "very",
    "complicated", "some", "kind", "sort", "bit", "which", "there", "here",
    "about", "more", "less", "also", "then", "than", "them", "they", "been",
    "were", "was", "you", "your", "our", "we", "us", "me", "my", "and", "for",
    "from", "one", "two", "all", "can", "will", "just", "get", "got", "see",
})

# Tokens that must never be treated as a genre request on their own.
QUERY_NON_GENRE = frozenset({"scifi", "concept", "concepts", "idea", "ideas"})

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
PROFILE_MIX: Dict[str, Tuple[float, float]] = {
    # (own weight, other weight). Asymmetric on purpose: this is one person's
    # evening informed by the other's taste, not a committee. Averaging 50/50
    # would recommend the compromise, which neither of them asked for.
    "together": (0.6, 0.4),
    "me": (0.75, 0.25),
    "partner": (0.25, 0.75),
}

TIER_BOOSTS: Dict[str, float] = {
    # A: you both liked it — the scarcest and most useful signal
    # B: one of you loved it, the other hasn't seen it — "he'd recommend this"
    # C: exploration
    "A": 1.5,
    "B": 0.8,
    "C": 0.0,
}


def parse_query_intent(query: Optional[str]) -> Dict[str, Any]:
    """Turn a free-text prompt into genre include/exclude signals + weights.

    The old behaviour substring-matched every query word against item
    overviews, so "relaxing but not too complicated, a bit funny" boosted any
    film whose blurb contained "funny" while "relaxing" did nothing at all.
    Intent words are now recognised explicitly and mapped to genres, so they
    steer the hard filter (exclusions especially) as well as the score.

    Returns:
        include_genres: genres the user positively asked for
        exclude_genres: genres contradicting the request (hard-excluded)
        boost_genres:   genres to nudge in scoring
        weight:         how strongly the query should shape the result
    """
    result: Dict[str, Any] = {
        "include_genres": [], "exclude_genres": [],
        "boost_genres": [], "weight": 0.0,
    }
    if not query or not query.strip():
        return result

    tokens = [w for w in re.findall(r"[a-z]+", query.lower()) if w]
    seen_include: Set[str] = set()
    seen_exclude: Set[str] = set()
    matched_terms: List[str] = []

    for i, word in enumerate(tokens):
        # Negation-aware: "not too complicated" must not read as wanting
        # complexity. Check the previous token for a negator.
        negated = i > 0 and tokens[i - 1] in ("not", "no", "never", "without", "less")
        intent = QUERY_INTENT.get(word)
        if not intent:
            continue
        matched_terms.append(word)

        for g in intent.get("genres", []):
            if negated:
                seen_exclude.add(g)
            else:
                seen_include.add(g)
                if g not in result["boost_genres"]:
                    result["boost_genres"].append(g)

        for g in intent.get("exclude_genres", []):
            # "not too complicated" negates the *exclusion* this term implies.
            if negated:
                seen_include.add(g)
            else:
                seen_exclude.add(g)

        if not negated:
            result["weight"] += intent.get("weight", 0.2)

    result["include_genres"] = sorted(seen_include)
    result["exclude_genres"] = sorted(seen_exclude)
    result["weight"] = round(min(result["weight"], 1.0), 2)
    result["matched_terms"] = matched_terms

    # Content words worth matching against overviews/titles: intent words are
    # already handled structurally, and stopwords carry no signal.
    result["content_words"] = sorted(
        w for w in set(tokens)
        if len(w) > 3 and w not in QUERY_STOPWORDS
        and w not in QUERY_INTENT and w not in QUERY_NON_GENRE
    )
    return result


def resolve_effective_genres(
    mood_genres: Optional[List[str]],
    chip_genres: Optional[List[str]],
    query: Optional[str],
) -> Dict[str, Any]:
    """Combine mood genres + user's chips + query intent into one filter spec.

    Precedence: explicit chips win (the user clicked them deliberately), then
    query intent, then the mood's own genres. Exclusions apply regardless —
    asking for "relaxing, not too complicated" should veto a violent film even
    if Horror is among the chips, because the phrase is a contradiction rather
    than a narrowing choice.
    """
    intent = parse_query_intent(query)
    chip_list = list(chip_genres or [])
    mood_list = list(mood_genres or [])

    include: List[str] = []
    for g in chip_list + intent["include_genres"] + mood_list:
        if g not in include:
            include.append(g)

    # Only apply exclusion filtering when the query actually expressed one,
    # and never let a mood's own genre veto its films (mood 4 = Horror must
    # still be able to return horror).
    exclude: List[str] = []
    if intent["exclude_genres"]:
        for g in intent["exclude_genres"]:
            if g in include and g in mood_list and mood_list and intent["weight"] < 0.3:
                continue
            if g not in exclude:
                exclude.append(g)

    return {
        "include": include,
        "exclude": exclude,
        "boost": intent["boost_genres"],
        "weight": intent["weight"],
        "content_words": intent.get("content_words", []),
        "matched_terms": intent.get("matched_terms", []),
    }


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

    def _resolve_profiles(self, profile: str) -> Tuple[int, int]:
        """Maps a profile selector to concrete profile ids.

        `together` is NOT a stored profile — it is a computed blend over both,
        so storing it would double-count every film both people rated.
        """
        ids = sorted(self.db.get_all_profile_tastes().keys())
        if not ids:
            return (1, 2)
        primary = ids[0]
        other = ids[1] if len(ids) > 1 else primary
        return (primary, other)

    def _taste_bonus(
        self,
        item_genres: Set[str],
        item_keywords: Set[str],
        own_taste: Dict[str, Any],
        other_taste: Dict[str, Any],
        mix: Tuple[float, float],
    ) -> Tuple[float, float, float]:
        """Blended taste bonus for one item, plus its own/other components.

        Profiles are blended HERE, at scoring time, never pre-averaged into a
        single profile — that would turn "you like horror, he doesn't" into a
        lukewarm average nobody actually wants.
        """
        own_w, other_w = mix

        def bonus_for(taste: Dict[str, Any]) -> float:
            genres = taste.get("preferred_genres", {})
            keywords = taste.get("preferred_keywords", {})
            total = 0.0
            for g in item_genres:
                if g in genres:
                    total += genres[g] * 0.2
            for k in item_keywords:
                if k in keywords:
                    total += keywords[k] * 0.15
            return min(total, 2.0)

        own_bonus = bonus_for(own_taste)
        other_bonus = bonus_for(other_taste)
        blended = own_w * own_bonus + other_w * other_bonus
        return blended, own_bonus, other_bonus

    def rank_candidates_profile_aware(
        self,
        candidates: List[Dict[str, Any]],
        profile: str = "together",
        query: Optional[str] = None,
        exclude_ids: Optional[Set[int]] = None,
        content_words: Optional[List[str]] = None,
        intent_boost_genres: Optional[Set[str]] = None,
        intent_bonus: float = 0.0,
    ) -> List[Dict[str, Any]]:
        """Profile-aware scoring: Bayesian quality + blended taste + co-view tier.

        The tier boost is what makes "together" work. Without it a Tier A film
        (you both loved it) always loses to a marginally better-rated unseen
        one, and your shared favourites quietly disappear from the list.
        """
        tastes = self.db.get_all_profile_tastes()
        if not tastes:
            tastes = {1: {"preferred_genres": {}, "preferred_keywords": {},
                          "rating_count": 0, "name": "Me", "weight": 0.0},
                      2: {"preferred_genres": {}, "preferred_keywords": {},
                          "rating_count": 0, "name": "Partner", "weight": 0.0}}
        ids = sorted(tastes.keys())
        own_id, other_id = ids[0], (ids[1] if len(ids) > 1 else ids[0])

        if profile == "partner":
            own_id, other_id = other_id, own_id   # view from his seat

        own_taste = tastes.get(own_id, {})
        other_taste = tastes.get(other_id, {})

        # For `me`/`partner` the mix is defined from the VIEWER's seat, so
        # swapping the ids must also swap which weight is "own". Swapping ids
        # alone would leave both modes using the same 0.75 on profile 1, making
        # `me` and `partner` return identical lists.
        if profile == "me":
            own_w, other_w = PROFILE_MIX["me"]
        elif profile == "partner":
            own_w, other_w = PROFILE_MIX["partner"]
        else:
            own_w, other_w = PROFILE_MIX["together"]
        mix = (own_w, other_w)

        coview = self.db.get_item_co_view(
            [c["id"] for c in candidates if c.get("id")], viewer_profile_id=own_id
        )
        exclude = exclude_ids or set()
        # In `me`/`partner` mode the viewer has no use for films they've already
        # rated. In `together` mode both may still have untested picks (tier B),
        # so only films BOTH have seen are dropped — handled upstream.
        viewer_seen_ids = {
            iid for iid, cv in coview.items() if own_id in (cv.get("seen_by") or [])
        }
        if profile in ("me", "partner"):
            exclude = exclude | viewer_seen_ids
        query_words = set(content_words) if content_words else set()
        # `intent_boost_genres` / `intent_bonus` come pre-computed from the
        # caller's query spec: genres the prompt asked for ("funny" -> Comedy)
        # get a real score bump. Previously only free-text overview matching
        # contributed, so a query could pull a film in and still rank it
        # mid-table.

        scored: List[Tuple[float, Dict[str, Any]]] = []
        for item in candidates:
            if item.get("id") in exclude:
                continue

            base_score = calculate_weighted_rating(
                float(item.get("rating") or 6.0),
                int(item.get("vote_count") or 100),
            )

            item_genres = set(_split_lower(item.get("genres")))
            item_keywords = set(_split_lower(item.get("tier2_keywords")))
            relevance_boost = 0.0
            # Query intent bump: the film carries a genre the prompt asked for
            # ("funny" -> Comedy), independent of either person's taste.
            if intent_boost_genres and (item_genres & intent_boost_genres):
                relevance_boost += intent_bonus
            taste_bonus, own_bonus, other_bonus = self._taste_bonus(
                item_genres, item_keywords, own_taste, other_taste, mix
            )

            cv = coview.get(item.get("id")) or {}
            tier = cv.get("tier", "C")
            tier_boost = TIER_BOOSTS.get(tier, 0.0)

            if query_words:
                item_text = f"{item.get('title')} {item.get('overview')} {item.get('tier2_keywords')}".lower()
                matches = sum(1 for w in query_words if len(w) > 3 and w in item_text)
                relevance_boost = min(matches * 0.3, 2.0)

            final_score = base_score + taste_bonus + tier_boost + relevance_boost

            item["recommendation_score"] = round(final_score, 2)
            item["base_weighted_rating"] = round(base_score, 2)
            item["taste_bonus"] = round(taste_bonus, 2)
            item["taste_bonus_own"] = round(own_bonus, 2)
            item["taste_bonus_other"] = round(other_bonus, 2)
            item["co_view_tier"] = tier
            item["tier_boost"] = tier_boost
            item["relevance_boost"] = round(relevance_boost, 2)
            if cv.get("combined_score") is not None:
                item["combined_score"] = cv["combined_score"]
            if cv.get("seen_by"):
                item["seen_by_profile_ids"] = cv["seen_by"]
            other_name = other_taste.get("name") or "They"
            if tier == "A":
                item["tier_reason"] = "You both liked it"
            elif tier == "B":
                item["tier_reason"] = f"{other_name} loved this — you haven't"

            scored.append((final_score, item))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored]

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
        profile: str = "together",
    ) -> Dict[str, Any]:
        """Executes multi-stage filtering, scoring, MMR diversity pruning, and pagination."""
        mood_info = MOOD_MAPPINGS.get(mood_id, MOOD_MAPPINGS[8])

        # Resolve Genres. The free-text query is a first-class filter input, not
        # just a scoring nudge: "relaxing, not too complicated" has to be able to
        # veto a violent film even when the mood's own genres include it.
        chip_genres = genres_override
        genre_spec = resolve_effective_genres(mood_info["genres"], chip_genres, query)
        genres = genre_spec["include"] or None
        excluded_genres = genre_spec["exclude"]
        intent_weight = genre_spec["weight"]
        content_words = genre_spec["content_words"]

        if media_type == "tv" and genres:
            expanded = list(genres)
            for g in genres:
                expanded.extend(TV_GENRE_ALIASES.get(g.strip().lower(), []))
            genres = list(dict.fromkeys(expanded))
        if media_type == "tv" and excluded_genres:
            expanded_ex = []
            for g in excluded_genres:
                expanded_ex.append(g)
                expanded_ex.extend(TV_GENRE_ALIASES.get(g.strip().lower(), []))
            excluded_genres = list(dict.fromkeys(expanded_ex))

        # Baseline Minimum Rating Floor
        min_rating = 6.0 if min_rating_override is None else min_rating_override
        if query and any(kw in query.lower() for kw in GUILTY_PLEASURE_KEYWORDS):
            min_rating = 0.0

        # Stage 1: Quality-cut candidate funnel — hard filters + Bayesian
        # audience-weighted ordering computed in SQL (real vote counts), cut
        # to the shortlist so Python scoring never touches the full catalogue.
        #
        # NB: the SQL `exclude_watched` drops anything EITHER profile has
        # watched. In a profile-aware run that would delete every rated film
        # before the scorer could see its tier, making Tier A unreachable — so
        # watched exclusion is handled in Python below, where "seen by both"
        # can be distinguished from "seen by one".
        sql_exclude_watched = exclude_watched and profile not in PROFILE_MIX
        candidates = self.db.get_top_candidates(
            media_type=media_type,
            genres=genres if genres else None,
            exclude_genres=excluded_genres or None,
            exclude_watched=sql_exclude_watched,
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

        # Profile-aware exclusion, applied in Python so "seen by both" can be told
        # apart from "seen by one". A film only ONE person has seen is exactly
        # the Tier B pick we want to surface ("he loved it, you haven't"), so it
        # is kept and badged rather than hidden. A film BOTH have seen is a
        # genuine "already watched together" and is dropped.
        if exclude_watched and profile in PROFILE_MIX:
            coview_all = self.db.get_item_co_view(
                [c["id"] for c in candidates if c.get("id")], viewer_profile_id=None
            )
            candidates = [
                c for c in candidates
                if len((coview_all.get(c["id"]) or {}).get("seen_by") or []) < 2
            ]
            if not candidates:
                return {"total_results": 0, "page": page, "page_size": page_size, "items": []}

        # Stage 2: Personal Taste & Co-view Scoring
        # Profile-aware path blends the two people's tastes and applies tier
        # boosts; the legacy path is unchanged when no profile signal exists.
        has_profile_signal = any(
            t.get("rating_count", 0) > 0 for t in self.db.get_all_profile_tastes().values()
        )
        if has_profile_signal:
            ranked = self.rank_candidates_profile_aware(
                candidates, profile=profile, query=query,
                content_words=content_words,
                intent_boost_genres={
                    g.strip().lower()
                    for g in (genre_spec.get("boost") or []) if g and g.strip()
                },
                intent_bonus=0.5 * intent_weight,
            )
        else:
            taste = self.db.get_user_taste_profile(min_rating=7.0)
            ranked = self.rank_candidates(candidates, taste, query=query)
            for item in ranked:
                item["co_view_tier"] = "C"
                item["tier_boost"] = 0.0

        if not ranked:
            return {"total_results": 0, "page": page, "page_size": page_size, "items": []}

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