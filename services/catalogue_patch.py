"""Applies a catalogue patch sent by the Mac (docs/DEPLOYMENT_PLAN.md, P6.1).

The server alone assigns `media_items.id` and never deletes items (plan section 3), so
a patch is keyed by `(media_type, tmdb_id)`, never by id. One JSON object per line:

    {"media_type": "movie", "tmdb_id": 603, "fields": {"title": "...", "facets": "..."}}
    {"table": "vibe_clusters", "rows": [{"id": 1, "label": "...", ...}]}

Item lines insert unknown items (they need a `title`), update the listed columns on known
ones, and never delete. A `vibe_clusters` block replaces that whole table. Only the
columns in ALLOWED_COLUMNS can be set, so a patch can never touch watch history, ratings
or any other user table. The whole patch is checked first and then applied in one
transaction: it either all lands or none of it does. Applying it twice changes nothing.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, Iterable, List, Tuple

MEDIA_TYPES = ("movie", "tv")

# Catalogue and enrichment columns of media_items. Not id, media_type, tmdb_id, created_at.
ALLOWED_COLUMNS = frozenset({
    "title", "original_title", "release_year", "overview", "genres", "genre_ids",
    "rating", "vote_count", "runtime", "poster_url", "tier2_keywords",
    "tier2_reviews_summary", "tier2_profiled_at", "tier2_checked", "origin_country",
    "original_language", "collection_id", "collection_name", "collection_order",
    "collection_checked", "tagline", "vibe_tags", "vibe_tagged_at", "facets",
    "vibe_cluster_id", "season_count", "season_count_checked",
})

# The only other table a patch may rewrite, and its columns.
REPLACE_TABLES: Dict[str, frozenset] = {
    "vibe_clusters": frozenset({"id", "label", "size", "top_keywords", "example_ids"}),
}


class PatchError(ValueError):
    """The patch is malformed or asks for something not allowed. Nothing was applied."""


def _scalar(value: Any, where: str) -> Any:
    if value is None or isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return value
    raise PatchError(f"{where}: values must be text, numbers or null")


def parse_patch(lines: Iterable[str]) -> Tuple[List[Dict[str, Any]], Dict[str, List[Dict[str, Any]]]]:
    """Validates every line. Returns (item entries, {table: rows}). Raises PatchError."""
    items: List[Dict[str, Any]] = []
    tables: Dict[str, List[Dict[str, Any]]] = {}
    for n, raw in enumerate(lines, 1):
        raw = raw.strip()
        if not raw:
            continue
        try:
            row = json.loads(raw)
        except ValueError as e:
            raise PatchError(f"line {n}: not valid JSON ({e})") from e
        if not isinstance(row, dict):
            raise PatchError(f"line {n}: expected a JSON object")
        if "table" in row:
            table = row["table"]
            if table not in REPLACE_TABLES:
                raise PatchError(f"line {n}: table {table!r} may not be patched")
            rows = row.get("rows")
            if not isinstance(rows, list):
                raise PatchError(f"line {n}: 'rows' must be a list")
            clean = []
            for r in rows:
                if not isinstance(r, dict) or not r:
                    raise PatchError(f"line {n}: each row must be an object")
                bad = set(r) - REPLACE_TABLES[table]
                if bad:
                    raise PatchError(f"line {n}: columns {sorted(bad)} may not be set on {table}")
                clean.append({k: _scalar(v, f"line {n} {table}.{k}") for k, v in r.items()})
            tables.setdefault(table, []).extend(clean)
            continue
        media_type, tmdb_id, fields = row.get("media_type"), row.get("tmdb_id"), row.get("fields")
        if media_type not in MEDIA_TYPES:
            raise PatchError(f"line {n}: media_type must be 'movie' or 'tv'")
        if not isinstance(tmdb_id, int) or isinstance(tmdb_id, bool):
            raise PatchError(f"line {n}: tmdb_id must be a whole number")
        if not isinstance(fields, dict):
            raise PatchError(f"line {n}: 'fields' must be an object")
        bad = set(fields) - ALLOWED_COLUMNS
        if bad:
            raise PatchError(f"line {n}: columns {sorted(bad)} may not be set")
        items.append({
            "media_type": media_type,
            "tmdb_id": tmdb_id,
            "fields": {k: _scalar(v, f"line {n} {k}") for k, v in fields.items()},
        })
    return items, tables


def apply_patch(db_path: str, lines: Iterable[str]) -> Dict[str, int]:
    """Applies a patch file's lines to the database. Returns counts."""
    items, tables = parse_patch(lines)
    counts = {"inserted": 0, "updated": 0, "unchanged": 0, "skipped": 0, "replaced_rows": 0}
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("BEGIN IMMEDIATE")
        for it in items:
            fields = it["fields"]
            existing = conn.execute(
                "SELECT * FROM media_items WHERE media_type = ? AND tmdb_id = ?",
                (it["media_type"], it["tmdb_id"]),
            ).fetchone()
            if existing is None:
                if not fields.get("title"):
                    counts["skipped"] += 1  # a new item needs a title
                    continue
                cols = ["media_type", "tmdb_id", *fields]
                conn.execute(
                    f"INSERT INTO media_items ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                    [it["media_type"], it["tmdb_id"], *fields.values()],
                )
                counts["inserted"] += 1
                continue
            names = [d[0] for d in conn.execute("SELECT * FROM media_items LIMIT 0").description]
            current = dict(zip(names, existing))
            changed = {k: v for k, v in fields.items() if current.get(k) != v}
            if not changed:
                counts["unchanged"] += 1
                continue
            sets = ", ".join(f"{k} = ?" for k in changed)
            conn.execute(
                f"UPDATE media_items SET {sets}, updated_at = CURRENT_TIMESTAMP "
                "WHERE media_type = ? AND tmdb_id = ?",
                [*changed.values(), it["media_type"], it["tmdb_id"]],
            )
            counts["updated"] += 1
        for table, rows in tables.items():
            conn.execute(f"DELETE FROM {table}")
            for r in rows:
                cols = list(r)
                conn.execute(
                    f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                    [r[c] for c in cols],
                )
            counts["replaced_rows"] += len(rows)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return counts
