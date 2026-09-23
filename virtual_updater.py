"""
Block 6: Virtual Media Updater (virtual_updater.py)
Manages virtual media sources that may not have physical files yet.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional

from database import DatabaseManager


class VirtualUpdater:
    """
    Handles virtual media sources that may be streamed or downloaded later.
    """

    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager

    def add_virtual_source(
        self,
        media_item_id: int,
        source_type: str,
        source_uri: str,
        parsed_title: str,
        parsed_year: Optional[int] = None,
        season_number: Optional[int] = None,
        episode_number: Optional[int] = None,
    ) -> int:
        """
        Adds a virtual source to the database.
        Returns the source ID.
        """
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO media_sources (
                    media_item_id, source_type, file_path, 
                    parsed_title, parsed_year, season_number, episode_number
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    media_item_id,
                    source_type,
                    source_uri,
                    parsed_title,
                    parsed_year,
                    season_number,
                    episode_number,
                ),
            )
            return cursor.lastrowid

    def check_source_availability(self, source_id: int) -> bool:
        """
        Checks if a virtual source is still available.
        """
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT source_type, file_path FROM media_sources WHERE id = ?",
                (source_id,),
            )
            source = cursor.fetchone()
            
            if not source:
                return False
                
            # TODO: Implement actual availability checks for different source types
            return True

    def sync_with_physical_media(self) -> Dict[str, int]:
        """
        Checks all virtual sources and updates their status if physical files exist.
        Returns counts of updated sources.
        """
        stats = {"updated": 0, "unavailable": 0, "unchanged": 0}
        
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, file_path FROM media_sources 
                WHERE source_type = 'virtual'
                """
            )
            
            for source_id, file_path in cursor.fetchall():
                if os.path.exists(file_path):
                    cursor.execute(
                        """
                        UPDATE media_sources 
                        SET source_type = 'local'
                        WHERE id = ?
                        """,
                        (source_id,),
                    )
                    stats["updated"] += 1
                elif not self.check_source_availability(source_id):
                    stats["unavailable"] += 1
                else:
                    stats["unchanged"] += 1
            
            conn.commit()
            
        return stats