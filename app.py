"""
Smart Movie Streamer & Recommender - Unified Hub (app.py)
Unified interactive console tying together scanning, enrichment,
mood recommendations, feedback logging, and Jellyfin TV status.
"""

from __future__ import annotations

import os
import sys
import requests
import yaml
from dotenv import load_dotenv

from database import DatabaseManager
from scanner import LocalFileScanner, load_config as load_scanner_config
from enricher import TMDBEnricher
from recommender import MoodRecommender, print_card, display_history, interactive_recommender


def check_jellyfin_status() -> tuple[bool, str]:
    """Checks if Jellyfin local server is running and returns local network IP."""
    import socket
    local_ip = "127.0.0.1"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass

    try:
        resp = requests.get("http://localhost:8096/web/", timeout=2)
        if resp.status_code == 200:
            return True, local_ip
    except Exception:
        pass
    return False, local_ip


def sync_library(config: dict, db_manager: DatabaseManager, api_key: str) -> None:
    """Runs scanner followed by metadata enricher in one seamless flow."""
    print("\n" + "=" * 60)
    print("           🔄 SYNCING MEDIA LIBRARY & METADATA")
    print("=" * 60)

    # 1. Scan folders
    folders = config.get("media_folders", ["./sample_library"])
    extensions = config.get("supported_extensions", None)
    scanner = LocalFileScanner(folders=folders, db_manager=db_manager, supported_extensions=extensions)
    summary = scanner.scan()
    print(f"\n[Scanner] Found {summary['total_found']} video file(s). Added {summary['new_registered']} new items.")

    # 2. Enrich un-enriched
    if api_key:
        print("\n[Enricher] Checking for TMDB metadata updates...")
        enricher = TMDBEnricher(api_key=api_key, db_manager=db_manager, region=config.get("region", "GB"))
        enr_summary = enricher.enrich()
        print(f"[Enricher] Enriched {enr_summary['enriched']} new item(s).")
    else:
        print("\n[!] TMDB_API_KEY missing in .env — skipping metadata enrichment.")

    print("\n[✓] Library sync complete!\n")


def view_taste_profile(db_manager: DatabaseManager) -> None:
    """Displays watched history and user taste affinity scores."""
    display_history(db_manager)

    taste = db_manager.get_user_taste_profile(min_rating=7.0)
    pref_genres = taste.get("preferred_genres", {})
    pref_keywords = taste.get("preferred_keywords", {})

    if pref_genres or pref_keywords:
        print("🧠 WHAT THE RECOMMENDER HAS LEARNED ABOUT YOUR TASTE:")
        print("-" * 60)
        if pref_genres:
            top_genres = sorted(pref_genres.items(), key=lambda x: x[1], reverse=True)[:5]
            print("  Top Loved Genres:   " + ", ".join(f"{g.title()} (+{round(s, 1)})" for g, s in top_genres))
        if pref_keywords:
            top_kw = sorted(pref_keywords.items(), key=lambda x: x[1], reverse=True)[:6]
            print("  Favorite Themes:    " + ", ".join(f"{k} (+{round(s, 1)})" for k, s in top_kw))
        print("-" * 60 + "\n")
    else:
        print("💡 Rate more movies 7/10 or higher after watching to build your taste profile!\n")


def main_menu() -> None:
    load_dotenv()
    api_key = os.getenv("TMDB_API_KEY", "").strip()

    config_path = "config.yaml"
    config: dict = {}
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}

    db_path = config.get("database_path", "catalogue.db")
    db_manager = DatabaseManager(db_path)
    recommender = MoodRecommender(db_manager=db_manager, api_key=api_key)

    while True:
        jf_running, local_ip = check_jellyfin_status()
        jf_status = "🟢 Active (Online)" if jf_running else "🔴 Offline"

        print("=" * 65)
        print("       🎬 SMART MOVIE STREAMER & RECOMMENDER HUB")
        print("=" * 65)
        print(f"  📱 Phone Web App:        http://{local_ip}:8000")
        print(f"  TV Streaming (Jellyfin): {jf_status}")
        if jf_running:
            print(f"  Connect TV to:           http://{local_ip}:8096")
        print("-" * 65)
        print("  [1] 🍿 Movie Night Recommender  (Pick mood & get suggestions)")
        print("  [2] 🔄 Sync Library             (Scan files + enrich metadata)")
        print("  [3] ⭐ Watch History & Taste    (View past ratings & learned taste)")
        print("  [4] 📺 TV Streaming Setup Info  (How to connect TCL Google TV)")
        print("  [5] 📱 Open Phone App in Browser")
        print("  [6] 🚪 Exit")
        print("-" * 65)

        choice = input("Enter choice (1-6): ").strip()

        if choice == "1":
            interactive_recommender(recommender, config)
        elif choice == "2":
            sync_library(config, db_manager, api_key)
        elif choice == "3":
            view_taste_profile(db_manager)
        elif choice == "4":
            print("\n" + "=" * 60)
            print("             📺 TV STREAMING SETUP GUIDE")
            print("=" * 60)
            print("1. Server URL:  http://localhost:8096 (on your laptop)")
            print(f"2. TV Address:  http://{local_ip}:8096 (in your TV's Jellyfin app)")
            print("3. Media Path:  " + os.path.abspath(config.get("media_folders", ["./sample_library"])[0]))
            print("\nSteps on TV:")
            print("  • Open the Jellyfin app on your TCL Google TV.")
            print(f"  • Connect to: http://{local_ip}:8096")
            print("  • Log in and start streaming!\n")
        elif choice == "5":
            import webbrowser
            webbrowser.open(f"http://localhost:8000")
            print(f"\n[✓] Opened http://localhost:8000 in your browser! On your phone, visit: http://{local_ip}:8000\n")
        elif choice == "6":
            print("\nEnjoy your movie night! 👋\n")
            break
        else:
            print("Invalid choice. Please select 1-5.")


if __name__ == "__main__":
    main_menu()
