"""
Backfill TMDB vote counts for the catalogue (utils/backfill_vote_counts.py)

One-time, resumable job that fills media_items.vote_count so the recommender's
quality cut can use real IMDb-style Bayesian weighting computed in SQL:

    score = (votes / (votes + 1000)) * rating + (1000 / (votes + 1000)) * 6.5

Run (DATABASE_PATH override supported, config.yaml default otherwise):

    DATABASE_PATH=catalogue_full.db uv run python utils/backfill_vote_counts.py

Safe to interrupt and re-run: items already holding a vote count are skipped.
"""

from __future__ import annotations

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database import DatabaseManager  # noqa: E402

load_dotenv()
API_KEY = os.getenv("TMDB_API_KEY", "").strip()
BASE_URL = "https://api.themoviedb.org/3"
WORKERS = 4            # polite TMDB concurrency
REQUEST_TIMEOUT = 10


def fetch_tmdb_votes(session: requests.Session, media_type: str, tmdb_id: int) -> Optional[Tuple[int, float]]:
    """Returns (vote_count, vote_average) or None if the item 404s / errors."""
    try:
        resp = session.get(
            f"{BASE_URL}/{media_type}/{tmdb_id}",
            params={"api_key": API_KEY},
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code != 200:
            return None
        data: Dict[str, Any] = resp.json()
        return int(data.get("vote_count") or 0), float(data.get("vote_average") or 0.0)
    except requests.RequestException:
        return None


def main() -> None:
    if not API_KEY:
        print("[Backfill] TMDB_API_KEY missing from .env — aborting.")
        return

    db_path = os.getenv("DATABASE_PATH") or "catalogue_full.db"
    db = DatabaseManager(db_path)
    pending = db.get_items_missing_vote_counts()
    total = len(pending)
    print(f"[Backfill] {total} items missing vote counts in {db_path}")
    if total == 0:
        print("[Backfill] Nothing to do.")
        return

    session = requests.Session()
    done = 0
    failed = 0
    started = time.time()

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {
            pool.submit(fetch_tmdb_votes, session, item["media_type"], item["tmdb_id"]): item
            for item in pending
        }
        for future in as_completed(futures):
            item = futures[future]
            result = future.result()
            done += 1
            if result is None:
                # Confirmed missing on TMDB (404/timeout) — mark -1 so it is
                # skipped on re-runs and excluded from the quality cut.
                db.update_vote_count(item["id"], -1)
                failed += 1
            else:
                vote_count, vote_average = result
                db.update_vote_count(item["id"], vote_count, vote_average)

            if done % 250 == 0 or done == total:
                rate = done / max(time.time() - started, 0.001)
                remaining = (total - done) / max(rate, 0.001) / 60.0
                print(f"[Backfill] {done}/{total} ({failed} not found) — ~{remaining:.0f} min remaining")

    print(f"[Backfill] Done. Filled {total - failed}, marked {failed} as not found.")


if __name__ == "__main__":
    main()