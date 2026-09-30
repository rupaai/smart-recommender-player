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
                    tmdb_id                 INTEGER UNIQUE,
                    title                   TEXT NOT NULL,
                    original_title          TEXT,
                    release_year            INTEGER,
                    overview                TEXT,
                    genres                  TEXT,                  -- Comma-separated names, e.g. "Action, Thriller"
                    genre_ids               TEXT,                  -- Comma-separated IDs, e.g. "28, 53"
                    rating                  REAL DEFAULT 0.0,      -- TMDB rating (0.0 - 10.0)
                    runtime                 INTEGER,               -- Minutes
                    poster_url              TEXT,
                    tier2_keywords          TEXT,                  -- Cached TMDB keywords/tags
                    tier2_reviews_summary   TEXT,                  -- Cached user review highlights
                    tier2_profiled_at       TIMESTAMP,             -- Cache timestamp
                    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
