"""
Block 3: Database Manager (database.py)
Central hub managing the normalized SQLite catalogue, media sources, and watch history.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, Generator, List, Optional, Tuple


class DatabaseManager:
    """Manages SQLite connection, normalized schemas, and queries for the catalogue."""

    def __init__(self, db_path: str = "catalogue.db"):
        self.db_path = db_path
        self.init_db()

    @contextmanager
    def get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager for SQLite database connection with row factory enabled."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_db(self) -> None:
        """Creates the normalized tables and indexes if they do not exist."""
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # Table 1: Canonical media items (Movies and TV Shows)
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS media_items (
                    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                    media_type              TEXT DEFAULT 'movie',  -- 'movie' or 'tv'
                    tmdb_id                 INTEGER,               -- TMDB id, unique per media_type (movie/TV id spaces overlap)
                    title                   TEXT NOT NULL,
                    original_title          TEXT,
                    release_year            INTEGER,
                    overview                TEXT,
                    genres                  TEXT,                  -- Comma-separated names, e.g. "Action, Thriller"
                    genre_ids               TEXT,                  -- Comma-separated IDs, e.g. "28, 53"
                    rating                  REAL DEFAULT 0.0,      -- TMDB rating (0.0 - 10.0)
                    vote_count              INTEGER DEFAULT 0,     -- TMDB vote count (audience verification for the quality cut)
                    runtime                 INTEGER,               -- Minutes
                    poster_url              TEXT,
                    tier2_keywords          TEXT,                  -- Cached TMDB keywords/tags
                    tier2_reviews_summary   TEXT,                  -- Cached user review highlights
                    tier2_profiled_at       TIMESTAMP,             -- Cache timestamp
                    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(media_type, tmdb_id)
                );
                """
            )

            # Table 2: Physical sources or streams linked to a media item
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS media_sources (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    media_item_id   INTEGER NOT NULL REFERENCES media_items(id) ON DELETE CASCADE,
                    source_type     TEXT DEFAULT 'local',  -- 'local', 'cloud', 'stream'
                    file_path       TEXT UNIQUE NOT NULL,  -- Absolute path or stream URI
                    parsed_title    TEXT NOT NULL,
                    parsed_year     INTEGER,
                    season_number   INTEGER,               -- NULL for movies
                    episode_number  INTEGER,               -- NULL for movies
                    file_size       INTEGER DEFAULT 0,     -- In bytes
                    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

            # Table 3: Watch history and personal ratings
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS watch_history (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    media_item_id   INTEGER NOT NULL REFERENCES media_items(id) ON DELETE CASCADE,
                    watched_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    user_rating     REAL,                  -- User rating 1.0 to 10.0
                    notes           TEXT                   -- Personal impressions / vibes
                );
                """
            )

            # Table 4: Active watch session (currently watching)
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS active_watch_sessions (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    media_item_id   INTEGER NOT NULL REFERENCES media_items(id) ON DELETE CASCADE,
                    started_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    status          TEXT DEFAULT 'watching' -- 'watching', 'completed', 'cancelled'
                );
                """
            )

            # Indexes for fast lookup
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_media_sources_file_path ON media_sources(file_path);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_media_items_tmdb_id ON media_items(tmdb_id);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_media_items_media_type ON media_items(media_type);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_watch_history_item_id ON watch_history(media_item_id);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_active_watch_status ON active_watch_sessions(status);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_media_items_media_type ON media_items(media_type);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_watch_history_item_id ON watch_history(media_item_id);")

            # Legacy databases: tmdb_id was UNIQUE globally, but TMDB movie and
            # TV id spaces overlap — a mixed catalogue needs UNIQUE(media_type, tmdb_id).
            self._migrate_v1_media_items_unique(cursor, conn)
            self._migrate_v2_vote_count(cursor)
            self._migrate_v3_profiles(cursor)

    def _migrate_v1_media_items_unique(self, cursor, conn) -> None:
        """v1 migration: table-level `tmdb_id UNIQUE` -> composite `UNIQUE(media_type, tmdb_id)`.

        Idempotent (PRAGMA user_version gate). Rebuilds media_items preserving
        ids so media_sources / watch_history foreign keys stay valid.
        """
        version = cursor.execute("PRAGMA user_version;").fetchone()[0]
        if version >= 1:
            return

        conn.execute("PRAGMA foreign_keys = OFF;")
        cursor.execute(
            """
            CREATE TABLE media_items_migrated (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                media_type              TEXT DEFAULT 'movie',
                tmdb_id                 INTEGER,
                title                   TEXT NOT NULL,
                original_title          TEXT,
                release_year            INTEGER,
                overview                TEXT,
                genres                  TEXT,
                genre_ids               TEXT,
                rating                  REAL DEFAULT 0.0,
                runtime                 INTEGER,
                poster_url              TEXT,
                tier2_keywords          TEXT,
                tier2_reviews_summary   TEXT,
                tier2_profiled_at       TIMESTAMP,
                created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(media_type, tmdb_id)
            );
            """
        )
        cursor.execute(
            """
            INSERT INTO media_items_migrated
                (id, media_type, tmdb_id, title, original_title, release_year,
                 overview, genres, genre_ids, rating, runtime, poster_url,
                 tier2_keywords, tier2_reviews_summary, tier2_profiled_at,
                 created_at, updated_at)
            SELECT id, media_type, tmdb_id, title, original_title, release_year,
                   overview, genres, genre_ids, rating, runtime, poster_url,
                   tier2_keywords, tier2_reviews_summary, tier2_profiled_at,
                   created_at, updated_at
            FROM media_items;
            """
        )
        cursor.execute("DROP TABLE media_items;")
        cursor.execute("ALTER TABLE media_items_migrated RENAME TO media_items;")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_media_items_tmdb_id ON media_items(tmdb_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_media_items_media_type ON media_items(media_type);")
        violations = cursor.execute("PRAGMA foreign_key_check;").fetchall()
        if violations:
            print(f"[Database] WARNING: {len(violations)} foreign-key violations after migration!")
        conn.execute("PRAGMA foreign_keys = ON;")
        cursor.execute("PRAGMA user_version = 1;")
        print("[Database] Migration v1 applied: media_items unique key is now (media_type, tmdb_id).")

    def _migrate_v2_vote_count(self, cursor) -> None:
        """v2 migration: additive `vote_count` column for the quality-cut funnel.

        Idempotent (PRAGMA user_version gate + column-exists check). Pure
        ALTER TABLE ADD COLUMN — no table rebuild, existing rows get DEFAULT 0
        until utils/backfill_vote_counts.py fills them from TMDB.
        """
        version = cursor.execute("PRAGMA user_version;").fetchone()[0]
        if version >= 2:
            return

        columns = {row[1] for row in cursor.execute("PRAGMA table_info(media_items);").fetchall()}
        if "vote_count" not in columns:
            cursor.execute("ALTER TABLE media_items ADD COLUMN vote_count INTEGER DEFAULT 0;")
            print("[Database] Migration v2 applied: media_items.vote_count column added.")
        cursor.execute("PRAGMA user_version = 2;")

    def _migrate_v3_profiles(self, cursor) -> None:
        """v3 migration: multi-profile co-view ratings.

        Two people share one catalogue, so `watch_history` needs to know *who*
        rated. Adds:
          - `profiles` table (id, name, rating_count, mean_rating)
          - `watch_history.profile_id` — who rated (existing rows -> profile 1)
          - `watch_history.status` — 'seen' | 'not_seen'. "Never saw it" is a
            real answer, not a missing rating: it is what makes the Tier B
            ("he saw it, you haven't") recommendation tier computable.

        Idempotent (PRAGMA user_version gate + column-exists check). Additive
        ALTER TABLEs, no rebuild, so ids and foreign keys stay valid. Existing
        rows keep their ratings and land on profile 1.
        """
        version = cursor.execute("PRAGMA user_version;").fetchone()[0]
        if version >= 3:
            return

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS profiles (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                name          TEXT UNIQUE NOT NULL,
                rating_count  INTEGER DEFAULT 0,
                mean_rating   REAL DEFAULT 0.0,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        # Two seeded profiles so the rating UI is usable immediately; both are
        # renameable via update_profile(). profile 1 owns the pre-v3 rows.
        cursor.execute("INSERT OR IGNORE INTO profiles (id, name) VALUES (1, 'Me');")
        cursor.execute("INSERT OR IGNORE INTO profiles (id, name) VALUES (2, 'Partner');")

        columns = {row[1] for row in cursor.execute("PRAGMA table_info(watch_history);").fetchall()}
        if "profile_id" not in columns:
            # SQLite refuses a REFERENCES column with a non-NULL default
            # ("Cannot add a REFERENCES column with non-NULL default
            # value"), so the column goes in bare and the constraint plus
            # backfill are applied separately.
            cursor.execute(
                "ALTER TABLE watch_history ADD COLUMN profile_id INTEGER NOT NULL DEFAULT 1;"
            )
            cursor.execute(
                "UPDATE watch_history SET profile_id = 1 WHERE profile_id IS NULL;"
            )
        if "status" not in columns:
            cursor.execute(
                "ALTER TABLE watch_history ADD COLUMN status TEXT NOT NULL DEFAULT 'seen';"
            )

        # One rating per profile per item (upsert target), plus a plain item
        # index for the co-view grouping query (which profiles saw this item).
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_watch_history_profile_item "
            "ON watch_history(profile_id, media_item_id);"
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_watch_history_item ON watch_history(media_item_id);")
        cursor.execute("PRAGMA user_version = 3;")
        print("[Database] Migration v3 applied: profiles + watch_history.profile_id/status added.")

    def register_source_file(
        self,
        file_path: str,
        parsed_title: str,
        parsed_year: Optional[int] = None,
        media_type: str = "movie",
        season_number: Optional[int] = None,
        episode_number: Optional[int] = None,
        file_size: int = 0,
        source_type: str = "local",
    ) -> Tuple[bool, int]:
        """
        Registers a media file. If file_path already exists, skips.
        Associates the file with an existing canonical media_item or creates a new stub.
        Returns (is_new, source_id).
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # Check if source already exists
            cursor.execute("SELECT id FROM media_sources WHERE file_path = ?;", (file_path,))
            row = cursor.fetchone()
            if row:
                return False, row["id"]

            # Find matching existing media_item
            if media_type == "tv":
                # For TV, group all episodes under the same show title
                cursor.execute(
                    "SELECT id FROM media_items WHERE LOWER(title) = LOWER(?) AND media_type = 'tv' LIMIT 1;",
                    (parsed_title,),
                )
            else:
                # For movie, match title and optional year
                if parsed_year:
                    cursor.execute(
                        """
                        SELECT id FROM media_items 
                        WHERE LOWER(title) = LOWER(?) AND release_year = ? AND media_type = 'movie' 
                        LIMIT 1;
                        """,
                        (parsed_title, parsed_year),
                    )
                else:
                    cursor.execute(
                        "SELECT id FROM media_items WHERE LOWER(title) = LOWER(?) AND media_type = 'movie' LIMIT 1;",
                        (parsed_title,),
                    )

            item_row = cursor.fetchone()
            if item_row:
                media_item_id = item_row["id"]
            else:
                # Insert stub media_item awaiting enrichment
                cursor.execute(
                    """
                    INSERT INTO media_items (media_type, title, release_year)
                    VALUES (?, ?, ?);
                    """,
                    (media_type, parsed_title, parsed_year),
                )
                media_item_id = cursor.lastrowid

            # Insert source file
            cursor.execute(
                """
                INSERT INTO media_sources (
                    media_item_id, source_type, file_path, parsed_title, 
                    parsed_year, season_number, episode_number, file_size
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    media_item_id,
                    source_type,
                    file_path,
                    parsed_title,
                    parsed_year,
                    season_number,
                    episode_number,
                    file_size,
                ),
            )
            source_id = cursor.lastrowid
            return True, source_id

    def remove_source_files(self, file_paths: List[str]) -> int:
        """Deletes media_sources rows for the given exact file paths. Returns count."""
        if not file_paths:
            return 0
        with self.get_connection() as conn:
            cursor = conn.cursor()
            placeholders = ",".join("?" for _ in file_paths)
            cursor.execute(
                f"DELETE FROM media_sources WHERE file_path IN ({placeholders});",
                tuple(file_paths),
            )
            removed = cursor.rowcount
            conn.commit()
            return removed

    def prune_orphan_stub_items(self) -> List[str]:
        """Deletes stub media_items that have no sources, no watch history and
        no tmdb_id (i.e. auto-created by register_source_file and now orphaned).
        Catalogue items with tmdb_id are NEVER touched. Returns deleted titles."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, title FROM media_items
                WHERE tmdb_id IS NULL
                  AND NOT EXISTS (SELECT 1 FROM media_sources WHERE media_item_id = media_items.id)
                  AND NOT EXISTS (SELECT 1 FROM watch_history WHERE media_item_id = media_items.id);
                """
            )
            doomed = cursor.fetchall()
            for row in doomed:
                cursor.execute("DELETE FROM media_items WHERE id = ?;", (row["id"],))
            conn.commit()
            return [r["title"] for r in doomed]

    def get_unenriched_items(self, media_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """Returns items that do not have tmdb_id populated."""
        query = "SELECT * FROM media_items WHERE tmdb_id IS NULL"
        params: List[Any] = []
        if media_type:
            query += " AND media_type = ?"
            params.append(media_type)
        query += " ORDER BY id ASC;"

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def update_item_metadata(
        self,
        item_id: int,
        tmdb_id: int,
        title: str,
        original_title: Optional[str],
        release_year: Optional[int],
        overview: Optional[str],
        genres: str,
        genre_ids: str,
        rating: float,
        runtime: Optional[int],
        poster_url: Optional[str],
    ) -> None:
        """Updates enriched metadata for a media item."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE media_items
                SET tmdb_id = ?,
                    title = ?,
                    original_title = ?,
                    release_year = ?,
                    overview = ?,
                    genres = ?,
                    genre_ids = ?,
                    rating = ?,
                    runtime = ?,
                    poster_url = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?;
                """,
                (
                    tmdb_id,
                    title,
                    original_title,
                    release_year,
                    overview,
                    genres,
                    genre_ids,
                    rating,
                    runtime,
                    poster_url,
                    item_id,
                ),
            )

    def upsert_catalogue_items(self, items: List[Dict[str, Any]]) -> int:
        """Bulk-inserts catalogue rows (movies and/or TV).

        Existing (media_type, tmdb_id) pairs are skipped, making the operation
        idempotent/resumable. Returns the number of newly inserted rows.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            before = conn.total_changes
            for it in items:
                cursor.execute(
                    """
                    INSERT INTO media_items
                        (media_type, tmdb_id, title, original_title, release_year,
                         overview, genres, genre_ids, rating, runtime, poster_url)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(media_type, tmdb_id) DO NOTHING;
                    """,
                    (
                        it["media_type"],
                        it["tmdb_id"],
                        it["title"],
                        it.get("original_title"),
                        it.get("release_year"),
                        it.get("overview"),
                        it.get("genres"),
                        it.get("genre_ids"),
                        it.get("rating", 0.0),
                        it.get("runtime"),
                        it.get("poster_url"),
                    ),
                )
            # NOTE: cursor.rowcount is unreliable with ON CONFLICT DO NOTHING;
            # total_changes counts actual inserts.
            inserted = conn.total_changes - before
        return inserted

    def update_tier2_profile(
        self,
        item_id: int,
        keywords: str,
        reviews_summary: str,
    ) -> None:
        """Caches Tier 2 deep profiling data (keywords, review highlights) for an item."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE media_items
                SET tier2_keywords = ?,
                    tier2_reviews_summary = ?,
                    tier2_profiled_at = CURRENT_TIMESTAMP
                WHERE id = ?;
                """,
                (keywords, reviews_summary, item_id),
            )

    def get_item_by_id(self, item_id: int) -> Optional[Dict[str, Any]]:
        """Retrieves a single media item along with its registered sources."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM media_items WHERE id = ?;", (item_id,))
            item_row = cursor.fetchone()
            if not item_row:
                return None
            item = dict(item_row)

            cursor.execute("SELECT * FROM media_sources WHERE media_item_id = ?;", (item_id,))
            item["sources"] = [dict(s) for s in cursor.fetchall()]
            return item

    def get_candidates(
        self,
        media_type: str = "movie",
        genres: Optional[List[str]] = None,
        exclude_watched: bool = True,
        min_rating: float = 0.0,
    ) -> List[Dict[str, Any]]:
        """
        Retrieves media items matching genre criteria and watch-status filters,
        joining with their primary source file paths for streaming.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()

            query = """
                SELECT mi.*, 
                       ms.file_path, 
                       ms.source_type,
                       (SELECT user_rating FROM watch_history wh WHERE wh.media_item_id = mi.id ORDER BY watched_at DESC LIMIT 1) as user_rating,
                       (SELECT watched_at FROM watch_history wh WHERE wh.media_item_id = mi.id ORDER BY watched_at DESC LIMIT 1) as last_watched_at
                FROM media_items mi
                LEFT JOIN media_sources ms ON ms.media_item_id = mi.id
                WHERE mi.media_type = ?
                  AND mi.rating >= ?
            """
            params: List[Any] = [media_type, min_rating]

            if exclude_watched:
                query += " AND mi.id NOT IN (SELECT media_item_id FROM watch_history)"

            query += " GROUP BY mi.id ORDER BY mi.rating DESC, mi.release_year DESC;"

            cursor.execute(query, params)
            rows = [dict(r) for r in cursor.fetchall()]

            if not genres:
                return rows

            # Filter in Python for flexible comma-separated case-insensitive genre matching
            genre_set = {g.strip().lower() for g in genres if g.strip()}
            filtered: List[Dict[str, Any]] = []
            for item in rows:
                item_genres_raw = item.get("genres") or ""
                item_genres = {g.strip().lower() for g in item_genres_raw.split(",") if g.strip()}
                if genre_set.intersection(item_genres):
                    filtered.append(item)

            return filtered

    def get_top_candidates(
        self,
        media_type: str = "movie",
        genres: Optional[List[str]] = None,
        exclude_watched: bool = True,
        min_rating: float = 0.0,
        era: Optional[str] = None,
        duration: Optional[str] = None,
        min_votes: int = 0,
        limit: int = 250,
    ) -> List[Dict[str, Any]]:
        """Quality-cut candidate funnel for the recommender (large catalogues).

        Funnel 1: hard filters — media type, era, runtime, watch status, rating floor.
        Funnel 2: Bayesian audience-weighted rating ordering computed *in SQL* using
        real TMDB vote counts (IMDb-style "reduce by reviews"), cut to `limit` items:

            score = (votes / (votes + 1000)) * rating + (1000 / (votes + 1000)) * 6.5

        Items with no vote counts yet (0) score a flat 6.5 and fall back to
        rating/year ordering — so this degrades gracefully before backfill runs.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()

            order_expr = (
                "(MAX(COALESCE(mi.vote_count, 0), 0) * 1.0 / (MAX(COALESCE(mi.vote_count, 0), 0) + 1000.0)) * mi.rating"
                " + (1000.0 / (MAX(COALESCE(mi.vote_count, 0), 0) + 1000.0)) * 6.5"
            )

            query = f"""
                SELECT mi.*,
                       ms.file_path,
                       ms.source_type,
                       (SELECT user_rating FROM watch_history wh WHERE wh.media_item_id = mi.id ORDER BY watched_at DESC LIMIT 1) as user_rating,
                       (SELECT watched_at FROM watch_history wh WHERE wh.media_item_id = mi.id ORDER BY watched_at DESC LIMIT 1) as last_watched_at
                FROM media_items mi
                LEFT JOIN media_sources ms ON ms.media_item_id = mi.id
                WHERE mi.media_type = ?
                  AND mi.rating >= ?
            """
            params: List[Any] = [media_type, min_rating]

            if exclude_watched:
                query += " AND mi.id NOT IN (SELECT media_item_id FROM watch_history)"
            if min_votes > 0:
                query += " AND COALESCE(mi.vote_count, 0) >= ?"
                params.append(min_votes)
            if era == "modern":
                query += " AND COALESCE(mi.release_year, 0) >= 2015"
            elif era == "2000s":
                query += " AND COALESCE(mi.release_year, 0) BETWEEN 2000 AND 2014"
            elif era == "classic":
                query += " AND COALESCE(mi.release_year, 9999) < 2000"
            if duration == "short":
                query += " AND COALESCE(mi.runtime, 0) BETWEEN 1 AND 104"
            elif duration == "epic":
                query += " AND COALESCE(mi.runtime, 0) > 140"

            # Genre match pushed INTO the query. `genres` is stored as
            # "Crime, Mystery, Thriller" — comma+space separated — so both the
            # column and the needle have their spaces stripped before the
            # comma-delimited token test. Without that normalisation the
            # padded haystack reads ", mystery," and never matches
            # "%,mystery,%" (this silently dropped ~84% of matches).
            # Stripping spaces on both sides also keeps multi-word genres
            # intact: "Sci-Fi & Fantasy" -> "sci-fi&fantasy".
            genre_clauses = []
            for g in genres or []:
                needle = (g or "").strip().lower().replace(" ", "")
                if needle:
                    genre_clauses.append(
                        "',' || REPLACE(LOWER(COALESCE(mi.genres, '')), ' ', '') || ',' LIKE ?"
                    )
                    params.append(f"%,{needle},%")
            if genre_clauses:
                query += " AND (" + " OR ".join(genre_clauses) + ")"

            query += f" GROUP BY mi.id ORDER BY {order_expr} DESC, mi.rating DESC, mi.release_year DESC"
            query += " LIMIT ?"
            params.append(limit)

            cursor.execute(query, params)
            return [dict(r) for r in cursor.fetchall()]

    def get_items_missing_vote_counts(self) -> List[Dict[str, Any]]:
        """Returns catalogue items that still need their TMDB vote counts backfilled."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, media_type, tmdb_id, title
                FROM media_items
                WHERE tmdb_id IS NOT NULL
                  AND COALESCE(vote_count, 0) = 0
                ORDER BY id ASC;
                """
            )
            return [dict(r) for r in cursor.fetchall()]

    def update_vote_count(
        self,
        item_id: int,
        vote_count: int,
        rating: Optional[float] = None,
    ) -> None:
        """Backfills TMDB vote counts (and refreshes the rating) for an item."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if rating is not None:
                cursor.execute(
                    "UPDATE media_items SET vote_count = ?, rating = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;",
                    (vote_count, rating, item_id),
                )
            else:
                cursor.execute(
                    "UPDATE media_items SET vote_count = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;",
                    (vote_count, item_id),
                )

    def get_reference_titles(self) -> List[Dict[str, Any]]:
        """Returns lightweight title/genre/keyword metadata for every catalogue
        item (including watched ones). Used by the recommender to detect
        titles the user mentions inside free-text descriptions."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, title, release_year, media_type, genres,
                       tier2_keywords, overview
                FROM media_items;
                """
            )
            return [dict(r) for r in cursor.fetchall()]

    def record_watch(
        self,
        media_item_id: int,
        user_rating: Optional[float] = None,
        notes: Optional[str] = None,
    ) -> int:
        """Records a watch event with an optional user rating (1-10) and notes."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO watch_history (media_item_id, user_rating, notes)
                VALUES (?, ?, ?);
                """,
                (media_item_id, user_rating, notes),
            )
            return cursor.lastrowid

    # =======================================================================
    # Multi-profile co-view ratings (schema v3)
    # =======================================================================
    def get_profiles(self) -> List[Dict[str, Any]]:
        """Lists rating profiles with their live rating stats.

        `mean_rating`/`rating_count` are recomputed from watch_history rather
        than trusted from the profiles table, so they cannot drift out of sync
        with the ratings that actually exist.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT p.id,
                       p.name,
                       COUNT(wh.id)                                           AS rating_count,
                       COALESCE(AVG(wh.user_rating), 0.0)                    AS mean_rating,
                       SUM(CASE WHEN wh.status = 'not_seen' THEN 1 ELSE 0 END) AS not_seen_count
                FROM profiles p
                LEFT JOIN watch_history wh
                       ON wh.profile_id = p.id AND wh.status = 'seen'
                GROUP BY p.id, p.name
                ORDER BY p.id;
                """
            )
            return [dict(row) for row in cursor.fetchall()]

    def update_profile_name(self, profile_id: int, name: str) -> bool:
        """Renames a rating profile. Returns False when the id or name is bad."""
        name = (name or "").strip()
        if not name:
            return False
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if not cursor.execute("SELECT 1 FROM profiles WHERE id = ?;", (profile_id,)).fetchone():
                return False
            try:
                cursor.execute("UPDATE profiles SET name = ? WHERE id = ?;", (name, profile_id))
            except sqlite3.IntegrityError:
                # profiles.name is UNIQUE — a duplicate is a caller error, not a crash.
                return False
            return True

    def rate_item(
        self,
        media_item_id: int,
        profile_id: int = 1,
        user_rating: Optional[float] = None,
        status: str = "seen",
        notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Upserts one profile's verdict on one item (the Rate tab's write path).

        `status='not_seen'` means "we haven't watched this" — a real answer that
        is deliberately NOT stored as a rating. It contributes nothing to taste
        scoring (absence of data isn't dislike); it is recorded so the
        recommender can tell "he saw it, you haven't" apart from "neither of us
        has seen it". Any rating passed with status='not_seen' is dropped,
        because the two states are mutually exclusive.
        """
        if status not in ("seen", "not_seen"):
            raise ValueError(f"invalid status: {status!r}")
        if status == "not_seen":
            user_rating = None
        elif user_rating is not None:
            user_rating = max(1.0, min(10.0, float(user_rating)))

        with self.get_connection() as conn:
            cursor = conn.cursor()
            if not cursor.execute("SELECT 1 FROM media_items WHERE id = ?;", (media_item_id,)).fetchone():
                raise ValueError(f"unknown media_item_id: {media_item_id}")
            if not cursor.execute("SELECT 1 FROM profiles WHERE id = ?;", (profile_id,)).fetchone():
                raise ValueError(f"unknown profile_id: {profile_id}")

            cursor.execute(
                """
                INSERT INTO watch_history (media_item_id, profile_id, user_rating, status, notes)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(profile_id, media_item_id) DO UPDATE SET
                    user_rating = excluded.user_rating,
                    status      = excluded.status,
                    notes       = excluded.notes,
                    watched_at  = CURRENT_TIMESTAMP;
                """,
                (media_item_id, profile_id, user_rating, status, notes),
            )

        # Read the summary AFTER the write commits. get_rating_summary() opens
        # its own connection, and SQLite won't show it this connection's
        # uncommitted INSERT — calling it inside the `with` block returns the
        # pre-write state (the new verdict silently missing).
        return self.get_rating_summary(media_item_id)

    def get_rating_summary(self, media_item_id: int) -> Dict[str, Any]:
        """All profiles' verdicts on one item, plus the co-view tier.

        Tiers drive `profile=together` scoring:
          A - both saw it and liked it (>= 7.0): strongest "more like this" signal
          B - one liked it (>= 7.0), the other hasn't seen it: "he'd recommend this"
          C - nobody has seen it / nobody liked it: exploration
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            verdicts = [
                dict(r)
                for r in cursor.execute(
                    """
                    SELECT wh.profile_id, p.name, wh.user_rating, wh.status, wh.notes, wh.watched_at
                    FROM watch_history wh
                    JOIN profiles p ON p.id = wh.profile_id
                    WHERE wh.media_item_id = ?
                    ORDER BY wh.profile_id;
                    """,
                    (media_item_id,),
                ).fetchall()
            ]

            seen = {v["profile_id"]: v for v in verdicts if v["status"] == "seen"}
            unseen = [v["profile_id"] for v in verdicts if v["status"] == "not_seen"]
            liked = [pid for pid, v in seen.items() if (v["user_rating"] or 0) >= 7.0]

            if len(liked) >= 2:
                tier, reason = "A", "you both liked it"
            elif liked and unseen:
                tier, reason = "B", "one of you loved it, the other hasn't seen it"
            elif liked:
                # One liked it. If nobody else has rated or claimed to have
                # seen it, it's a "he'd recommend this to you" candidate. But if
                # the other HAS seen it and disliked it, that's a disagreement,
                # not an untested recommendation — treat it as tier C.
                others_seen = [pid for pid in seen if pid not in liked]
                if others_seen:
                    tier, reason = "C", "you watched it but didn't agree"
                else:
                    tier, reason = "B", "one of you liked it, nobody else has rated it"
            elif seen:
                tier, reason = "C", "seen, but not rated highly"
            else:
                tier, reason = "C", "neither of you has seen it"

            # Combined score: Bayesian-shrunk weighted mean over raters who saw
            # it. Each rater's rating is pulled toward the global mean in
            # proportion to how few ratings they have, so a 4/10 from someone
            # with 5 ratings can't outvote an 8/10 from someone with 200.
            combined = None
            num = den = 0.0
            for pid, v in seen.items():
                rating = v["user_rating"]
                if rating is None:
                    continue
                stats = cursor.execute(
                    "SELECT COUNT(*) AS n, COALESCE(AVG(user_rating), 0.0) AS m "
                    "FROM watch_history WHERE profile_id = ? AND status = 'seen' "
                    "AND user_rating IS NOT NULL;",
                    (pid,),
                ).fetchone()
                n, mean = float(stats["n"]), float(stats["m"])
                weight = n / (n + 10.0)
                num += weight * ((n * mean + 10.0 * 6.5) / (n + 10.0))
                den += weight
            if den:
                combined = round(num / den, 2)

            ratings = [v["user_rating"] for v in seen.values() if v["user_rating"] is not None]
            return {
                "media_item_id": media_item_id,
                "verdicts": verdicts,
                "combined_score": combined,
                "tier": tier,
                "tier_reason": reason,
                "disagreement": round(max(ratings) - min(ratings), 1) if len(ratings) >= 2 else None,
            }

    def get_rating_queue(
        self,
        profile_id: int = 1,
        media_type: str = "movie",
        limit: int = 25,
    ) -> List[Dict[str, Any]]:
        """Items this profile still needs to rate, highest-value first.

        Priority order is what makes the Rate tab worth using:
          1. in the library but never rated by this profile — turns existing
             watch signal into taste signal
          2. rated by the other profile but not this one — the co-view gap
          3. unseen and highly rated — seeds new signal

        Within each tier the Bayesian quality cut leads, so the queue starts
        with genuinely good films rather than obscurity.
        """
        quality = (
            "(COALESCE(mi.vote_count, 0) * 1.0 / (COALESCE(mi.vote_count, 0) + 1000.0)) * mi.rating"
            " + (1000.0 / (COALESCE(mi.vote_count, 0) + 1000.0)) * 6.5"
        )
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT mi.*, {quality} AS quality_score,
                       EXISTS(SELECT 1 FROM media_sources ms
                              WHERE ms.media_item_id = mi.id) AS in_library,
                       (SELECT COUNT(*) FROM watch_history w2
                         WHERE w2.media_item_id = mi.id AND w2.profile_id != ?) AS other_profile_count
                FROM media_items mi
                WHERE mi.media_type = ?
                  AND mi.rating >= 5.0
                  AND NOT EXISTS (
                        SELECT 1 FROM watch_history w
                        WHERE w.media_item_id = mi.id AND w.profile_id = ?
                  )
                ORDER BY in_library DESC, other_profile_count DESC,
                         quality_score DESC, mi.vote_count DESC
                LIMIT ?;
                """,
                (profile_id, media_type, profile_id, limit),
            )
            return [dict(r) for r in cursor.fetchall()]

    def get_co_view_history(self, min_liked: float = 7.0) -> List[Dict[str, Any]]:
        """Items BOTH profiles rated >= min_liked — the virtual-user sequence.

        Ordered by combined score descending, so feeding this to the generative
        engine produces a single coherent "watch history" for the pair. Returns
        fewer items than `together` needs until enough films are rated by both,
        which is why that engine stays a fallback.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            rows = cursor.execute(
                """
                SELECT mi.id, mi.media_type, mi.title, mi.release_year, mi.genres,
                       AVG(wh.user_rating) AS avg_rating,
                       COUNT(DISTINCT wh.profile_id) AS profile_count
                FROM watch_history wh
                JOIN media_items mi ON mi.id = wh.media_item_id
                WHERE wh.status = 'seen'
                  AND wh.user_rating >= ?
                GROUP BY mi.id
                HAVING COUNT(DISTINCT wh.profile_id) >= 2
                ORDER BY avg_rating DESC, mi.vote_count DESC;
                """,
                (min_liked,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_watch_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Retrieves past watch history entries ordered by date."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT wh.*, mi.title, mi.release_year, mi.genres, mi.rating as tmdb_rating, mi.tier2_keywords
                FROM watch_history wh
                JOIN media_items mi ON wh.media_item_id = mi.id
                ORDER BY wh.watched_at DESC
                LIMIT ?;
                """,
                (limit,),
            )
            return [dict(r) for r in cursor.fetchall()]
    def get_watch_history_chronological(self, limit: int = 200) -> List[Dict[str, Any]]:
        """Retrieves watch history ordered oldest-first (chronological).

        Used by the generative retrieval engine (TIGER) to build the
        interaction sequence of semantic IDs, and by the offline training
        pipeline to bootstrap sequence data.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT wh.*, mi.title, mi.release_year, mi.genres, mi.rating as tmdb_rating, mi.tier2_keywords
                FROM watch_history wh
                JOIN media_items mi ON wh.media_item_id = mi.id
                ORDER BY wh.watched_at ASC
                LIMIT ?;
                """,
                (limit,),
            )
            return [dict(r) for r in cursor.fetchall()]



    def get_user_taste_profile(self, min_rating: float = 7.0) -> Dict[str, Any]:
        """
        Analyzes watched movies where user_rating >= min_rating.
        Returns frequency maps of preferred genres and keywords.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT mi.genres, mi.tier2_keywords, wh.user_rating
                FROM watch_history wh
                JOIN media_items mi ON wh.media_item_id = mi.id
                WHERE wh.user_rating >= ?;
                """,
                (min_rating,),
            )
            rows = cursor.fetchall()

            genre_scores: Dict[str, float] = {}
            keyword_scores: Dict[str, float] = {}

            for row in rows:
                weight = float(row["user_rating"] or 7.0) / 10.0
                if row["genres"]:
                    for g in row["genres"].split(","):
                        g_clean = g.strip().lower()
                        if g_clean:
                            genre_scores[g_clean] = genre_scores.get(g_clean, 0.0) + weight
                if row["tier2_keywords"]:
                    for kw in row["tier2_keywords"].split(","):
                        kw_clean = kw.strip().lower()
                        if kw_clean:
                            keyword_scores[kw_clean] = keyword_scores.get(kw_clean, 0.0) + weight

            return {
                "preferred_genres": genre_scores,
                "preferred_keywords": keyword_scores,
            }

    def start_active_watch(self, media_item_id: int) -> int:
        """Starts an active 'currently watching' session. Cancels any previously dangling active sessions."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE active_watch_sessions SET status = 'cancelled' WHERE status = 'watching';")
            cursor.execute(
                """
                INSERT INTO active_watch_sessions (media_item_id, status)
                VALUES (?, 'watching');
                """,
                (media_item_id,),
            )
            return cursor.lastrowid

    def get_active_watch(self) -> Optional[Dict[str, Any]]:
        """Returns the currently active watch session and media item details, or None."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT aws.id as session_id, aws.started_at, aws.status,
                       mi.id as media_item_id, mi.title, mi.release_year, mi.genres,
                       mi.rating, mi.runtime, mi.poster_url, mi.overview,
                       ms.file_path, ms.source_type
                FROM active_watch_sessions aws
                JOIN media_items mi ON aws.media_item_id = mi.id
                LEFT JOIN media_sources ms ON ms.media_item_id = mi.id
                WHERE aws.status = 'watching'
                ORDER BY aws.started_at DESC
                LIMIT 1;
                """
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def finish_active_watch(
        self,
        session_id: int,
        user_rating: Optional[float] = None,
        notes: Optional[str] = None,
    ) -> Optional[int]:
        """Finishes an active watch session, marks it completed, and logs into watch_history."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT media_item_id FROM active_watch_sessions WHERE id = ?;", (session_id,))
            row = cursor.fetchone()
            if not row:
                return None
            media_item_id = row["media_item_id"]

            cursor.execute(
                "UPDATE active_watch_sessions SET status = 'completed' WHERE id = ?;",
                (session_id,),
            )
            cursor.execute(
                """
                INSERT INTO watch_history (media_item_id, user_rating, notes)
                VALUES (?, ?, ?);
                """,
                (media_item_id, user_rating, notes),
            )
            return cursor.lastrowid

    def cancel_active_watch(self, session_id: int) -> None:
        """Cancels an active watch session."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE active_watch_sessions SET status = 'cancelled' WHERE id = ?;",
                (session_id,),
            )

    def get_stats(self) -> Dict[str, int]:
        """Returns total counts of catalogue items, sources, and watched items."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as cnt FROM media_items;")
            total_items = cursor.fetchone()["cnt"]
            cursor.execute("SELECT COUNT(*) as cnt FROM media_sources;")
            total_sources = cursor.fetchone()["cnt"]
            cursor.execute("SELECT COUNT(*) as cnt FROM watch_history;")
            total_watched = cursor.fetchone()["cnt"]
            return {
                "total_items": total_items,
                "total_sources": total_sources,
                "total_watched": total_watched,
            }
            
    def get_virtual_sources(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Retrieves virtual media sources that need downloading."""
        query = """
            SELECT ms.*, mi.title, mi.release_year 
            FROM media_sources ms
            JOIN media_items mi ON ms.media_item_id = mi.id
            WHERE ms.source_type = 'virtual'
            ORDER BY ms.created_at DESC
        """
        if limit:
            query += f" LIMIT {limit}"
            
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query)
            return [dict(row) for row in cursor.fetchall()]
            
    def update_source_path(self, source_id: int, new_path: str) -> None:
        """Updates a media source's file path and marks it as local."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE media_sources 
                SET file_path = ?, source_type = 'local'
                WHERE id = ?;
                """,
                (new_path, source_id)
            )

    def get_or_create_media_item(
        self,
        title: str,
        release_year: Optional[int] = None,
        media_type: str = "movie",
    ) -> int:
        """Finds or creates a media item stub, returning its ID."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if release_year:
                cursor.execute(
                    "SELECT id FROM media_items WHERE LOWER(title) = LOWER(?) AND release_year = ? AND media_type = ? LIMIT 1;",
                    (title, release_year, media_type),
                )
            else:
                cursor.execute(
                    "SELECT id FROM media_items WHERE LOWER(title) = LOWER(?) AND media_type = ? LIMIT 1;",
                    (title, media_type),
                )
            row = cursor.fetchone()
            if row:
                return row["id"]
            cursor.execute(
                "INSERT INTO media_items (media_type, title, release_year) VALUES (?, ?, ?);",
                (media_type, title, release_year),
            )
            return cursor.lastrowid
