"""
FastAPI Server for Smart Movie Streamer & Mobile PWA (server.py)
Provides REST API endpoints, background downloads, cancellation, and serves the mobile-first cinema companion.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import quote

import requests
import uvicorn
import yaml
from dotenv import load_dotenv
from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.concurrency import iterate_in_threadpool, run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from database import DatabaseManager
from enricher import TMDBEnricher
from scanner import LocalFileScanner
from services.device_profiles import profile_for
from services.jellyfin_playback import (
    ItemNotFound,
    JellyfinPlayback,
    JellyfinUnavailable,
    NotPlayable,
    SubtitleError,
    is_image_path,
    rewrite_playlist,
    safe_proxy_path,
    valid_jellyfin_id,
    valid_subtitle_id,
    strip_api_key,
)
from services.jellyfin_service import JellyfinService
from services.nl_browse import NLBrowse
from services.nl_people import (
    BOTH,
    COOKIE_MAX_AGE,
    COOKIE_NAME,
    People,
    PeopleConfigError,
    Prefs,
    Viewer,
    clean_prefs,
    resume_summary,
    safe_next,
)
from services.catalogue_search import CatalogueSearch, tmdb_import, tmdb_search
from services.generative_retriever import GenerativeRetriever, route_recommend
from services.recommender_service import MOOD_MAPPINGS, RecommenderService
from services import paths as model_paths
from services import verdicts as vd
from services.taste_runtime import TasteRuntime
from services.admin_api import build_router as build_admin_router
from services.vibe_index import VibeIndex
from services.vibe_popup import PopupSession, SessionStore, should_auto_ask
from services.torrent_service import (
    TorrentDownloader,
    TorrentSearcher,
    _clean_release_title,
    downloads_store,
)

load_dotenv()
api_key = os.getenv("TMDB_API_KEY", "").strip()

# Configuration
CONFIG_PATH = os.getenv("CONFIG_PATH", "config.yaml")
config: Dict[str, Any] = {}
if os.path.exists(CONFIG_PATH):
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

db_path = os.getenv("DATABASE_PATH") or config.get("database_path", "catalogue.db")
# Folders the server layout expects (no-ops when they exist; the media folders are
# created by TorrentDownloader below).
os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
if os.getenv("MODELS_DIR"):
    os.makedirs(os.path.join(os.path.dirname(os.path.abspath(os.environ["MODELS_DIR"])), "releases"), exist_ok=True)
db = DatabaseManager(db_path)
print(
    f"[DB] active catalogue: {os.path.abspath(db_path)} "
    f"({'DATABASE_PATH override' if os.getenv('DATABASE_PATH') else 'from config.yaml'})",
    flush=True,
)

recommender_cfg = config.get("recommender", {})
enable_tier2 = recommender_cfg.get("enable_tier2_profiling", True)
taste_runtime = TasteRuntime(db, recommender_cfg.get("taste_model"))
vibe_index = VibeIndex()
recommender_service = RecommenderService(
    db_manager=db,
    api_key=api_key,
    enable_tier2=enable_tier2,
    shortlist_size=recommender_cfg.get("shortlist_size", 250),
    final_pool=recommender_cfg.get("final_pool", 50),
    vibe_index=vibe_index,
    taste_runtime=taste_runtime,
)

# TIGER generative retrieval engine (parallel — legacy recommender untouched).
# Artifacts are loaded lazily from models/tiger/; if absent the router
# automatically serves the existing recommender instead.
generative_retriever = GenerativeRetriever(db_manager=db, config=config, taste_runtime=taste_runtime)

def reload_models() -> None:
    """Loads the model files again after the Mac publishes a new bundle (or one is rolled back)."""
    tiger_ok = generative_retriever.reload()
    vibe_ok = vibe_index.reload()
    root = model_paths.models_dir()
    if os.path.isdir(os.path.join(root, "tiger")) and not tiger_ok:
        raise RuntimeError("the TIGER model did not load")
    if os.path.isdir(os.path.join(root, "vibe")) and not vibe_ok:
        raise RuntimeError(f"the vibe index did not load: {vibe_index.status().get('error')}")


# Create torrent services
torrent_searcher = TorrentSearcher(config)
torrent_downloader = TorrentDownloader(config, db_manager=db)

# Jellyfin library manager (idempotent library setup, refresh + verification)
jellyfin_service = JellyfinService(config)
jellyfin_playback = JellyfinPlayback(jellyfin_service)  # Northern Lights player (M0)
nl_browse = NLBrowse(db, jellyfin_playback)  # Northern Lights browse (M2); search is wired in below

# Northern Lights who's watching (M4): people from config.yaml `northern_lights.people` or env NL_PEOPLE.
# None configured = one implicit viewer using JELLYFIN_USER_ID, no who's-watching screen (as before M4).
try:
    nl_people = People.from_config(config, os.getenv("NL_PEOPLE"))
except PeopleConfigError as _e:
    raise SystemExit(f"[Northern Lights] bad people configuration: {_e}")
if nl_people.configured and not jellyfin_service.user_id:
    jellyfin_service.user_id = nl_people.people[0].jellyfin_user_id  # library scans need some user

app = FastAPI(title="Smart Movie & TV Streamer & Recommender", version="0.4.0")

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Mac -> server admin routes (export, catalogue patch, model bundles); 404 unless ADMIN_TOKEN is set.
app.include_router(build_admin_router(lambda: db.db_path, reload_models))


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


@app.middleware("http")
async def revalidate_static(request: Request, call_next):
    # Without this, browsers (the TV app's WebView especially) keep using an old CSS/JS file for
    # a while after a deploy. no-cache still caches, but checks the ETag on every use.
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response
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
    session_id: Optional[int] = None  # feedback log: pass back for "Show more" pages


class DiscoverStartRequest(RecommendRequest):
    ask: bool = True  # False = "Just show me" / pop-up turned off in filters


class DiscoverAnswerRequest(BaseModel):
    session_id: int
    question_id: str
    option_id: str  # an option id, or "skip"


class DiscoverFinishRequest(BaseModel):
    session_id: int


class FeedbackRequest(BaseModel):
    media_item_id: int
    outcome: str  # 'opened' | 'dismissed' | 'downloaded' | 'played' | 'rated'
    session_id: Optional[int] = None
    rating: Optional[float] = Field(default=None, ge=1, le=10)


class StartWatchRequest(BaseModel):
    media_item_id: int


class FinishWatchRequest(BaseModel):
    session_id: int
    user_rating: Optional[float] = None
    notes: Optional[str] = None
    profile_ids: Optional[List[int]] = None  # who watched; default: the Northern Lights viewer (M4)


class CancelWatchRequest(BaseModel):
    session_id: int


# --- Co-view rating models (multi-profile, schema v3) ---
class RateRequest(BaseModel):
    media_item_id: int
    profile_id: int = 1
    user_rating: Optional[float] = Field(default=None, ge=1, le=10)
    status: str = "seen"  # 'seen' | 'not_seen'
    notes: Optional[str] = None
    # Verdict rating (plan: "Better ratings"). With a verdict, user_rating is ignored.
    verdict: Optional[str] = None  # rewatch | loved | liked | meh | not_for_me
    did_not_finish: bool = False
    worked: Optional[List[str]] = None
    didnt: Optional[List[str]] = None
    vibe_check: Optional[Dict[str, Any]] = None


class SeasonRateRequest(BaseModel):
    media_item_id: int
    profile_id: int = 1
    season_number: int = Field(ge=1)
    # Same five verdicts as a show. No verdict and no did_not_finish clears it.
    verdict: Optional[str] = None
    did_not_finish: bool = False


class TmdbImportRequest(BaseModel):
    media_type: str  # 'movie' | 'tv'
    tmdb_id: int


class WatchLaterRequest(BaseModel):
    media_item_id: int
    profile_id: int


class RenameProfileRequest(BaseModel):
    name: str


class TorrentSearchRequest(BaseModel):
    query: str
    sort_by_size: bool = True  # legacy; ignored when `sort` is given
    sort: Optional[str] = None  # 'best' | 'smallest' | 'quality'
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


def _model_bundle_id() -> Optional[str]:
    """bundle_id from <MODELS_DIR>/manifest.json, or None if absent or unreadable."""
    try:
        with open(os.path.join(model_paths.models_dir(), "manifest.json"), "r", encoding="utf-8") as f:
            bundle_id = json.load(f).get("bundle_id")
        return str(bundle_id) if bundle_id is not None else None
    except (OSError, ValueError, AttributeError):
        return None


@app.get("/healthz")
async def healthz() -> Any:
    """Liveness/readiness: git SHA, model bundle, DB schema version. 503 if the DB cannot be read."""
    try:
        with db.get_connection() as conn:
            conn.execute("SELECT 1").fetchone()
        schema = db.get_schema_version()
    except Exception as e:  # noqa: BLE001 - any DB failure means not ready
        return JSONResponse(status_code=503, content={"ok": False, "error": str(e)})
    return {
        "ok": True,
        "git_sha": os.getenv("GIT_SHA", "dev"),
        "model_bundle": _model_bundle_id(),
        "db_schema": schema,
    }


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
    kwargs = _recommend_kwargs(req, effective_page_size)

    popup = popup_sessions.get(req.session_id)
    if popup is not None:
        # "Show more" after the questions pop-up: page through the same
        # answered pool instead of starting over.
        results = await asyncio.to_thread(
            recommender_service.finalize, popup.ranked(), req.page, effective_page_size)
        results["engine"] = "legacy"
    else:
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
    _with_on_list(results.get("items"))

    # Feedback log (schema v7): one session per Discover request, one
    # impression per title shown. Logging must never break recommendations.
    try:
        session_id = req.session_id if req.session_id and db.session_exists(req.session_id) else None
        if session_id is None:
            session_id = db.create_discover_session(
                profile=req.profile,
                media_type=req.media_type,
                query_text=req.query,
                request=req.model_dump(exclude={"session_id", "exclude_ids", "page", "page_size", "top_n"}),
                engine=results.get("engine"),
            )
        start_rank = (req.page - 1) * effective_page_size + 1
        db.log_impressions(session_id, results.get("items") or [], start_rank)
        results["session_id"] = session_id
    except Exception as e:  # noqa: BLE001
        print(f"[Feedback] impression logging failed: {e}", flush=True)
    return results


def _recommend_kwargs(req: RecommendRequest, page_size: int) -> Dict[str, Any]:
    # Resolve mood genres so both engines receive the same context signal
    # (legacy already does this internally — byte-equivalent for it; the
    # generative engine needs it passed in explicitly).
    genres = req.genres
    if genres is None:
        genres = MOOD_MAPPINGS.get(req.mood_id, MOOD_MAPPINGS[8])["genres"]
    return dict(
        mood_id=req.mood_id,
        exclude_watched=not req.include_watched,
        era_preference=req.era,
        duration_preference=req.duration,
        page=req.page,
        page_size=page_size,
        sub_mood=req.sub_mood,
        query=req.query,
        media_type=req.media_type,
        genres_override=genres or None,
        min_rating_override=req.min_rating,
        variety=True,   # cold-start freshness: seeded 10-minute rotation
        profile=req.profile,
    )


# ---------------------------------------------------------------------------
# Questions pop-up ("guess the vibe", design doc §3, services/vibe_popup.py)
# ---------------------------------------------------------------------------
popup_sessions = SessionStore()


def _cluster_labels() -> Dict[int, str]:
    try:
        with db.get_connection() as conn:
            return {r[0]: r[1] for r in conn.execute("SELECT id, label FROM vibe_clusters")}
    except Exception:  # noqa: BLE001
        return {}


def _pool_for(kwargs: Dict[str, Any]) -> List[Dict[str, Any]]:
    res = recommender_service.recommend(**{**kwargs, "page": 1}, return_pool=True)
    return res.get("pool") or []


def _with_on_list(items: Optional[List[Dict[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
    """Adds `on_list` (profile ids with the title on Watch later) to each item, one query for all.
    Never raises: on failure items get an empty list."""
    if not items:
        return items
    try:
        ids = [i["id"] for i in items if isinstance(i, dict) and i.get("id") is not None]
        found = db.get_watch_later_ids(ids) if ids else {}
    except Exception as e:  # noqa: BLE001
        print(f"[WatchLater] on_list lookup failed: {e}", flush=True)
        found = {}
    for i in items:
        if isinstance(i, dict):
            i["on_list"] = list(found.get(i.get("id"), []))
    return items


def _popup_response(popup: PopupSession, page_size: int, vibe: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    results = recommender_service.finalize(popup.ranked(), 1, page_size)
    results.update({"engine": "legacy", "session_id": popup.id, "question": popup.public_question()})
    _with_on_list(results.get("items"))
    if vibe is not None:
        results["vibe"] = vibe
    try:
        db.update_discover_session(popup.id, **popup.log_fields())
        if popup.done:
            db.log_impressions(popup.id, results.get("items") or [], 1)
    except Exception as e:  # noqa: BLE001
        print(f"[Feedback] pop-up logging failed: {e}", flush=True)
    return results


@app.post("/api/discover/start")
async def discover_start(req: DiscoverStartRequest) -> Dict[str, Any]:
    """First results plus, when the request is thin, the first question."""
    page_size = req.top_n if req.top_n is not None else req.page_size
    kwargs = _recommend_kwargs(req, page_size)
    res = await asyncio.to_thread(recommender_service.recommend, **{**kwargs, "page": 1}, return_pool=True)
    pool = res.get("pool") or []
    session_id = db.create_discover_session(
        profile=req.profile, media_type=req.media_type, query_text=req.query,
        request=req.model_dump(exclude={"session_id", "exclude_ids", "page", "page_size", "top_n"}),
        engine="legacy+popup",
    )
    popup = PopupSession(session_id, {**kwargs, "kwargs": kwargs}, pool, _cluster_labels(),
                         recommender_service.vibe_index)
    if req.ask and pool and should_auto_ask(req.query, pool):
        await asyncio.to_thread(popup.next_question)
    popup.done = popup.current is None
    popup_sessions.put(popup)
    return await asyncio.to_thread(_popup_response, popup, page_size, res.get("vibe"))


@app.post("/api/discover/answer")
async def discover_answer(req: DiscoverAnswerRequest) -> Dict[str, Any]:
    """Applies one answer (or "skip"), re-ranks, and picks the next question."""
    popup = popup_sessions.get(req.session_id)
    if popup is None:
        raise HTTPException(status_code=404, detail="That question session has expired. Search again.")
    try:
        popup.answer(req.question_id, req.option_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    kwargs = popup.request["kwargs"]
    new_profile = popup.belief.get("profile")
    if new_profile and new_profile != kwargs.get("profile"):
        # "Who's on the sofa?" changes whose taste counts: rebuild the pool.
        kwargs = {**kwargs, "profile": new_profile}
        popup.request["kwargs"] = kwargs
        popup.set_pool(await asyncio.to_thread(_pool_for, kwargs))
    if popup.should_stop():
        popup.done = True
    else:
        await asyncio.to_thread(popup.next_question)
        popup.done = popup.current is None
    return await asyncio.to_thread(_popup_response, popup, kwargs.get("page_size") or 20)


@app.post("/api/discover/finish")
async def discover_finish(req: DiscoverFinishRequest) -> Dict[str, Any]:
    """"Just show me": stop asking and keep the answers so far."""
    popup = popup_sessions.get(req.session_id)
    if popup is None:
        raise HTTPException(status_code=404, detail="That question session has expired. Search again.")
    popup.done = True
    popup.current = None
    return await asyncio.to_thread(_popup_response, popup, popup.request["kwargs"].get("page_size") or 20)


def _log_outcome(media_item_id: Optional[int], outcome: str, rating: Optional[float] = None) -> None:
    """Attributes a play / rating / download to the Discover impression that
    showed this title recently, if any. Never raises."""
    if not media_item_id:
        return
    try:
        db.record_outcome(int(media_item_id), outcome, rating=rating)
    except Exception as e:  # noqa: BLE001
        print(f"[Feedback] outcome logging failed: {e}", flush=True)


@app.post("/api/feedback")
async def record_feedback(req: FeedbackRequest) -> Dict[str, Any]:
    """Records what happened to a recommended title: opened (synopsis tapped),
    dismissed ("not this one"), downloaded, played or rated."""
    try:
        updated = db.record_outcome(req.media_item_id, req.outcome, req.session_id, req.rating)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"updated": updated}


@app.get("/api/feedback/stats")
async def feedback_stats() -> Dict[str, Any]:
    """How much training signal has been logged so far."""
    return db.get_feedback_stats()


@app.get("/api/vibe/status")
async def vibe_status() -> Dict[str, Any]:
    """Whether the vibe index (models/vibe/, built on the Mac) is loaded."""
    index = recommender_service.vibe_index
    return index.status() if index else {"ready": False}


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


# --- Northern Lights who's watching (M4) ---
def _nl_viewer(request: Optional[Request]) -> Optional[Viewer]:
    """The viewer the nl_viewer cookie names; None = not chosen (or unknown). Always the implicit
    viewer when no people are configured."""
    header = request.headers.get("cookie") if request is not None else None
    return nl_people.parse_cookie(header)


def _nl_viewer_or_default(request: Optional[Request]) -> Viewer:
    """For API calls: the chosen viewer, else the first person (the pages ask before they get here)."""
    return _nl_viewer(request) or nl_people.default_viewer()


def _as_user(user: Optional[str], fn, *args):
    """Runs fn(*args) with Jellyfin calls made for this user (set inside the worker thread)."""
    with jellyfin_playback.as_user(user):
        return fn(*args)


def _nl_prefs_row(viewer: Viewer) -> Dict[str, Any]:
    """The prefs a viewer lives by: their own row. For "Both of us" the first person's, with anything
    set separately for Both laid over it."""
    if viewer.is_both:
        base = db.get_person_prefs(viewer.primary.id) or {} if viewer.primary else {}
        over = db.get_person_prefs(BOTH) or {}
        return {**base, **{k: v for k, v in over.items() if v is not None}}
    return db.get_person_prefs(viewer.key) or {}


def _nl_effective_prefs(viewer: Viewer) -> Prefs:
    return Prefs.from_row(_nl_prefs_row(viewer))


def _nl_page(request: Request, name: str, context: Dict[str, Any], tv: bool = False) -> Any:
    """A /nl/ page. With people configured and nobody chosen yet, the who's-watching screen first."""
    viewer = _nl_viewer(request)
    if viewer is None:
        here = request.url.path + (("?" + request.url.query) if request.url.query else "")
        return RedirectResponse(f"/nl/{'tv/' if tv else ''}who?next={quote(here, safe='')}", status_code=302)
    ctx = {**context, "viewer": viewer.public(), "people_configured": nl_people.configured, "tv": tv}
    return templates.TemplateResponse(request=request, name=name, context=ctx)


@app.get("/api/nl/viewer")
async def nl_viewer_info(request: Request) -> Dict[str, Any]:
    """Who is watching: the choices for the who's-watching screen, the chosen viewer (null = not
    chosen yet) and the recommender profiles their ratings go to. Never carries Jellyfin ids."""
    viewer = _nl_viewer(request)
    return {
        "configured": nl_people.configured,
        "choices": nl_people.choices(),
        "viewer": viewer.public() if viewer else None,
        "profile_ids": viewer.profile_ids if viewer else [],
    }


def _nl_choose(key: str, next_url: Optional[str], tv: bool) -> Response:
    default = "/nl/tv/" if tv else "/nl/"
    target = safe_next(next_url, default)
    if not nl_people.configured or nl_people.viewer_for(key) is None:
        return RedirectResponse(f"/nl/{'tv/' if tv else ''}who?next={quote(target, safe='')}", status_code=302)
    resp = RedirectResponse(target, status_code=302)
    resp.set_cookie(COOKIE_NAME, key.lower(), max_age=COOKIE_MAX_AGE, path="/", samesite="lax", httponly=True)
    return resp


@app.get("/nl/who", response_class=HTMLResponse)
async def nl_who_page(request: Request, next: Optional[str] = None) -> Any:
    """Who's watching? Without configured people there is nothing to choose: straight home."""
    if not nl_people.configured:
        return RedirectResponse("/nl/", status_code=302)
    return templates.TemplateResponse(request=request, name="nl/who.html", context={
        "active": "", "choices": nl_people.choices(), "next": safe_next(next, "/nl/"), "viewer": None,
        "people_configured": True, "tv": False, "choose_base": "/nl/who/",
    })


@app.get("/nl/who/{key}")
async def nl_who_choose(key: str, next: Optional[str] = None) -> Any:
    return _nl_choose(key, next, tv=False)


@app.get("/nl/tv/who", response_class=HTMLResponse)
async def nl_tv_who_page(request: Request, next: Optional[str] = None) -> Any:
    if not nl_people.configured:
        return RedirectResponse("/nl/tv/", status_code=302)
    return templates.TemplateResponse(request=request, name="nl/tv/who.html", context={
        "page": "who", "choices": nl_people.choices(), "next": safe_next(next, "/nl/tv/"), "viewer": None,
        "people_configured": True, "tv": True, "choose_base": "/nl/tv/who/",
    })


@app.get("/nl/tv/who/{key}")
async def nl_tv_who_choose(key: str, next: Optional[str] = None) -> Any:
    return _nl_choose(key, next, tv=True)


def _nl_prefs_reply(viewer: Viewer) -> Dict[str, Any]:
    row = _nl_prefs_row(viewer)
    return {"viewer": viewer.public(), "prefs": Prefs.from_row(row).public(), "saved": bool(row)}


@app.get("/api/nl/prefs")
async def nl_prefs_get(request: Request) -> Dict[str, Any]:
    """The current viewer's language and subtitle prefs (audio language, subtitle language, subtitles on
    by default, the subtitle look, the subtitle search language). Per person, not per device."""
    viewer = _nl_viewer_or_default(request)
    return await asyncio.to_thread(_nl_prefs_reply, viewer)


@app.put("/api/nl/prefs")
async def nl_prefs_put(request: Request, body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Updates the fields sent (null / "" clears a language); the rest stay. 400 on a bad value."""
    viewer = _nl_viewer_or_default(request)
    try:
        fields = clean_prefs(body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await asyncio.to_thread(db.set_person_prefs, viewer.key, fields)
    return await asyncio.to_thread(_nl_prefs_reply, viewer)


# --- Northern Lights player (M0): playback through our own proxy, never straight from Jellyfin ---
_PROXY_REQUEST_HEADERS = ("range", "if-range", "if-none-match", "if-modified-since")
_PROXY_RESPONSE_HEADERS = (
    "content-type", "content-length", "content-range", "accept-ranges", "etag", "last-modified",
)
_MAX_PLAYLIST_BYTES = 5 * 1024 * 1024


def _read_limited(resp: requests.Response, limit: int) -> bytes:
    """Reads at most `limit` bytes of a streamed response."""
    buf = bytearray()
    for chunk in resp.iter_content(chunk_size=64 * 1024):
        buf += chunk
        if len(buf) >= limit:
            break
    return bytes(buf)


@app.get("/api/nl/play/{item_id}")
async def nl_play_info(
    request: Request,
    item_id: int,
    audio: Optional[int] = Query(None, ge=0, description="Jellyfin audio stream index"),
    subtitle: Optional[int] = Query(None, ge=-1, description="Jellyfin subtitle stream index to burn in; -1 = none"),
    start: float = Query(0.0, ge=0, description="Start position in seconds"),
    caps: Optional[str] = Query(None, max_length=300, description="Codecs this browser can play, e.g. v=h264.hevc;a=aac;h=h264.aac"),
    source: Optional[int] = Query(None, ge=1, description="media_sources.id: which episode of a show (default: the first)"),
    client: Optional[str] = Query(None, pattern="^(tv)$", description="tv = the Android TV app (TV profile; caps ignored)"),
) -> Dict[str, Any]:
    """How to play one movie or episode: a proxied stream URL and its kind (file or hls),
    its audio and subtitle tracks, and where the viewer left off. For the viewer (who's watching):
    default tracks from their language prefs, and Resume from their own progress (the further of the
    two for "Both of us", with a note when they are in different places)."""
    viewer = _nl_viewer_or_default(request)
    prefer = _nl_effective_prefs(viewer).public()
    item = db.get_nl_title(item_id)
    if not item or not item.get("sources"):
        raise HTTPException(status_code=404, detail="Media file not found.")
    chosen = item["sources"][0]
    if source is not None:
        chosen = next((s for s in item["sources"] if s["id"] == source), None)
        if chosen is None:
            raise HTTPException(status_code=404, detail="That episode isn't in the library.")
    item = {**item, "sources": [chosen]}
    try:
        profile = profile_for(client, caps)
        info = await asyncio.to_thread(
            _as_user, viewer.jellyfin_users[0], _stream_for_viewer, item, audio, subtitle, start, profile, prefer
        )
        if nl_people.configured and info.get("jellyfin_id"):
            await asyncio.to_thread(_apply_resume_rule, viewer, info)
    except ItemNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except JellyfinUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    except NotPlayable as e:
        raise HTTPException(status_code=415, detail=str(e))
    info["media_item_id"] = item_id
    info["media_type"] = item.get("media_type") or "movie"
    info["source_id"] = chosen["id"]
    return info


def _stream_for_viewer(item, audio, subtitle, start, profile, prefer):
    if prefer and not (prefer.get("audio_lang") or prefer.get("subtitles_on")):
        prefer = None
    if prefer:
        return jellyfin_playback.stream_for(item, audio, subtitle, start, profile, prefer=prefer)
    return jellyfin_playback.stream_for(item, audio, subtitle, start, profile)


def _apply_resume_rule(viewer: Viewer, info: Dict[str, Any]) -> None:
    """Resume from the viewer's own progress; for Both the further of the two. Adds `resume_note`
    ("You're at 42:10, Sam is at 38:00") when they differ by more than two minutes. Jellyfin trouble
    for the other person never blocks playback."""
    primary = viewer.primary
    states: Dict[str, Dict[str, Any]] = {}
    if primary is not None:
        states[primary.id] = {"position": info.get("resume_seconds") or 0.0, "played": info.get("played")}
    for person in nl_people.people:
        if person.id in states:
            continue
        try:
            with jellyfin_playback.as_user(person.jellyfin_user_id):
                details = jellyfin_playback.item_details(info["jellyfin_id"])
        except Exception:  # noqa: BLE001 - best effort
            continue
        data = (details or {}).get("UserData") or {}
        states[person.id] = {
            "position": (data.get("PlaybackPositionTicks") or 0) / 10_000_000,
            "played": bool(data.get("Played")),
        }
    summary = resume_summary(viewer, states, nl_people.people)
    if summary["positions"] or viewer.is_both:
        info["resume_seconds"] = summary["resume_seconds"]
        info["played"] = summary["played"]
    info["resume_note"] = summary["note"]


def _nl_subtitle_item(item_id: int, source: Optional[int]) -> Dict[str, Any]:
    """The title (one episode of a show when `source` is given) the subtitle calls are about."""
    item = db.get_nl_title(item_id)
    if not item or not item.get("sources"):
        raise HTTPException(status_code=404, detail="Media file not found.")
    chosen = item["sources"][0]
    if source is not None:
        chosen = next((s for s in item["sources"] if s["id"] == source), None)
        if chosen is None:
            raise HTTPException(status_code=404, detail="That episode isn't in the library.")
    return {**item, "sources": [chosen]}


def _nl_subtitle_failure(e: Exception) -> JSONResponse:
    """Jellyfin's problems as short messages the page can show: {detail, code}."""
    if isinstance(e, SubtitleError):
        return JSONResponse(status_code=e.status, content={"detail": e.message, "code": e.code})
    if isinstance(e, ItemNotFound):
        return JSONResponse(status_code=404, content={"detail": str(e), "code": "not_found"})
    return JSONResponse(status_code=503, content={"detail": str(e), "code": "unavailable"})


@app.get("/api/nl/subtitles/{item_id}")
async def nl_subtitle_search(
    item_id: int,
    language: str = Query("eng", pattern="^[a-z]{3}$", description="Three-letter ISO 639-2 code, e.g. eng"),
    source: Optional[int] = Query(None, ge=1, description="media_sources.id: which episode of a show"),
) -> Any:
    """Search OpenSubtitles (through Jellyfin's plugin) for subtitles of one title or episode.
    Results are best first (file-hash matches, then most downloaded). The Jellyfin key stays here."""
    item = _nl_subtitle_item(item_id, source)
    try:
        found = await asyncio.to_thread(jellyfin_playback.search_subtitles, item, language)
    except (SubtitleError, ItemNotFound, JellyfinUnavailable) as e:
        return _nl_subtitle_failure(e)
    found["code"] = "ok" if found["results"] else "no_results"
    if not found["results"]:
        found["message"] = f"No subtitles found for {found['language_name']}."
    return found


class NLSubtitleDownload(BaseModel):
    subtitle_id: str = Field(..., min_length=1, max_length=512)


@app.post("/api/nl/subtitles/{item_id}")
async def nl_subtitle_download(
    item_id: int,
    req: NLSubtitleDownload,
    source: Optional[int] = Query(None, ge=1),
) -> Any:
    """Have Jellyfin download one of the searched subtitles and save it with the file. The page then
    re-reads /api/nl/play for the new track."""
    if not valid_subtitle_id(req.subtitle_id):
        return JSONResponse(status_code=400, content={"detail": "That subtitle id isn't valid.", "code": "bad_request"})
    item = _nl_subtitle_item(item_id, source)
    try:
        await asyncio.to_thread(jellyfin_playback.download_subtitle, item, req.subtitle_id)
    except (SubtitleError, ItemNotFound, JellyfinUnavailable) as e:
        return _nl_subtitle_failure(e)
    return {"success": True}


@app.get("/nl/jf/{path:path}")
async def nl_jellyfin_proxy(path: str, request: Request) -> Response:
    """Streams Jellyfin video, HLS and artwork (Items/<id>/Images/...) through our app. GET only,
    allowlisted paths only; the API key stays here."""
    clean = safe_proxy_path(path)
    if clean is None:
        raise HTTPException(status_code=404, detail="Not found.")
    jf = jellyfin_service
    if not jf.configured:
        raise HTTPException(status_code=503, detail="Jellyfin is not configured.")

    upstream_url = f"{jf.base_url}/{quote(clean, safe='/')}"
    query = strip_api_key("?" + request.url.query)[1:] if request.url.query else ""
    if query:
        upstream_url += "?" + query
    headers = {k: v for k, v in request.headers.items() if k.lower() in _PROXY_REQUEST_HEADERS}
    headers["Accept"] = "*/*"
    headers["Accept-Encoding"] = "identity"
    try:
        upstream = await run_in_threadpool(
            lambda: jf.session.get(
                upstream_url, headers=headers, stream=True, timeout=(5, 60), allow_redirects=False
            )
        )
    except requests.RequestException:
        raise HTTPException(status_code=502, detail="Jellyfin could not be reached.")

    content_type = upstream.headers.get("content-type", "")
    if "mpegurl" in content_type.lower() or clean.lower().endswith(".m3u8"):
        try:
            body = await run_in_threadpool(_read_limited, upstream, _MAX_PLAYLIST_BYTES + 1)
        except Exception:
            raise HTTPException(status_code=502, detail="Jellyfin playlist could not be read.")
        finally:
            upstream.close()
        if len(body) > _MAX_PLAYLIST_BYTES:
            raise HTTPException(status_code=502, detail="Playlist too large.")
        text = rewrite_playlist(body.decode("utf-8", errors="replace"))
        return Response(
            content=text,
            status_code=upstream.status_code,
            media_type="application/vnd.apple.mpegurl",
            headers={"Cache-Control": "no-store"},
        )

    out_headers = {k: v for k, v in upstream.headers.items() if k.lower() in _PROXY_RESPONSE_HEADERS}
    if is_image_path(clean) and upstream.status_code == 200:
        out_headers["Cache-Control"] = "private, max-age=86400"

    async def body_iter():
        try:
            async for chunk in iterate_in_threadpool(upstream.iter_content(chunk_size=64 * 1024)):
                yield chunk
        finally:
            upstream.close()  # also runs when the client disconnects and the stream is cancelled

    return StreamingResponse(body_iter(), status_code=upstream.status_code, headers=out_headers)


class NLProgressRequest(BaseModel):
    jellyfin_id: str
    play_session_id: str = ""
    media_source_id: str = ""
    position_seconds: float = Field(0.0, ge=0)
    paused: bool = False
    event: Literal["start", "progress", "stop"]
    play_method: str = "DirectPlay"


def _report_to_users(users: List[Optional[str]], call) -> None:
    """Runs `call(user)` for every Jellyfin user ("Both of us" = two). One failing does not stop the
    other; it only raises (the first error) when none succeeded."""
    errors: List[JellyfinUnavailable] = []
    done = 0
    for user in users:
        try:
            with jellyfin_playback.as_user(user):
                call(user)
            done += 1
        except JellyfinUnavailable as e:
            errors.append(e)
    if errors and not done:
        raise errors[0]
    for e in errors:
        print(f"[NL] one viewer's Jellyfin report failed: {e}", flush=True)


@app.post("/api/nl/progress")
async def nl_progress(req: NLProgressRequest, request: Request) -> Dict[str, Any]:
    """Tells Jellyfin where the viewer is (start, progress, stop) so it can offer 'Resume'.
    Each person's progress goes to their own Jellyfin user; "Both of us" reports to both."""
    if not valid_jellyfin_id(req.jellyfin_id):
        raise HTTPException(status_code=400, detail="Bad item id.")
    viewer = _nl_viewer_or_default(request)
    try:
        await asyncio.to_thread(
            _report_to_users, viewer.jellyfin_users,
            lambda user: jellyfin_playback.report_progress(
                req.event, req.jellyfin_id, req.play_session_id, req.media_source_id,
                req.position_seconds, req.paused, req.play_method, **({"user": user} if user else {}),
            ),
        )
    except JellyfinUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {"success": True}


async def _nl_set_played(jellyfin_id: str, played: bool, request: Request) -> Dict[str, Any]:
    if not valid_jellyfin_id(jellyfin_id):
        raise HTTPException(status_code=400, detail="Bad item id.")
    viewer = _nl_viewer_or_default(request)
    try:
        await asyncio.to_thread(
            _report_to_users, viewer.jellyfin_users, lambda user: jellyfin_playback.set_played(jellyfin_id, played)
        )
    except JellyfinUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {"success": True, "played": played}


@app.post("/api/nl/played/{jellyfin_id}")
async def nl_mark_played(jellyfin_id: str, request: Request) -> Dict[str, Any]:
    """Mark a title as watched in Jellyfin (for both users when "Both of us")."""
    return await _nl_set_played(jellyfin_id, True, request)


@app.delete("/api/nl/played/{jellyfin_id}")
async def nl_mark_unplayed(jellyfin_id: str, request: Request) -> Dict[str, Any]:
    """Mark a title as not watched in Jellyfin."""
    return await _nl_set_played(jellyfin_id, False, request)


@app.get("/api/nl/library")
async def nl_library(request: Request, type: str = Query("movie", pattern="^(movie|tv)$")) -> Dict[str, Any]:
    """Cards for every movie or show that has a file. Movies partly watched carry a progress
    bar, the latest additions a "New" badge. Works with Jellyfin down (no progress then)."""
    viewer = _nl_viewer_or_default(request)
    items = await asyncio.to_thread(_as_user, viewer.jellyfin_users[0], nl_browse.library, type, None, True, viewer.jellyfin_users)
    return {"items": items}


@app.get("/api/nl/home")
async def nl_home_data(request: Request) -> Dict[str, Any]:
    """Home: Continue Watching (Jellyfin Resume of the viewer) plus the newest movies and shows."""
    viewer = _nl_viewer_or_default(request)
    return await asyncio.to_thread(_as_user, viewer.jellyfin_users[0], nl_browse.home, viewer.jellyfin_users)


@app.get("/api/nl/title/{item_id}")
async def nl_title_data(item_id: int, request: Request) -> Dict[str, Any]:
    viewer = _nl_viewer_or_default(request)
    data = await asyncio.to_thread(_as_user, viewer.jellyfin_users[0], nl_browse.title, item_id, viewer.jellyfin_users)
    if not data:
        raise HTTPException(status_code=404, detail="That title isn't in the library.")
    return data


@app.get("/api/nl/show/{item_id}")
async def nl_show_data(item_id: int, request: Request) -> Dict[str, Any]:
    """A show's seasons and episodes with watched state, and its Next up episode (the viewer's;
    for "Both of us" the first person's)."""
    viewer = _nl_viewer_or_default(request)
    data = await asyncio.to_thread(_as_user, viewer.jellyfin_users[0], nl_browse.show, item_id)
    if not data:
        raise HTTPException(status_code=404, detail="That show isn't in the library.")
    return data


class NLShowPrefs(BaseModel):
    autoplay: bool


@app.get("/api/nl/show/{item_id}/prefs")
async def nl_show_prefs_get(item_id: int) -> Dict[str, Any]:
    return {"autoplay": await asyncio.to_thread(db.get_show_autoplay, item_id)}


@app.put("/api/nl/show/{item_id}/prefs")
async def nl_show_prefs_set(item_id: int, prefs: NLShowPrefs) -> Dict[str, Any]:
    """Autoplay of the next episode, per show (on by default)."""
    try:
        value = await asyncio.to_thread(db.set_show_autoplay, item_id, prefs.autoplay)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"autoplay": value}


@app.get("/api/nl/next/{item_id}")
async def nl_next_episode(request: Request, item_id: int, source: int = Query(..., ge=1)) -> Dict[str, Any]:
    """The episode after `source` for the player's next-episode card (has_next false at the end)."""
    viewer = _nl_viewer_or_default(request)
    return await asyncio.to_thread(_as_user, viewer.jellyfin_users[0], nl_browse.next_episode, item_id, source)


@app.get("/api/nl/search")
async def nl_search(request: Request, q: str = Query("", max_length=100), limit: int = Query(20, ge=1, le=40)) -> Dict[str, Any]:
    """Movies, shows and episodes matching the text. Each result is Ready (in the library,
    with a link) or Not in library yet (with a link to the Download tab of the main app)."""
    viewer = _nl_viewer_or_default(request)
    results = await asyncio.to_thread(_as_user, viewer.jellyfin_users[0], nl_browse.search, q, limit)
    return {"query": q, "results": results}


@app.get("/nl/", response_class=HTMLResponse)
async def nl_home(request: Request) -> Any:
    return _nl_page(request, "nl/home.html", {"active": "home"})


@app.get("/nl/movies", response_class=HTMLResponse)
async def nl_movies_page(request: Request) -> Any:
    return _nl_page(request, "nl/browse.html", {"active": "movies", "kind": "movie", "heading": "Movies"})


@app.get("/nl/shows", response_class=HTMLResponse)
async def nl_shows_page(request: Request) -> Any:
    return _nl_page(request, "nl/browse.html", {"active": "shows", "kind": "tv", "heading": "Shows"})


@app.get("/nl/title/{item_id}", response_class=HTMLResponse)
async def nl_title_page(item_id: int, request: Request) -> Any:
    return _nl_page(request, "nl/title.html", {"active": "", "item_id": item_id})


@app.get("/nl/show/{item_id}", response_class=HTMLResponse)
async def nl_show_page(item_id: int, request: Request) -> Any:
    return _nl_page(request, "nl/show.html", {"active": "shows", "item_id": item_id})


@app.get("/nl/me", response_class=HTMLResponse)
async def nl_me_page(request: Request) -> Any:
    """The viewer's own settings: their languages and subtitles, and who's watching."""
    return _nl_page(request, "nl/me.html", {"active": "me"})


@app.get("/nl/tv/", response_class=HTMLResponse)
async def nl_tv_home(request: Request) -> Any:
    return _nl_page(request, "nl/tv/home.html", {"page": "home"}, tv=True)


@app.get("/nl/tv/search", response_class=HTMLResponse)
async def nl_tv_search(request: Request) -> Any:
    return _nl_page(request, "nl/tv/search.html", {"page": "search"}, tv=True)


@app.get("/nl/tv/title/{item_id}", response_class=HTMLResponse)
async def nl_tv_title(item_id: int, request: Request) -> Any:
    return _nl_page(request, "nl/tv/title.html", {"page": "title", "item_id": item_id}, tv=True)


@app.get("/nl/tv/show/{item_id}", response_class=HTMLResponse)
async def nl_tv_show(item_id: int, request: Request) -> Any:
    return _nl_page(request, "nl/tv/show.html", {"page": "show", "item_id": item_id}, tv=True)


@app.get("/nl/tv/me", response_class=HTMLResponse)
async def nl_tv_me(request: Request) -> Any:
    return _nl_page(request, "nl/tv/me.html", {"page": "me"}, tv=True)


@app.get("/nl/play/{item_id}", response_class=HTMLResponse)
async def nl_play_page(item_id: int, request: Request) -> Any:
    """Minimal full-screen player page for one movie or episode."""
    return _nl_page(request, "nl/player.html", {"item_id": item_id}, tv=request.query_params.get("tv") == "1")


@app.post("/api/watch/start")
async def start_watch(req: StartWatchRequest) -> Dict[str, Any]:
    """Begins watching a movie session."""
    session_id = db.start_active_watch(req.media_item_id)
    _log_outcome(req.media_item_id, "played")
    active = db.get_active_watch()
    return {"success": True, "session_id": session_id, "active": active}


@app.post("/api/watch/finish")
async def finish_watch(req: FinishWatchRequest, request: Request) -> Dict[str, Any]:
    """Completes watch session with star rating & review notes, updating taste profile.
    With who's-watching set up, the watch and rating are logged for the viewer's recommender
    profile(s) ("Both of us" = both) instead of always landing on profile 1."""
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT media_item_id FROM active_watch_sessions WHERE id = ?;", (req.session_id,)
        ).fetchone()
    viewer = _nl_viewer(request) if nl_people.configured else None
    profile_ids = req.profile_ids or (viewer.profile_ids if viewer else [])
    if profile_ids:
        try:
            done = db.mark_watch_done_for_profiles(req.session_id, profile_ids, req.user_rating, req.notes)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        record_id = req.session_id if done else None
    else:
        record_id = db.finish_active_watch(
            session_id=req.session_id,
            user_rating=req.user_rating,
            notes=req.notes,
        )
    if not record_id:
        raise HTTPException(status_code=404, detail="Active session not found.")
    if req.user_rating and row:
        _log_outcome(row["media_item_id"], "rated", req.user_rating)
    if row:
        try:
            db.mark_watch_later_done(row["media_item_id"], "watched")
        except Exception as e:  # noqa: BLE001
            print(f"[WatchLater] could not mark {row['media_item_id']} done: {e}", flush=True)
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


# ---------------------------------------------------------------------------
# Watch later (shared list, schema v11)
# ---------------------------------------------------------------------------
@app.get("/api/watch-later")
async def get_watch_later(
    include_done: bool = False,
    media_type: Optional[str] = None,
    added_by: Optional[int] = None,
) -> Dict[str, Any]:
    """The shared list, one entry per title, newest add first."""
    items = await asyncio.to_thread(
        db.get_watch_later, include_done=include_done, media_type=media_type or None, added_by=added_by
    )
    return {"items": items, "count": len(items)}


@app.post("/api/watch-later")
async def add_watch_later(req: WatchLaterRequest) -> Dict[str, Any]:
    """Puts a title on the list for one person; returns the grouped entry."""
    try:
        return await asyncio.to_thread(db.add_to_watch_later, req.media_item_id, req.profile_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.delete("/api/watch-later/{media_item_id}")
async def remove_watch_later(media_item_id: int, profile_id: int) -> Dict[str, Any]:
    """Takes one person's add off the list."""
    removed = await asyncio.to_thread(db.remove_from_watch_later, media_item_id, profile_id)
    return {"removed": bool(removed)}


@app.post("/api/watch-later/{media_item_id}/done")
async def done_watch_later(media_item_id: int) -> Dict[str, Any]:
    """Marks a title on the list as watched (the Later tab's "Watched it")."""
    n = await asyncio.to_thread(db.mark_watch_later_done, media_item_id, "watched")
    return {"done": n}


@app.post("/api/watch-later/{media_item_id}/undo")
async def undo_watch_later(media_item_id: int) -> Dict[str, Any]:
    """Brings a title marked done back onto the list."""
    n = await asyncio.to_thread(db.undo_watch_later_done, media_item_id)
    return {"reopened": n}


@app.get("/api/profiles")
async def get_profiles() -> List[Dict[str, Any]]:
    """Lists the co-view rating profiles (Labiba and Gabriele) with live stats."""
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
    # Per-country, not per-region: a chip labelled "South Asian" that counts
    # only Indian titles (1,432) while Bangladesh sits separately at 645 was
    # actively misleading. Each country counts its own rows, so the chip
    # number always matches the chip name. "South Asian" stays reachable as a
    # phrase in the free-text query, where it correctly unions five countries.
    "IN": "🇮🇳 Indian",
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
    "BD": "🇧🇩 Bangladeshi",
    "PK": "🇵🇰 Pakistani",
    "LK": "🇱🇰 Sri Lankan",
    "NP": "🇳🇵 Nepali",
    "SA": "🇸🇦 Saudi",
    "TH": "🇹🇭 Thai",
    "ID": "🇮🇩 Indonesian",
    "PH": "🇵🇭 Filipino",
    "VN": "🇻🇳 Vietnamese",
    "GR": "🇬🇷 Greek",
    "SE": "🇸🇪 Swedish",
    "DK": "🇩🇰 Danish",
    "NO": "🇳🇴 Norwegian",
    "FI": "🇫🇮 Finnish",
    "NL": "🇳🇱 Dutch",
    "PL": "🇵🇱 Polish",
    "UA": "🇺🇦 Ukrainian",
    "AR": "🇦🇷 Argentine",
    "CO": "🇨🇴 Colombian",
    "CL": "🇨🇱 Chilean",
}


# Multi-country groupings offered as a single chip. The phrase written into
# the query is one parse_origin_intent() recognises, so these route through
# exactly the same code path as typing them.
ORIGIN_GROUPS: Dict[str, List[str]] = {
    "South Asian": ["IN", "BD", "PK", "LK", "NP"],
    "Korean": ["KR"],
    "East Asian": ["JP", "CN", "HK", "TW", "KR"],
    "Latin American": ["BR", "MX", "AR", "CO", "CL"],
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

    # Regional groupings first in the UI, counted from their member countries
    # so the number on the chip is the number of films it will actually return.
    groups = []
    for label, members in ORIGIN_GROUPS.items():
        total = sum(counts.get(c, 0) for c in members)
        if total >= 20:
            groups.append({"code": label, "name": label, "count": total,
                           "countries": list(members)})
    return {"origins": groups + labelled}


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
    _with_on_list(items)
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
            req.verdict,
            req.did_not_finish,
            req.worked,
            req.didnt,
            req.vibe_check,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if req.status == "seen":
        logged = vd.legacy_rating(req.verdict, req.did_not_finish) if req.verdict else req.user_rating
        if logged:
            _log_outcome(req.media_item_id, "rated", logged)
    return summary


@app.get("/api/ratings/vocab")
async def get_rating_vocab() -> Dict[str, Any]:
    """The verdicts and "why" chips the Rate card shows, from one place."""
    return {
        "verdicts": [{"id": k, **{"label": v["label"]}} for k, v in vd.VERDICTS.items()],
        "extreme": list(vd.EXTREME_VERDICTS),
        "chips": vd.CHIPS,
        "season_extra": vd.SEASON_DID_NOT_FINISH,
    }


@app.post("/api/ratings/season")
async def rate_season(req: SeasonRateRequest) -> Dict[str, Any]:
    """One optional per-season verdict on a TV show (upsert; empty clears it).

    A confirmed show verdict is never changed by this. Without one, the show
    gets a verdict derived from its seasons, marked inferred.
    """
    try:
        return await asyncio.to_thread(
            db.rate_season, req.media_item_id, req.profile_id, req.season_number, req.verdict, req.did_not_finish
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/api/ratings/details/{item_id}")
async def get_rating_details(item_id: int, profile_id: int = 1) -> Dict[str, Any]:
    """This person's verdict and answers for one title, plus the vibe-check
    questions to show for it."""
    details = await asyncio.to_thread(db.get_rating_details, item_id, profile_id)
    questions = await asyncio.to_thread(db.get_vibe_check_questions, item_id)
    seasons = await asyncio.to_thread(db.get_season_details, item_id, profile_id)
    return {"media_item_id": item_id, "profile_id": profile_id, "details": details, "questions": questions,
            "seasons": seasons}


@app.get("/api/ratings/pairs")
async def get_rating_pairs(
    profile_id: int = 1,
    media_type: str = "movie",
    limit: int = Query(default=10, ge=1, le=30),
    seed: Optional[int] = Query(default=None),
) -> Dict[str, Any]:
    """A "This or that" round: pairs of films this person has seen, same genre."""
    seed_value = seed if isinstance(seed, int) else None
    return await asyncio.to_thread(db.get_pair_round, profile_id, media_type, limit, seed_value)


class PairAnswer(BaseModel):
    profile_id: int = 1
    a_id: int
    b_id: int
    choice: str  # a | b | same


@app.post("/api/ratings/pairs")
async def answer_rating_pair(req: PairAnswer) -> Dict[str, Any]:
    """One "This or that" answer. Skip is client-side: nothing is stored."""
    try:
        return await asyncio.to_thread(db.record_pair_answer, req.profile_id, req.a_id, req.b_id, req.choice)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/ratings/inferred-queue")
async def get_inferred_rating_queue(
    profile_id: int = 1,
    media_type: str = "movie",
    limit: int = Query(default=100, ge=1, le=300),
) -> Dict[str, Any]:
    """"Sort out my 7s": old 1-10 ratings that only have a guessed verdict."""
    items = await asyncio.to_thread(db.get_inferred_rating_queue, profile_id, media_type, limit)
    for item in items:
        _with_co_view(item)
    _with_on_list(items)
    return {"profile_id": profile_id, "media_type": media_type, "items": items}


catalogue_search = CatalogueSearch(db)
nl_browse.catalogue = catalogue_search


def _with_co_view(item: Dict[str, Any]) -> Dict[str, Any]:
    """Adds both profiles' verdicts, the shape the Rate card already reads."""
    summary = db.get_rating_summary(item["id"])
    item["co_view"] = {
        "tier": summary["tier"],
        "tier_reason": summary["tier_reason"],
        "combined_score": summary["combined_score"],
        "disagreement": summary["disagreement"],
        "verdicts": summary["verdicts"],
    }
    return item


@app.get("/api/catalogue/search")
async def search_catalogue(
    q: str = Query(..., min_length=1, description="Title text; 2+ characters"),
    media_type: str = Query(default="all", description="movie | tv | all"),
    limit: int = Query(default=10, ge=1, le=25),
    profile_id: Optional[int] = Query(default=None, description="Adds this profile's own verdict"),
) -> Dict[str, Any]:
    """Search-to-rate (design doc §1): accent- and punctuation-insensitive title
    search over the catalogue, exact matches first, then by vote count."""
    mtype = media_type if isinstance(media_type, str) else "all"
    pid = profile_id if isinstance(profile_id, int) else None
    lim = limit if isinstance(limit, int) else 10
    items = await asyncio.to_thread(catalogue_search.search, q, mtype, lim, pid)
    return {"query": q, "items": _with_on_list([_with_co_view(it) for it in items])}


@app.get("/api/catalogue/tmdb-search")
async def search_tmdb(q: str = Query(..., min_length=2), media_type: str = "all") -> Dict[str, Any]:
    """Fallback when a title isn't in the catalogue. Needs TMDB_API_KEY + internet."""
    if not api_key:
        raise HTTPException(status_code=503, detail="TMDB_API_KEY is not set on the server.")
    try:
        items = await asyncio.to_thread(tmdb_search, api_key, db, q, media_type)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return {"query": q, "items": items}


@app.post("/api/catalogue/tmdb-import")
async def import_from_tmdb(req: TmdbImportRequest, profile_id: Optional[int] = None) -> Dict[str, Any]:
    """Adds one TMDB title to the catalogue and returns it ready to rate."""
    if not api_key:
        raise HTTPException(status_code=503, detail="TMDB_API_KEY is not set on the server.")
    try:
        item_id = await asyncio.to_thread(tmdb_import, api_key, db, req.media_type, req.tmdb_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    catalogue_search.invalidate()
    item = db.get_item_by_id(item_id) or {"id": item_id}
    item["in_library"] = bool(item.pop("sources", None))
    return _with_co_view(item)


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
        sort=req.sort,
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

        if req.movie_id and req.movie_id > 0:
            _log_outcome(req.movie_id, "downloaded")
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