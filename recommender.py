"""
Block 4: Recommender Engine (recommender.py)
Interactive mood-based recommender with dynamic Tier 2 deep profiling,
taste biasing from watch history, and playback link display.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple

import requests
import yaml
from database import DatabaseManager
from dotenv import load_dotenv


class BaseRecommender(ABC):
    """Abstract interface for media recommenders."""

    @abstractmethod
    def recommend(self, **kwargs: Any) -> List[Dict[str, Any]]:
        """Generates recommendations based on inputs."""
        pass


class MoodRecommender(BaseRecommender):
    """
    Recommends media based on mood, interactive narrowing, dynamic TMDB Tier 2 profiling,
    and historical rating preferences.
    """

    MOOD_MAPPINGS: Dict[int, Dict[str, Any]] = {
        1: {
            "name": "😊 Happy / Feel-Good",
            "genres": ["Comedy", "Family", "Animation", "Music"],
            "vibe": "Uplifting, lighthearted, and cheerful",
            "submoods": [
                {"name": "Family Fun", "genres": ["Family", "Animation"]},
                {"name": "Lighthearted Comedy", "genres": ["Comedy", "Romance"]}
            ]
        },
        2: {
            "name": "😢 Sad / Emotional",
            "genres": ["Drama", "Romance"],
            "vibe": "Poignant, touching, and emotionally resonant",
            "submoods": [
                {"name": "Tearjerker", "genres": ["Drama"]},
                {"name": "Coming of Age", "genres": ["Drama", "Comedy"]}
            ]
        },
        3: {
            "name": "😱 Thrilling / On the Edge",
            "genres": ["Action", "Thriller", "Adventure"],
            "vibe": "High-octane, intense, and suspenseful",
            "submoods": [
                {"name": "Action-Packed", "genres": ["Action", "Adventure"]},
                {"name": "Psychological Thriller", "genres": ["Thriller", "Mystery"]}
            ]
        },
        4: {
            "name": "👻 Scary / Spooky",
            "genres": ["Horror", "Mystery"],
            "vibe": "Eerie, chilling, and atmospheric dread",
            "submoods": [
                {"name": "Supernatural Horror", "genres": ["Horror", "Fantasy"]},
                {"name": "Suspenseful Mystery", "genres": ["Mystery", "Thriller"]}
            ]
        },
        5: {
            "name": "❤️ Romantic",
            "genres": ["Romance"],
            "vibe": "Passionate, sweet, and heartwarming",
            "submoods": [
                {"name": "Romantic Comedy", "genres": ["Romance", "Comedy"]},
                {"name": "Period Romance", "genres": ["Romance", "History"]}
            ]
        },
        6: {
            "name": "🤯 Mind-Bending / Thoughtful",
            "genres": ["Science Fiction", "Sci-Fi", "Mystery"],
            "vibe": "Complex, cerebral, and reality-questioning",
            "submoods": [
                {"name": "Sci-Fi Adventure", "genres": ["Science Fiction", "Adventure"]},
                {"name": "Mind-Bending Mystery", "genres": ["Mystery", "Thriller"]}
            ]
        },
        7: {
            "name": "😌 Relaxing / Chill",
            "genres": ["Documentary", "Animation", "Family"],
            "vibe": "Calming, visually beautiful, and comforting",
            "submoods": [
                {"name": "Nature Documentary", "genres": ["Documentary"]},
                {"name": "Animated Comfort", "genres": ["Animation", "Family"]}
            ]
        },
        8: {
            "name": "🎲 Surprise Me!",
            "genres": [],  # No genre filter
            "vibe": "Wildcard selection from your entire catalogue",
            "submoods": [
                {"name": "Staff Picks", "genres": []},
                {"name": "Hidden Gems", "genres": []}
            ]
        },
    }

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
        self.session = requests.Session()

    def fetch_tier2_profile(self, tmdb_id: int, media_type: str = "movie") -> Tuple[str, str]:
        """
        Dynamically queries TMDB for keywords and reviews to build a deep profile.
        Returns (keywords_str, reviews_summary).
        """
        if not self.api_key:
            return "", ""

        keywords: List[str] = []
        reviews_summary = ""

        # 1. Fetch Keywords
        kw_endpoint = f"/tv/{tmdb_id}/keywords" if media_type == "tv" else f"/movie/{tmdb_id}/keywords"
        try:
            resp = self.session.get(
                f"{self.TMDB_BASE_URL}{kw_endpoint}",
                params={"api_key": self.api_key},
                timeout=8,
            )
            if resp.status_code == 200:
                data = resp.json()
                kw_list = data.get("results") or data.get("keywords") or []
                keywords = [k["name"].lower() for k in kw_list[:8]]
        except Exception:
            pass

        # 2. Fetch User Reviews
        review_endpoint = f"/tv/{tmdb_id}/reviews" if media_type == "tv" else f"/movie/{tmdb_id}/reviews"
        try:
            resp = self.session.get(
                f"{self.TMDB_BASE_URL}{review_endpoint}",
                params={"api_key": self.api_key},
                timeout=8,
            )
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("results", [])
                if results:
                    # Extract the first review excerpt (cleaned)
                    first_rev = results[0].get("content", "").replace("\r\n", " ").replace("\n", " ").strip()
                    sentences = [s.strip() for s in first_rev.split(".") if len(s.strip()) > 15]
                    if sentences:
                        reviews_summary = f'"{sentences[0]}."'
        except Exception:
            pass

        return ", ".join(keywords), reviews_summary

    def ensure_tier2_profiling(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """
        Ensures an item has Tier 2 profiling. If not cached, fetches dynamically and writes to DB.
        """
        if not self.enable_tier2 or not item.get("tmdb_id"):
            return item

        # Check if already cached in DB
        if item.get("tier2_profiled_at") and (item.get("tier2_keywords") or item.get("tier2_reviews_summary")):
            return item

        # Fetch and cache dynamically
        item_id = item["id"]
        tmdb_id = item["tmdb_id"]
        media_type = item.get("media_type") or "movie"

        print(f"  [Deep Profiler] Fetching public reviews & themes for: '{item['title']}'...")
        keywords, review_summary = self.fetch_tier2_profile(tmdb_id, media_type)

        self.db.update_tier2_profile(item_id, keywords, review_summary)
        item["tier2_keywords"] = keywords
        item["tier2_reviews_summary"] = review_summary
        item["tier2_profiled_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"  [✓] Profile cached in database.")
        return item

    def rank_candidates(
        self,
        candidates: List[Dict[str, Any]],
        taste_profile: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """
        Ranks candidates by:
        Base TMDB rating + Taste affinity bonus (from past user ratings).
        """
        preferred_genres = taste_profile.get("preferred_genres", {})
        preferred_keywords = taste_profile.get("preferred_keywords", {})

        scored_items: List[Tuple[float, Dict[str, Any]]] = []
        for item in candidates:
            base_rating = float(item.get("rating") or 5.0)
            taste_bonus = 0.0

            # Genre affinity
            item_genres = [g.strip().lower() for g in (item.get("genres") or "").split(",") if g.strip()]
            for g in item_genres:
                if g in preferred_genres:
                    taste_bonus += preferred_genres[g] * 0.25

            # Keyword affinity
            item_keywords = [k.strip().lower() for k in (item.get("tier2_keywords") or "").split(",") if k.strip()]
            for k in item_keywords:
                if k in preferred_keywords:
                    taste_bonus += preferred_keywords[k] * 0.2

            # Cap taste bonus to avoid completely overriding TMDB rating
            taste_bonus = min(taste_bonus, 2.0)
            final_score = base_rating + taste_bonus

            item["recommendation_score"] = round(final_score, 2)
            item["taste_bonus"] = round(taste_bonus, 2)
            scored_items.append((final_score, item))

        scored_items.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored_items]

    def recommend(
        self,
        mood_id: int,
        exclude_watched: bool = True,
        era_preference: Optional[str] = None,
        duration_preference: Optional[str] = None,
        top_n: int = 5,
        sub_mood: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Executes the recommendation pipeline for a selected mood."""
        mood_info = self.MOOD_MAPPINGS.get(mood_id, self.MOOD_MAPPINGS[8])
        
        # Use sub-mood genres if specified
        genres = None
        if sub_mood:
            for sm in mood_info.get("submoods", []):
                if sm["name"].lower() == sub_mood.lower():
                    genres = sm["genres"]
                    break
        
        # Fall back to main mood genres if no sub-mood match
        if not genres:
            genres = mood_info["genres"]

        # Step 1: Candidate query from database
        candidates = self.db.get_candidates(
            media_type="movie",
            genres=genres if genres else None,
            exclude_watched=exclude_watched,
        )

        if not candidates:
            # Fallback: if excluded watched yielded 0, retry including watched
            if exclude_watched:
                print("\n[Notice] No unwatched movies match this mood. Checking your full catalogue...")
                candidates = self.db.get_candidates(
                    media_type="movie",
                    genres=genres if genres else None,
                    exclude_watched=False,
                )

        if not candidates:
            return []

        # Step 2: Apply era narrowing filter if requested
        if era_preference == "modern":
            candidates = [c for c in candidates if c.get("release_year") and c["release_year"] >= 2015] or candidates
        elif era_preference == "2000s":
            candidates = [
                c for c in candidates if c.get("release_year") and 2000 <= c["release_year"] < 2015
            ] or candidates
        elif era_preference == "classic":
            candidates = [c for c in candidates if c.get("release_year") and c["release_year"] < 2000] or candidates

        # Step 3: Apply duration narrowing filter if requested
        if duration_preference == "short":
            candidates = [c for c in candidates if c.get("runtime") and c["runtime"] < 105] or candidates
        elif duration_preference == "standard":
            candidates = [c for c in candidates if c.get("runtime") and 105 <= c["runtime"] <= 140] or candidates
        elif duration_preference == "epic":
            candidates = [c for c in candidates if c.get("runtime") and c["runtime"] > 140] or candidates

        # Step 4: Dynamic Tier 2 profiling for top candidates
        profiling_pool = candidates[: min(len(candidates), top_n * 2)]
        for item in profiling_pool:
            self.ensure_tier2_profiling(item)

        # Step 5: Rank with taste profile
        taste_profile = self.db.get_user_taste_profile(min_rating=7.0)
        ranked = self.rank_candidates(candidates, taste_profile)

        return ranked[:top_n]


def print_card(index: int, item: Dict[str, Any]) -> None:
    """Renders a movie card in clean ASCII format."""
    title = item.get("title", "Unknown")
    year = item.get("release_year") or "N/A"
    rating = item.get("rating") or "N/A"
    runtime = f"{item['runtime']} min" if item.get("runtime") else "N/A"
    genres = item.get("genres") or "N/A"
    score = item.get("recommendation_score", rating)
    taste_bonus = item.get("taste_bonus", 0.0)
    file_path = item.get("file_path", "No file found")
    keywords = item.get("tier2_keywords") or "Atmospheric, Character-driven"
    reviews_summary = item.get("tier2_reviews_summary")
    overview = item.get("overview") or "No synopsis available."

    bonus_tag = f" (+{taste_bonus} taste boost)" if taste_bonus > 0 else ""

    print("=" * 80)
    print(f"  🎬 #{index}. {title.upper()} ({year})")
    print("-" * 80)
    print(f"  ⭐ TMDB Rating:    ★ {rating}/10  |  Match Score: {score}/10{bonus_tag}")
    print(f"  ⏱  Duration:       {runtime}")
    print(f"  🏷  Genres:         {genres}")
    print(f"  🎭 Vibe / Tags:    {keywords}")
    if reviews_summary:
        print(f"  💬 Audience Buzz:  {reviews_summary}")
    print(f"  📺 Jellyfin File:  {file_path}")
    print(f"\n  📝 Synopsis:\n     {overview}\n")


def display_history(db_manager: DatabaseManager) -> None:
    """Displays user watch history."""
    history = db_manager.get_watch_history(limit=25)
    print("\n" + "=" * 60)
    print("               WATCH HISTORY & RATINGS")
    print("=" * 60)
    if not history:
        print("  No movies watched yet. Your logged movies will appear here!\n")
        return

    for h in history:
        rating_str = f"★ {h['user_rating']}/10" if h.get("user_rating") else "Unrated"
        date_str = str(h.get("watched_at", ""))[:10]
        notes = f" - \"{h['notes']}\"" if h.get("notes") else ""
        print(f"  • [{date_str}] {h['title']} ({h.get('release_year') or 'N/A'}) - {rating_str}{notes}")
    print("=" * 60 + "\n")


def load_config(config_path: str = "config.yaml") -> Dict[str, Any]:
    """Loads configuration from YAML file or falls back to defaults."""
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def interactive_recommender(recommender: MoodRecommender, config: Dict[str, Any]) -> None:
    """Interactive CLI movie night recommender flow."""
    rec_cfg = config.get("recommender", {})
    top_n = rec_cfg.get("default_top_n", 3)
    exclude_watched = rec_cfg.get("exclude_watched", True)

    print("\n" + "=" * 80)
    print("       🍿 WELCOME TO YOUR PERSONAL MOVIE RECOMMENDER 🍿")
    print("=" * 80)
    print("What kind of movie night are you in the mood for tonight?\n")

    for num, mood in recommender.MOOD_MAPPINGS.items():
        print(f"  [{num}] {mood['name']}")
        print(f"      ↳ {mood['vibe']}")

    # 1. Select mood
    while True:
        choice = input("\nEnter your mood (1-8): ").strip()
        if choice.isdigit() and 1 <= int(choice) <= 8:
            mood_id = int(choice)
            mood_info = recommender.MOOD_MAPPINGS[mood_id]
            
            # Show sub-moods if available
            if mood_info.get("submoods"):
                print("\nAvailable sub-moods:")
                for i, sm in enumerate(mood_info["submoods"], 1):
                    print(f"  [{i}] {sm['name']}")
                sub_choice = input("Select sub-mood (1-2, or Enter for main mood): ").strip()
                if sub_choice.isdigit() and 1 <= int(sub_choice) <= len(mood_info["submoods"]):
                    sub_mood = mood_info["submoods"][int(sub_choice)-1]["name"]
            break
        print("Please choose a valid number between 1 and 8.")

    # 2. Narrowing: Era
    print("\nAny era preference?")
    print("  [1] Modern (2015 - Present)")
    print("  [2] 2000s (2000 - 2014)")
    print("  [3] Classic (Pre-2000)")
    print("  [4] Any era")
    era_choice = input("Select era (1-4, default 4): ").strip()
    era_map = {"1": "modern", "2": "2000s", "3": "classic", "4": None}
    era_pref = era_map.get(era_choice, None)

    # 3. Narrowing: Runtime
    print("\nHow long do you want to watch for?")
    print("  [1] Quick watch (< 105 mins)")
    print("  [2] Standard feature (105 - 140 mins)")
    print("  [3] Epic movie (> 140 mins)")
    print("  [4] Any length")
    dur_choice = input("Select duration (1-4, default 4): ").strip()
    dur_map = {"1": "short", "2": "standard", "3": "epic", "4": None}
    dur_pref = dur_map.get(dur_choice, None)

    print("\nAnalyzing your catalogue & fetching deep profiles...")
    results = recommender.recommend(
        mood_id=mood_id,
        exclude_watched=exclude_watched,
        era_preference=era_pref,
        duration_preference=dur_pref,
        top_n=top_n,
    )

    if not results:
        print("\n[!] No matching movies found with those criteria. Try expanding your filters!")
        return

    print(f"\nHere are your Top {len(results)} personalized recommendations for tonight:\n")
    for idx, item in enumerate(results, 1):
        print_card(idx, item)

    # Post-recommendation: Log watch
    watch_prompt = input("Did you pick one of these to watch tonight? (1-3, or N to skip): ").strip()
    if watch_prompt.isdigit() and 1 <= int(watch_prompt) <= len(results):
        chosen_item = results[int(watch_prompt) - 1]
        print(f"\nAwesome! Enjoy watching '{chosen_item['title']}' on your Jellyfin TV app.")
        rate_input = input("Give it a rating now if you've seen it before (1-10, or Enter to skip): ").strip()
        user_rating = float(rate_input) if rate_input.replace(".", "", 1).isdigit() else None
        notes_input = input("Any thoughts or notes? (or Enter to skip): ").strip()
        notes = notes_input if notes_input else None

        recommender.db.record_watch(
            media_item_id=chosen_item["id"],
            user_rating=user_rating,
            notes=notes,
        )
        print(f"[✓] Saved to your watch history! This will help tune your future recommendations.\n")


def main() -> None:
    load_dotenv()
    api_key = os.getenv("TMDB_API_KEY", "").strip()

    parser = argparse.ArgumentParser(description="Home Streaming Movie Recommender")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml file")
    parser.add_argument("--mood", type=int, choices=range(1, 9), help="Mood number (1-8) for headless/non-interactive mode")
    parser.add_argument("--sub-mood", type=str, help="Sub-mood name for more specific recommendations")
    parser.add_argument("--era", choices=["modern", "2000s", "classic"], help="Era filter")
    parser.add_argument("--duration", choices=["short", "standard", "epic"], help="Duration filter")
    parser.add_argument("--top-n", type=int, default=3, help="Number of recommendations to return")
    parser.add_argument("--include-watched", action="store_true", help="Include already watched movies")
    parser.add_argument("--history", action="store_true", help="View watch history and personal ratings")
    args = parser.parse_args()

    config = load_config(args.config)
    db_path = config.get("database_path", "catalogue.db")
    enable_tier2 = config.get("recommender", {}).get("enable_tier2_profiling", True)

    db_manager = DatabaseManager(db_path)
    recommender = MoodRecommender(db_manager=db_manager, api_key=api_key, enable_tier2=enable_tier2)

    if args.history:
        display_history(db_manager)
        return

    # Non-interactive mode
    if args.mood:
        print(f"\n--- Recommender Mode (Mood: {args.mood}) ---")
        if args.sub_mood:
            print(f"Sub-mood: {args.sub_mood}")
        results = recommender.recommend(
            mood_id=args.mood,
            exclude_watched=not args.include_watched,
            era_preference=args.era,
            duration_preference=args.duration,
            top_n=args.top_n,
            sub_mood=args.sub_mood,
        )
        for idx, item in enumerate(results, 1):
            print_card(idx, item)
        return

    # Interactive mode
    interactive_recommender(recommender, config)


if __name__ == "__main__":
    main()
