"""
Catalogue title search (services/catalogue_search.py)

Powers search-to-rate (design doc §1): type a few letters in the Rate tab,
pick a film or show, rate it.

Matching is accent- and punctuation-insensitive ("amelie" finds "Amélie",
"spiderman" finds "Spider-Man"), over both the title and the original title.
Ranking: exact → starts with → a word starts with → contains → all words
present, then most-voted first.

The index is a plain in-memory list (about 50k short strings, a few MB),
built on first use and rebuilt whenever the catalogue's row count or
highest id changes. Scanning it is a few
milliseconds even on a Pi, and SQLite can't do accent-insensitive LIKE.

Titles that aren't in the catalogue can be found on TMDB and imported
(`tmdb_search`, `tmdb_import`). Those need TMDB_API_KEY and internet.
"""

from __future__ import annotations

import re
import threading
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

import requests

TMDB_BASE = "https://api.themoviedb.org/3"
IMAGE_BASE_URL = "https://image.tmdb.org/t/p/w500"

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalise(text: Optional[str]) -> str:
    """'Amélie & Co.' -> 'amelie and co'. Lowercase ASCII words, single spaces."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("&", " and ").replace("'", "").replace("’", "")
    return " ".join(_NON_ALNUM.split(text)).strip()


def _squash(text: str) -> str:
    return text.replace(" ", "")


def match_rank(query: str, title: str) -> Optional[int]:
    """0 exact, 1 starts with, 2 a word starts with, 3 contains, 4 all words
    present in any order, None = no match. Both arguments already normalised."""
    if not query or not title:
        return None
    if title == query or _squash(title) == _squash(query):
        return 0
    if title.startswith(query) or _squash(title).startswith(_squash(query)):
        return 1
    if f" {query}" in f" {title}":
        return 2
    if query in title or _squash(query) in _squash(title):
        return 3
    words = query.split()
    if len(words) > 1 and all(w in title for w in words):
        return 4
    return None


class CatalogueSearch:
    def __init__(self, db: Any):
        self.db = db
        self._index: Optional[List[Tuple[int, str, str, str, int]]] = None
        self._signature: Optional[Tuple[Any, ...]] = None
        self._lock = threading.Lock()

    def invalidate(self) -> None:
        with self._lock:
            self._index = None

    def _load(self) -> List[Tuple[int, str, str, str, int]]:
        with self._lock:
            with self.db.get_connection() as conn:
                # Rebuild when titles were added or removed elsewhere (scanner,
                # enricher, imports). COUNT + MAX(id) on the primary key is ~1 ms.
                signature = tuple(conn.execute("SELECT COUNT(*), MAX(id) FROM media_items").fetchone())
                if self._index is None or signature != self._signature:
                    self._signature = signature
                    rows = conn.execute(
                        "SELECT id, media_type, title, original_title, COALESCE(vote_count, 0) "
                        "FROM media_items WHERE title IS NOT NULL"
                    ).fetchall()
                    self._index = [
                        (r[0], r[1] or "movie", normalise(r[2]), normalise(r[3]), max(int(r[4] or 0), 0))
                        for r in rows
                    ]
            return self._index

    def search_ids(self, query: str, media_type: str = "all", limit: int = 10) -> List[int]:
        q = normalise(query)
        if len(q) < 2:
            return []
        hits: List[Tuple[int, int, int]] = []
        for item_id, mtype, title, original, votes in self._load():
            if media_type in ("movie", "tv") and mtype != media_type:
                continue
            ranks = [r for r in (match_rank(q, title), match_rank(q, original)) if r is not None]
            if ranks:
                hits.append((min(ranks), -votes, item_id))
        hits.sort()
        return [item_id for _, _, item_id in hits[:limit]]

    def search(self, query: str, media_type: str = "all", limit: int = 10,
               profile_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Results with what the card needs: poster, meta, in-library flag and
        this profile's existing verdict (so the dropdown can say "you: 8/10")."""
        ids = self.search_ids(query, media_type, limit)
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        with self.db.get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT mi.id, mi.media_type, mi.tmdb_id, mi.title, mi.original_title,
                       mi.release_year, mi.genres, mi.runtime, mi.rating, mi.vote_count,
                       mi.poster_url, mi.overview,
                       EXISTS(SELECT 1 FROM media_sources ms WHERE ms.media_item_id = mi.id) AS in_library
                FROM media_items mi WHERE mi.id IN ({marks})
                """,
                ids,
            ).fetchall()
            mine: Dict[int, Dict[str, Any]] = {}
            if profile_id is not None:
                for v in conn.execute(
                    f"SELECT media_item_id, status, user_rating FROM watch_history "
                    f"WHERE profile_id = ? AND media_item_id IN ({marks})",
                    (profile_id, *ids),
                ).fetchall():
                    mine[v[0]] = {"status": v[1], "user_rating": v[2]}
        by_id = {r["id"]: dict(r) for r in rows}
        out = []
        for item_id in ids:
            item = by_id.get(item_id)
            if item:
                item["my_verdict"] = mine.get(item_id)
                out.append(item)
        return out


# ---------------------------------------------------------------------------
# TMDB fallback: "Not in the catalogue? Find it on TMDB"
# ---------------------------------------------------------------------------

def _tmdb_get(api_key: str, path: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    try:
        resp = requests.get(f"{TMDB_BASE}{path}", params={"api_key": api_key, **(params or {})}, timeout=10)
    except requests.RequestException:
        return None
    return resp.json() if resp.status_code == 200 else None


def tmdb_search(api_key: str, db: Any, query: str, media_type: str = "all", limit: int = 8) -> List[Dict[str, Any]]:
    """Movies and shows on TMDB matching the text. Each result says whether it
    is already in the catalogue (`catalogue_id`)."""
    data = _tmdb_get(api_key, "/search/multi", {"query": query, "include_adult": "false"})
    if data is None:
        raise RuntimeError("TMDB is unreachable (check TMDB_API_KEY and internet).")
    results = []
    for r in data.get("results") or []:
        mtype = r.get("media_type")
        if mtype not in ("movie", "tv") or (media_type in ("movie", "tv") and mtype != media_type):
            continue
        date = r.get("release_date") or r.get("first_air_date") or ""
        results.append({
            "media_type": mtype,
            "tmdb_id": r["id"],
            "title": r.get("title") or r.get("name"),
            "release_year": int(date[:4]) if date[:4].isdigit() else None,
            "poster_url": f"{IMAGE_BASE_URL}{r['poster_path']}" if r.get("poster_path") else None,
            "vote_count": r.get("vote_count") or 0,
            "overview": r.get("overview"),
        })
        if len(results) >= limit:
            break
    if results:
        with db.get_connection() as conn:
            for res in results:
                row = conn.execute(
                    "SELECT id FROM media_items WHERE media_type = ? AND tmdb_id = ?",
                    (res["media_type"], res["tmdb_id"]),
                ).fetchone()
                res["catalogue_id"] = row[0] if row else None
    return results


def tmdb_details_to_row(data: Dict[str, Any], media_type: str) -> Dict[str, Any]:
    date = data.get("release_date") or data.get("first_air_date") or ""
    genres = data.get("genres") or []
    if media_type == "movie":
        countries = [c.get("iso_3166_1") for c in data.get("production_countries") or [] if c.get("iso_3166_1")]
        runtime = data.get("runtime")
    else:
        countries = list(data.get("origin_country") or [])
        runs = data.get("episode_run_time") or []
        runtime = runs[0] if runs else None
    return {
        "media_type": media_type,
        "tmdb_id": data["id"],
        "title": data.get("title") or data.get("name"),
        "original_title": data.get("original_title") or data.get("original_name"),
        "release_year": int(date[:4]) if date[:4].isdigit() else None,
        "overview": data.get("overview"),
        "genres": ", ".join(g["name"] for g in genres if g.get("name")) or None,
        "genre_ids": ", ".join(str(g["id"]) for g in genres if g.get("id")) or None,
        "rating": round(float(data.get("vote_average") or 0.0), 1),
        "vote_count": data.get("vote_count") or 0,
        "runtime": runtime,
        "poster_url": f"{IMAGE_BASE_URL}{data['poster_path']}" if data.get("poster_path") else None,
        "original_language": data.get("original_language"),
        "origin_country": ",".join(countries) or None,
    }


def tmdb_import(api_key: str, db: Any, media_type: str, tmdb_id: int) -> int:
    """Adds one TMDB title to the catalogue (no-op if present). Returns its id."""
    if media_type not in ("movie", "tv"):
        raise ValueError("media_type must be 'movie' or 'tv'")
    data = _tmdb_get(api_key, f"/{media_type}/{int(tmdb_id)}")
    if data is None:
        raise RuntimeError("TMDB lookup failed (unknown id, or TMDB unreachable).")
    row = tmdb_details_to_row(data, media_type)
    db.upsert_catalogue_items([row])
    with db.get_connection() as conn:
        found = conn.execute(
            "SELECT id FROM media_items WHERE media_type = ? AND tmdb_id = ?", (media_type, row["tmdb_id"])
        ).fetchone()
        if data.get("tagline"):
            conn.execute("UPDATE media_items SET tagline = COALESCE(tagline, ?) WHERE id = ?",
                         (data["tagline"], found[0]))
        # upsert only fills vote_count via its origin backfill; set it outright here.
        conn.execute("UPDATE media_items SET vote_count = MAX(COALESCE(vote_count, 0), ?) WHERE id = ?",
                     (row["vote_count"], found[0]))
    return int(found[0])
