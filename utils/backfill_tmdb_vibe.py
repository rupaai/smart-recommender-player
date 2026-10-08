"""
Backfill TMDB keywords, review excerpts and taglines (utils/backfill_tmdb_vibe.py)

The vibe documents (design doc §2.1) and the LLM mood tagger both read
media_items.tier2_keywords, tier2_reviews_summary and tagline. They were 0 %
filled on the full catalogue. One TMDB call per title fetches all three:

    GET /movie/{id}?append_to_response=keywords,reviews   (or /tv/{id})

Resumable (tier2_checked: 0 = not fetched, 1 = fetched, -1 = TMDB 404), most-
voted titles first, so it's safe to interrupt and re-run. Free: TMDB only.

    uv run python utils/backfill_tmdb_vibe.py
    uv run python utils/backfill_tmdb_vibe.py --limit 3000    # quick partial run

DATABASE_PATH overrides the catalogue (default: catalogue_full.db).
"""

from __future__ import annotations

import argparse
import os
import re
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
MAX_REVIEWS = 3
REVIEW_CHARS = 320

NOT_FOUND = "not_found"


def _get(session: requests.Session, path: str, params: Dict[str, Any]) -> Any:
    """JSON body, NOT_FOUND on 404, None on transient failure (retried next run)."""
    for attempt in range(3):
        try:
            resp = session.get(f"{BASE_URL}{path}", params={"api_key": API_KEY, **params},
                               timeout=REQUEST_TIMEOUT)
        except requests.RequestException:
            time.sleep(1 + attempt)
            continue
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 404:
            return NOT_FOUND
        if resp.status_code == 429:
            time.sleep(float(resp.headers.get("Retry-After", 2)))
            continue
        return None
    return None


_MARKUP = re.compile(r"(\*\*|__|\[/?[a-z]+\]|<[^>]+>|https?://\S+)")


def clean_review(text: str, limit: int = REVIEW_CHARS) -> str:
    """First few sentences of a review, without markdown, links or line breaks."""
    text = _MARKUP.sub("", text or "")
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return (cut[: end + 1] if end > limit // 3 else cut.rsplit(" ", 1)[0] + "…").strip()


def parse_details(data: Dict[str, Any], media_type: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    kw_block = data.get("keywords") or {}
    kws = kw_block.get("keywords") if media_type == "movie" else kw_block.get("results")
    keywords = ", ".join(k["name"] for k in (kws or []) if k.get("name")) or None

    reviews = (data.get("reviews") or {}).get("results") or []
    # Rated reviews first (more often real viewer reactions), then shortest first:
    # short reviews tend to say how it felt rather than retell the plot.
    reviews.sort(key=lambda r: ((r.get("author_details") or {}).get("rating") is None,
                                len(r.get("content") or "")))
    snippets = [clean_review(r.get("content", "")) for r in reviews[:MAX_REVIEWS]]
    summary = " | ".join(f'"{s}"' for s in snippets if s) or None

    tagline = (data.get("tagline") or "").strip() or None
    return keywords, summary, tagline


def fetch(session: requests.Session, item: Dict[str, Any]) -> Any:
    media = "tv" if item["media_type"] == "tv" else "movie"
    data = _get(session, f"/{media}/{int(item['tmdb_id'])}",
                {"append_to_response": "keywords,reviews", "language": "en-US"})
    if data in (None, NOT_FOUND):
        return data
    return parse_details(data, media)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="only the N most-voted unchecked titles")
    ap.add_argument("--media-type", choices=["movie", "tv"])
    args = ap.parse_args()

    if not API_KEY:
        print("[Vibe backfill] TMDB_API_KEY missing from .env — aborting.")
        return

    db_path = os.getenv("DATABASE_PATH") or "catalogue_full.db"
    db = DatabaseManager(db_path)
    pending: List[Dict[str, Any]] = db.get_items_missing_tier2(args.media_type)
    if args.limit:
        pending = pending[: args.limit]
    total = len(pending)
    print(f"[Vibe backfill] {total} titles to fetch from TMDB into {db_path}")

    session = requests.Session()
    done = with_kw = with_reviews = missing = errors = 0
    started = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(fetch, session, it): it for it in pending}
        for fut in as_completed(futures):
            item = futures[fut]
            res = fut.result()
            done += 1
            if res is None:
                errors += 1                         # transient, left unchecked for next run
            elif res == NOT_FOUND:
                db.set_tier2_vibe(item["id"], None, None, None, checked=-1)
                missing += 1
            else:
                keywords, reviews, tagline = res
                db.set_tier2_vibe(item["id"], keywords, reviews, tagline, checked=1)
                with_kw += bool(keywords)
                with_reviews += bool(reviews)
            if done % 500 == 0 or done == total:
                rate = done / max(time.time() - started, 0.001)
                eta = (total - done) / max(rate, 0.001) / 60
                print(f"[Vibe backfill] {done}/{total}: {with_kw} with keywords, {with_reviews} with "
                      f"reviews, {missing} not on TMDB, {errors} errors, ~{eta:.0f} min left")

    print(f"[Vibe backfill] Done. {errors} transient errors; re-run to retry them.")


if __name__ == "__main__":
    main()
