"""
Block 2: Metadata Enricher (enricher.py)
Fetches canonical metadata from TMDB for unenriched media items and updates the database.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

import requests
import yaml
from database import DatabaseManager
from dotenv import load_dotenv


class BaseEnricher(ABC):
    """Abstract interface for metadata enrichers."""

    @abstractmethod
    def enrich(self, limit: Optional[int] = None) -> Dict[str, int]:
        """Runs enrichment on unenriched items and returns count summary."""
        pass


class TMDBEnricher(BaseEnricher):
    """Fetches movie and TV metadata from TMDB API v3."""

    BASE_URL = "https://api.themoviedb.org/3"
    IMAGE_BASE_URL = "https://image.tmdb.org/t/p/w500"

    def __init__(
        self,
        api_key: str,
        db_manager: DatabaseManager,
        region: str = "GB",
        delay_seconds: float = 0.25,
    ):
        if not api_key:
            raise ValueError(
                "TMDB API Key is missing. Please set TMDB_API_KEY in your .env file."
            )
        self.api_key = api_key
        self.db = db_manager
        self.region = region
        self.delay = delay_seconds
        self.session = requests.Session()

    def _get(self, endpoint: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Performs a rate-limited GET request to TMDB API."""
        time.sleep(self.delay)
        params["api_key"] = self.api_key
        url = f"{self.BASE_URL}{endpoint}"
        try:
            resp = self.session.get(url, params=params, timeout=10)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 2))
                print(f"[Enricher] Rate limit hit. Waiting {retry_after}s...")
                time.sleep(retry_after)
                return self._get(endpoint, params)
            else:
                print(f"[Enricher] API error {resp.status_code} for {url}")
                return None
        except requests.RequestException as e:
            print(f"[Enricher] Network request failed for {url}: {e}")
            return None

    def search_movie(self, title: str, year: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Searches TMDB for a movie by title and optional year."""
        params: Dict[str, Any] = {"query": title, "region": self.region, "include_adult": False}
        if year:
            params["year"] = year

        data = self._get("/search/movie", params)
        results = data.get("results", []) if data else []

        # If no results and year was provided, retry search without year
        if not results and year:
            params.pop("year", None)
            data = self._get("/search/movie", params)
            results = data.get("results", []) if data else []

        if results:
            # Pick best match (first result)
            return results[0]
        return None

    def search_tv(self, title: str, year: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Searches TMDB for a TV show by title and optional first air year."""
        params: Dict[str, Any] = {"query": title, "include_adult": False}
        if year:
            params["first_air_date_year"] = year

        data = self._get("/search/tv", params)
        results = data.get("results", []) if data else []

        if not results and year:
            params.pop("first_air_date_year", None)
            data = self._get("/search/tv", params)
            results = data.get("results", []) if data else []

        if results:
            return results[0]
        return None

    def get_movie_details(self, tmdb_id: int) -> Optional[Dict[str, Any]]:
        """Fetches full movie details including runtime and full genre objects."""
        return self._get(f"/movie/{tmdb_id}", {})

    def get_tv_details(self, tmdb_id: int) -> Optional[Dict[str, Any]]:
        """Fetches full TV show details."""
        return self._get(f"/tv/{tmdb_id}", {})

    def enrich(self, limit: Optional[int] = None) -> Dict[str, int]:
        """Processes unenriched items from the database and populates TMDB metadata."""
        items = self.db.get_unenriched_items()
        if limit:
            items = items[:limit]

        if not items:
            print("[Enricher] All items in database are already enriched.")
            return {"total": 0, "enriched": 0, "failed": 0}

        print(f"[Enricher] Found {len(items)} unenriched media item(s).")
        enriched_count = 0
        failed_count = 0

        for item in items:
            item_id = item["id"]
            title = item["title"]
            year = item["release_year"]
            media_type = item["media_type"] or "movie"

            print(f"[Enricher] Looking up {media_type.upper()}: '{title}' ({year or 'N/A'})...")

            if media_type == "tv":
                match = self.search_tv(title, year)
                if match:
                    details = self.get_tv_details(match["id"])
                    if details:
                        genres = ", ".join(g["name"] for g in details.get("genres", []))
                        genre_ids = ", ".join(str(g["id"]) for g in details.get("genres", []))
                        first_air = details.get("first_air_date") or ""
                        rel_year = int(first_air[:4]) if len(first_air) >= 4 and first_air[:4].isdigit() else year
                        runtimes = details.get("episode_run_time") or []
                        runtime = int(runtimes[0]) if runtimes else None
                        poster_path = details.get("poster_path")
                        poster_url = f"{self.IMAGE_BASE_URL}{poster_path}" if poster_path else None

                        self.db.update_item_metadata(
                            item_id=item_id,
                            tmdb_id=details["id"],
                            title=details.get("name", title),
                            original_title=details.get("original_name"),
                            release_year=rel_year,
                            overview=details.get("overview"),
                            genres=genres,
                            genre_ids=genre_ids,
                            rating=round(float(details.get("vote_average", 0.0)), 1),
                            runtime=runtime,
                            poster_url=poster_url,
                        )
                        enriched_count += 1
                        print(f"  [✓] Enriched: '{details.get('name')}' (Rating: {details.get('vote_average')})")
                        continue

            else:
                # Movie lookup
                match = self.search_movie(title, year)
                if match:
                    details = self.get_movie_details(match["id"])
                    if details:
                        genres = ", ".join(g["name"] for g in details.get("genres", []))
                        genre_ids = ", ".join(str(g["id"]) for g in details.get("genres", []))
                        rel_date = details.get("release_date") or ""
                        rel_year = int(rel_date[:4]) if len(rel_date) >= 4 and rel_date[:4].isdigit() else year
                        runtime = details.get("runtime")
                        poster_path = details.get("poster_path")
                        poster_url = f"{self.IMAGE_BASE_URL}{poster_path}" if poster_path else None

                        self.db.update_item_metadata(
                            item_id=item_id,
                            tmdb_id=details["id"],
                            title=details.get("title", title),
                            original_title=details.get("original_title"),
                            release_year=rel_year,
                            overview=details.get("overview"),
                            genres=genres,
                            genre_ids=genre_ids,
                            rating=round(float(details.get("vote_average", 0.0)), 1),
                            runtime=runtime,
                            poster_url=poster_url,
                        )
                        enriched_count += 1
                        print(f"  [✓] Enriched: '{details.get('title')}' ({rel_year}) - Rating: {details.get('vote_average')}")
                        continue

            print(f"  [✗] No TMDB match found for: '{title}' ({year})")
            failed_count += 1

        summary = {
            "total": len(items),
            "enriched": enriched_count,
            "failed": failed_count,
        }
        return summary


def load_config(config_path: str = "config.yaml") -> Dict[str, Any]:
    """Loads configuration from YAML file or falls back to defaults."""
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def main() -> None:
    load_dotenv()
    api_key = os.getenv("TMDB_API_KEY", "").strip()

    if not api_key or api_key == "your_key_here":
        print(
            "\n[Error] Missing TMDB API key!\n"
            "Please create a free TMDB account at https://www.themoviedb.org/settings/api\n"
            "and add your API key to the .env file like this:\n"
            "  TMDB_API_KEY=your_key_here\n"
        )
        sys.exit(1)

    parser = argparse.ArgumentParser(description="Enrich catalogue media items with TMDB metadata.")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml file")
    parser.add_argument("--limit", type=int, default=None, help="Max number of items to enrich in this run")
    args = parser.parse_args()

    config = load_config(args.config)
    db_path = config.get("database_path", "catalogue.db")
    region = config.get("region", "GB")

    db_manager = DatabaseManager(db_path)
    enricher = TMDBEnricher(api_key=api_key, db_manager=db_manager, region=region)

    print("--- Metadata Enricher Starting ---")
    summary = enricher.enrich(limit=args.limit)
    print("--- Enrichment Complete ---")
    print(f"Total items inspected: {summary['total']}")
    print(f"Successfully enriched: {summary['enriched']}")
    print(f"Failed to find match:  {summary['failed']}")


if __name__ == "__main__":
    main()
