"""
Backfill TV season counts (utils/backfill_season_counts.py)

Fills media_items.season_count so the Rate tab can offer one quick verdict per
season (and hide the row for one-season shows).

Two passes, both resumable — safe to interrupt and re-run:
  1. Offline: shows with files get the highest season_number in media_sources.
  2. GET /tv/{id} -> number_of_seasons (most-voted shows first). TMDB's count
     leaves out "Specials" (season 0), which is what we want.

Run from your own terminal (needs internet + TMDB_API_KEY in .env):

    uv run python utils/backfill_season_counts.py
    uv run python utils/backfill_season_counts.py --limit 500   # quick partial run
    uv run python utils/backfill_season_counts.py --offline     # pass 1 only

DATABASE_PATH overrides the catalogue (default: catalogue_full.db).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database import DatabaseManager  # noqa: E402

load_dotenv()
API_KEY = os.getenv("TMDB_API_KEY", "").strip()
BASE_URL = "https://api.themoviedb.org/3"
WORKERS = 8
REQUEST_TIMEOUT = 10

NOT_FOUND = "not_found"


def _get(session: requests.Session, path: str) -> Any:
    """JSON body, NOT_FOUND on 404, None on transient failure (retried next run)."""
    for attempt in range(3):
        try:
            resp = session.get(f"{BASE_URL}{path}", params={"api_key": API_KEY},
                               timeout=REQUEST_TIMEOUT)
        except requests.RequestException:
            time.sleep(1 + attempt)
            continue
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 404:
            return NOT_FOUND
        if resp.status_code == 429:  # rate limited — back off
            time.sleep(float(resp.headers.get("Retry-After", 2)))
            continue
        return None
    return None


def fetch_season_count(session: requests.Session, tmdb_id: int) -> Any:
    data = _get(session, f"/tv/{tmdb_id}")
    if data in (None, NOT_FOUND):
        return data
    count = data.get("number_of_seasons")
    return int(count) if isinstance(count, int) and count > 0 else 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="only look up the N most-voted unchecked shows")
    ap.add_argument("--offline", action="store_true", help="only fill counts from your files (no TMDB)")
    args = ap.parse_args()

    db_path = os.getenv("DATABASE_PATH") or "catalogue_full.db"
    db = DatabaseManager(db_path)

    # ---- Pass 1: from files we already have ----
    filled = db.fill_season_counts_from_files()
    print(f"[Seasons] Pass 1: {filled} shows got a season count from their files in {db_path}")
    if args.offline:
        return
    if not API_KEY:
        print("[Seasons] TMDB_API_KEY missing from .env — skipping the TMDB pass.")
        return

    # ---- Pass 2: TMDB number_of_seasons ----
    pending = db.get_tv_missing_season_count()
    if args.limit:
        pending = pending[: args.limit]
    total = len(pending)
    print(f"[Seasons] Pass 2: {total} shows to look up on TMDB")
    session = requests.Session()
    done = multi = missing = errors = 0
    started = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(fetch_season_count, session, int(it["tmdb_id"])): it for it in pending}
        for fut in as_completed(futures):
            item = futures[fut]
            res = fut.result()
            done += 1
            if res is None:
                errors += 1                      # transient — left unchecked for next run
            elif res == NOT_FOUND:
                db.set_season_count(item["id"], None, checked=-1)
                missing += 1
            else:
                db.set_season_count(item["id"], res or None, checked=1)
                if res > 1:
                    multi += 1
            if done % 500 == 0 or done == total:
                rate = done / max(time.time() - started, 0.001)
                eta = (total - done) / max(rate, 0.001) / 60
                print(f"[Seasons] {done}/{total} — {multi} with 2+ seasons, "
                      f"{missing} not on TMDB, {errors} errors — ~{eta:.0f} min left")

    print(f"[Seasons] Done. {errors} transient errors — re-run to retry them.")


if __name__ == "__main__":
    main()
