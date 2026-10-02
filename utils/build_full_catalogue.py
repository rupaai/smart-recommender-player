"""
Builds a full TMDB catalogue database with movies and TV shows separated.

Popularity-sorted TMDB discover pages (the API's hard cap is 500 pages per
endpoint => 10,000 movies + 10,000 TV = the full API-accessible catalogue).
Fully resumable: existing (media_type, tmdb_id) rows are skipped, and the
runtime details pass only fetches items whose runtime is still NULL.

Usage:
    uv run python utils/build_full_catalogue.py                  # full build
    uv run python utils/build_full_catalogue.py --max-pages 50   # quick test
    uv run python utils/build_full_catalogue.py --skip-runtime   # list data only
"""

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import DatabaseManager  # noqa: E402

TMDB_BASE = "https://api.themoviedb.org/3"
IMAGE_BASE_URL = "https://image.tmdb.org/t/p/w500"


class TmdbClient:
    """Rate-limited TMDB API v3 client with 429 backoff (enricher conventions)."""

    def __init__(self, api_key: str, delay: float = 0.12):
        self.api_key = api_key
        self.delay = delay
        self.session = requests.Session()

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        p = dict(params or {})
        p["api_key"] = self.api_key
        for _ in range(3):
            time.sleep(self.delay)
            try:
                resp = self.session.get(f"{TMDB_BASE}{path}", params=p, timeout=20)
            except requests.RequestException as e:
                print(f"[TMDB] Network error on {path}: {e}", flush=True)
                time.sleep(2)
                continue
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 2))
                print(f"[TMDB] Rate limited — waiting {wait}s...", flush=True)
                time.sleep(wait)
                continue
            print(f"[TMDB] HTTP {resp.status_code} for {path}", flush=True)
            return None
        return None


def fetch_genre_maps(tm: TmdbClient) -> tuple:
    movie_map = {g["id"]: g["name"] for g in ((tm.get("/genre/movie/list") or {}).get("genres") or [])}
    tv_map = {g["id"]: g["name"] for g in ((tm.get("/genre/tv/list") or {}).get("genres") or [])}
    return movie_map, tv_map


def discover_rows(
    tm: TmdbClient,
    media_type: str,
    pages: int,
    genre_map: Dict[int, str],
    db: DatabaseManager,
) -> int:
    """Fetches discover pages for one media type and upserts rows incrementally.

    Pages are fetched in parallel (deep TMDB discover pages get progressively
    slower server-side, so sequential fetching takes hours for 500 pages).
    """
    path = "/discover/movie" if media_type == "movie" else "/discover/tv"
    base_params = {
        "sort_by": "popularity.desc",
        "include_adult": "false",
        "vote_count.gte": "200" if media_type == "movie" else "100",
    }
    inserted_total = 0

    def fetch_page(page: int):
        return tm.get(path, {**base_params, "page": page})

    with ThreadPoolExecutor(max_workers=6) as pool:
        page_results = pool.map(fetch_page, range(1, pages + 1))
        for page, data in zip(range(1, pages + 1), page_results):
            if not data:
                print(f"[Discover] {media_type}: page {page} failed — stopping this type.", flush=True)
                break
            results = data.get("results") or []
            if not results:
                break
        rows = []
        for r in results:
            date = r.get("release_date") or r.get("first_air_date") or ""
            year = int(date[:4]) if len(date) >= 4 and date[:4].isdigit() else None
            gids = [int(g) for g in (r.get("genre_ids") or []) if str(g).lstrip("-").isdigit()]
            rows.append({
                "media_type": media_type,
                "tmdb_id": r["id"],
                "title": r.get("title") or r.get("name"),
                "original_title": r.get("original_title") or r.get("original_name"),
                "release_year": year,
                "overview": r.get("overview"),
                "genres": ", ".join(genre_map[g] for g in gids if g in genre_map) or None,
                "genre_ids": ", ".join(str(g) for g in gids) or None,
                "rating": round(float(r.get("vote_average") or 0.0), 1),
                "runtime": None,
                "poster_url": f"{IMAGE_BASE_URL}{r['poster_path']}" if r.get("poster_path") else None,
            })
        inserted_total += db.upsert_catalogue_items(rows)
        if page % 25 == 0 or page == pages:
            print(f"[Discover] {media_type}: page {page}/{pages} (inserted so far: {inserted_total})", flush=True)
    return inserted_total

def fill_runtime(
    tm: TmdbClient,
    db: DatabaseManager,
    workers: int = 8,
    limit: Optional[int] = None,
) -> None:
    """Fetches runtime via details calls for rows where runtime IS NULL.

    Resumable: a rerun only touches rows that are still missing runtime.
    """
    with db.get_connection() as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                """
                SELECT id, media_type, tmdb_id FROM media_items
                WHERE runtime IS NULL AND tmdb_id IS NOT NULL
                ORDER BY media_type, tmdb_id;
                """
            ).fetchall()
        ]
    if limit:
        rows = rows[:limit]
    if not rows:
        print("[Runtime] All items already have runtime — nothing to do.", flush=True)
        return
    print(f"[Runtime] Fetching details for {len(rows)} items ({workers} workers)...", flush=True)

    def work(row: Dict[str, Any]) -> int:
        tmdb_id = row["tmdb_id"]
        if row["media_type"] == "movie":
            details = tm.get(f"/movie/{tmdb_id}")
            runtime = details.get("runtime") if details else None
        else:
            details = tm.get(f"/tv/{tmdb_id}")
            if details:
                epi = details.get("episode_run_time") or []
                runtime = round(sum(epi) / len(epi)) if epi else None
            else:
                runtime = None
        if runtime:
            with db.get_connection() as conn:
                conn.execute("UPDATE media_items SET runtime = ? WHERE id = ?;", (runtime, row["id"]))
        return 1 if runtime else 0

    done = filled = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for ok in pool.map(work, rows):
            done += 1
            filled += ok
            if done % 500 == 0 or done == len(rows):
                print(f"[Runtime] {done}/{len(rows)} fetched ({filled} with runtime)", flush=True)
    print(f"[Runtime] Complete: {filled}/{len(rows)} items now have runtime.", flush=True)

def copy_watch_history(source_db: str, db: DatabaseManager) -> None:
    """Copies watch history from the test DB, matching items by (title, media_type)."""
    with db.get_connection() as conn:
        existing = conn.execute("SELECT COUNT(*) FROM watch_history").fetchone()[0]
        if existing:
            print(f"[History] Target already has {existing} rows — skipping history copy.", flush=True)
            return
    if not os.path.exists(source_db):
        print(f"[History] Source DB '{source_db}' not found — skipping.", flush=True)
        return
    import sqlite3

    src = sqlite3.connect(source_db)
    src.row_factory = sqlite3.Row
    try:
        rows = [
            dict(r)
            for r in src.execute(
                """
                SELECT h.user_rating, h.notes, h.watched_at, i.title, i.media_type
                FROM watch_history h JOIN media_items i ON i.id = h.media_item_id
                """
            ).fetchall()
        ]
    finally:
        src.close()
    copied = unmatched = 0
    for h in rows:
        with db.get_connection() as conn:
            match = conn.execute(
                "SELECT id FROM media_items WHERE LOWER(title) = LOWER(?) AND media_type = ? LIMIT 1;",
                (h["title"], h["media_type"]),
            ).fetchone()
            if match:
                conn.execute(
                    "INSERT INTO watch_history (media_item_id, watched_at, user_rating, notes) VALUES (?, ?, ?, ?);",
                    (match["id"], h["watched_at"], h["user_rating"], h["notes"]),
                )
                copied += 1
            else:
                unmatched += 1
    print(f"[History] Copied {copied} row(s) into the full catalogue ({unmatched} unmatched).", flush=True)

def main() -> int:
    load_dotenv()
    api_key = os.getenv("TMDB_API_KEY", "").strip()
    if not api_key or api_key == "your_key_here":
        print("[Error] TMDB_API_KEY missing in .env")
        return 1

    parser = argparse.ArgumentParser(description="Build the full TMDB catalogue database.")
    parser.add_argument("--db", default="catalogue_full.db", help="Target database file")
    parser.add_argument("--max-pages", type=int, default=500, help="Discover pages per media type (500 = 10k items)")
    parser.add_argument("--skip-runtime", action="store_true", help="Skip the per-item runtime details pass")
    parser.add_argument("--runtime-limit", type=int, default=None, help="Max runtime fetches this run (resumable)")
    parser.add_argument("--media-types", default="movie,tv", help="Comma-separated list-phase types to fetch (movie,tv)")
    parser.add_argument("--delay", type=float, default=0.12, help="Seconds between TMDB requests")
    parser.add_argument("--workers", type=int, default=8, help="Threads for the runtime pass")
    parser.add_argument("--history-source", default="catalogue.db", help="DB to copy watch history from")
    args = parser.parse_args()

    tm = TmdbClient(api_key, delay=args.delay)
    db = DatabaseManager(args.db)
    movie_genres, tv_genres = fetch_genre_maps(tm)
    print(f"[Build] Target: {args.db} | pages/type: {args.max_pages}", flush=True)

    wanted = [t.strip() for t in args.media_types.split(",") if t.strip() in ("movie", "tv")]
    inserted_movies = discover_rows(tm, "movie", args.max_pages, movie_genres, db) if "movie" in wanted else 0
    inserted_tv = discover_rows(tm, "tv", args.max_pages, tv_genres, db) if "tv" in wanted else 0
    print(f"[Build] List phase done — movies +{inserted_movies}, tv +{inserted_tv}", flush=True)

    if not args.skip_runtime:
        fill_runtime(tm, db, workers=args.workers, limit=args.runtime_limit)

    copy_watch_history(args.history_source, db)

    with db.get_connection() as conn:
        stats = conn.execute(
            "SELECT media_type, COUNT(*), SUM(runtime IS NOT NULL) FROM media_items GROUP BY media_type;"
        ).fetchall()
        history = conn.execute("SELECT COUNT(*) FROM watch_history").fetchone()[0]
    for media_type, count, with_runtime in stats:
        print(f"[Summary] {media_type}: {count} items ({with_runtime} with runtime)", flush=True)
    print(f"[Summary] watch_history rows: {history}", flush=True)
    print("[Summary] Plug in with: DATABASE_PATH=<db file> in .env", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())