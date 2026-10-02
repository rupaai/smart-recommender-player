"""
FastAPI Server for Smart Movie Streamer & Mobile PWA (server.py)
Provides REST API endpoints, background downloads, cancellation, and serves the mobile-first cinema companion.
"""

from __future__ import annotations

import asyncio
import os
import socket
from typing import Any, Dict, List, Optional

import requests
import uvicorn
import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from database import DatabaseManager
from enricher import TMDBEnricher
from scanner import LocalFileScanner
from services.jellyfin_service import JellyfinService
from services.generative_retriever import GenerativeRetriever, route_recommend
from services.recommender_service import MOOD_MAPPINGS, RecommenderService
from services.torrent_service import TorrentDownloader, TorrentSearcher, downloads_store

load_dotenv()
api_key = os.getenv("TMDB_API_KEY", "").strip()

# Configuration
CONFIG_PATH = "config.yaml"
config: Dict[str, Any] = {}
if os.path.exists(CONFIG_PATH):
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

db_path = os.getenv("DATABASE_PATH") or config.get("database_path", "catalogue.db")
db = DatabaseManager(db_path)
print(
    f"[DB] active catalogue: {os.path.abspath(db_path)} "
    f"({'DATABASE_PATH override' if os.getenv('DATABASE_PATH') else 'from config.yaml'})",
    flush=True,
)

recommender_cfg = config.get("recommender", {})
enable_tier2 = recommender_cfg.get("enable_tier2_profiling", True)
recommender_service = RecommenderService(
    db_manager=db,
    api_key=api_key,
    enable_tier2=enable_tier2,
)

# TIGER generative retrieval engine (parallel — legacy recommender untouched).
# Artifacts are loaded lazily from models/tiger/; if absent the router
# automatically serves the existing recommender instead.
generative_retriever = GenerativeRetriever(db_manager=db, config=config)

# Create torrent services
torrent_searcher = TorrentSearcher(config)
torrent_downloader = TorrentDownloader(config, db_manager=db)

# Jellyfin library manager (idempotent library setup, refresh + verification)
jellyfin_service = JellyfinService(config)

app = FastAPI(title="Smart Movie & TV Streamer & Recommender", version="0.4.0")

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def setup_jellyfin_libraries() -> None:
    """Idempotently ensures the dedicated Movies / TV Shows libraries exist.

    Additive-only and fully fault-tolerant: if Jellyfin is offline or not
    configured, the server still boots normally and setup is simply skipped.
    """
    try:
        result = await asyncio.to_thread(jellyfin_service.ensure_libraries)
        print(f"[Jellyfin] Libraries ensured: {result}", flush=True)
    except Exception as e:
        print(f"[Jellyfin] Library setup skipped ({e}) — server continues.", flush=True)

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
    page: int = Field(default=1, ge=1, description="Page index (1-based)")
    page_size: int = Field(default=10, ge=1, le=50, description="Items per page")
    top_n: Optional[int] = Field(default=None, description="Legacy limit parameter alias")
    include_watched: bool = False
    query: Optional[str] = None
    media_type: str = "movie"  # 'movie' or 'tv'
    sub_mood: Optional[str] = None
    genres: Optional[List[str]] = None
    min_rating: Optional[float] = None


class StartWatchRequest(BaseModel):
    media_item_id: int


class FinishWatchRequest(BaseModel):
    session_id: int
    user_rating: Optional[float] = None
    notes: Optional[str] = None


class CancelWatchRequest(BaseModel):
    session_id: int


class TorrentSearchRequest(BaseModel):
    query: str
    sort_by_size: bool = True
    media_type: str = "movie"  # 'movie' or 'tv'


class TorrentDownloadRequest(BaseModel):
    magnet_link: str
    movie_id: Optional[int] = None
    movie_title: str
    media_type: str = "movie"  # 'movie' or 'tv'


class TorrentCancelRequest(BaseModel):
    movie_id: int


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


@app.get("/api/jellyfin/status")
async def get_jellyfin_status() -> Dict[str, Any]:
    """Jellyfin diagnostics: server online, libraries (types + locations), env config."""
    return await asyncio.to_thread(jellyfin_service.diagnostics)


@app.get("/api/moods")
async def get_moods() -> List[Dict[str, Any]]:
    """Returns available mood options including sub-moods for the interactive picker."""
    moods_list = []
    for mood_id, info in MOOD_MAPPINGS.items():
        moods_list.append({
            "id": mood_id,
            "name": info["name"],
            "vibe": info.get("vibe", ""),
            "genres": info["genres"],
            "submoods": info.get("submoods", []),
        })
    return moods_list


@app.post("/api/recommend")
async def get_recommendations(req: RecommendRequest) -> Dict[str, Any]:
    """Runs recommendation pipeline with dynamic Tier 2 deep profiling, taste scoring, Bayesian rating, MMR diversity, and pagination.

    Executes off the main asyncio thread to prevent blocking event loop.
    """
    # Respect top_n legacy parameter if passed by client, otherwise use page_size
    effective_page_size = req.top_n if req.top_n is not None else req.page_size

    # Resolve mood genres so both engines receive the same context signal
    # (legacy already does this internally — byte-equivalent for it; the
    # generative engine needs it passed in explicitly).
    genres = req.genres
    if genres is None:
        genres = MOOD_MAPPINGS.get(req.mood_id, MOOD_MAPPINGS[8])["genres"]

    kwargs = dict(
        mood_id=req.mood_id,
        exclude_watched=not req.include_watched,
        era_preference=req.era,
        duration_preference=req.duration,
        page=req.page,
        page_size=effective_page_size,
        sub_mood=req.sub_mood,
        query=req.query,
        media_type=req.media_type,
        genres_override=genres or None,
        min_rating_override=req.min_rating,
    )

    # Feature-flag router: dispatches between the untouched legacy recommender
    # and the parallel TIGER generative retrieval engine (with fallback safety).
    results = await asyncio.to_thread(
        route_recommend,
        recommender_service,
        generative_retriever,
        generative_retriever.engine_mode,
        **kwargs,
    )
    return results


@app.get("/api/watch/current")
async def get_current_watch() -> Dict[str, Any]:
    """Returns active watching movie session, or empty object."""
    active = db.get_active_watch()
    return {"active": active}


@app.get("/api/stream/{item_id}")
async def stream_media(item_id: int) -> Dict[str, Any]:
    """Returns the Jellyfin playback URL for the selected movie or episode."""
    item = db.get_item_by_id(item_id)
    if not item or not item.get("sources"):
        raise HTTPException(status_code=404, detail="Media file not found.")
    file_path = item["sources"][0]["file_path"]
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Video file not found on disk.")

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


@app.post("/api/torrent/search")
async def search_torrents(req: TorrentSearchRequest) -> Dict[str, Any]:
    """Searches for torrents matching the query with configurable sort order and media type (movies/shows)."""
    results = torrent_searcher.search(
        req.query,
        sort_by_size=req.sort_by_size,
        media_type=req.media_type,
    )
    return {"results": results}


@app.post("/api/torrent/download")
async def download_torrent(req: TorrentDownloadRequest) -> Dict[str, Any]:
    """Launches torrent download as a background task and returns immediately."""
    try:
        movie_id = req.movie_id
        if not movie_id or movie_id <= 0:
            movie_id = db.get_or_create_media_item(
                title=req.movie_title,
                media_type=req.media_type,
            )

        asyncio.create_task(
            torrent_downloader.download(
                req.magnet_link,
                movie_id,
                req.movie_title,
                media_type=req.media_type,
            )
        )
        return {
            "success": True,
            "movie_id": movie_id,
            "message": f"Download task started for {req.media_type}.",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/torrent/cancel")
async def cancel_torrent(req: TorrentCancelRequest) -> Dict[str, Any]:
    """Cancels an in-progress torrent download."""
    success = await torrent_downloader.cancel(req.movie_id)
    return {"success": success}


@app.get("/api/torrent/status/{movie_id}")
async def get_torrent_status(movie_id: int) -> Dict[str, Any]:
    """Returns the current download progress for a given movie or TV show."""
    status_data = downloads_store.get(
        movie_id,
        {"status": "idle", "progress": 0.0, "movie_id": movie_id},
    )
    return status_data


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
    print(f"🎬 SMART MOVIE & TV STREAMER PWA SERVER RUNNING")
    print(f"📱 Access on your phone:   http://{get_local_ip()}:{port}")
    print(f"💻 Access on your laptop:  http://localhost:{port}")
    print(f"📺 Jellyfin Server:        http://{get_local_ip()}:8096")
    print("=" * 65)
    uvicorn.run("server:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    run_server()