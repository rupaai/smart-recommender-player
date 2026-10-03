"""
FastAPI Server for Smart Movie Streamer & Mobile PWA (server.py)
Provides REST API endpoints, background downloads, cancellation, and serves the mobile-first cinema companion.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
from typing import Any, Dict, List, Optional

import requests
import uvicorn
import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
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
from services.torrent_service import (
    TorrentDownloader,
    TorrentSearcher,
    _clean_release_title,
    downloads_store,
)

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
    shortlist_size=recommender_cfg.get("shortlist_size", 250),
    final_pool=recommender_cfg.get("final_pool", 50),
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
    profile: str = "together"  # 'me' | 'partner' | 'together'
    exclude_ids: Optional[List[int]] = None


class StartWatchRequest(BaseModel):
    media_item_id: int


class FinishWatchRequest(BaseModel):
    session_id: int
    user_rating: Optional[float] = None
    notes: Optional[str] = None


class CancelWatchRequest(BaseModel):
    session_id: int


# --- Co-view rating models (multi-profile, schema v3) ---
class RateRequest(BaseModel):
    media_item_id: int
    profile_id: int = 1
    user_rating: Optional[float] = Field(default=None, ge=1, le=10)
    status: str = "seen"  # 'seen' | 'not_seen'
    notes: Optional[str] = None


class RenameProfileRequest(BaseModel):
    name: str


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


class MediaCleanupRequest(BaseModel):
    paths: List[str]
    item_ids: Optional[List[int]] = None


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


@app.post("/api/jellyfin/repair")
async def repair_jellyfin_libraries() -> Dict[str, Any]:
    """Attaches missing paths to Movies/TV Shows libraries, forces realtime
    monitoring, recreates EMPTY libraries as a fallback, then refreshes."""
    return await asyncio.to_thread(jellyfin_service.repair_libraries)


@app.get("/api/jellyfin/repair")
async def repair_jellyfin_libraries_get(apply: int = 0) -> Dict[str, Any]:
    """GET form of the repair endpoint (for curl/fetch-tooling convenience).
    Pass ?apply=1 to actually mutate; without it this only reports."""
    if not apply:
        diag = await asyncio.to_thread(jellyfin_service.diagnostics)
        return {
            "mode": "dry-run",
            "note": "call /api/jellyfin/repair (POST) or ?apply=1 to fix libraries",
            "current_state": diag,
        }
    return await asyncio.to_thread(jellyfin_service.repair_libraries)


@app.get("/api/media/files")
async def list_media_files() -> Dict[str, Any]:
    """Read-only listing of every video file under the configured media roots.

    Diagnostic helper (e.g. verifying the organizer's folder layout):
    returns per-root absolute file paths with sizes and mtimes.
    """
    VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".webm"}

    def _scan() -> Dict[str, Any]:
        out: Dict[str, Any] = {"roots": {}}
        for root_dir in config.get("media_folders", []) or []:
            files: List[Dict[str, Any]] = []
            if os.path.isdir(root_dir):
                for dirpath, _dirnames, filenames in os.walk(root_dir):
                    for fname in filenames:
                        if os.path.splitext(fname)[1].lower() in VIDEO_EXTS:
                            fpath = os.path.join(dirpath, fname)
                            try:
                                st = os.stat(fpath)
                                files.append({
                                    "path": fpath,
                                    "size": st.st_size,
                                    "mtime": st.st_mtime,
                                })
                            except OSError:
                                continue
            files.sort(key=lambda f: f["path"])
            out["roots"][root_dir] = {"exists": os.path.isdir(root_dir), "files": files}
        return out

    return await asyncio.to_thread(_scan)


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
        variety=True,   # cold-start freshness: seeded 10-minute rotation
        profile=req.profile,
    )

    # Feature-flag router: dispatches between the untouched legacy recommender
    # and the parallel TIGER generative retrieval engine (with fallback safety).
    # Per-item exclusion is applied after routing so it works for both engines.
    results = await asyncio.to_thread(
        route_recommend,
        recommender_service,
        generative_retriever,
        generative_retriever.engine_mode,
        **kwargs,
    )

    # exclude_ids: per-tap "not this one" — drop items the user dismissed so
    # the next request surfaces something else instead of the same list.
    if req.exclude_ids and results.get("items"):
        excluded = set(req.exclude_ids)
        results["items"] = [i for i in results["items"] if i.get("id") not in excluded]
        results["total_results"] = len(results["items"])
        results["has_more"] = False
    # Always advertise pagination. The engines already emit `has_more`, but a
    # result path that omits it (or an error envelope) previously left the UI
    # with no way to tell "that's everything" from "there's more" — and the UI
    # then hid its Show-more button, silently capping the user at one page.
    results.setdefault("has_more", False)
    results.setdefault("page", 1)
    results.setdefault("page_size", len(results.get("items") or []))
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


@app.get("/api/profiles")
async def get_profiles() -> List[Dict[str, Any]]:
    """Lists the co-view rating profiles (you + partner) with live stats."""
    return db.get_profiles()


@app.put("/api/profiles/{profile_id}")
async def rename_profile(profile_id: int, req: RenameProfileRequest) -> Dict[str, Any]:
    """Renames a rating profile. 400 on unknown id or a duplicate name."""
    if not db.update_profile_name(profile_id, req.name):
        raise HTTPException(status_code=400, detail="Unknown profile id or duplicate name.")
    return {"ok": True, "profiles": db.get_profiles()}


# Display names for the origin chips. The phrase written into the query must
# be one the recommender's parse_origin_intent() recognises.
ORIGIN_LABELS: Dict[str, str] = {
    "IN": "🇮🇳 South Asian",
    "JP": "🇯🇵 Japanese",
    "KR": "🇰🇷 Korean",
    "CN": "🇨🇳 Chinese",
    "HK": "🇭🇰 Hong Kong",
    "TW": "🇹🇼 Taiwanese",
    "ES": "🇪🇸 Spanish",
    "FR": "🇫🇷 French",
    "DE": "🇩🇪 German",
    "IT": "🇮🇹 Italian",
    "BR": "🇧🇷 Brazilian",
    "MX": "🇲🇽 Mexican",
    "NG": "🇳🇬 Nigerian",
    "IR": "🇮🇷 Iranian",
    "EG": "🇪🇬 Egyptian",
    "TR": "🇹🇷 Turkish",
    "RU": "🇷🇺 Russian",
}


@app.get("/api/origins")
async def list_origins():
    """Countries of origin present in the catalogue, with counts.

    Only regions actually imported (utils/backfill_origin_country.py) appear,
    so the UI never offers a region with nothing to show. Feeds the "From"
    chips, which write an origin phrase into the free-text query — origin is a
    place, so it cannot be expressed as a genre.
    """
    with db.get_connection() as conn:
        rows = conn.execute(
            "SELECT origin_country, COUNT(*) AS n FROM media_items "
            "WHERE origin_country IS NOT NULL AND origin_country != '' "
            "GROUP BY origin_country ORDER BY n DESC;"
        ).fetchall()
    # A row can carry several codes ("IN,GB") for co-productions; count each.
    counts: Dict[str, int] = {}
    for raw, n in rows:
        for code in (raw or "").split(","):
            code = code.strip().upper()
            if code:
                counts[code] = counts.get(code, 0) + n
    labelled = [
        {"code": code, "name": ORIGIN_LABELS.get(code, code), "count": counts[code]}
        for code in sorted(counts, key=lambda c: -counts[c])
        if counts[code] >= 20
    ]
    return {"origins": labelled}


@app.get("/api/ratings/genres")
async def list_rating_genres():
    """Genres worth offering as rating-queue chips.

    Derived from the catalogue rather than hardcoded in the UI: the old fixed
    list of 9 included "Documentary", which only 7 films in the catalogue
    carry — clicking it produced a 1–2 card queue that felt broken. Anything
    below the floor is omitted, and the count lets the UI show how deep each
    genre actually goes.
    """
    with db.get_connection() as conn:
        cursor = conn.cursor()
        rows = cursor.execute(
            "SELECT genres FROM media_items WHERE genres IS NOT NULL AND genres != ''"
        ).fetchall()

    counts: Dict[str, int] = {}
    for (raw,) in rows:
        for g in (raw or "").split(","):
            name = g.strip()
            if name:
                counts[name] = counts.get(name, 0) + 1

    # Below this a genre can't fill a 20-card page, so offering it is a trap.
    floor = 20
    ranked = sorted(
        ((name, n) for name, n in counts.items() if n >= floor),
        key=lambda kv: (-kv[1], kv[0]),
    )
    return {"genres": [{"name": name, "count": n} for name, n in ranked]}


@app.get("/api/ratings/queue")
async def get_rating_queue(
    profile_id: int = 1,
    media_type: str = "movie",
    limit: int = Query(default=25, ge=1, le=100),
    genres: Optional[str] = Query(default=None, description="Comma-separated genre filter"),
    seed: Optional[int] = Query(default=None, description="Rotation seed; omit to use a 10-min bucket"),
    balanced: bool = Query(default=False, description="Walk the genre wheel — best for building a taste profile fast"),
) -> Dict[str, Any]:
    """Next items for this profile to rate, highest-value first.

    Each profile rates independently — this is a to-do list *for the active
    profile*, not a list of their existing ratings. `genres` narrows to a slice
    (the UI's genre chips); `seed` rotates which films come up; `balanced` walks
    the genre wheel so a short rating session still covers many genres.
    """
    # FastAPI substitutes Query(...) defaults over HTTP, but a direct call
    # (tests, scripts) passes the Query object itself, which has no .split().
    # Normalise anything non-string to None.
    genre_list = (
        [g.strip() for g in genres.split(",") if g.strip()]
        if isinstance(genres, str) and genres
        else None
    )
    seed_value = seed if isinstance(seed, int) else None
    balanced_value = balanced if isinstance(balanced, bool) else False
    items = await asyncio.to_thread(
        db.get_rating_queue, profile_id, media_type, limit, genre_list, seed_value, balanced_value
    )

    # Attach what the OTHER profile already said, so the card can show
    # "you rated this 8/10" while the current profile rates it too.
    other_id = 2 if profile_id == 1 else 1
    for item in items:
        summary = db.get_rating_summary(item["id"])
        item["co_view"] = {
            "tier": summary["tier"],
            "tier_reason": summary["tier_reason"],
            "combined_score": summary["combined_score"],
            "disagreement": summary["disagreement"],
            "verdicts": summary["verdicts"],
        }
        item["other_profile_id"] = other_id
    return {"profile_id": profile_id, "media_type": media_type, "items": items}


@app.post("/api/ratings")
async def rate_item(req: RateRequest) -> Dict[str, Any]:
    """Records one profile's verdict on one item (upsert).

    `status="not_seen"` means "we haven't watched this" — stored as its own
    state with no rating attached, never as a zero or a low score.
    """
    try:
        summary = await asyncio.to_thread(
            db.rate_item,
            req.media_item_id,
            req.profile_id,
            req.user_rating,
            req.status,
            req.notes,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return summary


@app.get("/api/ratings/summary/{item_id}")
async def get_rating_summary(item_id: int) -> Dict[str, Any]:
    """Both profiles' verdicts on one item, the combined score, and its tier."""
    return db.get_rating_summary(item_id)


@app.get("/api/ratings/co-view")
async def get_co_view_history(min_liked: float = 7.0) -> Dict[str, Any]:
    """Films BOTH profiles rated >= min_liked — the virtual-user sequence.

    This is what a `together` recommendation is grounded in; it stays empty
    until enough films have been rated by both.
    """
    items = db.get_co_view_history(min_liked=min_liked)
    return {
        "count": len(items),
        "min_liked": min_liked,
        "items": items,
        "note": (
            "Both profiles have liked these films — the shared taste signal."
            if items
            else "Nothing rated by both profiles yet. Rate together to build this."
        ),
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
        # Torrent release names must never become catalogue titles or folder
        # names ("New Girl S05E12 D-Day 1080p DSNP WEB-DL...") — clean once,
        # here, so the DB item AND the organizer both use the real title.
        clean_movie_title = _clean_release_title(req.movie_title)
        if not movie_id or movie_id <= 0:
            movie_id = db.get_or_create_media_item(
                title=clean_movie_title,
                media_type=req.media_type,
            )

        asyncio.create_task(
            torrent_downloader.download(
                req.magnet_link,
                movie_id,
                clean_movie_title,
                media_type=req.media_type,
            )
        )
        return {
            "success": True,
            "movie_id": movie_id,
            "clean_title": clean_movie_title,
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


@app.post("/api/media/cleanup")
async def cleanup_media(req: MediaCleanupRequest) -> Dict[str, Any]:
    """Deletes the given media files/trees (guarded), removes their DB rows,
    prunes orphaned stub items, and triggers a Jellyfin refresh.

    Guardrails: every path must be absolute AND inside a configured media
    root (media_folders). Refuses anything else — never touches other paths.
    """
    roots = {os.path.abspath(r) for r in (config.get("media_folders", []) or [])}

    def _guarded(path: str) -> Optional[str]:
        ap = os.path.abspath(path)
        if any(ap == r or ap.startswith(r + os.sep) for r in roots):
            return ap
        return None

    report: Dict[str, Any] = {"deleted_paths": [], "skipped_paths": [], "removed_sources": 0,
                              "pruned_items": [], "jellyfin_refresh": False}

    def _do_cleanup() -> Dict[str, Any]:
        for raw_path in req.paths:
            ap = _guarded(raw_path)
            if ap is None:
                report["skipped_paths"].append(f"outside media roots: {raw_path}")
                continue
            if os.path.isdir(ap):
                shutil.rmtree(ap, ignore_errors=True)
            elif os.path.isfile(ap):
                os.remove(ap)
            report["deleted_paths"].append(ap)

        # DB rows: sources matching any deleted file (exact match per file),
        # then items that no longer have any source / history / tmdb_id.
        file_paths = [p for p in report["deleted_paths"] if not os.path.exists(p)]
        report["removed_sources"] = db.remove_source_files(file_paths)
        if req.item_ids:
            with db.get_connection() as conn:
                for item_id in req.item_ids:
                    cur = conn.cursor()
                    cur.execute(
                        "SELECT COUNT(*) AS n FROM watch_history WHERE media_item_id = ?;",
                        (item_id,),
                    )
                    if cur.fetchone()["n"] == 0:
                        cur.execute("DELETE FROM media_items WHERE id = ?;", (item_id,))
                        report["pruned_items"].append(str(item_id))
                conn.commit()
        report["pruned_items"] += db.prune_orphan_stub_items()

        # Refresh Jellyfin so deleted content disappears promptly.
        try:
            resp = jellyfin_service.session.post(
                f"{jellyfin_service.base_url}/Library/Refresh", timeout=10
            )
            report["jellyfin_refresh"] = resp.status_code in (200, 204)
        except Exception as e:
            report["jellyfin_refresh_error"] = str(e)
        return report

    return await asyncio.to_thread(_do_cleanup)


@app.get("/api/db/sources")
async def list_db_sources() -> Dict[str, Any]:
    """Read-only dump of media_sources rows + their parent item titles.

    Diagnostic helper for spotting stale source rows pointing at deleted files.
    """
    def _dump() -> Dict[str, Any]:
        with db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT s.id, s.file_path, s.source_type, s.file_size,
                       s.season_number, s.episode_number,
                       s.media_item_id, i.title, i.media_type, i.tmdb_id
                FROM media_sources s
                LEFT JOIN media_items i ON i.id = s.media_item_id
                ORDER BY s.id;
                """
            )
            rows = [dict(r) for r in cur.fetchall()]
        for r in rows:
            r["file_exists"] = os.path.isfile(r["file_path"])
        return {"count": len(rows), "rows": rows}

    return await asyncio.to_thread(_dump)


@app.post("/api/db/sources/prune")
async def prune_stale_db_sources() -> Dict[str, Any]:
    """Removes media_sources rows whose file_path no longer exists on disk,
    then prunes orphaned stub items. Never deletes files."""
    def _prune() -> Dict[str, Any]:
        removed: List[str] = []
        with db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, file_path FROM media_sources;")
            for row in cur.fetchall():
                if not os.path.isfile(row["file_path"]):
                    removed.append(row["file_path"])
            if removed:
                db.remove_source_files(removed)
        pruned = db.prune_orphan_stub_items()
        return {"removed_source_rows": removed, "pruned_items": pruned}

    return await asyncio.to_thread(_prune)


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