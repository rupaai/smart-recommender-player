"""
Recommender Service (services/recommender_service.py)
Mood-based filtering, taste scoring from watch history, baseline rating rules,
support for Movies and TV Shows, and optional ONNX vector embedding drop-in.

Works completely without a trained model. The optional `get_vector_embeddings()`
adaptor checks for a models/model.onnx file at runtime.
"""

from __future__ import annotations

import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import requests
from database import DatabaseManager


# ===========================================================================
# Mood ↔ Genre Mapping
# ===========================================================================
MOOD_MAPPINGS: Dict[int, Dict[str, Any]] = {
    1: {
        "name": "😊 Happy / Feel-Good",
        "genres": ["Comedy", "Family", "Animation", "Music"],
        "vibe": "Uplifting, lighthearted, and cheerful",
        "submoods": [
            {"name": "Family Fun", "genres": ["Family", "Animation"]},
            {"name": "Lighthearted Comedy", "genres": ["Comedy", "Romance"]},
            {"name": "Feel-Good Animation", "genres": ["Animation", "Comedy"]},
        ],
    },
    2: {
        "name": "😢 Sad / Emotional",
        "genres": ["Drama", "Romance"],
        "vibe": "Poignant, touching, and emotionally resonant",
        "submoods": [
            {"name": "Tearjerker", "genres": ["Drama"]},
            {"name": "Bittersweet Romance", "genres": ["Romance", "Drama"]},
            {"name": "Coming of Age", "genres": ["Drama", "Comedy"]},
        ],
    },
    3: {
        "name": "😱 Thrilling / On the Edge",
        "genres": ["Action", "Thriller", "Adventure"],
        "vibe": "High-octane, intense, and suspenseful",
        "submoods": [
            {"name": "Action-Packed", "genres": ["Action", "Adventure"]},
            {"name": "Psychological Thriller", "genres": ["Thriller", "Mystery"]},
            {"name": "Crime & Heist", "genres": ["Crime", "Thriller"]},
        ],
    },
    4: {
        "name": "👻 Scary / Spooky",
        "genres": ["Horror", "Mystery"],
        "vibe": "Eerie, chilling, and atmospheric dread",
        "submoods": [
            {"name": "Supernatural Horror", "genres": ["Horror", "Fantasy"]},
            {"name": "Suspenseful Mystery", "genres": ["Mystery", "Thriller"]},
            {"name": "Psychological Horror", "genres": ["Horror", "Drama"]},
        ],
    },
    5: {
        "name": "❤️ Romantic",
        "genres": ["Romance"],
        "vibe": "Passionate, sweet, and heartwarming",
        "submoods": [
            {"name": "Romantic Comedy", "genres": ["Romance", "Comedy"]},
            {"name": "Period Romance", "genres": ["Romance", "History"]},
            {"name": "Intense Passion", "genres": ["Romance", "Drama"]},
        ],
    },
    6: {
        "name": "🤯 Mind-Bending / Thoughtful",
        "genres": ["Science Fiction", "Sci-Fi", "Mystery"],
        "vibe": "Complex, cerebral, and reality-questioning",
        "submoods": [
            {"name": "Sci-Fi Space Adventure", "genres": ["Science Fiction", "Adventure"]},
            {"name": "Mind-Bending Mystery", "genres": ["Mystery", "Thriller"]},
            {"name": "Dystopian Cyberpunk", "genres": ["Science Fiction", "Action"]},
        ],
    },
    7: {
        "name": "😌 Relaxing / Chill",
        "genres": ["Documentary", "Animation", "Family"],
        "vibe": "Calming, visually beautiful, and comforting",
        "submoods": [
            {"name": "Nature & Wildlife", "genres": ["Documentary"]},
            {"name": "Animated Comfort", "genres": ["Animation", "Family"]},
            {"name": "Gentle Comedy", "genres": ["Comedy", "Family"]},
        ],
    },
    8: {
        "name": "🎲 Surprise Me!",
        "genres": [],
        "vibe": "Wildcard selection from your entire catalogue",
        "submoods": [
            {"name": "Top Rated Staff Picks", "genres": []},
            {"name": "Hidden Gems", "genres": []},
            {"name": "Cult Classics", "genres": []},
        ],
    },
}

# Keywords that suppress or invert the default rating floor
GUILTY_PLEASURE_KEYWORDS = frozenset({
    "guilty pleasure",
    "so bad it's good",
    "so bad its good",
    "cringe",
    "b-movie",
    "b movie",
    "trash",
    "campy",
    "cheesy",
})


# ===========================================================================
# TMDB TV Genre Name Aliases
# ===========================================================================
# TMDB uses different genre names for TV shows vs movies ("Sci-Fi & Fantasy"
# vs "Science Fiction"). These aliases let the movie-oriented mood mappings
# match TV genre naming as well.
TV_GENRE_ALIASES: Dict[str, List[str]] = {
    "action": ["Action & Adventure", "Action"],
    "adventure": ["Action & Adventure", "Adventure"],
    "science fiction": ["Sci-Fi & Fantasy", "Science Fiction"],
    "sci-fi": ["Sci-Fi & Fantasy", "Sci-Fi"],
    "fantasy": ["Sci-Fi & Fantasy", "Fantasy"],
    "war": ["War & Politics", "War"],
    "politics": ["War & Politics", "Politics"],
    "family": ["Family", "Kids"],
    "kids": ["Kids", "Family"],
    "children": ["Kids", "Family"],
}


# ===========================================================================
# Optional ONNX Vector Embedding Adaptor
# ===========================================================================
def get_vector_embeddings(text: str) -> Optional[list]:
    """Checks if models/model.onnx exists and loads via onnxruntime.

    Returns a list of floats (embedding vector) or None if the model
    is not deployed. The main pipeline gracefully falls back to the
    SQL/taste-scoring approach when this returns None.
    """
    model_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "models", "model.onnx"
    )
    if not os.path.exists(model_path):
        return None

    try:
        import onnxruntime as ort
        from transformers import AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained("BAAI/bge-small-en-v1.5")
        encoded = tokenizer(
            text, padding="max_length", truncation=True,
            max_length=128, return_tensors="np",
        )

        session = ort.InferenceSession(model_path)
        inputs = {
            "input_ids": encoded["input_ids"],
            "attention_mask": encoded["attention_mask"],
            "token_type_ids": encoded.get("token_type_ids", encoded["input_ids"] * 0),
        }
        output = session.run(None, inputs)
        hidden = output[0]  # (1, seq, dim)
        mask = encoded["attention_mask"]
        pooled = (hidden * mask[:, :, None]).sum(axis=1) / mask.sum(axis=1, keepdims=True)
        return pooled[0].tolist()

    except Exception as e:
        print(f"[Recommender] Vector embedding error: {e}", flush=True)
        return None


# ===========================================================================
# Recommender Service
# ===========================================================================
class RecommenderService:
    """Smart recommender engine using mood mappings, era/runtime filters,
    taste scoring from past watch history, and baseline rating rules.

    Works entirely from SQLite without any trained models.
    Supports both Movies and TV Shows.
    """

    TMDB_BASE_URL = "https://api.themoviedb.org/3"

    def __init__(
        self,
        db_manager: DatabaseManager,
        api_key: Optional[str] = None,
        enable_tier2: bool = True,
    ):
        self.db = db_manager
        self.api_key = api_key
        self.enable_tier2 = enable_tier2
        self.http = requests.Session()

    # ------------------------------------------------------------------
    # Tier 2 Deep Profiling (TMDB keywords + reviews)
    # ------------------------------------------------------------------
    def fetch_tier2_profile(
        self, tmdb_id: int, media_type: str = "movie"
    ) -> Tuple[str, str]:
        """Fetches TMDB keywords and first review excerpt for an item."""
        if not self.api_key:
            return "", ""

        keywords: List[str] = []
        reviews_summary = ""

        # Keywords
        kw_ep = f"/tv/{tmdb_id}/keywords" if media_type == "tv" else f"/movie/{tmdb_id}/keywords"
        try:
            resp = self.http.get(
                f"{self.TMDB_BASE_URL}{kw_ep}",
                params={"api_key": self.api_key},
                timeout=8,
            )
            if resp.status_code == 200:
                data = resp.json()
                kw_list = data.get("results") or data.get("keywords") or []
                keywords = [k["name"].lower() for k in kw_list[:8]]
        except Exception:
            pass

        # Reviews
        rev_ep = f"/tv/{tmdb_id}/reviews" if media_type == "tv" else f"/movie/{tmdb_id}/reviews"
        try:
            resp = self.http.get(
                f"{self.TMDB_BASE_URL}{rev_ep}",
                params={"api_key": self.api_key},
                timeout=8,
            )
            if resp.status_code == 200:
                results = resp.json().get("results", [])
                if results:
                    text = results[0].get("content", "").replace("\r\n", " ").replace("\n", " ").strip()
                    sentences = [s.strip() for s in text.split(".") if len(s.strip()) > 15]
                    if sentences:
                        reviews_summary = f'"{sentences[0]}."'
        except Exception:
            pass

        return ", ".join(keywords), reviews_summary

    def ensure_tier2(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Ensures Tier 2 profiling is cached for an item."""
        if not self.enable_tier2 or not item.get("tmdb_id"):
            return item
        if item.get("tier2_profiled_at") and (item.get("tier2_keywords") or item.get("tier2_reviews_summary")):
            return item

        kw, rev = self.fetch_tier2_profile(item["tmdb_id"], item.get("media_type", "movie"))
        self.db.update_tier2_profile(item["id"], kw, rev)
        item["tier2_keywords"] = kw
        item["tier2_reviews_summary"] = rev
        item["tier2_profiled_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        return item

    # ------------------------------------------------------------------
    # Taste Scoring & Context Matching
    # ------------------------------------------------------------------
    def _extract_reference_signal(
        self, query_text: str
    ) -> Tuple[Dict[str, float], Dict[str, float]]:
        """Detects movie/TV titles mentioned inside a free-text description.

        e.g. "something like Interstellar but funnier" uses Interstellar's
        genres, keywords and overview words as extra affinity signals so
        candidates similar to the referenced titles score higher.
        """
        ref_genres: Dict[str, float] = {}
        ref_keywords: Dict[str, float] = {}
        if not query_text:
            return ref_genres, ref_keywords

        try:
            titles = self.db.get_reference_titles()
        except Exception as e:
            print(f"[Recommender] Reference titles unavailable: {e}", flush=True)
            return ref_genres, ref_keywords

        q_norm = f" {re.sub(r'[^a-z0-9]+', ' ', query_text).strip()} "
        for t in titles:
            raw_title = str(t.get("title") or "").strip()
            norm_title = re.sub(r"[^a-z0-9]+", " ", raw_title.lower()).strip()
            # Skip tiny titles ("Up", "It") to avoid false-positive matches
            if len(norm_title) < 4:
                continue
            if f" {norm_title} " not in q_norm:
                continue

            for g in _split_lower(t.get("genres")):
                ref_genres[g] = ref_genres.get(g, 0.0) + 1.0
            corpus_words = re.findall(
                r"\w+",
                f"{t.get('tier2_keywords') or ''} {t.get('overview') or ''}".lower(),
            )
            for w in corpus_words:
                if len(w) > 3:
                    ref_keywords[w] = ref_keywords.get(w, 0.0) + 0.5
            print(f"[Recommender] Reference title detected: {raw_title}", flush=True)

        return ref_genres, ref_keywords

    def rank_candidates(
        self,
        candidates: List[Dict[str, Any]],
        taste_profile: Dict[str, Any],
        query: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Ranks candidates using: Final Score = Base Rating + Taste Bonus + Query Relevance Bonus."""
        preferred_genres = taste_profile.get("preferred_genres", {})
        preferred_keywords = taste_profile.get("preferred_keywords", {})

        query_text = (query or "").lower()
        query_tokens = [w for w in re.findall(r"\w+", query_text) if len(w) > 2]
        query_phrases = [
            p.strip()
            for p in re.findall(r"[a-z0-9']+\s+[a-z0-9']+", query_text)
            if len(p.strip()) > 6
        ]

        # Reference titles mentioned in the description act as extra taste signals
        ref_genres, ref_keywords = self._extract_reference_signal(query_text)

        scored: List[Tuple[float, Dict[str, Any]]] = []
        for item in candidates:
            base = float(item.get("rating") or 5.0)
            bonus = 0.0

            # Genre affinity
            for g in _split_lower(item.get("genres")):
                if g in preferred_genres:
                    bonus += preferred_genres[g] * 0.25
                if g in ref_genres:
                    bonus += ref_genres[g] * 0.5

            # Keyword affinity (taste profile matches phrases, refs match words)
            if _split_lower(item.get("tier2_keywords")) or ref_keywords:
                for k in _split_lower(item.get("tier2_keywords")):
                    if k in preferred_keywords:
                        bonus += preferred_keywords[k] * 0.2
                if ref_keywords:
                    for w in re.findall(
                        r"\w+", str(item.get("tier2_keywords") or "").lower()
                    ):
                        if len(w) > 3 and w in ref_keywords:
                            bonus += ref_keywords[w] * 0.3

            bonus = min(bonus, 2.0)

            # Query relevance boost (matching title, synopsis, keywords, phrases)
            query_boost = 0.0
            if query_tokens:
                corpus = (
                    f"{item.get('title', '')} {item.get('overview', '')} "
                    f"{item.get('genres', '')} {item.get('tier2_keywords', '')}"
                ).lower()
                token_matches = sum(1 for token in query_tokens if token in corpus)
                phrase_matches = sum(1 for phrase in query_phrases if phrase in corpus)
                if token_matches or phrase_matches:
                    query_boost = min(token_matches * 0.4 + phrase_matches * 0.6, 2.0)

                # Overview word overlap with referenced titles (story-idea similarity)
                if ref_keywords:
                    overview_words = set(
                        re.findall(r"\w+", str(item.get("overview") or "").lower())
                    )
                    overlap = sum(
                        1 for w in overview_words if len(w) > 3 and w in ref_keywords
                    )
                    query_boost += min(overlap * 0.1, 0.5)

            final = base + bonus + query_boost

            item["recommendation_score"] = round(final, 2)
            item["taste_bonus"] = round(bonus, 2)
            item["query_boost"] = round(query_boost, 2)
            scored.append((final, item))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored]

    # ------------------------------------------------------------------
    # Main Recommendation Pipeline
    # ------------------------------------------------------------------
    def recommend(
        self,
        mood_id: int = 8,
        exclude_watched: bool = True,
        era_preference: Optional[str] = None,
        duration_preference: Optional[str] = None,
        top_n: int = 5,
        sub_mood: Optional[str] = None,
        query: Optional[str] = None,
        media_type: str = "movie",
        genres_override: Optional[List[str]] = None,
        min_rating_override: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """Executes the enhanced recommendation pipeline.

        Args:
            mood_id: 1-8 mood index.
            exclude_watched: Filter out movies already rated/watched.
            era_preference: 'modern', '2000s', 'classic', or None.
            duration_preference: 'short', 'standard', 'epic', or None.
            top_n: Number of items to return.
            sub_mood: Specific sub-mood title.
            query: Free-form text describing vibes, actors, or themes.
            media_type: 'movie' or 'tv'.
            genres_override: Optional list of explicit genre names.
            min_rating_override: Optional custom minimum rating floor.
        """
        mood_info = MOOD_MAPPINGS.get(mood_id, MOOD_MAPPINGS[8])

        # Resolve genres
        if genres_override and len(genres_override) > 0:
            genres = genres_override
        elif sub_mood:
            genres = None
            for sm in mood_info.get("submoods", []):
                if sm["name"].lower() == sub_mood.lower():
                    genres = sm["genres"]
                    break
            if not genres:
                genres = mood_info["genres"]
        else:
            genres = mood_info["genres"]

        # TV shows use different TMDB genre naming — expand with aliases so
        # the movie-oriented mood mappings also match TV catalogues.
        if media_type == "tv" and genres:
            expanded: List[str] = list(genres)
            for g in genres:
                expanded.extend(TV_GENRE_ALIASES.get(g.strip().lower(), []))
            genres = list(dict.fromkeys(expanded))

        # ── Stage 1: Baseline Rating Rule ──────────────────────────
        min_rating = 6.0 if min_rating_override is None else min_rating_override
        if query:
            q_lower = query.lower()
            if any(kw in q_lower for kw in GUILTY_PLEASURE_KEYWORDS):
                min_rating = 0.0
                print("[Recommender] Guilty-pleasure mode: rating floor suppressed", flush=True)

        # ── Stage 1: Hard Metadata Filtering ───────────────────────
        candidates = self.db.get_candidates(
            media_type=media_type,
            genres=genres if genres else None,
            exclude_watched=exclude_watched,
            min_rating=min_rating,
        )

        if not candidates and exclude_watched:
            print("[Recommender] No unwatched matches. Checking full catalogue...", flush=True)
            candidates = self.db.get_candidates(
                media_type=media_type,
                genres=genres if genres else None,
                exclude_watched=False,
                min_rating=min_rating,
            )

        if not candidates:
            # If still none, check without genre restriction
            candidates = self.db.get_candidates(
                media_type=media_type,
                genres=None,
                exclude_watched=False,
                min_rating=0.0,
            )

        if not candidates:
            return []

        # ── Era Filter ─────────────────────────────────────────────
        if era_preference == "modern":
            filtered = [c for c in candidates if c.get("release_year") and c["release_year"] >= 2015]
            if filtered: candidates = filtered
        elif era_preference == "2000s":
            filtered = [c for c in candidates if c.get("release_year") and 2000 <= c["release_year"] < 2015]
            if filtered: candidates = filtered
        elif era_preference == "classic":
            filtered = [c for c in candidates if c.get("release_year") and c["release_year"] < 2000]
            if filtered: candidates = filtered

        # ── Runtime Filter (Movies only) ───────────────────────────
        if media_type == "movie" and duration_preference:
            if duration_preference == "short":
                filtered = [c for c in candidates if c.get("runtime") and c["runtime"] < 105]
                if filtered: candidates = filtered
            elif duration_preference == "standard":
                filtered = [c for c in candidates if c.get("runtime") and 105 <= c["runtime"] <= 140]
                if filtered: candidates = filtered
            elif duration_preference == "epic":
                filtered = [c for c in candidates if c.get("runtime") and c["runtime"] > 140]
                if filtered: candidates = filtered

        # ── Stage 2: Tier 2 Profiling (top candidates) ─────────────
        pool = candidates[: min(len(candidates), top_n * 2)]
        for item in pool:
            self.ensure_tier2(item)

        # ── Stage 3: Personal Taste & Context Scoring ──────────────
        taste = self.db.get_user_taste_profile(min_rating=7.0)
        ranked = self.rank_candidates(candidates, taste, query=query)

        # ── Optional: Vector Re-ranking (drop-in when a model is deployed) ──
        # get_vector_embeddings() stays available for a future ONNX drop-in;
        # without models/model.onnx the SQL/taste pipeline above is authoritative.
        return ranked[:top_n]


# ===========================================================================
# Helpers
# ===========================================================================
def _split_lower(csv: Optional[str]) -> List[str]:
    """Splits a comma-separated string into lowercase stripped tokens."""
    if not csv:
        return []
    return [t.strip().lower() for t in str(csv).split(",") if t.strip()]
