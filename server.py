"""
FastAPI Server for Smart Movie Streamer & Mobile PWA (server.py)
Provides REST API endpoints and serves the mobile-first cinema companion.
"""

from __future__ import annotations

import os
import socket
from typing import Any, Dict, List, Optional

import asyncio
import requests
import uvicorn
import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from ws_manager import manager

from database import DatabaseManager
from enricher import TMDBEnricher
from recommender import MoodRecommender
from scanner import LocalFileScanner
from torrent_searcher import TorrentSearcher
from torrent_downloader import TorrentDownloader

load_dotenv()
api_key = os.getenv("TMDB_API_KEY", "").strip()

# Configuration
CONFIG_PATH = "config.yaml"
config: Dict[str, Any] = {}
if os.path.exists(CONFIG_PATH):
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

db_path = config.get("database_path", "catalogue.db")
db = DatabaseManager(db_path)
recommender = MoodRecommender(db_manager=db, api_key=api_key)
torrent_searcher = TorrentSearcher(config)
torrent_downloader = TorrentDownloader(config)

app = FastAPI(title="Smart Movie Streamer & Recommender", version="0.1.0")

# WebSocket manager imported from ws_manager.py

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Directories
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(TEMPLATES_DIR, exist_ok=True)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)


def get_local_ip() -> str:
    """Discovers host machine's Wi-Fi / LAN IP address."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def is_jellyfin_online() -> bool:
    """Checks if Jellyfin local server is responding on port 8096."""
    try:
        resp = requests.get("http://localhost:8096/web/", timeout=1.5)
        return resp.status_code == 200
    except Exception:
        return False


# --- Request Models ---
class RecommendRequest(BaseModel):
    mood_id: int = 8
    era: Optional[str] = None
    duration: Optional[str] = None
    top_n: int = 4
    include_watched: bool = False
    query: Optional[str] = None


class StartWatchRequest(BaseModel):
    media_item_id: int


class FinishWatchRequest(BaseModel):
    session_id: int
    user_rating: Optional[float] = None
    notes: Optional[str] = None


class CancelWatchRequest(BaseModel):
    session_id: int


# --- Routes ---
@app.get("/", response_class=HTMLResponse)
async def serve_index(request: Request) -> Any:
    """Serves the mobile-first PWA interface."""
    local_ip = get_local_ip()
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"local_ip": local_ip},
    )


@app.get("/api/status")
async def get_status() -> Dict[str, Any]:
    """Returns system status including Jellyfin online state and catalogue statistics."""
    local_ip = get_local_ip()
    jf_online = is_jellyfin_online()
    stats = db.get_stats()
    return {
        "jellyfin_online": jf_online,
        "jellyfin_url": f"http://{local_ip}:8096",
        "local_ip": local_ip,
        "stats": stats,
    }


@app.get("/api/moods")
async def get_moods() -> List[Dict[str, Any]]:
    """Returns available mood options for the interactive picker."""
    moods_list = []
    for mood_id, info in recommender.MOOD_MAPPINGS.items():
        moods_list.append({
            "id": mood_id,
            "name": info["name"],
            "vibe": info["vibe"],
            "genres": info["genres"],
        })
    return moods_list


@app.post("/api/recommend")
async def get_recommendations(req: RecommendRequest) -> List[Dict[str, Any]]:
    """Runs recommendation pipeline with dynamic Tier 2 deep profiling."""
    results = recommender.recommend(
        mood_id=req.mood_id,
        exclude_watched=not req.include_watched,
        era_preference=req.era,
        duration_preference=req.duration,
        top_n=req.top_n,
    )

    # Filter further by voice/text query if provided
    if req.query and req.query.strip():
        q = req.query.lower().strip()
        matched = []
        for item in results:
            text_corpus = (
                f"{item.get('title', '')} {item.get('overview', '')} "
                f"{item.get('genres', '')} {item.get('tier2_keywords', '')}"
            ).lower()
            if any(term in text_corpus for term in q.split()):
                matched.append(item)
        if matched:
            results = matched

    return results


@app.get("/api/watch/current")
async def get_current_watch() -> Dict[str, Any]:
    """Returns active watching movie session, or empty object."""
    active = db.get_active_watch()
    return {"active": active}


@app.get("/api/stream/{item_id}")
async def stream_media(item_id: int) -> Dict[str, Any]:
    """Returns the Jellyfin playback URL for the selected movie."""
    item = db.get_item_by_id(item_id)
    if not item or not item.get("sources"):
        raise HTTPException(status_code=404, detail="Media file not found.")
    file_path = item["sources"][0]["file_path"]
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Video file not found on disk.")
    
    # Construct Jellyfin URL
    local_ip = get_local_ip()
    file_name = os.path.basename(file_path)
    jellyfin_url = f"http://{local_ip}:8096/web/index.html#!/details?filename={file_name}"
    return {"jellyfin_url": jellyfin_url}


@app.post("/api/watch/start")
async def start_watch(req: StartWatchRequest) -> Dict[str, Any]:
    """Begins watching a movie session."""
    session_id = db.start_active_watch(req.media_item_id)
    active = db.get_active_watch()
    return {"success": True, "session_id": session_id, "active": active}


@app.post("/api/watch/finish")
async def finish_watch(req: FinishWatchRequest) -> Dict[str, Any]:
    """Completes watch session with star rating & review notes, updating taste profile."""
    record_id = db.finish_active_watch(
        session_id=req.session_id,
        user_rating=req.user_rating,
        notes=req.notes,
    )
    if not record_id:
        raise HTTPException(status_code=404, detail="Active session not found.")
    return {"success": True, "record_id": record_id}


@app.post("/api/watch/cancel")
async def cancel_watch(req: CancelWatchRequest) -> Dict[str, Any]:
    """Cancels an active watch session."""
    db.cancel_active_watch(req.session_id)
    return {"success": True}


@app.get("/api/history")
async def get_history() -> Dict[str, Any]:
    """Retrieves watch history and learned taste profile."""
    history = db.get_watch_history(limit=50)
    taste = db.get_user_taste_profile(min_rating=7.0)
    return {
        "history": history,
        "taste_profile": taste,
    }


@app.websocket("/ws/progress")
async def websocket_progress(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await asyncio.sleep(1)  # Keep connection alive
    except Exception:
        manager.disconnect(websocket)

@app.post("/api/torrent/search")
async def search_torrents(query: str) -> Dict[str, Any]:
    """Searches for torrents matching the query."""
    results = torrent_searcher.search(query)
    return {"results": results}


@app.post("/api/sync")
async def sync_catalogue() -> Dict[str, Any]:
    """Scans media folders and enriches new items with TMDB metadata."""
    folders = config.get("media_folders", ["./sample_library"])
    extensions = config.get("supported_extensions", None)
    scanner = LocalFileScanner(folders=folders, db_manager=db, supported_extensions=extensions)
    scan_summary = scanner.scan()

    enr_summary = {"total": 0, "enriched": 0, "failed": 0}
    if api_key:
        enricher = TMDBEnricher(api_key=api_key, db_manager=db, region=config.get("region", "GB"))
        enr_summary = enricher.enrich()

    return {
        "scan": scan_summary,
        "enrich": enr_summary,
    }


def run_server(host: str = "0.0.0.0", port: int = 8000) -> None:
    """Launches Uvicorn server for the application."""
    print("=" * 65)
    print(f"🎬 SMART MOVIE STREAMER PWA SERVER RUNNING")
    print(f"📱 Access on your phone:   http://{get_local_ip()}:{port}")
    print(f"💻 Access on your laptop:  http://localhost:{port}")
    print(f"📺 Jellyfin Server:        http://{get_local_ip()}:8096")
    print("=" * 65)
    uvicorn.run("server:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    run_server()
