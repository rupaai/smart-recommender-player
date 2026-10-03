"""
Backfills origin_country (ISO 3166-1 alpha-2) for catalogue rows.

"South Asian", "Korean", "Nigerian" are places, not genres, so the genre
filters could never satisfy them — the recommender had no country signal and
returned Hollywood no matter what was asked for. TMDB's discover payload does
NOT include origin_country, but it does filter on it server-side, and the
per-title detail endpoint does return it.

Two modes:
  --import-region   Fetch a region's catalogue from TMDB discover (which
                    filters correctly server-side) and add those titles to the
                    catalogue with origin_country set. This is the fast path:
                    one API call per 20 films, no per-title detail calls.
  --fix-labels       Correct rows whose stored origin_country contradicts
                    their original_language. TMDB mislabels co-productions —
                    it filed 3 Idiots as GB, a Hindi film. Language is the more
                    reliable signal for cinema of a specific place.

Usage:
    uv run python utils/backfill_origin_country.py --import-region
    uv run python utils/backfill_origin_country.py --import-region IN
    uv run python utils/backfill_origin_country.py --fix-labels
    uv run python utils/backfill_origin_country.py --region-stats

DATABASE_PATH overrides the target catalogue.
"""
import argparse
import os
import sys
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import DatabaseManager  # noqa: E402

TMDB_BASE = "https://api.themoviedb.org/3"
IMAGE_BASE_URL = "https://image.tmdb.org/t/p/w500"

# Regions worth having in a catalogue, with their languages. TMDB origin codes
# are ISO 3166-1 alpha-2.
REGION_LANGUAGES: Dict[str, List[str]] = {
    "IN": ["hi", "ta", "te", "ml", "kn", "bn", "pa", "mr", "gu", "ur"],
    "KR": ["ko"],
    "JP": ["ja"],
    "CN": ["zh", "cn"],
    "HK": ["cn"],
    "TW": ["zh", "cn"],
    "NG": ["yo", "en"],
    "ES": ["es"],
    "FR": ["fr"],
    "DE": ["de"],
    "BR": ["pt"],
    "MX": ["es"],
    "IR": ["fa"],
    "EG": ["ar"],
    "TR": ["tr"],
}

# Films whose primary language belongs to a region are considered to originate
# from it even when TMDB's country field says otherwise (co-productions, and
# the outright mislabels above).
LANGUAGE_REGION: Dict[str, str] = {}
for region, langs in REGION_LANGUAGES.items():
    for lang in langs:
        LANGUAGE_REGION.setdefault(lang, region)

REGION_NAMES = {
    "IN": "South Asian (Indian cinema)", "KR": "Korean", "JP": "Japanese",
    "CN": "Chinese", "HK": "Hong Kong", "TW": "Taiwanese", "NG": "Nigerian",
    "ES": "Spanish", "FR": "French", "DE": "German", "BR": "Brazilian",
    "MX": "Mexican", "IR": "Iranian", "EG": "Egyptian", "TR": "Turkish",
}


class TmdbClient:
    def __init__(self, api_key: str, delay: float = 0.12):
        self.api_key = api_key
        self.delay = delay
        self.session = requests.Session()

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        p = dict(params or {})
        p["api_key"] = self.api_key
        for _ in range(3):
            time_sleep(self.delay)
            try:
                resp = self.session.get(f"{TMDB_BASE}{path}", params=p, timeout=20)
            except requests.RequestException as e:
                print(f"[TMDB] Network error on {path}: {e}", flush=True)
                time_sleep(2)
                continue
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 2))
                print(f"[TMDB] Rate limited - waiting {wait}s...", flush=True)
                time_sleep(wait)
                continue
            return None
        return None


def time_sleep(seconds: float) -> None:
    import time
    time.sleep(seconds)


def fetch_genre_maps(tm: TmdbClient) -> tuple:
    movie_map = {g["id"]: g["name"] for g in ((tm.get("/genre/movie/list") or {}).get("genres") or [])}
    tv_map = {g["id"]: g["name"] for g in ((tm.get("/genre/tv/list") or {}).get("genres") or [])}
    return movie_map, tv_map


def import_region(tm: TmdbClient, db: DatabaseManager, region: str, pages: int,
                  media_types: List[str], genre_maps: Dict[str, Dict[int, str]],
                  vote_floor: int = 1) -> int:
    """Adds a region's catalogue from TMDB discover, stamping origin_country.

    TMDB filters `with_origin_country` correctly server-side, so this gets
    real cinema of a place rather than guessing from genre or title.
    """
    total = 0
    for media_type in media_types:
        path = "/discover/movie" if media_type == "movie" else "/discover/tv"
        genre_map = genre_maps[media_type]
        for page in range(1, pages + 1):
            data = tm.get(path, {
                "with_origin_country": region,
                "sort_by": "popularity.desc",
                "include_adult": "false",
                # Vote floor is per-region, not global. A fixed 100 imported
                # 427 Indian films but only 6 Bengali ones, because TMDB's
                # Bangladeshi titles carry far fewer votes than mainstream
                # Bollywood (the Apu Trilogy has 512, most have <20). Small
                # cinemas were being silently skipped. Floor 1 keeps every
                # title; the recommender's Bayesian quality cut still handles
                # low-confidence rows at request time.
                "vote_count.gte": str(vote_floor),
                "page": page,
            })
            if not data:
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
                    # Without this every imported row reads as vote_count=0, and
                    # the recommender's Bayesian quality term has nothing to
                    # shrink against — so a 1-vote film ties a classic.
                    "vote_count": int(r.get("vote_count") or 0),
                    "runtime": None,
                    "original_language": r.get("original_language"),
                    "origin_country": region,
                    "poster_url": f"{IMAGE_BASE_URL}{r['poster_path']}" if r.get("poster_path") else None,
                })
            inserted = db.upsert_catalogue_items(rows)
            total += inserted
            if page % 5 == 0:
                print(f"[{region}/{media_type}] page {page} (inserted so far: {total})", flush=True)
    return total


def fix_labels(db: DatabaseManager, dry_run: bool = False) -> int:
    """Corrects origin_country where it contradicts original_language.

    TMDB filed 3 Idiots as GB. When a film's audio language belongs to a region
    (Hindi -> IN), that region is the better answer.
    """
    with db.get_connection() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, title, media_type, origin_country, original_language "
            "FROM media_items WHERE original_language IS NOT NULL AND original_language != '';"
        ).fetchall()]
    fixes = []
    for r in rows:
        expected = LANGUAGE_REGION.get((r["original_language"] or "").lower())
        if not expected:
            continue
        current = r["origin_country"] or ""
        codes = [c.strip().upper() for c in current.split(",") if c.strip()]
        if expected in codes:
            continue
        codes.append(expected)
        fixes.append((",".join(codes), r["id"]))
    if dry_run:
        for r in rows[:0]:
            pass
        print(f"[fix-labels] would correct {len(fixes)} rows")
        return len(fixes)
    with db.get_connection() as conn:
        conn.executemany(
            "UPDATE media_items SET origin_country = ? WHERE id = ?;", fixes
        )
    print(f"[fix-labels] corrected {len(fixes)} rows")
    return len(fixes)


def region_stats(db: DatabaseManager) -> None:
    with db.get_connection() as conn:
        print("origin_country coverage:")
        for row in conn.execute(
            "SELECT COALESCE(origin_country,'(unset)'), COUNT(*) FROM media_items "
            "GROUP BY origin_country ORDER BY COUNT(*) DESC LIMIT 30;"
        ).fetchall():
            label = REGION_NAMES.get(row[0], row[0])
            print(f"   {row[0]:8} {row[1]:6}  {label}")
        total = conn.execute("SELECT COUNT(*) FROM media_items;").fetchone()[0]
        have = conn.execute(
            "SELECT COUNT(*) FROM media_items WHERE origin_country IS NOT NULL "
            "AND origin_country != '';"
        ).fetchone()[0]
        print(f"\n   {have}/{total} rows have origin_country "
              f"({100 * have / max(total, 1):.1f}%)")


def main() -> None:
    load_dotenv()
    api_key = os.getenv("TMDB_API_KEY")
    if not api_key:
        print("TMDB_API_KEY missing from .env")
        return

    parser = argparse.ArgumentParser()
    parser.add_argument("--import-region", nargs="?", const="IN",
                        help="Import a region's catalogue (default IN)")
    parser.add_argument("--pages", type=int, default=10,
                        help="Discover pages per region per media type (20 films each)")
    parser.add_argument("--media-types", default="movie,tv")
    parser.add_argument("--vote-floor", type=int, default=1,
                        help="Minimum TMDB votes per title (default 1: keeps small cinemas)")
    parser.add_argument("--fix-labels", action="store_true")
    parser.add_argument("--region-stats", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    db = DatabaseManager(os.getenv("DATABASE_PATH") or "catalogue_full.db")
    media_types = [m.strip() for m in args.media_types.split(",") if m.strip()]

    if args.region_stats:
        region_stats(db)
        return
    if args.fix_labels:
        fix_labels(db, dry_run=args.dry_run)
        return
    if args.import_region:
        tm = TmdbClient(api_key)
        movie_map, tv_map = fetch_genre_maps(tm)
        genre_maps = {"movie": movie_map, "tv": tv_map}
        regions = [args.import_region] if isinstance(args.import_region, str) else args.import_region
        for region in regions:
            print(f"[import] {REGION_NAMES.get(region, region)} ({region})", flush=True)
            n = import_region(tm, db, region.upper(), args.pages, media_types,
                              genre_maps, vote_floor=args.vote_floor)
            print(f"[import] {region}: {n} new titles", flush=True)
        region_stats(db)


if __name__ == "__main__":
    main()
