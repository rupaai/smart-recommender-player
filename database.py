"""
Block 3: Database Manager (database.py)
Central hub managing the normalized SQLite catalogue, media sources, and watch history.
"""

from __future__ import annotations

import json
import os
import random
import re
import sqlite3
import time
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

    def get_schema_version(self) -> int:
        """Current schema version, as tracked by PRAGMA user_version (see the _migrate_* steps)."""
        with self.get_connection() as conn:
            return int(conn.execute("PRAGMA user_version;").fetchone()[0])

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
            self._migrate_v4_origin_country(cursor)
            self._migrate_v5_collections(cursor)
            self._migrate_v6_vibe(cursor)
            self._migrate_v7_feedback(cursor)
            self._migrate_v8_verdicts(cursor)
            self._migrate_v9_pairs(cursor)
            self._migrate_v10_season_ratings(cursor)
            self._migrate_v11_watch_later(cursor)
            self._migrate_v12_nl_browse(cursor)
            self._migrate_v13_nl_people(cursor)
            self._migrate_v14_nl_picks(cursor)
            self._migrate_v15_nl_polish_a(cursor)

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
        # Two seeded profiles (Labiba and Gabriele) so the rating UI is usable
        # immediately; both are renameable via update_profile(). profile 1 owns the pre-v3 rows.
        cursor.execute("INSERT OR IGNORE INTO profiles (id, name) VALUES (1, 'Labiba');")
        cursor.execute("INSERT OR IGNORE INTO profiles (id, name) VALUES (2, 'Gabriele');")

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

    def _migrate_v4_origin_country(self, cursor: sqlite3.Cursor) -> None:
        """Adds country-of-origin columns so 'South Asian', 'Korean' etc. work.

        "South Asian" is a place, not a genre, so genre filtering could never
        satisfy it — the recommender had no country signal at all and returned
        Hollywood regardless. TMDB's discover endpoint does not return
        `origin_country` per result, but it *does* filter on it via
        `with_origin_country`, and `with_original_language` narrows the same
        set. Storing both lets the recommender filter by origin without
        re-querying TMDB on every request.

        Idempotent (user_version gate + column-exists check). Additive
        ALTER TABLEs, so existing rows and ids are untouched.
        """
        version = cursor.execute("PRAGMA user_version;").fetchone()[0]
        if version >= 4:
            return

        cols = {
            row[1] for row in cursor.execute("PRAGMA table_info(media_items);").fetchall()
        }
        # ISO 3166-1 alpha-2 codes, comma-separated, e.g. "IN,GB".
        if "origin_country" not in cols:
            cursor.execute(
                "ALTER TABLE media_items ADD COLUMN origin_country TEXT;"
            )
        # ISO 639-1 code of the original audio language, e.g. "hi".
        if "original_language" not in cols:
            cursor.execute(
                "ALTER TABLE media_items ADD COLUMN original_language TEXT;"
            )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_media_items_origin_country "
            "ON media_items(origin_country);"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_media_items_original_language "
            "ON media_items(original_language);"
        )
        cursor.execute("PRAGMA user_version = 4;")
        print("[Database] Migration v4 applied: media_items.origin_country/original_language added.")

    def _migrate_v5_collections(self, cursor: sqlite3.Cursor) -> None:
        """Adds franchise/collection columns so series are watched in order.

        Without these the recommender had no idea that Deathly Hallows Part 1
        is the 7th Harry Potter film, so it happily recommended sequels to
        people who hadn't seen the first one. Filled from TMDB's
        `belongs_to_collection` by utils/backfill_collections.py.

          collection_id      TMDB collection id (NULL = standalone / unknown)
          collection_name    e.g. "Harry Potter Collection"
          collection_order   1-based position in the collection by release date
          collection_checked 0 = not looked up yet, 1 = looked up, -1 = TMDB 404

        Idempotent (user_version gate + column-exists check), additive only.
        """
        version = cursor.execute("PRAGMA user_version;").fetchone()[0]
        if version >= 5:
            return
        cols = {
            row[1] for row in cursor.execute("PRAGMA table_info(media_items);").fetchall()
        }
        for name, ddl in (
            ("collection_id", "INTEGER"),
            ("collection_name", "TEXT"),
            ("collection_order", "INTEGER"),
            ("collection_checked", "INTEGER DEFAULT 0"),
        ):
            if name not in cols:
                cursor.execute(f"ALTER TABLE media_items ADD COLUMN {name} {ddl};")
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_media_items_collection "
            "ON media_items(collection_id);"
        )
        cursor.execute("PRAGMA user_version = 5;")
        print("[Database] Migration v5 applied: media_items.collection_* added.")

    def _migrate_v6_vibe(self, cursor: sqlite3.Cursor) -> None:
        """Adds the vibe-search columns (design doc §2.1, §4).

          tagline          TMDB tagline (filled by utils/backfill_tmdb_vibe.py)
          tier2_checked    0 = keywords/reviews not fetched yet, 1 = fetched,
                           -1 = TMDB 404 (tier2_* columns already existed)
          vibe_tags        JSON from utils/mood_tag.py (LLM dials, flags, tags)
          vibe_tagged_at   when vibe_tags was written
          facets           JSON of 0-1 dials used for ranking (LLM value or
                           anchor fallback), filled by the vibe build
          vibe_cluster_id  k-means cluster from the vibe build

        Plus mood_tag_batches, which lets the tagger resume after Ctrl-C and
        report the real cost of each Batch API run.

        Idempotent (user_version gate + column-exists check), additive only.
        """
        version = cursor.execute("PRAGMA user_version;").fetchone()[0]
        if version >= 6:
            return
        cols = {
            row[1] for row in cursor.execute("PRAGMA table_info(media_items);").fetchall()
        }
        for name, ddl in (
            ("tagline", "TEXT"),
            ("tier2_checked", "INTEGER DEFAULT 0"),
            ("vibe_tags", "TEXT"),
            ("vibe_tagged_at", "TIMESTAMP"),
            ("facets", "TEXT"),
            ("vibe_cluster_id", "INTEGER"),
        ):
            if name not in cols:
                cursor.execute(f"ALTER TABLE media_items ADD COLUMN {name} {ddl};")
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS mood_tag_batches (
                batch_id     TEXT PRIMARY KEY,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                label        TEXT,               -- 'pilot' or 'run'
                model        TEXT,
                item_ids     TEXT NOT NULL,      -- JSON list of media_items.id
                status       TEXT DEFAULT 'submitted',  -- submitted / collected / canceled
                succeeded    INTEGER DEFAULT 0,
                failed       INTEGER DEFAULT 0,
                usage_json   TEXT,               -- summed token usage
                cost_usd     REAL
            );
            """
        )
        cursor.execute("PRAGMA user_version = 6;")
        print("[Database] Migration v6 applied: vibe columns + mood_tag_batches added.")

    def _migrate_v7_feedback(self, cursor: sqlite3.Cursor) -> None:
        """Feedback logging (design doc §4-§5): what was asked, what was shown,
        and what happened to each title. This is the training signal for the
        ranking weights, question usefulness and the embedder fine-tune.

          discover_sessions     one Discover request (page 1 and its "Show more"
                                pages), with weekday/hour for day-of-week
                                learning and the pop-up's answers once it exists
          discover_impressions  one row per title shown in a session, with its
                                rank and score parts; `outcome` climbs
                                shown < opened < dismissed < downloaded < played < rated
          vibe_clusters         named k-means clusters from the vibe build

        Idempotent (user_version gate + IF NOT EXISTS), additive only.
        """
        version = cursor.execute("PRAGMA user_version;").fetchone()[0]
        if version >= 7:
            return
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS discover_sessions (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                profile       TEXT,              -- 'me' | 'partner' | 'together'
                profile_id    INTEGER,           -- set when one person's taste is used
                media_type    TEXT,
                query_text    TEXT,
                request_json  TEXT,              -- mood, filters, chips
                engine        TEXT,              -- legacy / generative / vibe
                weekday       INTEGER,           -- 0 = Monday (local time)
                hour          INTEGER,
                answers_json  TEXT,              -- pop-up answers {question_id: option_id}
                asked_order   TEXT,              -- JSON list of question ids
                rules_json    TEXT,              -- parsed hard rules
                belief_json   TEXT               -- final vibe belief
            );
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS discover_impressions (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id     INTEGER NOT NULL REFERENCES discover_sessions(id) ON DELETE CASCADE,
                media_item_id  INTEGER NOT NULL REFERENCES media_items(id) ON DELETE CASCADE,
                rank           INTEGER,
                score_json     TEXT,
                shown_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                outcome        TEXT DEFAULT 'shown',
                outcome_at     TIMESTAMP,
                rating         REAL,
                UNIQUE(session_id, media_item_id)
            );
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_impressions_item ON discover_impressions(media_item_id, shown_at);"
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS vibe_clusters (
                id            INTEGER PRIMARY KEY,
                label         TEXT,
                size          INTEGER,
                top_keywords  TEXT,
                example_ids   TEXT                -- JSON list of media_items.id nearest the centroid
            );
            """
        )
        cursor.execute("PRAGMA user_version = 7;")
        print("[Database] Migration v7 applied: discover_sessions / discover_impressions / vibe_clusters added.")

    def _migrate_v8_verdicts(self, cursor: sqlite3.Cursor) -> None:
        """Verdict ratings (plan: "Better ratings"): five verdicts, "why" chips
        and vibe-check answers beside the 1-10 number.

          rating_details  one row per profile and title: verdict, didn't-finish
                          flag, worked / didn't chips, vibe-check answers, the
                          -1..+1 taste_score, and `inferred` (1 = guessed from an
                          old 1-10 rating, 0 = the person chose it)
          rating_history  the previous state each time a rating is changed, so a
                          change of mind never wipes the old one

        Existing ratings get an inferred verdict read against that person's own
        spread (services.verdicts.infer_verdict). watch_history.user_rating is
        left untouched. Idempotent (user_version gate + IF NOT EXISTS).
        """
        version = cursor.execute("PRAGMA user_version;").fetchone()[0]
        if version >= 8:
            return
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS rating_details (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id      INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                media_item_id   INTEGER NOT NULL REFERENCES media_items(id) ON DELETE CASCADE,
                verdict         TEXT NOT NULL,
                did_not_finish  INTEGER NOT NULL DEFAULT 0,
                worked          TEXT,              -- JSON list of chip ids
                didnt           TEXT,              -- JSON list of chip ids
                vibe_check      TEXT,              -- JSON {question_id: option id | [option ids]}
                taste_score     REAL NOT NULL,     -- -1..+1
                inferred        INTEGER NOT NULL DEFAULT 0,
                updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(profile_id, media_item_id)
            );
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS rating_history (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id      INTEGER NOT NULL,
                media_item_id   INTEGER NOT NULL,
                user_rating     REAL,
                verdict         TEXT,
                taste_score     REAL,
                inferred        INTEGER,
                notes           TEXT,
                recorded_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_rating_history_item ON rating_history(profile_id, media_item_id);"
        )

        from services.verdicts import infer_verdict

        rows = cursor.execute(
            "SELECT profile_id, media_item_id, user_rating FROM watch_history "
            "WHERE COALESCE(status, 'seen') = 'seen' AND user_rating IS NOT NULL AND profile_id IS NOT NULL;"
        ).fetchall()
        by_profile: Dict[int, List[float]] = {}
        for r in rows:
            by_profile.setdefault(r[0], []).append(float(r[2]))
        for r in rows:
            guess = infer_verdict(float(r[2]), by_profile[r[0]])
            cursor.execute(
                "INSERT OR IGNORE INTO rating_details (profile_id, media_item_id, verdict, taste_score, inferred) "
                "VALUES (?, ?, ?, ?, 1);",
                (r[0], r[1], guess["verdict"], guess["taste_score"]),
            )
        cursor.execute("PRAGMA user_version = 8;")
        print(f"[Database] Migration v8 applied: rating_details / rating_history added, {len(rows)} ratings given an inferred verdict.")

    def _migrate_v9_pairs(self, cursor: sqlite3.Cursor) -> None:
        """"This or that" (services/pairs.py): which of two seen films, from the
        same genre, is more this person's thing.

          rating_pairs    one row per answer: the winner and loser (for "about the
                          same", `same` = 1 and the two ids are just the pair as
                          shown), the shared genre, and `dial_diffs` (JSON: the
                          mood dials that differed most, winner minus loser)
          rating_details  gains `pair_score` (Bradley-Terry strength) and
                          `pair_nudge` (+/-0.15). `taste_score` stays the pure
                          verdict score; the final score is built on read.

        Idempotent (user_version gate + column-exists check), additive only.
        """
        version = cursor.execute("PRAGMA user_version;").fetchone()[0]
        if version >= 9:
            return
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS rating_pairs (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id    INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                winner_id     INTEGER NOT NULL REFERENCES media_items(id) ON DELETE CASCADE,
                loser_id      INTEGER NOT NULL REFERENCES media_items(id) ON DELETE CASCADE,
                same          INTEGER NOT NULL DEFAULT 0,
                shared_genre  TEXT,
                dial_diffs    TEXT,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        # One answer per unordered pair per person.
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_rating_pairs_unique "
            "ON rating_pairs(profile_id, MIN(winner_id, loser_id), MAX(winner_id, loser_id));"
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_rating_pairs_profile ON rating_pairs(profile_id);")
        existing = {r[1] for r in cursor.execute("PRAGMA table_info(rating_details);").fetchall()}
        for col in ("pair_score", "pair_nudge"):
            if col not in existing:
                cursor.execute(f"ALTER TABLE rating_details ADD COLUMN {col} REAL;")
        cursor.execute("PRAGMA user_version = 9;")
        print("[Database] Migration v9 applied: rating_pairs added, rating_details.pair_score / pair_nudge added.")

    def _migrate_v10_season_ratings(self, cursor: sqlite3.Cursor) -> None:
        """Optional per-season TV verdicts on top of the show-level verdict.

          media_items.season_count          seasons the show has (TMDB
                                            number_of_seasons, else the highest
                                            season_number in media_sources);
                                            filled by utils/backfill_season_counts.py
          media_items.season_count_checked  0 = not looked up on TMDB, 1 = done, -1 = 404
          season_ratings                    one row per profile, show and season:
                                            verdict (NULL when only "didn't
                                            finish"), did_not_finish, taste_score
          rating_details.inferred_source    why a verdict is inferred: 'rating'
                                            (an old 1-10 number) or 'seasons'
          rating_details.dropped_at_season  first season marked didn't finish

        Gated on what exists rather than only the version number (v9 is the
        "This or that" pairs). Additive only.
        """
        version = cursor.execute("PRAGMA user_version;").fetchone()[0]
        item_cols = {r[1] for r in cursor.execute("PRAGMA table_info(media_items);").fetchall()}
        for name, decl in (("season_count", "INTEGER"), ("season_count_checked", "INTEGER DEFAULT 0")):
            if name not in item_cols:
                cursor.execute(f"ALTER TABLE media_items ADD COLUMN {name} {decl};")
        rd_cols = {r[1] for r in cursor.execute("PRAGMA table_info(rating_details);").fetchall()}
        if "inferred_source" not in rd_cols:
            cursor.execute("ALTER TABLE rating_details ADD COLUMN inferred_source TEXT;")
            cursor.execute("UPDATE rating_details SET inferred_source = 'rating' WHERE inferred = 1;")
        if "dropped_at_season" not in rd_cols:
            cursor.execute("ALTER TABLE rating_details ADD COLUMN dropped_at_season INTEGER;")
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS season_ratings (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id      INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                media_item_id   INTEGER NOT NULL REFERENCES media_items(id) ON DELETE CASCADE,
                season_number   INTEGER NOT NULL,
                verdict         TEXT,              -- NULL when only "didn't finish"
                did_not_finish  INTEGER NOT NULL DEFAULT 0,
                taste_score     REAL NOT NULL,     -- -1..+1
                updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(profile_id, media_item_id, season_number)
            );
            """
        )
        if version < 10:
            cursor.execute("PRAGMA user_version = 10;")
            print("[Database] Migration v10 applied: season_ratings, media_items.season_count added.")

    def _migrate_v11_watch_later(self, cursor: sqlite3.Cursor) -> None:
        """Shared "Watch later" list, plus real names for the two seeded profiles.

          watch_later   one row per person per title: who added it (profile_id),
                        when, and, once the title is watched or rated as seen,
                        done_at / done_reason ('rated' | 'watched'). Done rows are
                        kept so the list can offer "watched from your list" + undo.

        Also renames the old seeded profiles: id 1 'Me' -> 'Labiba' and id 2
        'Partner' -> 'Gabriele'. Only rows still carrying the old default name
        are touched (a custom name is left alone), and a rename is skipped when
        the target name is already taken (profiles.name is UNIQUE). Runs once,
        when the DB moves to v11; fresh DBs are already seeded with the new
        names by v3.

        Idempotent (user_version gate + IF NOT EXISTS), additive only.
        """
        version = cursor.execute("PRAGMA user_version;").fetchone()[0]
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS watch_later (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                media_item_id  INTEGER NOT NULL REFERENCES media_items(id) ON DELETE CASCADE,
                profile_id     INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                added_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                done_at        TIMESTAMP,          -- set when watched / rated as seen
                done_reason    TEXT,               -- 'rated' | 'watched'
                UNIQUE(media_item_id, profile_id)
            );
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_watch_later_done_added ON watch_later(done_at, added_at);"
        )
        if version < 11:
            for pid, old, new in ((1, "Me", "Labiba"), (2, "Partner", "Gabriele")):
                taken = cursor.execute("SELECT 1 FROM profiles WHERE name = ?;", (new,)).fetchone()
                if not taken:
                    cursor.execute(
                        "UPDATE profiles SET name = ? WHERE id = ? AND name = ?;", (new, pid, old)
                    )
            cursor.execute("PRAGMA user_version = 11;")
            print("[Database] Migration v11 applied: watch_later added, seeded profiles renamed.")

    def _migrate_v12_nl_browse(self, cursor: sqlite3.Cursor) -> None:
        """Northern Lights M2 (browse).

          media_sources.jellyfin_id   Jellyfin's id for this file, cached so the browse
                                      pages do not scan Jellyfin on every request
                                      (filled lazily; re-checked when Jellyfin reports a
                                      different id for the same path)
          nl_show_prefs               per-show settings; for now autoplay of the next
                                      episode (1 = on, the default, 0 = off)

        Idempotent (user_version gate + column-exists check + IF NOT EXISTS), additive only.
        """
        version = cursor.execute("PRAGMA user_version;").fetchone()[0]
        cols = {row[1] for row in cursor.execute("PRAGMA table_info(media_sources);").fetchall()}
        if "jellyfin_id" not in cols:
            cursor.execute("ALTER TABLE media_sources ADD COLUMN jellyfin_id TEXT;")
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS nl_show_prefs (
                media_item_id  INTEGER PRIMARY KEY REFERENCES media_items(id) ON DELETE CASCADE,
                autoplay       INTEGER NOT NULL DEFAULT 1,
                updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        if version < 12:
            cursor.execute("PRAGMA user_version = 12;")
            print("[Database] Migration v12 applied: media_sources.jellyfin_id, nl_show_prefs added.")

    def _migrate_v13_nl_people(self, cursor: sqlite3.Cursor) -> None:
        """Northern Lights M4 (who's watching).

          nl_person_prefs   one row per viewer key (a configured person's id, "both", or
                            "default" when no people are configured): preferred audio and
                            subtitle language, subtitles on by default, the subtitle look
                            (JSON) and the subtitle search language.

        The people themselves live in config, not here. Idempotent and additive only.
        """
        version = cursor.execute("PRAGMA user_version;").fetchone()[0]
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS nl_person_prefs (
                person_id       TEXT PRIMARY KEY,
                audio_lang      TEXT,
                subtitle_lang   TEXT,
                subtitles_on    INTEGER,
                sub_style       TEXT,
                search_lang     TEXT,
                updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        if version < 13:
            cursor.execute("PRAGMA user_version = 13;")
            print("[Database] Migration v13 applied: nl_person_prefs added.")

    def _migrate_v14_nl_picks(self, cursor: sqlite3.Cursor) -> None:
        """Northern Lights M6 (picks for two).

          watch_later.owner_profile_id   NULL = on the shared watchlist (everyone sees it, the
                                         default and what every pre-M6 row is); a profile id =
                                         "Just me": only that person sees it.
          nl_interest                    yes / maybe / pass per person per title. "Both said yes"
                                         floats to the top of the watchlist and feeds the picks.

        Idempotent (user_version gate + column-exists check + IF NOT EXISTS), additive only.
        """
        version = cursor.execute("PRAGMA user_version;").fetchone()[0]
        cols = {row[1] for row in cursor.execute("PRAGMA table_info(watch_later);").fetchall()}
        if "owner_profile_id" not in cols:
            cursor.execute("ALTER TABLE watch_later ADD COLUMN owner_profile_id INTEGER;")
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS nl_interest (
                media_item_id  INTEGER NOT NULL REFERENCES media_items(id) ON DELETE CASCADE,
                profile_id     INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                state          TEXT NOT NULL CHECK (state IN ('yes', 'maybe', 'pass')),
                updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (media_item_id, profile_id)
            );
            """
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_nl_interest_profile ON nl_interest(profile_id, state);")
        if version < 14:
            cursor.execute("PRAGMA user_version = 14;")
            print("[Database] Migration v14 applied: watch_later.owner_profile_id, nl_interest added.")

    def _migrate_v15_nl_polish_a(self, cursor: sqlite3.Cursor) -> None:
        """Northern Lights M7a (player polish).

          nl_show_viewer_prefs   per show AND per viewer key (a person's id, "both" or "default"):
                                 skip_intro = 1 means "always skip the intro" for that viewer.
                                 (nl_show_prefs stays show-wide: it holds the autoplay switch.)

        Idempotent (user_version gate + IF NOT EXISTS), additive only.
        """
        version = cursor.execute("PRAGMA user_version;").fetchone()[0]
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS nl_show_viewer_prefs (
                media_item_id  INTEGER NOT NULL REFERENCES media_items(id) ON DELETE CASCADE,
                viewer_key     TEXT NOT NULL,
                skip_intro     INTEGER NOT NULL DEFAULT 0,
                updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (media_item_id, viewer_key)
            );
            """
        )
        if version < 15:
            cursor.execute("PRAGMA user_version = 15;")
            print("[Database] Migration v15 applied: nl_show_viewer_prefs added.")

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
            # `total_changes` is the only reliable signal for whether an INSERT
            # conflicted — cursor.rowcount is not. It is sampled directly after
            # each INSERT, before the backfill UPDATE below runs, since that
            # UPDATE also bumps the counter and would inflate the figure.
            inserted = 0
            for it in items:
                before_insert = conn.total_changes
                cursor.execute(
                    """
                    INSERT INTO media_items
                        (media_type, tmdb_id, title, original_title, release_year,
                         overview, genres, genre_ids, rating, runtime, poster_url,
                         original_language, origin_country)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        it.get("original_language"),
                        it.get("origin_country"),
                    ),
                )
                inserted += conn.total_changes - before_insert
                # DO NOTHING means a pre-existing row never receives the newly
                # available columns. The region importer re-visits titles that
                # are already in the catalogue precisely to stamp origin on
                # them, so fill in only what is still missing — never clobber
                # data that is already set.
                if it.get("origin_country") or it.get("original_language"):
                    cursor.execute(
                        """
                        UPDATE media_items
                        SET origin_country = COALESCE(NULLIF(media_items.origin_country, ''), ?),
                            original_language = COALESCE(NULLIF(media_items.original_language, ''), ?),
                            -- vote_count is a fact, not a fallback: an earlier
                            -- import wrote it as 0 because the field was never
                            -- mapped, and the COALESCE-NULLIF form above can
                            -- only fill blanks, never correct a wrong 0. A
                            -- genuine 0-vote title keeps its 0 only if TMDB
                            -- still reports 0 on this pass.
                            vote_count = COALESCE(NULLIF(?, 0), media_items.vote_count)
                        WHERE media_type = ? AND tmdb_id = ?;
                        """,
                        (
                            it.get("origin_country"),
                            it.get("original_language"),
                            it.get("vote_count") or 0,
                            it["media_type"],
                            it["tmdb_id"],
                        ),
                    )
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

    def get_items_missing_tier2(self, media_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """Items whose TMDB keywords / reviews / tagline haven't been fetched yet,
        most-voted first (so a partial run covers the films that matter most)."""
        sql = (
            "SELECT id, media_type, tmdb_id, title FROM media_items "
            "WHERE COALESCE(tier2_checked, 0) = 0 AND tmdb_id IS NOT NULL"
        )
        params: List[Any] = []
        if media_type:
            sql += " AND media_type = ?"
            params.append(media_type)
        sql += " ORDER BY COALESCE(vote_count, 0) DESC;"
        with self.get_connection() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def set_tier2_vibe(
        self,
        item_id: int,
        keywords: Optional[str],
        reviews_summary: Optional[str],
        tagline: Optional[str],
        checked: int = 1,
    ) -> None:
        """Stores TMDB keywords, review excerpts and tagline (checked=-1 = TMDB 404)."""
        with self.get_connection() as conn:
            conn.execute(
                """
                UPDATE media_items
                SET tier2_keywords = COALESCE(?, tier2_keywords),
                    tier2_reviews_summary = COALESCE(?, tier2_reviews_summary),
                    tagline = COALESCE(?, tagline),
                    tier2_profiled_at = CURRENT_TIMESTAMP,
                    tier2_checked = ?
                WHERE id = ?;
                """,
                (keywords, reviews_summary, tagline, checked, item_id),
            )

    # ----- Feedback logging (schema v7) -----

    OUTCOME_ORDER = {"shown": 0, "opened": 1, "dismissed": 2, "downloaded": 3, "played": 4, "rated": 5}
    # Watching or rating a title counts for the impression that led to it if it
    # was shown within this many days.
    ATTRIBUTION_DAYS = 7

    def create_discover_session(
        self,
        profile: Optional[str],
        media_type: Optional[str],
        query_text: Optional[str],
        request: Optional[Dict[str, Any]] = None,
        engine: Optional[str] = None,
        profile_id: Optional[int] = None,
        now: Optional[datetime] = None,
    ) -> int:
        import json as _json
        now = now or datetime.now()
        with self.get_connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO discover_sessions
                    (profile, profile_id, media_type, query_text, request_json, engine, weekday, hour)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (profile, profile_id, media_type, query_text,
                 _json.dumps(request or {}, sort_keys=True, default=str), engine,
                 now.weekday(), now.hour),
            )
            return int(cur.lastrowid)

    def session_exists(self, session_id: int) -> bool:
        with self.get_connection() as conn:
            return conn.execute("SELECT 1 FROM discover_sessions WHERE id = ?;", (session_id,)).fetchone() is not None

    SESSION_LOG_FIELDS = ("answers_json", "asked_order", "rules_json", "belief_json", "engine")

    def update_discover_session(self, session_id: int, **fields: Any) -> None:
        """Stores the pop-up's answers / asked order / belief on the session."""
        cols = [k for k in fields if k in self.SESSION_LOG_FIELDS]
        if not cols:
            return
        with self.get_connection() as conn:
            conn.execute(f"UPDATE discover_sessions SET {', '.join(f'{c} = ?' for c in cols)} WHERE id = ?;",
                         (*[fields[c] for c in cols], session_id))

    def log_impressions(self, session_id: int, items: List[Dict[str, Any]], start_rank: int = 1) -> int:
        """Records the titles shown, in order. Re-showing a title in the same
        session keeps its first rank. Returns rows added."""
        import json as _json
        score_keys = ("recommendation_score", "score", "taste_bonus", "query_boost", "tier_boost",
                      "relevance_boost", "generative_rank", "co_view_tier", "combined_score",
                      "vibe_similarity", "vibe_boost", "vibe_cluster_id", "popup_boost",
                      "facet_fit", "cluster_affinity")
        added = 0
        with self.get_connection() as conn:
            for offset, it in enumerate(items):
                if not it.get("id"):
                    continue
                parts = {k: it[k] for k in score_keys if it.get(k) is not None}
                before = conn.total_changes
                conn.execute(
                    """
                    INSERT INTO discover_impressions (session_id, media_item_id, rank, score_json)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(session_id, media_item_id) DO NOTHING;
                    """,
                    (session_id, int(it["id"]), start_rank + offset,
                     _json.dumps(parts, sort_keys=True, default=str) if parts else None),
                )
                added += conn.total_changes - before
        return added

    def record_outcome(
        self,
        media_item_id: int,
        outcome: str,
        session_id: Optional[int] = None,
        rating: Optional[float] = None,
    ) -> bool:
        """Moves an impression's outcome forward (never backward: a title that
        was played stays 'played' even if it's later opened again).

        With `session_id`, that session's impression is updated. Without it
        (playing from the library, rating in the Rate tab) the most recent
        impression of the title within ATTRIBUTION_DAYS is used. Returns True
        when an impression was updated.
        """
        if outcome not in self.OUTCOME_ORDER:
            raise ValueError(f"unknown outcome {outcome!r}")
        with self.get_connection() as conn:
            if session_id is not None:
                row = conn.execute(
                    "SELECT id, outcome FROM discover_impressions WHERE session_id = ? AND media_item_id = ?;",
                    (session_id, media_item_id),
                ).fetchone()
            else:
                row = conn.execute(
                    f"""
                    SELECT id, outcome FROM discover_impressions
                    WHERE media_item_id = ?
                      AND shown_at >= datetime('now', '-{int(self.ATTRIBUTION_DAYS)} days')
                    ORDER BY shown_at DESC, id DESC LIMIT 1;
                    """,
                    (media_item_id,),
                ).fetchone()
            if not row:
                return False
            if self.OUTCOME_ORDER[outcome] < self.OUTCOME_ORDER.get(row["outcome"] or "shown", 0):
                return False
            conn.execute(
                """
                UPDATE discover_impressions
                SET outcome = ?, outcome_at = CURRENT_TIMESTAMP, rating = COALESCE(?, rating)
                WHERE id = ?;
                """,
                (outcome, rating, row["id"]),
            )
            return True

    def get_feedback_stats(self) -> Dict[str, Any]:
        """Counts per outcome, so you can see when there's enough signal to learn from."""
        with self.get_connection() as conn:
            sessions = conn.execute("SELECT COUNT(*) FROM discover_sessions;").fetchone()[0]
            rows = conn.execute(
                "SELECT outcome, COUNT(*) FROM discover_impressions GROUP BY outcome;"
            ).fetchall()
        return {"sessions": sessions, "outcomes": {r[0]: r[1] for r in rows}}

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

    def get_library_items(self, media_type: str = "movie") -> List[Dict[str, Any]]:
        """Titles that have at least one source file, for the Northern Lights lists.

        Besides the card fields it returns `added_at` (newest file) and, for shows,
        `seasons` (distinct season numbers among the files) and `file_paths` (every source file)."""
        with self.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT mi.id, mi.media_type, mi.title, mi.release_year AS year, mi.runtime, mi.poster_url,
                       (SELECT MAX(ms.created_at) FROM media_sources ms WHERE ms.media_item_id = mi.id) AS added_at,
                       (SELECT COUNT(DISTINCT ms.season_number) FROM media_sources ms
                         WHERE ms.media_item_id = mi.id AND ms.season_number IS NOT NULL) AS seasons,
                       (SELECT ms.jellyfin_id FROM media_sources ms
                         WHERE ms.media_item_id = mi.id AND ms.jellyfin_id IS NOT NULL LIMIT 1) AS jellyfin_id,
                       (SELECT GROUP_CONCAT(ms.file_path, char(31)) FROM media_sources ms
                         WHERE ms.media_item_id = mi.id) AS file_paths
                FROM media_items mi
                WHERE mi.media_type = ?
                  AND EXISTS (SELECT 1 FROM media_sources ms WHERE ms.media_item_id = mi.id)
                ORDER BY LOWER(mi.title), mi.release_year;
                """,
                (media_type,),
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["file_paths"] = (d.get("file_paths") or "").split("\x1f") if d.get("file_paths") else []
                out.append(d)
            return out

    # --- Northern Lights browse (schema v12) -------------------------------------

    def get_nl_title(self, item_id: int) -> Optional[Dict[str, Any]]:
        """One title for the details page: the catalogue row, its source files in
        season/episode order, and when the newest file arrived. None if it has no file."""
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM media_items WHERE id = ?;", (item_id,)).fetchone()
            if not row:
                return None
            item = dict(row)
            sources = conn.execute(
                "SELECT * FROM media_sources WHERE media_item_id = ? "
                "ORDER BY COALESCE(season_number, 0), COALESCE(episode_number, 0), id;",
                (item_id,),
            ).fetchall()
            item["sources"] = [dict(s) for s in sources]
            if not item["sources"]:
                return None
            item["added_at"] = max((s["created_at"] or "") for s in item["sources"]) or None
            return item

    def get_sources_by_paths(self, paths: List[str]) -> Dict[str, Dict[str, Any]]:
        """{file_path: source row + media_type/title of its item} for the exact paths given."""
        paths = [p for p in dict.fromkeys(paths) if p]
        out: Dict[str, Dict[str, Any]] = {}
        with self.get_connection() as conn:
            for i in range(0, len(paths), 400):
                chunk = paths[i:i + 400]
                marks = ",".join("?" * len(chunk))
                for r in conn.execute(
                    f"""
                    SELECT ms.id AS source_id, ms.media_item_id, ms.file_path, ms.season_number,
                           ms.episode_number, ms.jellyfin_id, mi.media_type, mi.title, mi.runtime,
                           mi.release_year AS year, mi.poster_url
                    FROM media_sources ms JOIN media_items mi ON mi.id = ms.media_item_id
                    WHERE ms.file_path IN ({marks});
                    """,
                    chunk,
                ).fetchall():
                    out[r["file_path"]] = dict(r)
        return out

    def get_source(self, source_id: int, media_item_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """One media_sources row; when media_item_id is given it must belong to that item."""
        with self.get_connection() as conn:
            if media_item_id is None:
                row = conn.execute("SELECT * FROM media_sources WHERE id = ?;", (source_id,)).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM media_sources WHERE id = ? AND media_item_id = ?;", (source_id, media_item_id)
                ).fetchone()
            return dict(row) if row else None

    def set_jellyfin_ids(self, ids: Dict[int, str]) -> int:
        """Caches {media_sources.id: jellyfin id}. Only rows whose value changes are written."""
        changed = 0
        with self.get_connection() as conn:
            for source_id, jf_id in ids.items():
                cur = conn.execute(
                    "UPDATE media_sources SET jellyfin_id = ? WHERE id = ? AND COALESCE(jellyfin_id, '') != ?;",
                    (jf_id, source_id, jf_id),
                )
                changed += cur.rowcount
        return changed

    def get_show_autoplay(self, media_item_id: int) -> bool:
        """Autoplay of the next episode for this show; on unless it was switched off."""
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT autoplay FROM nl_show_prefs WHERE media_item_id = ?;", (media_item_id,)
            ).fetchone()
        return True if row is None else bool(row["autoplay"])

    def set_show_autoplay(self, media_item_id: int, autoplay: bool) -> bool:
        """Stores the per-show autoplay setting. Raises ValueError if the title is not a show."""
        with self.get_connection() as conn:
            row = conn.execute("SELECT media_type FROM media_items WHERE id = ?;", (media_item_id,)).fetchone()
            if not row or row["media_type"] != "tv":
                raise ValueError("autoplay is a setting for TV shows")
            conn.execute(
                """
                INSERT INTO nl_show_prefs (media_item_id, autoplay, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(media_item_id) DO UPDATE SET autoplay = excluded.autoplay, updated_at = CURRENT_TIMESTAMP;
                """,
                (media_item_id, 1 if autoplay else 0),
            )
        return bool(autoplay)

    def get_show_skip_intro(self, media_item_id: int, viewer_key: str) -> bool:
        """Does this viewer always skip the intro of this show? Off unless they switched it on."""
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT skip_intro FROM nl_show_viewer_prefs WHERE media_item_id = ? AND viewer_key = ?;",
                (media_item_id, viewer_key),
            ).fetchone()
        return False if row is None else bool(row["skip_intro"])

    def set_show_skip_intro(self, media_item_id: int, viewer_key: str, skip_intro: bool) -> bool:
        """Stores the viewer's "always skip intro" for one show. Raises ValueError if it is not a show."""
        with self.get_connection() as conn:
            row = conn.execute("SELECT media_type FROM media_items WHERE id = ?;", (media_item_id,)).fetchone()
            if not row or row["media_type"] != "tv":
                raise ValueError("skip intro is a setting for TV shows")
            conn.execute(
                """
                INSERT INTO nl_show_viewer_prefs (media_item_id, viewer_key, skip_intro, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(media_item_id, viewer_key)
                DO UPDATE SET skip_intro = excluded.skip_intro, updated_at = CURRENT_TIMESTAMP;
                """,
                (media_item_id, viewer_key, 1 if skip_intro else 0),
            )
        return bool(skip_intro)

    # ------------------------------------------------------------------ Northern Lights people (M4)

    def get_person_prefs(self, person_id: str) -> Optional[Dict[str, Any]]:
        """The saved language / subtitle prefs of one viewer key, or None if nothing was ever saved."""
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM nl_person_prefs WHERE person_id = ?;", (person_id,)).fetchone()
        if row is None:
            return None
        try:
            style = json.loads(row["sub_style"]) if row["sub_style"] else None
        except ValueError:
            style = None
        return {
            "audio_lang": row["audio_lang"],
            "subtitle_lang": row["subtitle_lang"],
            "subtitles_on": None if row["subtitles_on"] is None else bool(row["subtitles_on"]),
            "sub_style": style,
            "search_lang": row["search_lang"],
        }

    def set_person_prefs(self, person_id: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        """Updates the given (already validated) pref fields and leaves the others alone."""
        allowed = ("audio_lang", "subtitle_lang", "subtitles_on", "sub_style", "search_lang")
        values: Dict[str, Any] = {}
        for key in allowed:
            if key in fields:
                v = fields[key]
                if key == "sub_style":
                    v = json.dumps(v, separators=(",", ":")) if v else None
                elif key == "subtitles_on":
                    v = None if v is None else (1 if v else 0)
                values[key] = v
        with self.get_connection() as conn:
            conn.execute("INSERT OR IGNORE INTO nl_person_prefs (person_id) VALUES (?);", (person_id,))
            if values:
                sets = ", ".join(f"{k} = ?" for k in values)
                conn.execute(
                    f"UPDATE nl_person_prefs SET {sets}, updated_at = CURRENT_TIMESTAMP WHERE person_id = ?;",
                    (*values.values(), person_id),
                )
        return self.get_person_prefs(person_id) or {}

    def mark_watch_done_for_profiles(
        self, session_id: int, profile_ids: List[int], user_rating: Optional[float] = None, notes: Optional[str] = None
    ) -> Optional[int]:
        """Finishes the active watch session and logs it as seen by exactly these profiles
        (who's watching), instead of the legacy row that always landed on profile 1.
        Returns the media_item_id, or None if the session is unknown."""
        with self.get_connection() as conn:
            row = conn.execute("SELECT media_item_id FROM active_watch_sessions WHERE id = ?;", (session_id,)).fetchone()
            if not row:
                return None
            conn.execute("UPDATE active_watch_sessions SET status = 'completed' WHERE id = ?;", (session_id,))
            media_item_id = row["media_item_id"]
        for pid in profile_ids:
            if user_rating is not None:
                self.rate_item(media_item_id, profile_id=pid, user_rating=user_rating, status="seen", notes=notes)
                continue
            # Seen but not rated: never wipe a rating this person already gave.
            with self.get_connection() as conn:
                if conn.execute("SELECT 1 FROM profiles WHERE id = ?;", (pid,)).fetchone():
                    conn.execute(
                        "INSERT INTO watch_history (media_item_id, profile_id, user_rating, status, notes) "
                        "VALUES (?, ?, NULL, 'seen', ?) ON CONFLICT(profile_id, media_item_id) DO UPDATE SET status = 'seen' "
                        "WHERE watch_history.status != 'seen';",
                        (media_item_id, pid, notes),
                    )
        return media_item_id

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
                query += " AND mi.id NOT IN (SELECT media_item_id FROM watch_history WHERE COALESCE(status, 'seen') = 'seen')"

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
        exclude_genres: Optional[List[str]] = None,
        origin_countries: Optional[List[str]] = None,
        origin_languages: Optional[List[str]] = None,
        exclude_watched: bool = True,
        min_rating: float = 0.0,
        era: Optional[str] = None,
        duration: Optional[str] = None,
        min_votes: int = 0,
        concept_terms: Optional[List[str]] = None,
        limit: int = 250,
        only_ids: Optional[List[int]] = None,
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

            # The Bayesian prior is scaled to the pool, not fixed at 1000.
            # A fixed prior suited a mainstream catalogue (mean ~820 votes)
            # but pins an entire small national cinema to the 6.5 prior:
            # Bangladeshi titles average 3.7 votes and peak at 190, so SQL
            # ordering degenerated to rowid order and the Python scorer then
            # re-sorted a flat list. prior_votes is derived below from the
            # votes actually present in this filtered result set.
            prior_votes = 1000
            prior_params = [float(prior_votes)] * 3

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
                query += " AND mi.id NOT IN (SELECT media_item_id FROM watch_history WHERE COALESCE(status, 'seen') = 'seen')"
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

            # Genre *exclusion* (query intent like "relaxing, not too
            # complicated"). Applied in SQL so a contradicting genre can never
            # enter the shortlist — filtering in Python after the Bayesian cut
            # would silently shrink the pool and return fewer results than the
            # user asked for.
            #
            # The predicate MUST be `NOT (... LIKE ...)` and NOT
            # `(... NOT LIKE ... OR ...)`. Both look equivalent, but SQLite
            # treats the 0 result of `NOT LIKE` as falsy rather than as a
            # boolean for `NOT` to invert, so the negated form dropped EVERY
            # row (912 clean animation films -> 0 results). Positives inside,
            # NOT outside.
            exclude_clauses = []
            for g in exclude_genres or []:
                needle = (g or "").strip().lower().replace(" ", "")
                if needle:
                    exclude_clauses.append(
                        "',' || REPLACE(LOWER(COALESCE(mi.genres, '')), ' ', '') || ',' LIKE ?"
                    )
                    params.append(f"%,{needle},%")
            if exclude_clauses:
                query += " AND NOT (" + " OR ".join(exclude_clauses) + ")"

            # Country-of-origin filter ("South Asian", "Korean"). `genres` is
            # stored comma-separated, same shape, so reuse the padding trick.
            #
            # When origin_languages is supplied it is ANDed with the country
            # rather than used as an alternative: TMDB mislabels origin for
            # co-productions (it files Point Break as `IN`), while a film's
            # original_language is reliable. Requiring both keeps the
            # Hollywood false positives out.
            country_clauses = []
            for c in origin_countries or []:
                code = (c or "").strip().lower()
                if code:
                    country_clauses.append(
                        "',' || REPLACE(LOWER(COALESCE(mi.origin_country, '')), ' ', '') || ',' LIKE ?"
                    )
                    params.append(f"%,{code},%")
            if country_clauses:
                query += " AND (" + " OR ".join(country_clauses) + ")"

            if origin_languages:
                lang_clauses = []
                for lang in origin_languages:
                    code = (lang or "").strip().lower()
                    if code:
                        lang_clauses.append(
                            "LOWER(COALESCE(mi.original_language, '')) = ?"
                        )
                        params.append(code)
                if lang_clauses:
                    query += " AND (" + " OR ".join(lang_clauses) + ")"

            # Vibe search: the same hard filters, applied to the titles whose
            # vibe vectors are closest to the request.
            if only_ids is not None:
                ids = [int(i) for i in only_ids][:5000]
                if not ids:
                    return []
                query += f" AND mi.id IN ({','.join('?' * len(ids))})"
                params.extend(ids)

            # Recompute the Bayesian prior from the rows THIS query returns.
            # The fixed 1000 suited a mainstream catalogue (mean ~820 votes) but
            # pinned an entire small national cinema to the 6.5 prior:
            # Bangladeshi titles average 3.7 votes and peak at 190, so SQL
            # ordering degenerated to rowid order. Probe without ORDER BY —
            # the pool median is an input to the ORDER BY that ranks it.
            probe_cursor = conn.cursor()
            probe_cursor.execute(
                query.replace("SELECT mi.*,", "SELECT COALESCE(mi.vote_count, 0),", 1)
                + " LIMIT 4000",
                params,
            )
            pool_votes = sorted(int(r[0] or 0) for r in probe_cursor.fetchall())
            if pool_votes:
                median_votes = pool_votes[len(pool_votes) // 2]
                prior_votes = max(10, min(1000, median_votes))
                prior_params = [float(prior_votes)] * 3

            # Tie-break on vote_count, NOT raw rating. Bayesian deliberately
            # flattens an under-attested film toward the global mean, so a
            # 2-vote 10.0 and a 35-vote 6.7 both land near 6.51 — and ordering
            # by `rating` next then promoted the 2-vote film to the top of a
            # "bangladeshi movies" list ahead of The Clay Bird and Monpura.
            # Among equally-shrunk titles, the better-attested one wins.
            # Rebuilt here so it captures the pool-derived prior_votes.
            order_expr = (
                "(MAX(COALESCE(mi.vote_count, 0), 0) * 1.0 / (MAX(COALESCE(mi.vote_count, 0), 0) + ?)) * mi.rating"
                " + (? / (MAX(COALESCE(mi.vote_count, 0), 0) + ?)) * 6.5"
            )

            # Premise relevance has to be part of the ORDER BY, not a bonus
            # applied after the cut. A concept request ranks by rating, so a
            # premise-relevant film at 7.3 sits ~1000th and is never scored at
            # all (They Live was excluded from its own shortlist this way).
            # Promoted into the expression so premise match can lift a title
            # above the quality cut when the user asked for an idea.
            concept_params: List[Any] = []
            if concept_terms:
                boosts: List[str] = []
                for term in concept_terms:
                    if not term or len(term) < 3:
                        continue
                    boosts.append(
                        "(CASE WHEN LOWER(COALESCE(mi.title, '')) LIKE ? "
                        "OR LOWER(COALESCE(mi.overview, '')) LIKE ? THEN 1 ELSE 0 END)"
                    )
                    like = f"%{term.lower()}%"
                    concept_params.extend([like, like])
                if boosts:
                    order_expr += " + 3.0 * (" + " + ".join(boosts) + ")"
            query += (
                f" GROUP BY mi.id ORDER BY {order_expr} DESC,"
                " MAX(COALESCE(mi.vote_count, 0)) DESC, mi.rating DESC,"
                " mi.release_year DESC"
            )
            query += " LIMIT ?"
            params.extend(prior_params)
            params.extend(concept_params)
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

    # ------------------------------------------------------------------
    # Watch later (shared list, one row per person per title)
    # ------------------------------------------------------------------

    @staticmethod
    def _wl_visible(personal_of: Optional[List[int]]) -> Tuple[str, list]:
        """SQL for which watch_later rows a reader may see: the shared ones (owner NULL) and, when
        `personal_of` names profiles, those profiles' "Just me" rows. None / [] = shared only, which
        is all the pre-M6 UI ever sees."""
        ids = [int(i) for i in (personal_of or [])]
        if not ids:
            return "wl.owner_profile_id IS NULL", []
        return f"(wl.owner_profile_id IS NULL OR wl.owner_profile_id IN ({','.join('?' * len(ids))}))", ids

    def _watch_later_entry(
        self, cursor: sqlite3.Cursor, media_item_id: int, personal_of: Optional[List[int]] = None
    ) -> Optional[Dict[str, Any]]:
        """Builds the grouped list entry for one title (all visible people), or None."""
        vis, vparams = self._wl_visible(personal_of)
        entries = self._watch_later_entries(
            cursor, f"wl.media_item_id = ? AND {vis}", (media_item_id, *vparams), include_done=True
        )
        return entries[0] if entries else None

    def _watch_later_entries(
        self, cursor: sqlite3.Cursor, where: str, params: tuple, include_done: bool
    ) -> List[Dict[str, Any]]:
        """Groups watch_later rows (filtered by `where`) into one entry per title."""
        rows = cursor.execute(
            f"""
            SELECT wl.media_item_id, wl.profile_id, wl.added_at, wl.done_at, wl.done_reason, wl.owner_profile_id,
                   p.name AS profile_name,
                   m.title, m.release_year, m.media_type, m.poster_url, m.genres,
                   m.runtime, m.rating, m.overview,
                   (SELECT s.file_path FROM media_sources s
                     WHERE s.media_item_id = m.id AND s.source_type = 'local'
                     ORDER BY s.id LIMIT 1) AS file_path
            FROM watch_later wl
            JOIN media_items m ON m.id = wl.media_item_id
            JOIN profiles p ON p.id = wl.profile_id
            WHERE {where}
            ORDER BY wl.added_at ASC, wl.id ASC;
            """,
            params,
        ).fetchall()
        grouped: Dict[int, Dict[str, Any]] = {}
        for r in rows:
            e = grouped.get(r["media_item_id"])
            if e is None:
                e = grouped[r["media_item_id"]] = {
                    "media_item_id": r["media_item_id"],
                    "title": r["title"],
                    "release_year": r["release_year"],
                    "media_type": r["media_type"],
                    "poster_url": r["poster_url"],
                    "genres": r["genres"],
                    "runtime": r["runtime"],
                    "rating": r["rating"],
                    "overview": r["overview"],
                    "file_path": r["file_path"],
                    "added_by": [],
                    "both": False,
                    "first_added_at": r["added_at"],
                    "done_at": r["done_at"],
                    "done_reason": r["done_reason"],
                    "personal": r["owner_profile_id"] is not None,
                    "owner_profile_id": r["owner_profile_id"],
                    "_all_done": True,
                }
            e["added_by"].append(
                {"profile_id": r["profile_id"], "name": r["profile_name"], "added_at": r["added_at"]}
            )
            if r["owner_profile_id"] is None:
                e["personal"], e["owner_profile_id"] = False, None   # any shared row makes the title shared
            if r["added_at"] and (not e["first_added_at"] or r["added_at"] < e["first_added_at"]):
                e["first_added_at"] = r["added_at"]
            if r["done_at"] is None:
                e["_all_done"] = False
            elif e["done_at"] is None or r["done_at"] > e["done_at"]:
                e["done_at"], e["done_reason"] = r["done_at"], r["done_reason"]
        out = []
        for e in grouped.values():
            all_done = e.pop("_all_done")
            if not all_done:
                e["done_at"], e["done_reason"] = None, None
            elif not include_done:
                continue
            e["both"] = len({a["profile_id"] for a in e["added_by"]}) >= 2
            out.append(e)
        # Newest add first (latest added_at among the people on the title).
        out.sort(key=lambda e: (max(a["added_at"] or "" for a in e["added_by"]), e["media_item_id"]), reverse=True)
        return out

    def add_to_watch_later(
        self, media_item_id: int, profile_id: int, owner_profile_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Adds a title to the list for one person (reopens it if already done).

        Shared by default. `owner_profile_id` makes it "Just me": only that person sees it. Adding
        a title shared that the person had kept to themselves shares it; adding "Just me" what is
        already shared leaves it shared."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if not cursor.execute("SELECT 1 FROM media_items WHERE id = ?;", (media_item_id,)).fetchone():
                raise ValueError(f"Unknown media_item_id {media_item_id}")
            if not cursor.execute("SELECT 1 FROM profiles WHERE id = ?;", (profile_id,)).fetchone():
                raise ValueError(f"Unknown profile_id {profile_id}")
            if owner_profile_id is not None and owner_profile_id != profile_id:
                raise ValueError("A 'Just me' add belongs to the person who adds it")
            cursor.execute(
                """
                INSERT INTO watch_later (media_item_id, profile_id, owner_profile_id) VALUES (?, ?, ?)
                ON CONFLICT(media_item_id, profile_id) DO UPDATE SET
                    added_at = CASE WHEN watch_later.done_at IS NOT NULL THEN CURRENT_TIMESTAMP ELSE watch_later.added_at END,
                    done_at = NULL,
                    done_reason = NULL,
                    owner_profile_id = CASE WHEN excluded.owner_profile_id IS NULL THEN NULL
                                            ELSE watch_later.owner_profile_id END;
                """,
                (media_item_id, profile_id, owner_profile_id),
            )
            return self._watch_later_entry(
                cursor, media_item_id, [owner_profile_id] if owner_profile_id is not None else None
            )

    def remove_from_watch_later(self, media_item_id: int, profile_id: int) -> bool:
        """Deletes one person's row for a title; True when a row was removed."""
        with self.get_connection() as conn:
            cur = conn.execute(
                "DELETE FROM watch_later WHERE media_item_id = ? AND profile_id = ?;",
                (media_item_id, profile_id),
            )
            return cur.rowcount > 0

    def get_watch_later(
        self,
        include_done: bool = False,
        media_type: Optional[str] = None,
        added_by: Optional[int] = None,
        personal_of: Optional[List[int]] = None,
    ) -> List[Dict[str, Any]]:
        """Lists the shared list grouped by title, newest add first; done titles only if asked.
        "Just me" rows stay hidden unless `personal_of` names their owner."""
        clauses, params = [], []
        vis, vparams = self._wl_visible(personal_of)
        clauses.append(vis)
        params.extend(vparams)
        if media_type:
            clauses.append("m.media_type = ?")
            params.append(media_type)
        if added_by is not None:
            clauses.append(
                "wl.media_item_id IN (SELECT media_item_id FROM watch_later WHERE profile_id = ?)"
            )
            params.append(added_by)
        with self.get_connection() as conn:
            return self._watch_later_entries(
                conn.cursor(), " AND ".join(clauses), tuple(params), include_done
            )

    def get_watch_later_ids(
        self, media_item_ids: List[int], personal_of: Optional[List[int]] = None
    ) -> Dict[int, List[int]]:
        """Maps media_item_id -> profile_ids with an open (not done) row, in one query.
        "Just me" rows count only for the profiles in `personal_of`."""
        ids = [int(i) for i in media_item_ids if i is not None]
        if not ids:
            return {}
        out: Dict[int, List[int]] = {}
        vis, vparams = self._wl_visible(personal_of)
        with self.get_connection() as conn:
            for start in range(0, len(ids), 500):
                chunk = ids[start:start + 500]
                marks = ",".join("?" * len(chunk))
                for r in conn.execute(
                    f"SELECT wl.media_item_id, wl.profile_id FROM watch_later wl "
                    f"WHERE wl.done_at IS NULL AND {vis} AND wl.media_item_id IN ({marks}) ORDER BY wl.id;",
                    [*vparams, *chunk],
                ):
                    out.setdefault(r["media_item_id"], []).append(r["profile_id"])
        return out

    def remove_from_watchlist(self, media_item_id: int, profile_ids: List[int]) -> int:
        """Takes a title off the watchlist for the people at the screen: every shared row (the
        list is shared, so it leaves for both) and the "Just me" rows these profiles own. Other
        people's "Just me" rows are never touched. Returns rows removed."""
        ids = [int(i) for i in profile_ids]
        clause, params = "owner_profile_id IS NULL", []
        if ids:
            clause = f"(owner_profile_id IS NULL OR owner_profile_id IN ({','.join('?' * len(ids))}))"
            params = ids
        with self.get_connection() as conn:
            cur = conn.execute(
                f"DELETE FROM watch_later WHERE media_item_id = ? AND {clause};", (media_item_id, *params)
            )
            return cur.rowcount

    # --- Northern Lights picks for two (schema v14) --------------------------------

    def set_interest(self, media_item_id: int, profile_id: int, state: Optional[str]) -> Optional[str]:
        """Sets one person's yes / maybe / pass on a title; None clears it. Returns the new state."""
        if state is not None and state not in ("yes", "maybe", "pass"):
            raise ValueError("state must be yes, maybe or pass")
        with self.get_connection() as conn:
            if not conn.execute("SELECT 1 FROM media_items WHERE id = ?;", (media_item_id,)).fetchone():
                raise ValueError(f"Unknown media_item_id {media_item_id}")
            if not conn.execute("SELECT 1 FROM profiles WHERE id = ?;", (profile_id,)).fetchone():
                raise ValueError(f"Unknown profile_id {profile_id}")
            if state is None:
                conn.execute(
                    "DELETE FROM nl_interest WHERE media_item_id = ? AND profile_id = ?;", (media_item_id, profile_id)
                )
                return None
            conn.execute(
                """
                INSERT INTO nl_interest (media_item_id, profile_id, state) VALUES (?, ?, ?)
                ON CONFLICT(media_item_id, profile_id) DO UPDATE SET state = excluded.state,
                                                                   updated_at = CURRENT_TIMESTAMP;
                """,
                (media_item_id, profile_id, state),
            )
            return state

    def get_interest(self, media_item_ids: Optional[List[int]] = None) -> Dict[int, Dict[int, str]]:
        """{media_item_id: {profile_id: state}} for the given titles (all titles when None)."""
        out: Dict[int, Dict[int, str]] = {}
        with self.get_connection() as conn:
            if media_item_ids is None:
                for r in conn.execute("SELECT media_item_id, profile_id, state FROM nl_interest;").fetchall():
                    out.setdefault(r["media_item_id"], {})[r["profile_id"]] = r["state"]
                return out
            ids = [int(i) for i in media_item_ids if i is not None]
            for start in range(0, len(ids), 500):
                chunk = ids[start:start + 500]
                marks = ",".join("?" * len(chunk))
                for r in conn.execute(
                    f"SELECT media_item_id, profile_id, state FROM nl_interest WHERE media_item_id IN ({marks});", chunk
                ):
                    out.setdefault(r["media_item_id"], {})[r["profile_id"]] = r["state"]
        return out

    def get_items_by_ids(self, media_item_ids: List[int]) -> Dict[int, Dict[str, Any]]:
        """Catalogue rows (title, genres, rating, votes, runtime, poster ...) for the given ids."""
        ids = [int(i) for i in media_item_ids if i is not None]
        out: Dict[int, Dict[str, Any]] = {}
        with self.get_connection() as conn:
            for start in range(0, len(ids), 500):
                chunk = ids[start:start + 500]
                marks = ",".join("?" * len(chunk))
                for r in conn.execute(
                    f"SELECT id, media_type, title, release_year, genres, rating, vote_count, runtime, poster_url, overview "
                    f"FROM media_items WHERE id IN ({marks});", chunk
                ):
                    out[r["id"]] = dict(r)
        return out

    def get_seen_item_ids(self, profile_ids: List[int]) -> set:
        """Titles any of these profiles has marked seen (rated or not)."""
        ids = [int(i) for i in profile_ids]
        if not ids:
            return set()
        with self.get_connection() as conn:
            rows = conn.execute(
                f"SELECT DISTINCT media_item_id FROM watch_history "
                f"WHERE status = 'seen' AND profile_id IN ({','.join('?' * len(ids))});", ids
            ).fetchall()
        return {r["media_item_id"] for r in rows}

    def get_profile_ratings(self, profile_ids: List[int]) -> List[Dict[str, Any]]:
        """Every seen-and-rated title of these profiles: media_item_id, title, genres, profile_id,
        rating (the latest rating where one title was rated twice)."""
        ids = [int(i) for i in profile_ids]
        if not ids:
            return []
        with self.get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT wh.media_item_id, wh.profile_id, wh.user_rating AS rating, m.title, m.genres
                FROM watch_history wh JOIN media_items m ON m.id = wh.media_item_id
                WHERE wh.status = 'seen' AND wh.user_rating IS NOT NULL
                  AND wh.profile_id IN ({','.join('?' * len(ids))})
                ORDER BY wh.id;
                """, ids
            ).fetchall()
        latest: Dict[tuple, Dict[str, Any]] = {}
        for r in rows:
            latest[(r["media_item_id"], r["profile_id"])] = dict(r)
        return list(latest.values())

    def mark_watch_later_done(self, media_item_id: int, reason: str) -> int:
        """Marks every open row for a title done ('rated' | 'watched'); returns rows changed."""
        with self.get_connection() as conn:
            cur = conn.execute(
                "UPDATE watch_later SET done_at = CURRENT_TIMESTAMP, done_reason = ? "
                "WHERE media_item_id = ? AND done_at IS NULL;",
                (reason, media_item_id),
            )
            return cur.rowcount

    def undo_watch_later_done(self, media_item_id: int) -> int:
        """Reopens a done title (clears done_at/done_reason); returns rows changed."""
        with self.get_connection() as conn:
            cur = conn.execute(
                "UPDATE watch_later SET done_at = NULL, done_reason = NULL "
                "WHERE media_item_id = ? AND done_at IS NOT NULL;",
                (media_item_id,),
            )
            return cur.rowcount

    def _watch_later_done_safe(self, media_item_id: int, reason: str) -> None:
        """Marks a title done on the Watch later list; never lets a failure break rating or watching."""
        try:
            self.mark_watch_later_done(media_item_id, reason)
        except Exception as e:  # noqa: BLE001
            print(f"[WatchLater] could not mark {media_item_id} done: {e}", flush=True)

    def rate_item(
        self,
        media_item_id: int,
        profile_id: int = 1,
        user_rating: Optional[float] = None,
        status: str = "seen",
        notes: Optional[str] = None,
        verdict: Optional[str] = None,
        did_not_finish: bool = False,
        worked: Optional[List[str]] = None,
        didnt: Optional[List[str]] = None,
        vibe_check: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Upserts one profile's verdict on one item (the Rate tab's write path).

        `status='not_seen'` means "we haven't watched this" — a real answer that
        is deliberately NOT stored as a rating. It contributes nothing to taste
        scoring (absence of data isn't dislike); it is recorded so the
        recommender can tell "he saw it, you haven't" apart from "neither of us
        has seen it". Any rating passed with status='not_seen' is dropped,
        because the two states are mutually exclusive.

        A `verdict` (rewatch / loved / liked / meh / not_for_me) is the new way
        to rate: it fills `rating_details` (with the chips and vibe-check
        answers) and writes a matching legacy 1-10 number to
        `watch_history.user_rating` for older readers. A bare `user_rating`
        still works but removes any verdict, which would no longer match it.
        The state being replaced is copied to `rating_history`, and a re-rate
        that sends no notes keeps the existing ones.
        """
        from services import verdicts as vd

        if status not in ("seen", "not_seen"):
            raise ValueError(f"invalid status: {status!r}")
        verdict = vd.validate_verdict(verdict)
        if status == "not_seen":
            user_rating = None
            verdict = None
        elif verdict is not None:
            user_rating = vd.legacy_rating(verdict, bool(did_not_finish))
        elif user_rating is not None:
            user_rating = max(1.0, min(10.0, float(user_rating)))

        with self.get_connection() as conn:
            cursor = conn.cursor()
            if not cursor.execute("SELECT 1 FROM media_items WHERE id = ?;", (media_item_id,)).fetchone():
                raise ValueError(f"unknown media_item_id: {media_item_id}")
            if not cursor.execute("SELECT 1 FROM profiles WHERE id = ?;", (profile_id,)).fetchone():
                raise ValueError(f"unknown profile_id: {profile_id}")

            # Keep what is being replaced, unless this save only adds chips,
            # answers or a note (the Rate card autosaves those while it is open).
            prev = cursor.execute(
                """
                SELECT wh.user_rating, rd.verdict, rd.did_not_finish, rd.taste_score, rd.inferred, wh.notes
                FROM watch_history wh
                LEFT JOIN rating_details rd
                       ON rd.profile_id = wh.profile_id AND rd.media_item_id = wh.media_item_id
                WHERE wh.profile_id = ? AND wh.media_item_id = ? AND wh.status = 'seen';
                """,
                (profile_id, media_item_id),
            ).fetchone()
            unchanged = (
                prev is not None and verdict is not None and prev["verdict"] == verdict
                and bool(prev["did_not_finish"]) == bool(did_not_finish) and not prev["inferred"]
            )
            if prev is not None and not unchanged:
                cursor.execute(
                    """
                    INSERT INTO rating_history (profile_id, media_item_id, user_rating, verdict, taste_score, inferred, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?);
                    """,
                    (profile_id, media_item_id, prev["user_rating"], prev["verdict"],
                     prev["taste_score"], prev["inferred"], prev["notes"]),
                )

            cursor.execute(
                """
                INSERT INTO watch_history (media_item_id, profile_id, user_rating, status, notes)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(profile_id, media_item_id) DO UPDATE SET
                    user_rating = excluded.user_rating,
                    status      = excluded.status,
                    notes       = COALESCE(excluded.notes, watch_history.notes),
                    watched_at  = CURRENT_TIMESTAMP;
                """,
                (media_item_id, profile_id, user_rating, status, notes),
            )

            if verdict is not None:
                # None = "not sent": keep whatever chips / answers are stored.
                worked_list = vd.clean_chips(worked) if worked is not None else None
                worked_json = json.dumps(worked_list) if worked_list is not None else None
                didnt_json = (
                    json.dumps([c for c in vd.clean_chips(didnt) if c not in (worked_list or [])])
                    if didnt is not None else None
                )
                score = vd.taste_score(verdict, bool(did_not_finish))
                cursor.execute(
                    """
                    INSERT INTO rating_details
                        (profile_id, media_item_id, verdict, did_not_finish, worked, didnt, vibe_check,
                         taste_score, inferred, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, CURRENT_TIMESTAMP)
                    ON CONFLICT(profile_id, media_item_id) DO UPDATE SET
                        verdict        = excluded.verdict,
                        did_not_finish = excluded.did_not_finish,
                        worked         = COALESCE(excluded.worked, rating_details.worked),
                        didnt          = COALESCE(excluded.didnt, rating_details.didnt),
                        vibe_check     = COALESCE(excluded.vibe_check, rating_details.vibe_check),
                        taste_score    = excluded.taste_score,
                        inferred       = 0,
                        inferred_source = NULL,
                        updated_at     = CURRENT_TIMESTAMP;
                    """,
                    (
                        profile_id, media_item_id, verdict, 1 if did_not_finish else 0,
                        worked_json, didnt_json,
                        json.dumps(vd.clean_vibe_check(vibe_check)) if vibe_check is not None else None,
                        score,
                    ),
                )
                # A confirmed show verdict always wins; this only records where
                # the person stopped, if they rated seasons too.
                self._sync_show_from_seasons(cursor, profile_id, media_item_id)
            else:
                cursor.execute(
                    "DELETE FROM rating_details WHERE profile_id = ? AND media_item_id = ?;",
                    (profile_id, media_item_id),
                )

        if status == "seen":
            self._watch_later_done_safe(media_item_id, "rated")

        # Read the summary AFTER the write commits. get_rating_summary() opens
        # its own connection, and SQLite won't show it this connection's
        # uncommitted INSERT — calling it inside the `with` block returns the
        # pre-write state (the new verdict silently missing).
        return self.get_rating_summary(media_item_id)


    # --- Per-season TV verdicts (schema v10) -----------------------------------

    def get_season_count(self, media_item_id: int) -> Optional[int]:
        """Seasons a show has: TMDB's number when backfilled, else the highest
        season_number among its files. None when neither is known."""
        with self.get_connection() as conn:
            row = conn.execute(
                """
                SELECT mi.media_type, mi.season_count,
                       (SELECT MAX(season_number) FROM media_sources WHERE media_item_id = mi.id) AS from_files
                FROM media_items mi WHERE mi.id = ?;
                """,
                (media_item_id,),
            ).fetchone()
        if not row or row["media_type"] != "tv":
            return None
        for value in (row["season_count"], row["from_files"]):
            if value and int(value) > 0:
                return int(value)
        return None

    def get_season_ratings(self, media_item_id: int, profile_id: int) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            rows = conn.execute(
                "SELECT season_number, verdict, did_not_finish, taste_score, updated_at FROM season_ratings "
                "WHERE profile_id = ? AND media_item_id = ? ORDER BY season_number;",
                (profile_id, media_item_id),
            ).fetchall()
        out = [dict(r) for r in rows]
        for r in out:
            r["did_not_finish"] = bool(r["did_not_finish"])
        return out

    def get_season_details(self, media_item_id: int, profile_id: int) -> Dict[str, Any]:
        """What the Rate card's Seasons row needs: how many seasons to show, this
        person's season verdicts and where they stopped."""
        from services import verdicts as vd

        ratings = self.get_season_ratings(media_item_id, profile_id)
        count = self.get_season_count(media_item_id)
        rated_max = max((r["season_number"] for r in ratings), default=0)
        if rated_max and (count is None or rated_max > count):
            count = rated_max
        return {"count": count, "ratings": ratings, "dropped_at_season": vd.dropped_at_season(ratings)}

    def rate_season(
        self,
        media_item_id: int,
        profile_id: int,
        season_number: int,
        verdict: Optional[str] = None,
        did_not_finish: bool = False,
    ) -> Dict[str, Any]:
        """Upserts one season verdict; no verdict and no didn't-finish clears it.

        The show-level row is then brought in line: a confirmed show verdict is
        never touched (only `dropped_at_season` is updated); otherwise the show
        gets a verdict derived from its seasons, marked inferred with
        inferred_source = 'seasons', exactly where the readers already look.
        """
        from services import verdicts as vd

        verdict = vd.validate_verdict(verdict)
        season_number = int(season_number)
        if season_number < 1:
            raise ValueError(f"invalid season_number: {season_number}")
        with self.get_connection() as conn:
            cursor = conn.cursor()
            item = cursor.execute("SELECT media_type FROM media_items WHERE id = ?;", (media_item_id,)).fetchone()
            if not item:
                raise ValueError(f"unknown media_item_id: {media_item_id}")
            if item["media_type"] != "tv":
                raise ValueError("season verdicts are for TV shows only")
            if not cursor.execute("SELECT 1 FROM profiles WHERE id = ?;", (profile_id,)).fetchone():
                raise ValueError(f"unknown profile_id: {profile_id}")

            score = vd.season_score(verdict, bool(did_not_finish))
            if score is None:
                cursor.execute(
                    "DELETE FROM season_ratings WHERE profile_id = ? AND media_item_id = ? AND season_number = ?;",
                    (profile_id, media_item_id, season_number),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO season_ratings
                        (profile_id, media_item_id, season_number, verdict, did_not_finish, taste_score, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(profile_id, media_item_id, season_number) DO UPDATE SET
                        verdict        = excluded.verdict,
                        did_not_finish = excluded.did_not_finish,
                        taste_score    = excluded.taste_score,
                        updated_at     = CURRENT_TIMESTAMP;
                    """,
                    (profile_id, media_item_id, season_number, verdict, 1 if did_not_finish else 0, score),
                )
            self._sync_show_from_seasons(cursor, profile_id, media_item_id)

        if score is not None:
            self._watch_later_done_safe(media_item_id, "rated")

        return {
            "media_item_id": media_item_id,
            "profile_id": profile_id,
            "seasons": self.get_season_details(media_item_id, profile_id),
            "details": self.get_rating_details(media_item_id, profile_id),
            "summary": self.get_rating_summary(media_item_id),
        }

    def _sync_show_from_seasons(self, cursor: sqlite3.Cursor, profile_id: int, media_item_id: int) -> None:
        """Keeps the show-level row consistent with its season verdicts."""
        from services import verdicts as vd

        seasons = [
            dict(r) for r in cursor.execute(
                "SELECT season_number, verdict, did_not_finish, taste_score FROM season_ratings "
                "WHERE profile_id = ? AND media_item_id = ?;",
                (profile_id, media_item_id),
            ).fetchall()
        ]
        derived = vd.derive_show_score(seasons)
        dropped = vd.dropped_at_season(seasons)
        current = cursor.execute(
            "SELECT inferred, inferred_source FROM rating_details WHERE profile_id = ? AND media_item_id = ?;",
            (profile_id, media_item_id),
        ).fetchone()

        if current is not None and not current["inferred"]:
            # A confirmed show verdict always wins.
            cursor.execute(
                "UPDATE rating_details SET dropped_at_season = ? WHERE profile_id = ? AND media_item_id = ?;",
                (dropped, profile_id, media_item_id),
            )
            return

        if derived is None:
            # No season verdicts left. Remove a show verdict that came only
            # from them; the earlier state is in rating_history.
            if current is not None and current["inferred_source"] == "seasons":
                self._copy_to_rating_history(cursor, profile_id, media_item_id)
                cursor.execute(
                    "DELETE FROM rating_details WHERE profile_id = ? AND media_item_id = ?;",
                    (profile_id, media_item_id),
                )
                cursor.execute(
                    "DELETE FROM watch_history WHERE profile_id = ? AND media_item_id = ? AND status = 'seen';",
                    (profile_id, media_item_id),
                )
            return

        # Seasons replace a verdict guessed from an old 1-10 number: they are
        # what the person actually said. The old state goes to rating_history.
        if current is not None and current["inferred_source"] != "seasons":
            self._copy_to_rating_history(cursor, profile_id, media_item_id)
        legacy = vd.legacy_rating(derived["verdict"])
        cursor.execute(
            """
            INSERT INTO watch_history (media_item_id, profile_id, user_rating, status)
            VALUES (?, ?, ?, 'seen')
            ON CONFLICT(profile_id, media_item_id) DO UPDATE SET
                user_rating = excluded.user_rating,
                status      = 'seen';
            """,
            (media_item_id, profile_id, legacy),
        )
        cursor.execute(
            """
            INSERT INTO rating_details
                (profile_id, media_item_id, verdict, did_not_finish, taste_score, inferred,
                 inferred_source, dropped_at_season, updated_at)
            VALUES (?, ?, ?, 0, ?, 1, 'seasons', ?, CURRENT_TIMESTAMP)
            ON CONFLICT(profile_id, media_item_id) DO UPDATE SET
                verdict           = excluded.verdict,
                did_not_finish    = 0,
                taste_score       = excluded.taste_score,
                inferred          = 1,
                inferred_source   = 'seasons',
                dropped_at_season = excluded.dropped_at_season,
                updated_at        = CURRENT_TIMESTAMP;
            """,
            (profile_id, media_item_id, derived["verdict"], derived["taste_score"], dropped),
        )

    def _copy_to_rating_history(self, cursor: sqlite3.Cursor, profile_id: int, media_item_id: int) -> None:
        cursor.execute(
            """
            INSERT INTO rating_history (profile_id, media_item_id, user_rating, verdict, taste_score, inferred, notes)
            SELECT wh.profile_id, wh.media_item_id, wh.user_rating, rd.verdict, rd.taste_score, rd.inferred, wh.notes
            FROM watch_history wh
            LEFT JOIN rating_details rd
                   ON rd.profile_id = wh.profile_id AND rd.media_item_id = wh.media_item_id
            WHERE wh.profile_id = ? AND wh.media_item_id = ? AND wh.status = 'seen';
            """,
            (profile_id, media_item_id),
        )

    def get_dropped_shows(self, profile_id: int) -> List[Dict[str, Any]]:
        """Shows this person stopped partway: the season they dropped and the
        show's score. Recorded for later use in ranking; nothing reads it yet."""
        with self.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT mi.id AS media_item_id, mi.title, MIN(sr.season_number) AS dropped_at_season,
                       rd.taste_score, rd.inferred
                FROM season_ratings sr
                JOIN media_items mi ON mi.id = sr.media_item_id
                LEFT JOIN rating_details rd
                       ON rd.profile_id = sr.profile_id AND rd.media_item_id = sr.media_item_id
                WHERE sr.profile_id = ? AND sr.did_not_finish = 1
                GROUP BY mi.id
                ORDER BY mi.title;
                """,
                (profile_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_tv_missing_season_count(self) -> List[Dict[str, Any]]:
        """TV shows not yet looked up on TMDB for their season count, most-voted first."""
        with self.get_connection() as conn:
            rows = conn.execute(
                "SELECT id, tmdb_id, title FROM media_items "
                "WHERE media_type = 'tv' AND tmdb_id IS NOT NULL "
                "AND COALESCE(season_count_checked, 0) = 0 "
                "ORDER BY COALESCE(vote_count, 0) DESC;"
            ).fetchall()
        return [dict(r) for r in rows]

    def set_season_count(self, media_item_id: int, count: Optional[int], checked: int = 1) -> None:
        with self.get_connection() as conn:
            conn.execute(
                "UPDATE media_items SET season_count = COALESCE(?, season_count), season_count_checked = ? "
                "WHERE id = ?;",
                (count if count and count > 0 else None, checked, media_item_id),
            )

    def fill_season_counts_from_files(self) -> int:
        """Shows with no season count yet get the highest season_number among
        their files. Doesn't mark them checked, so TMDB still gets asked."""
        with self.get_connection() as conn:
            cur = conn.execute(
                """
                UPDATE media_items SET season_count = (
                    SELECT MAX(season_number) FROM media_sources WHERE media_item_id = media_items.id
                )
                WHERE media_type = 'tv' AND season_count IS NULL
                  AND EXISTS (SELECT 1 FROM media_sources
                              WHERE media_item_id = media_items.id AND season_number > 0);
                """
            )
            return cur.rowcount

    def get_rating_details(self, media_item_id: int, profile_id: int) -> Optional[Dict[str, Any]]:
        """One person's verdict, chips and vibe-check answers for a title, or None."""
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM rating_details WHERE profile_id = ? AND media_item_id = ?;",
                (profile_id, media_item_id),
            ).fetchone()
        if not row:
            return None
        out = dict(row)
        for key in ("worked", "didnt"):
            out[key] = json.loads(out[key]) if out.get(key) else []
        out["vibe_check"] = json.loads(out["vibe_check"]) if out.get("vibe_check") else {}
        out["did_not_finish"] = bool(out["did_not_finish"])
        out["inferred"] = bool(out["inferred"])
        from services import pairs as pr
        out["final_taste_score"] = pr.apply_nudge(out["taste_score"], out.get("pair_nudge") or 0.0)
        return out

    # --- "This or that" pairs (services/pairs.py) ---------------------------

    def get_pair_round(self, profile_id: int, media_type: str = "movie", limit: int = 10,
                       seed: Optional[int] = None) -> Dict[str, Any]:
        """Up to `limit` informative pairs for one person, plus how many good
        pairs exist in total (so the card can say "3 of 10")."""
        from services import pairs as pr

        with self.get_connection() as conn:
            films = [dict(r) for r in conn.execute(
                """
                SELECT mi.id, mi.title, mi.release_year, mi.media_type, mi.genres, mi.poster_url, mi.vibe_cluster_id,
                       rd.verdict, rd.taste_score,
                       (SELECT COUNT(*) FROM rating_pairs rp
                         WHERE rp.profile_id = rd.profile_id AND rp.same = 0
                           AND (rp.winner_id = mi.id OR rp.loser_id = mi.id)) AS comparisons
                FROM rating_details rd
                JOIN media_items mi ON mi.id = rd.media_item_id
                JOIN watch_history wh ON wh.profile_id = rd.profile_id AND wh.media_item_id = rd.media_item_id
                WHERE rd.profile_id = ? AND mi.media_type = ? AND wh.status = 'seen';
                """,
                (profile_id, media_type),
            ).fetchall()]
            answered = [(r[0], r[1]) for r in conn.execute(
                "SELECT winner_id, loser_id FROM rating_pairs WHERE profile_id = ?;", (profile_id,)
            ).fetchall()]
        rng = random.Random(seed) if seed is not None else random.Random()
        picked = pr.pick_pairs(films, answered, limit=limit, rng=rng)
        return {"profile_id": profile_id, "media_type": media_type, "pairs": picked,
                "answered": len(answered)}

    def record_pair_answer(self, profile_id: int, a_id: int, b_id: int, choice: str) -> Dict[str, Any]:
        """Stores one "This or that" answer (`choice` = 'a' | 'b' | 'same'),
        then refits this person's pair scores and nudges. Answering a pair
        twice keeps the first answer."""
        from services import pairs as pr

        if choice not in ("a", "b", "same"):
            raise ValueError("choice must be 'a', 'b' or 'same'")
        if a_id == b_id:
            raise ValueError("a pair needs two different films")
        with self.get_connection() as conn:
            rows = {r["id"]: dict(r) for r in conn.execute(
                """
                SELECT mi.id, mi.genres, mi.facets FROM media_items mi
                JOIN rating_details rd ON rd.media_item_id = mi.id AND rd.profile_id = ?
                WHERE mi.id IN (?, ?);
                """,
                (profile_id, a_id, b_id),
            ).fetchall()}
            if len(rows) != 2:
                raise ValueError("both films must be rated by this profile")
            genre = pr.shared_genre(rows[a_id], rows[b_id])
            if genre is None:
                raise ValueError("the films share no genre")
            dup = conn.execute(
                "SELECT 1 FROM rating_pairs WHERE profile_id = ? AND MIN(winner_id, loser_id) = ? AND MAX(winner_id, loser_id) = ?;",
                (profile_id, min(a_id, b_id), max(a_id, b_id)),
            ).fetchone()
            if dup:
                return {"saved": False, "reason": "already answered"}

            def facets(i):
                try:
                    return json.loads(rows[i]["facets"]) if rows[i]["facets"] else {}
                except (TypeError, ValueError):
                    return {}

            same = 1 if choice == "same" else 0
            winner, loser = (b_id, a_id) if choice == "b" else (a_id, b_id)
            diffs = pr.dial_differences(facets(winner), facets(loser))
            conn.execute(
                "INSERT INTO rating_pairs (profile_id, winner_id, loser_id, same, shared_genre, dial_diffs) "
                "VALUES (?, ?, ?, ?, ?, ?);",
                (profile_id, winner, loser, same, genre, json.dumps(diffs)),
            )
        self.refresh_pair_scores(profile_id)
        return {"saved": True, "winner_id": None if same else winner, "loser_id": None if same else loser,
                "same": bool(same), "shared_genre": genre, "dial_diffs": diffs}

    def refresh_pair_scores(self, profile_id: int) -> Dict[int, float]:
        """Refits Bradley-Terry strengths from this person's decisive answers
        and stores `pair_score` / `pair_nudge` on their rating_details rows.
        `taste_score` (the verdict score) is never touched."""
        from services import pairs as pr

        with self.get_connection() as conn:
            answers = [(r[0], r[1]) for r in conn.execute(
                "SELECT winner_id, loser_id FROM rating_pairs WHERE profile_id = ? AND same = 0;", (profile_id,)
            ).fetchall()]
            strengths = pr.pair_scores(answers)
            conn.execute(
                "UPDATE rating_details SET pair_score = NULL, pair_nudge = NULL WHERE profile_id = ?;", (profile_id,)
            )
            for item_id, strength in strengths.items():
                conn.execute(
                    "UPDATE rating_details SET pair_score = ?, pair_nudge = ? WHERE profile_id = ? AND media_item_id = ?;",
                    (strength, pr.nudge_for(strength), profile_id, item_id),
                )
        return strengths

    def get_final_taste_scores(self, profile_id: int) -> Dict[int, float]:
        """{media_item_id: verdict score + pair nudge, kept inside its verdict}."""
        from services import pairs as pr

        with self.get_connection() as conn:
            rows = conn.execute(
                "SELECT media_item_id, taste_score, pair_nudge FROM rating_details WHERE profile_id = ?;",
                (profile_id,),
            ).fetchall()
        return {r["media_item_id"]: pr.apply_nudge(r["taste_score"], r["pair_nudge"] or 0.0) for r in rows}

    def get_vibe_check_questions(self, media_item_id: int) -> Dict[str, Any]:
        """The vibe-check questions to show for one film (3 up front, the rest
        under "More"), chosen from its mood dials and tags."""
        from services.verdicts import pick_vibe_questions

        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT facets, vibe_tags FROM media_items WHERE id = ?;", (media_item_id,)
            ).fetchone()
        facets = tags = None
        if row:
            try:
                facets = json.loads(row["facets"]) if row["facets"] else None
                tags = json.loads(row["vibe_tags"]) if row["vibe_tags"] else None
            except (TypeError, ValueError):
                pass
        return pick_vibe_questions(facets, tags)

    def get_inferred_rating_queue(self, profile_id: int, media_type: str = "movie", limit: int = 100) -> List[Dict[str, Any]]:
        """Films this person rated before verdicts existed and hasn't confirmed:
        the "Sort out my 7s" round. 7s first, then 8s, then the rest."""
        with self.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT mi.*, wh.user_rating AS old_rating,
                       EXISTS(SELECT 1 FROM media_sources ms WHERE ms.media_item_id = mi.id) AS in_library
                FROM rating_details rd
                JOIN watch_history wh ON wh.profile_id = rd.profile_id AND wh.media_item_id = rd.media_item_id
                JOIN media_items mi ON mi.id = rd.media_item_id
                WHERE rd.profile_id = ? AND rd.inferred = 1 AND COALESCE(rd.inferred_source, 'rating') = 'rating'
                  AND wh.status = 'seen' AND mi.media_type = ?
                ORDER BY CASE CAST(wh.user_rating AS INTEGER) WHEN 7 THEN 0 WHEN 8 THEN 1 ELSE 2 END,
                         mi.title
                LIMIT ?;
                """,
                (profile_id, media_type, limit),
            ).fetchall()
        return [dict(r) for r in rows]

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
                    SELECT wh.profile_id, p.name, wh.user_rating, wh.status, wh.notes, wh.watched_at,
                           rd.verdict, rd.taste_score, rd.inferred, rd.did_not_finish,
                           rd.inferred_source, rd.dropped_at_season
                    FROM watch_history wh
                    JOIN profiles p ON p.id = wh.profile_id
                    LEFT JOIN rating_details rd
                           ON rd.profile_id = wh.profile_id AND rd.media_item_id = wh.media_item_id
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
        genres: Optional[List[str]] = None,
        seed: Optional[int] = None,
        balanced: bool = False,
    ) -> List[Dict[str, Any]]:
        """Items this profile still needs to rate, highest-value first.

        Each profile rates independently, so this is a to-do list *for the
        active profile* — not a list of their existing ratings.

        Priority depends on WHO is rating:
          - For profile 1: films profile 2 already rated lead (the co-view gap —
            "he loved it, have you?"), then in-library films, then quality.
          - For profile 2: in-library films lead (rate what's actually on disk),
            then films profile 1 rated, then quality.
        The old fixed `in_library, other_profile_count` order pushed profile 1's
        own ratings to the top of profile 2's queue, which read as "here are my
        opinions, rate them".

        Genre balance: the pure quality cut is Drama-dominated (~23% of the top
        250), which made the queue feel like one serious register. `genres`
        filters to a slice (the UI's genre chips); when unset, a seeded rotation
        is applied so repeated loads surface different films instead of the same
        canonical top-rated list.

        `balanced=True` is the mode for building a taste profile quickly. It
        walks the genre wheel so each genre is exhausted before any repeats —
        20 ratings then covers ~20 genres instead of ~3, which stops the
        profile from coming out Drama-shaped regardless of what you actually
        like. Genre is matched on ANY of a film's genres (not the primary one,
        since TMDB lists "Drama" first on 2,368 items and that would make the
        wheel useless).
        """
        quality = (
            "(COALESCE(mi.vote_count, 0) * 1.0 / (COALESCE(mi.vote_count, 0) + 1000.0)) * mi.rating"
            " + (1000.0 / (COALESCE(mi.vote_count, 0) + 1000.0)) * 6.5"
        )
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # Placeholder order must follow the SQL text exactly:
            #   1. other_profile_count != ?   (in the SELECT)
            #   2. media_type = ?            (in the WHERE)
            #   3. genre LIKE ?              (appended into the WHERE)
            #   4. profile_id = ?            (NOT EXISTS, last)
            params: List[Any] = [profile_id, media_type]

            genre_clauses = []
            for g in genres or []:
                needle = (g or "").strip().lower().replace(" ", "")
                if needle:
                    genre_clauses.append(
                        "',' || REPLACE(LOWER(COALESCE(mi.genres, '')), ' ', '') || ',' LIKE ?"
                    )
                    params.append(f"%,{needle},%")
            genre_sql = (" AND (" + " OR ".join(genre_clauses) + ")") if genre_clauses else ""

            params.append(profile_id)

            # Priority expression depends on the viewer (see docstring).
            if profile_id == 1:
                order_sql = "other_profile_count DESC, in_library DESC"
            else:
                order_sql = "in_library DESC, other_profile_count DESC"

            # Over-fetch so the rotation / balancing below has material to work
            # with. Balanced mode needs far more: it walks the whole genre wheel.
            pool = max(limit * 12, 600) if balanced else max(limit * 6, 150)
            cursor.execute(
                f"""
                SELECT mi.*, {quality} AS quality_score,
                       EXISTS(SELECT 1 FROM media_sources ms
                              WHERE ms.media_item_id = mi.id) AS in_library,
                       (SELECT COUNT(*) FROM watch_history w2
                         WHERE w2.media_item_id = mi.id AND w2.profile_id != ?) AS other_profile_count
                FROM media_items mi
                WHERE mi.media_type = ?
                  AND mi.rating >= 5.0{genre_sql}
                  AND NOT EXISTS (
                        SELECT 1 FROM watch_history w
                        WHERE w.media_item_id = mi.id AND w.profile_id = ?
                  )
                ORDER BY {order_sql}, quality_score DESC, mi.vote_count DESC
                LIMIT ?;
                """,
                (*params, pool),
            )
            rows = [dict(r) for r in cursor.fetchall()]

        # Seeded rotation. Without this the queue is identical on every load, so
        # skipping just walks the same canonical list. A per-(profile, 10-min
        # bucket) seed keeps pagination coherent within a session while still
        # changing what gets suggested over time.
        if seed is None:
            seed = int(time.time() // 600)
        rng = random.Random(f"{profile_id}:{media_type}:{seed}")

        if balanced:
            # Walk the genre wheel: take one film from each genre, then a second
            # from each, and so on. A film counts for every genre it carries, so
            # "Se7en" (Crime, Mystery, Thriller) can serve any of the three —
            # that's what stops this collapsing back into Drama-only.
            #
            # Ranked by the per-viewer priority first so "rate what he liked"
            # still wins inside each genre, then by quality.
            def _balanced_rank(item: Dict[str, Any]) -> Tuple[int, float]:
                if profile_id == 1:
                    key = (int(item.get("other_profile_count") or 0),
                           int(item.get("in_library") or 0))
                else:
                    key = (int(item.get("in_library") or 0),
                           int(item.get("other_profile_count") or 0))
                return (key[0] + key[1], float(item.get("quality_score") or 0.0))

            ordered = sorted(rows, key=_balanced_rank, reverse=True)

            # Pass 1 seeds the wheel with the strongest pick per genre.
            wheel: List[str] = []
            per_genre: Dict[str, List[Dict[str, Any]]] = {}
            for item in ordered:
                tags = [g.strip() for g in (item.get("genres") or "").split(",") if g.strip()]
                if not tags:
                    tags = ["Other"]
                for tag in tags:
                    if tag not in per_genre:
                        per_genre[tag] = []
                        wheel.append(tag)
                    per_genre[tag].append(item)

            # Round-robin the wheel, skipping genres already spent, until we have
            # enough items or run dry.
            result: List[Dict[str, Any]] = []
            seen: set = set()
            depth = 0
            while len(result) < limit:
                added = False
                for tag in wheel:
                    if len(result) >= limit:
                        break
                    bucket = per_genre.get(tag) or []
                    while depth < len(bucket):
                        item = bucket[depth]
                        depth += 1
                        if item["id"] not in seen:
                            seen.add(item["id"])
                            result.append(item)
                            added = True
                            break
                if not added:
                    break
            return result[:limit]

        # Genre balance. The pure quality cut is Drama-dominated (~23% of the top
        # 250) because TMDB lists "Drama" first on 2,368 films, so bucketing by
        # the *primary* genre just produces one giant Drama bucket. Instead each
        # film is filed under a pseudo-randomly chosen one of ITS genres, then
        # buckets are round-robined — so a Comedy that happens to lead with Drama
        # still surfaces in the comedy bucket some of the time.
        by_genre: Dict[str, List[Dict[str, Any]]] = {}
        for item in rows:
            genres = [g.strip() for g in (item.get("genres") or "").split(",") if g.strip()]
            if not genres:
                genres = ["Other"]
            bucket = genres[rng.randrange(len(genres))]
            by_genre.setdefault(bucket, []).append(item)

        interleaved: List[Dict[str, Any]] = []
        genre_keys = sorted(by_genre)
        rng.shuffle(genre_keys)
        while genre_keys:
            for key in list(genre_keys):
                interleaved.append(by_genre[key].pop(0))
                if not by_genre[key]:
                    genre_keys.remove(key)

        # Rotate the head so consecutive loads differ, then restore the per-viewer
        # priority with a stable sort. Sorting on priority only (never quality)
        # keeps the genre round-robin intact inside each priority group.
        if interleaved:
            offset = rng.randrange(len(interleaved))
            interleaved = interleaved[offset:] + interleaved[:offset]

        def _priority(item: Dict[str, Any]) -> Tuple[int, int]:
            if profile_id == 1:
                return (int(item.get("other_profile_count") or 0), int(item.get("in_library") or 0))
            return (int(item.get("in_library") or 0), int(item.get("other_profile_count") or 0))

        # Stable sort: keeps the genre round-robin order *within* each priority
        # group, so balancing survives while priority still leads.
        interleaved.sort(key=_priority, reverse=True)
        return interleaved[:limit]

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

    def get_profile_taste_profile(self, profile_id: int, min_rating: float = 7.0) -> Dict[str, Any]:
        """One profile's taste: genre/keyword weights from THEIR high ratings.

        Separate from `get_user_taste_profile` (legacy, profile-agnostic) so the
        two people keep independent tastes and are blended at scoring time —
        pre-averaging them would split the difference into a lukewarm nothing.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT mi.genres, mi.tier2_keywords, wh.user_rating, rd.taste_score
                FROM watch_history wh
                JOIN media_items mi ON wh.media_item_id = mi.id
                LEFT JOIN rating_details rd
                       ON rd.profile_id = wh.profile_id AND rd.media_item_id = wh.media_item_id
                WHERE wh.profile_id = ? AND wh.status = 'seen'
                  AND (rd.taste_score > 0 OR (rd.taste_score IS NULL AND wh.user_rating >= ?));
                """,
                (profile_id, min_rating),
            )
            rows = cursor.fetchall()

        genre_scores: Dict[str, float] = {}
        keyword_scores: Dict[str, float] = {}
        for row in rows:
            weight = self._taste_weight(row)
            for raw in (row["genres"] or "").split(","):
                key = raw.strip().lower()
                if key:
                    genre_scores[key] = genre_scores.get(key, 0.0) + weight
            for raw in (row["tier2_keywords"] or "").split(","):
                key = raw.strip().lower()
                if key:
                    keyword_scores[key] = keyword_scores.get(key, 0.0) + weight

        return {
            "preferred_genres": genre_scores,
            "preferred_keywords": keyword_scores,
            "rating_count": len(rows),
        }

    def get_all_profile_tastes(self, min_rating: float = 7.0) -> Dict[int, Dict[str, Any]]:
        """Every profile's taste + rating stats, keyed by profile id.

        `mean_rating` / `rating_count` drive the shrinkage weights: a profile
        with 3 ratings should not outvote one with 200.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            profiles = cursor.execute("SELECT id, name FROM profiles ORDER BY id;").fetchall()
            out: Dict[int, Dict[str, Any]] = {}
            for p in profiles:
                pid = p["id"]
                stats = cursor.execute(
                    "SELECT COUNT(*) AS n, COALESCE(AVG(user_rating), 0.0) AS m "
                    "FROM watch_history WHERE profile_id = ? AND status = 'seen' "
                    "AND user_rating IS NOT NULL;",
                    (pid,),
                ).fetchone()
                taste = self.get_profile_taste_profile(pid, min_rating=min_rating)
                taste["name"] = p["name"]
                taste["rating_count"] = int(stats["n"])
                taste["mean_rating"] = float(stats["m"])
                # Shrinkage weight: a new rater counts, but less than an
                # established one. k=10 matches the score-shrinkage constant.
                taste["weight"] = int(stats["n"]) / (int(stats["n"]) + 10.0)
                out[pid] = taste
            return out

    def get_item_co_view(
        self, item_ids: List[int], viewer_profile_id: Optional[int] = None
    ) -> Dict[int, Dict[str, Any]]:
        """Co-view verdicts for many items in one query (avoids N+1).

        Tiers are **viewer-relative** when `viewer_profile_id` is given, because
        "worth recommending" depends on who is asking:

          A - the viewer liked it AND the other person liked it too
          B - the OTHER person liked it and the viewer hasn't seen it
              ("what would he recommend to me?") — only if the viewer is
              actually unexposed; if the viewer already rated it, recommending
              it back is pointless
          C - everything else (exploration, or already seen by the viewer)

        Without a viewer, falls back to the symmetric tier used by the rating
        UI (where both people are on the same page).
        """
        if not item_ids:
            return {}
        placeholders = ",".join("?" * len(item_ids))
        with self.get_connection() as conn:
            cursor = conn.cursor()
            rows = cursor.execute(
                f"""
                SELECT media_item_id, profile_id, user_rating, status
                FROM watch_history
                WHERE media_item_id IN ({placeholders})
                """,
                item_ids,
            ).fetchall()

        by_item: Dict[int, List[Dict[str, Any]]] = {}
        for r in rows:
            by_item.setdefault(r["media_item_id"], []).append({
                "profile_id": r["profile_id"],
                "user_rating": r["user_rating"],
                "status": r["status"],
            })

        out: Dict[int, Dict[str, Any]] = {}
        for item_id, verdicts in by_item.items():
            seen = {v["profile_id"]: v for v in verdicts if v["status"] == "seen"}
            unseen = {v["profile_id"] for v in verdicts if v["status"] == "not_seen"}
            liked = [pid for pid, v in seen.items() if (v["user_rating"] or 0) >= 7.0]

            if viewer_profile_id is None:
                # Symmetric tiers (rating-UI view).
                if len(liked) >= 2:
                    tier = "A"
                elif liked and unseen:
                    tier = "B"
                elif liked:
                    others_seen = [pid for pid in seen if pid not in liked]
                    tier = "C" if others_seen else "B"
                elif seen:
                    tier = "C"
                else:
                    tier = "C"
            else:
                viewer_seen = viewer_profile_id in seen
                others = [pid for pid in seen if pid != viewer_profile_id]
                others_liked = [pid for pid in others if pid in liked]
                if viewer_seen and others_liked:
                    tier = "A"          # shared favourite
                elif (not viewer_seen) and others_liked:
                    tier = "B"          # "he loved it, you haven't"
                else:
                    tier = "C"          # viewer already rated it, or unseen by all

            out[item_id] = {
                "tier": tier,
                "liked_by": liked,
                "seen_by": list(seen.keys()),
                "not_seen_by": list(unseen),
                "combined_score": self._shrunk_combined(seen),
            }
        return out

    @staticmethod
    def _shrunk_combined(seen: Dict[int, Dict[str, Any]]) -> Optional[float]:
        """Bayesian-shrunk weighted mean of the ratings that exist."""
        ratings = [v["user_rating"] for v in seen.values() if v["user_rating"] is not None]
        if not ratings:
            return None
        # Counts aren't available per-rater here, so weight by how many raters
        # exist and shrink mildly toward the global mean.
        n = len(ratings)
        mean = sum(ratings) / n
        adj = (n * mean + 10.0 * 6.5) / (n + 10.0)
        return round(adj, 2)

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
    # ------------------------------------------------------------------
    # Series / franchise order
    # ------------------------------------------------------------------
    # Fallback for films whose collection hasn't been looked up on TMDB yet:
    # a trailing sequel number ("Toy Story 3", "Rocky IV", "The Godfather
    # Part II", "Dune: Part Two"). Only ever used to point at an EARLIER film
    # with the bare stem title that shares a genre, so "Super 8" can't be
    # mistaken for a sequel to "Super".
    _SEQUEL_RE = re.compile(
        r"^(?P<stem>.+?)[\s:\-–]+(?:part\s+(?P<word>two|three|four|five|six)"
        r"|(?:part\s+)?(?P<num>[2-9]|ii|iii|iv|v|vi|vii|viii|ix|x))"
        r"(?:\s*[:\-–].*)?$",
        re.IGNORECASE,
    )
    _NUMERALS = {"2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9,
                 "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7,
                 "viii": 8, "ix": 9, "x": 10, "two": 2, "three": 3,
                 "four": 4, "five": 5, "six": 6}

    @classmethod
    def parse_sequel_title(cls, title: Optional[str]) -> Optional[Tuple[str, int]]:
        """'Toy Story 3' -> ('Toy Story', 3). None when no sequel marker."""
        m = cls._SEQUEL_RE.match((title or "").strip())
        if not m:
            return None
        stem = m.group("stem").strip(" :-–")
        token = (m.group("word") or m.group("num") or "").lower()
        n = cls._NUMERALS.get(token)
        if not stem or not n or n < 2:
            return None
        return stem, n

    def get_movies_missing_collection(self) -> List[Dict[str, Any]]:
        """Movies whose TMDB collection hasn't been looked up yet.

        Most-voted first, so an interrupted backfill has already covered the
        films that actually get recommended.
        """
        with self.get_connection() as conn:
            rows = conn.execute(
                "SELECT id, tmdb_id, title FROM media_items "
                "WHERE media_type = 'movie' AND tmdb_id IS NOT NULL "
                "AND COALESCE(collection_checked, 0) = 0 "
                "ORDER BY COALESCE(vote_count, 0) DESC;"
            ).fetchall()
            return [dict(r) for r in rows]

    def set_item_collection(
        self, item_id: int, collection_id: Optional[int],
        collection_name: Optional[str], checked: int = 1,
    ) -> None:
        with self.get_connection() as conn:
            conn.execute(
                "UPDATE media_items SET collection_id = ?, collection_name = ?, "
                "collection_checked = ? WHERE id = ?;",
                (collection_id, collection_name, checked, item_id),
            )

    def get_collections_missing_order(self) -> List[int]:
        with self.get_connection() as conn:
            rows = conn.execute(
                "SELECT DISTINCT collection_id FROM media_items "
                "WHERE collection_id IS NOT NULL AND collection_order IS NULL;"
            ).fetchall()
            return [int(r[0]) for r in rows]

    def set_collection_order(
        self, collection_id: int, collection_name: Optional[str],
        ordered_tmdb_ids: List[int],
    ) -> int:
        """Writes 1-based watch order for every part of a collection we hold."""
        updated = 0
        with self.get_connection() as conn:
            for pos, tmdb_id in enumerate(ordered_tmdb_ids, start=1):
                cur = conn.execute(
                    "UPDATE media_items SET collection_id = ?, "
                    "collection_name = COALESCE(?, collection_name), "
                    "collection_order = ? "
                    "WHERE media_type = 'movie' AND tmdb_id = ?;",
                    (collection_id, collection_name, pos, tmdb_id),
                )
                updated += cur.rowcount
        return updated

    def get_series_context(self, item_ids: List[int]) -> Dict[int, Dict[str, Any]]:
        """For each movie id that belongs to a series: the series key/name, the
        item's position, and every entry we hold in watch order with a `seen`
        flag (seen = any profile marked it 'seen').

        Uses TMDB collection data when present; otherwise the sequel-title
        fallback. Items TMDB confirmed as standalone (checked=1, no collection)
        never use the fallback.
        """
        ids = [int(i) for i in item_ids if i]
        if not ids:
            return {}
        member_cols = (
            "mi.*, "
            "(SELECT file_path FROM media_sources ms WHERE ms.media_item_id = mi.id LIMIT 1) AS file_path, "
            "(SELECT source_type FROM media_sources ms WHERE ms.media_item_id = mi.id LIMIT 1) AS source_type"
        )
        out: Dict[int, Dict[str, Any]] = {}
        with self.get_connection() as conn:
            ph = ",".join("?" * len(ids))
            rows = [dict(r) for r in conn.execute(
                f"SELECT id, title, release_year, genres, collection_id, collection_name, "
                f"collection_checked FROM media_items "
                f"WHERE media_type = 'movie' AND id IN ({ph});", ids,
            ).fetchall()]

            series: Dict[str, Dict[str, Any]] = {}   # key -> {name, members}
            item_key: Dict[int, str] = {}

            # 1. TMDB collections
            coll_ids = sorted({r["collection_id"] for r in rows if r.get("collection_id")})
            if coll_ids:
                cph = ",".join("?" * len(coll_ids))
                members = [dict(r) for r in conn.execute(
                    f"SELECT {member_cols} FROM media_items mi "
                    f"WHERE mi.media_type = 'movie' AND mi.collection_id IN ({cph});",
                    coll_ids,
                ).fetchall()]
                for cid in coll_ids:
                    ms = [m for m in members if m["collection_id"] == cid]
                    ms.sort(key=lambda m: (
                        m.get("collection_order") if m.get("collection_order") is not None else 999,
                        m.get("release_year") or 9999, m["id"],
                    ))
                    name = next((m.get("collection_name") for m in ms if m.get("collection_name")), None)
                    series[f"c:{cid}"] = {"name": name, "members": ms}
                for r in rows:
                    if r.get("collection_id"):
                        item_key[r["id"]] = f"c:{r['collection_id']}"

            # 2. Sequel-title fallback for films not yet looked up on TMDB
            for r in rows:
                if r["id"] in item_key or r.get("collection_id"):
                    continue
                if int(r.get("collection_checked") or 0) == 1:
                    continue  # TMDB says standalone
                parsed = self.parse_sequel_title(r.get("title"))
                if not parsed:
                    continue
                stem, _n = parsed
                year = r.get("release_year") or 9999
                stem_row = conn.execute(
                    f"SELECT {member_cols} FROM media_items mi "
                    "WHERE mi.media_type = 'movie' AND LOWER(mi.title) = LOWER(?) "
                    "AND COALESCE(mi.release_year, 0) < ? "
                    "ORDER BY mi.release_year DESC LIMIT 1;",
                    (stem, year),
                ).fetchone()
                if not stem_row:
                    continue
                stem_row = dict(stem_row)
                g_seq = {g.strip().lower() for g in (r.get("genres") or "").split(",") if g.strip()}
                g_stem = {g.strip().lower() for g in (stem_row.get("genres") or "").split(",") if g.strip()}
                if not (g_seq & g_stem):
                    continue
                key = f"t:{stem_row['id']}"
                item_key[r["id"]] = key
                if key in series:
                    continue
                stem_row["_series_n"] = 1
                ms = [stem_row]
                for cand in conn.execute(
                    f"SELECT {member_cols} FROM media_items mi "
                    "WHERE mi.media_type = 'movie' AND LOWER(mi.title) LIKE LOWER(?) "
                    "AND COALESCE(mi.release_year, 0) > ?;",
                    (stem + "%", stem_row.get("release_year") or 0),
                ).fetchall():
                    cand = dict(cand)
                    pc = self.parse_sequel_title(cand.get("title"))
                    if pc and pc[0].lower() == stem.lower():
                        cand["_series_n"] = pc[1]
                        ms.append(cand)
                ms.sort(key=lambda m: (m["_series_n"], m.get("release_year") or 9999))
                series[key] = {"name": stem, "members": ms}

            if not item_key:
                return {}

            # 3. Seen flags across every member
            all_ids = sorted({m["id"] for s in series.values() for m in s["members"]})
            aph = ",".join("?" * len(all_ids))
            seen = {int(r[0]) for r in conn.execute(
                f"SELECT DISTINCT media_item_id FROM watch_history "
                f"WHERE COALESCE(status, 'seen') = 'seen' AND media_item_id IN ({aph});",
                all_ids,
            ).fetchall()}

        for s in series.values():
            for pos, m in enumerate(s["members"], start=1):
                m["series_position"] = pos
                m["seen"] = m["id"] in seen
                m.pop("_series_n", None)
        for iid, key in item_key.items():
            s = series[key]
            pos = next((m["series_position"] for m in s["members"] if m["id"] == iid), None)
            if pos is None:
                continue
            out[iid] = {"key": key, "name": s["name"], "position": pos,
                        "size": len(s["members"]), "members": s["members"]}
        return out

    def get_recent_liked_history(
        self, profile_ids: List[int], limit: int = 30, min_rating: float = 6.0,
    ) -> List[int]:
        """Item ids the given profiles actually watched and liked, oldest-first,
        newest `limit` only.

        This is the generative engine's input. The old source
        (get_watch_history_chronological, oldest 100 rows) froze the model on
        the first ratings ever entered, fed it "we haven't seen it" answers as
        if they were watches, and counted a 3/10 the same as a 10/10.
        """
        if not profile_ids:
            return []
        ph = ",".join("?" * len(profile_ids))
        with self.get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT wh.media_item_id, MAX(wh.watched_at) AS last_at
                FROM watch_history wh
                LEFT JOIN rating_details rd
                       ON rd.profile_id = wh.profile_id AND rd.media_item_id = wh.media_item_id
                WHERE COALESCE(wh.status, 'seen') = 'seen'
                  AND wh.profile_id IN ({ph})
                  AND (
                        rd.taste_score > 0
                     OR (rd.taste_score IS NULL AND (wh.user_rating IS NULL OR wh.user_rating >= ?))
                  )
                GROUP BY wh.media_item_id
                ORDER BY last_at DESC, wh.media_item_id DESC
                LIMIT ?;
                """,
                [*profile_ids, min_rating, limit],
            ).fetchall()
        return [int(r[0]) for r in reversed(rows)]

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



    @staticmethod
    def _taste_weight(row) -> float:
        """How much one liked film counts toward the taste profile: a verdict's
        taste_score (0.5-1.0 for anything liked) when there is one, else the old
        rating / 10."""
        score = row["taste_score"]
        if score is not None:
            return max(0.1, 0.5 + 0.5 * float(score))
        return float(row["user_rating"] or 7.0) / 10.0

    def get_user_taste_profile(self, min_rating: float = 7.0) -> Dict[str, Any]:
        """
        Analyzes watched movies where user_rating >= min_rating.
        Returns frequency maps of preferred genres and keywords.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT mi.genres, mi.tier2_keywords, wh.user_rating, rd.taste_score
                FROM watch_history wh
                JOIN media_items mi ON wh.media_item_id = mi.id
                LEFT JOIN rating_details rd
                       ON rd.profile_id = wh.profile_id AND rd.media_item_id = wh.media_item_id
                WHERE rd.taste_score > 0 OR (rd.taste_score IS NULL AND wh.user_rating >= ?);
                """,
                (min_rating,),
            )
            rows = cursor.fetchall()

            genre_scores: Dict[str, float] = {}
            keyword_scores: Dict[str, float] = {}

            for row in rows:
                weight = self._taste_weight(row)
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
