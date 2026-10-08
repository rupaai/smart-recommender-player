"""
Backfill TMDB franchise/collection data (utils/backfill_collections.py)

Fills media_items.collection_id / collection_name / collection_order so the
recommender serves series in watch order (Philosopher's Stone before
Deathly Hallows) instead of whichever sequel scores highest.

Two passes, both resumable — safe to interrupt and re-run:
  1. GET /movie/{id}       -> belongs_to_collection (most-voted films first)
  2. GET /collection/{id}  -> every part, ordered by release date

Run from your own terminal (needs internet + TMDB_API_KEY in .env):

    uv run python utils/backfill_collections.py
    uv run python utils/backfill_collections.py --limit 2000   # quick partial run

DATABASE_PATH overrides the catalogue (default: catalogue_full.db).
"""

from __future__ import annotations

import argparse
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


def fetch_movie_collection(session: requests.Session, tmdb_id: int) -> Any:
    data = _get(session, f"/movie/{tmdb_id}")
    if data in (None, NOT_FOUND):
        return data
    coll = data.get("belongs_to_collection") or None
    if not coll:
        return (None, None)
    return (int(coll["id"]), coll.get("name"))


def fetch_collection_parts(session: requests.Session, collection_id: int) -> Optional[Tuple[str, List[int]]]:
    data = _get(session, f"/collection/{collection_id}")
    if data in (None, NOT_FOUND):
        return None
    parts = data.get("parts") or []
    # Unreleased/undated parts go last; ties broken by TMDB id.
    parts.sort(key=lambda p: (p.get("release_date") or "9999-99-99", p.get("id") or 0))
    return data.get("name"), [int(p["id"]) for p in parts if p.get("id")]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="only look up the N most-voted unchecked films")
    args = ap.parse_args()

    if not API_KEY:
        print("[Collections] TMDB_API_KEY missing from .env — aborting.")
        return

    db_path = os.getenv("DATABASE_PATH") or "catalogue_full.db"
    db = DatabaseManager(db_path)
    session = requests.Session()

    # ---- Pass 1: which collection does each film belong to? ----
    pending = db.get_movies_missing_collection()
    if args.limit:
        pending = pending[: args.limit]
    total = len(pending)
    print(f"[Collections] Pass 1: {total} films to look up in {db_path}")
    done = in_series = missing = errors = 0
    started = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(fetch_movie_collection, session, int(it["tmdb_id"])): it for it in pending}
        for fut in as_completed(futures):
            item = futures[fut]
            res = fut.result()
            done += 1
            if res is None:
                errors += 1                      # transient — left unchecked for next run
            elif res == NOT_FOUND:
                db.set_item_collection(item["id"], None, None, checked=-1)
                missing += 1
            else:
                cid, name = res
                db.set_item_collection(item["id"], cid, name, checked=1)
                if cid:
                    in_series += 1
            if done % 500 == 0 or done == total:
                rate = done / max(time.time() - started, 0.001)
                eta = (total - done) / max(rate, 0.001) / 60
                print(f"[Collections] {done}/{total} — {in_series} in a series, "
                      f"{missing} not on TMDB, {errors} errors — ~{eta:.0f} min left")

    # ---- Pass 2: watch order inside each collection ----
    coll_ids = db.get_collections_missing_order()
    print(f"[Collections] Pass 2: ordering {len(coll_ids)} collections")
    ordered = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(fetch_collection_parts, session, cid): cid for cid in coll_ids}
        for fut in as_completed(futures):
            cid = futures[fut]
            res = fut.result()
            if res:
                name, tmdb_ids = res
                db.set_collection_order(cid, name, tmdb_ids)
                ordered += 1

    print(f"[Collections] Done. {in_series} films linked to a series this run; "
          f"{ordered}/{len(coll_ids)} collections ordered. "
          f"{errors} transient errors — re-run to retry them.")


if __name__ == "__main__":
    main()
