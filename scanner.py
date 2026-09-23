"""
Block 1: Media Scanner (scanner.py)
Walks media directories, parses titles/years/episodes from filenames,
and registers files into the catalogue database.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from database import DatabaseManager


class BaseScanner(ABC):
    """Abstract interface for media ingest scanners."""

    @abstractmethod
    def scan(self) -> Dict[str, int]:
        """Runs the scan and returns a summary dict of counts."""
        pass


class LocalFileScanner(BaseScanner):
    """Scans local filesystem media folders and parses filenames into media items & sources."""

    DEFAULT_EXTENSIONS = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".webm"}

    # Common quality and scene keywords to strip from parsed titles
    SCENE_NOISE = re.compile(
        r"\b(1080p|720p|2160p|4k|hdr|uhd|bluray|blu-ray|web-dl|webrip|web|hdtv|"
        r"x264|x265|hevc|h264|h265|aac|dts|dts-hd|truehd|atmos|ddp5\.1|ac3|"
        r"remux|repack|proper|unrated|extended|directors\.cut)\b",
        re.IGNORECASE,
    )

    # TV regex: matches S01E02 or 1x02
    TV_PATTERN = re.compile(r"[\. _\-]?(?:s([0-9]{1,2})e([0-9]{1,2})|([0-9]{1,2})x([0-9]{1,2}))[\. _\-]?", re.IGNORECASE)

    # Year pattern: e.g. (2010), [2010], .2010. or 2010
    YEAR_PATTERN = re.compile(r"[\(\[\. _\-](19[0-9]{2}|20[0-9]{2})[\)\]\. _\-]?", re.IGNORECASE)

    def __init__(
        self,
        folders: List[str],
        db_manager: DatabaseManager,
        supported_extensions: Optional[List[str]] = None,
    ):
        self.folders = [os.path.abspath(f) for f in folders]
        self.db = db_manager
        self.supported_extensions = (
            {ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in supported_extensions}
            if supported_extensions
            else self.DEFAULT_EXTENSIONS
        )

    @classmethod
    def parse_filename(cls, filename: str) -> Dict[str, Any]:
        """
        Parses title, year, media_type, season, and episode from a video filename.
        Works across common movie and TV release naming schemes.
        """
        stem = Path(filename).stem

        # 1. Check for TV Show pattern (e.g. S01E02 or 1x02)
        tv_match = cls.TV_PATTERN.search(stem)
        if tv_match:
            media_type = "tv"
            if tv_match.group(1) is not None:
                season = int(tv_match.group(1))
                episode = int(tv_match.group(2))
            else:
                season = int(tv_match.group(3))
                episode = int(tv_match.group(4))

            # The show title is usually everything preceding the season/episode tag
            raw_title = stem[: tv_match.start()]
            raw_title = re.sub(r"[\._\-]+", " ", raw_title).strip()
            # Also check if a year was in the show title, e.g. "Doctor Who (2005)"
            year_match = cls.YEAR_PATTERN.search(raw_title)
            year = int(year_match.group(1)) if year_match else None
            if year_match:
                raw_title = raw_title[: year_match.start()].strip()

            return {
                "parsed_title": raw_title or stem,
                "parsed_year": year,
                "media_type": media_type,
                "season_number": season,
                "episode_number": episode,
            }

        # 2. Movie pattern: Look for a 4-digit release year
        year_match = cls.YEAR_PATTERN.search(stem)
        year: Optional[int] = None
        if year_match:
            year = int(year_match.group(1))
            # Title is everything before the year
            raw_title = stem[: year_match.start()]
        else:
            raw_title = stem

        # Strip scene noise
        cleaned = cls.SCENE_NOISE.sub(" ", raw_title)
        # Replace dots, underscores, dashes with space
        cleaned = re.sub(r"[\._\-]+", " ", cleaned).strip()
        # Clean multiple spaces
        cleaned = re.sub(r"\s+", " ", cleaned).strip()

        return {
            "parsed_title": cleaned or stem,
            "parsed_year": year,
            "media_type": "movie",
            "season_number": None,
            "episode_number": None,
        }

    def scan(self) -> Dict[str, int]:
        """Walks configured directories and registers newly found media files."""
        total_found = 0
        new_registered = 0
        already_registered = 0

        for folder in self.folders:
            if not os.path.exists(folder):
                print(f"[Scanner] Warning: Folder does not exist, skipping: {folder}")
                continue

            print(f"[Scanner] Scanning directory: {folder}")
            for root, _, files in os.walk(folder):
                for file in files:
                    ext = os.path.splitext(file)[1].lower()
                    if ext in self.supported_extensions:
                        total_found += 1
                        file_path = os.path.abspath(os.path.join(root, file))
                        file_size = os.path.getsize(file_path)

                        info = self.parse_filename(file)
                        is_new, _ = self.db.register_source_file(
                            file_path=file_path,
                            parsed_title=info["parsed_title"],
                            parsed_year=info["parsed_year"],
                            media_type=info["media_type"],
                            season_number=info["season_number"],
                            episode_number=info["episode_number"],
                            file_size=file_size,
                            source_type="local",
                        )

                        if is_new:
                            new_registered += 1
                            yr = f" ({info['parsed_year']})" if info["parsed_year"] else ""
                            ep = f" S{info['season_number']:02d}E{info['episode_number']:02d}" if info["season_number"] else ""
                            print(f"  [+] Added: {info['parsed_title']}{yr}{ep} [{info['media_type']}]")
                        else:
                            already_registered += 1

        summary = {
            "total_found": total_found,
            "new_registered": new_registered,
            "already_registered": already_registered,
        }
        return summary


def create_dummy_library(target_dir: str = "./sample_library") -> None:
    """
    Creates a realistic mock media library with 0-byte dummy video files.
    Allows immediate zero-download testing of the scanning, enrichment, and recommender pipeline.
    """
    os.makedirs(target_dir, exist_ok=True)

    dummy_files = [
        # Mind-Bending / Sci-Fi
        ("Inception (2010)", "Inception.2010.1080p.BluRay.x264.mkv"),
        ("Interstellar (2014)", "Interstellar.2014.2160p.UHD.HDR.mkv"),
        ("The Matrix (1999)", "The.Matrix.1999.1080p.mkv"),
        # Happy / Feel-Good / Comedy / Animation
        ("The Grand Budapest Hotel (2014)", "The.Grand.Budapest.Hotel.2014.mkv"),
        ("Paddington 2 (2017)", "Paddington.2.2017.1080p.mp4"),
        ("Spirited Away (2001)", "Spirited.Away.2001.mkv"),
        # Thrilling / Action / Adventure
        ("The Dark Knight (2008)", "The.Dark.Knight.2008.1080p.mkv"),
        ("Parasite (2019)", "Parasite.2019.BluRay.mkv"),
        ("Mad Max Fury Road (2015)", "Mad.Max.Fury.Road.2015.mkv"),
        # Scary / Horror
        ("A Quiet Place (2018)", "A.Quiet.Place.2018.mkv"),
        ("Get Out (2017)", "Get.Out.2017.mkv"),
        # Romantic / Drama
        ("La La Land (2016)", "La.La.Land.2016.mp4"),
        ("Before Sunrise (1995)", "Before.Sunrise.1995.mkv"),
        # Relaxing / Documentary
        ("My Octopus Teacher (2020)", "My.Octopus.Teacher.2020.mkv"),
        # TV Shows (multi-file test)
        ("Stranger Things", "Stranger.Things.S01E01.mkv"),
        ("Stranger Things", "Stranger.Things.S01E02.mkv"),
    ]

    print(f"[Dummy Creator] Generating sample media files in: {target_dir}")
    created_count = 0
    for folder_name, filename in dummy_files:
        sub_folder = os.path.join(target_dir, folder_name)
        os.makedirs(sub_folder, exist_ok=True)
        file_path = os.path.join(sub_folder, filename)
        if not os.path.exists(file_path):
            with open(file_path, "wb") as f:
                pass  # 0-byte file
            created_count += 1

    print(f"[Dummy Creator] Created {created_count} sample media files.")


def load_config(config_path: str = "config.yaml") -> Dict[str, Any]:
    """Loads configuration from YAML file or falls back to defaults."""
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan media folders and register video files into catalogue DB.")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml file")
    parser.add_argument(
        "--create-dummy-library",
        nargs="?",
        const="./sample_library",
        help="Generate a realistic sample library with 0-byte media files for instant zero-download testing.",
    )
    args = parser.parse_args()

    if args.create_dummy_library:
        create_dummy_library(args.create_dummy_library)
        print("[Dummy Creator] Done! You can now run scanner.py to catalogue these files.")
        return

    config = load_config(args.config)
    folders = config.get("media_folders", ["./sample_library"])
    db_path = config.get("database_path", "catalogue.db")
    extensions = config.get("supported_extensions", None)

    db_manager = DatabaseManager(db_path)
    scanner = LocalFileScanner(folders=folders, db_manager=db_manager, supported_extensions=extensions)

    print(f"--- Media Scanner Starting ---")
    summary = scanner.scan()
    print(f"--- Scan Complete ---")
    print(f"Total video files found: {summary['total_found']}")
    print(f"New items registered:    {summary['new_registered']}")
    print(f"Already in database:     {summary['already_registered']}")


if __name__ == "__main__":
    main()
