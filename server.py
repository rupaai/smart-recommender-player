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
from urllib.parse import quote, urlsplit

import requests
import uvicorn
import yaml
from dotenv import load_dotenv
from fastapi import Body, FastAPI, Header, HTTPException, Query, Request
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
from services.nl_cast import (
    CastConfig,
    CastConfigError,
    CastTokens,
    cast_payload,
    secret_from_env,
    strip_param,
    token_values,
)
from services.nl_player_extras import empty_extras, player_extras
from services.nl_type import TypeChannels, TypingError, describe as describe_type_error, normalise_code
from services.nl_polish_b import NLExtras
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
from services.nl_picks import NLPicks, PicksError, parse_max_runtime
from services import nl_downloads
from services import nl_health
from services import nl_reactions
from services import nl_settings
from services import nl_together
from services.nl_choose import NLChoose, clean_tz
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
from services.catalogue_search import CatalogueSearch, _tmdb_get, tmdb_import, tmdb_search
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
download_requests = nl_downloads.DownloadRequests()   # M10a: what Retry needs; the magnet link never leaves the server

# Jellyfin library manager (idempotent library setup, refresh + verification)
jellyfin_service = JellyfinService(config)
nl_health_state = nl_health.Health(config, jellyfin_service, downloads_store)   # M10b: header status and admin
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

# Northern Lights casting (M8): where the Chromecast reaches this app and the signed stream tokens.
# NL_CAST_SECRET signs the tokens; without it a random secret lives in memory only (tokens die on restart).
try:
    nl_cast = CastConfig.from_config(config)
except CastConfigError as _e:
    raise SystemExit(f"[Northern Lights] bad cast configuration: {_e}")
nl_cast_tokens = CastTokens(secret_from_env())

# Northern Lights phone keyboard for TV search (M8b): short-lived codes held in memory only.
nl_type = TypeChannels()

# Northern Lights picks for two (M6): watchlist, yes / maybe / pass, and the "For both of us" row.
nl_picks = NLPicks(db, nl_browse, recommender_service, lambda: nl_people)

# Northern Lights choosing for two (M9b): Choose for us, vetoes, the rotating chooser. `northern_lights.timezone`
# (an IANA name like Europe/London) is only the fallback for a browser that sends no UTC offset.
nl_choose = NLChoose(
    db, nl_picks, lambda: nl_people, MOOD_MAPPINGS,
    tz_name=str(((config.get("northern_lights") or {}).get("timezone")) or "") or None,
)


# Northern Lights browse polish (M7b): cast, trailer, tech details and "More like this". The TMDB key is used
# here only; the pages get a YouTube key and library titles, never the key. No key = TMDB parts stay empty.
def _nl_tmdb(path: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    return _tmdb_get(api_key, path, params) if api_key else None


nl_extras = NLExtras(db, nl_browse, _nl_tmdb, lambda: recommender_service.vibe_index)

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
    max_kbps: Optional[int] = Query(None, ge=0, le=200000, description="Quality for this viewing: 8000, 4000, 2000 or 1000 kbit/s; 0 = Auto; absent = the person's default"),
) -> Dict[str, Any]:
    """How to play one movie or episode: a proxied stream URL and its kind (file or hls),
    its audio and subtitle tracks, and where the viewer left off. For the viewer (who's watching):
    default tracks from their language prefs, and Resume from their own progress (the further of the
    two for "Both of us", with a note when they are in different places)."""
    return await _nl_play(request, item_id, audio, subtitle, start, caps, source, client, max_kbps)


async def _nl_play(
    request: Request,
    item_id: int,
    audio: Optional[int],
    subtitle: Optional[int],
    start: float,
    caps: Optional[str],
    source: Optional[int],
    client: Optional[str],
    max_kbps: Optional[int] = None,
) -> Dict[str, Any]:
    viewer = _nl_viewer_or_default(request)
    prefs = _nl_effective_prefs(viewer)
    prefer = prefs.public()
    # M10c, P19: this viewing's quality (the Picture menu) wins, else the person's default; 0 = Auto.
    if max_kbps is None:
        quality = prefs.quality_kbps
    elif max_kbps == 0:
        quality = None
    elif nl_settings.valid_quality(max_kbps):
        quality = max_kbps
    else:
        raise HTTPException(status_code=400, detail="Quality must be 8000, 4000, 2000 or 1000 (kbit/s), or 0 for Auto.")
    if client == "cast":
        quality = None   # the Chromecast has its own profile ceiling
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
            _as_user, viewer.jellyfin_users[0], _stream_for_viewer, item, audio, subtitle, start, profile, prefer,
            quality, "This TV" if client == "tv" else ("This Chromecast" if client == "cast" else "This browser"),
        )
        states: Dict[str, Dict[str, Any]] = {}
        if nl_people.configured and info.get("jellyfin_id"):
            states = await asyncio.to_thread(_apply_resume_rule, viewer, info)
    except ItemNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except JellyfinUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    except NotPlayable as e:
        raise HTTPException(status_code=415, detail=str(e))
    info["quality"] = {**(info.get("quality") or {}), "default_kbps": prefs.quality_kbps, "options": nl_settings.quality_options()}
    info["media_item_id"] = item_id
    info["media_type"] = item.get("media_type") or "movie"
    info["source_id"] = chosen["id"]
    info["together_prompt"] = await asyncio.to_thread(_pact_prompt_for, viewer, item_id, item, chosen, states)
    return info


def _pact_prompt_for(viewer: Viewer, item_id: int, item: Dict[str, Any], chosen: Dict[str, Any], states) -> Optional[Dict[str, Any]]:
    """U5: the "You said you'd watch this together" question for this play, or None."""
    if item.get("media_type") != "tv" or not nl_together.together_possible(nl_people.people) or viewer.is_both:
        return None
    if not db.get_pact(item_id):
        return None
    return nl_together.pact_prompt(
        viewer, nl_people.people, states or {}, True, chosen["id"] in db.get_seen_together(item_id)
    )


@app.get("/api/nl/cast/config")
async def nl_cast_config() -> Dict[str, Any]:
    """Whether casting is set up (cast_base_url configured) and which receiver app the sender launches."""
    return nl_cast.public()


@app.get("/api/nl/cast/play/{item_id}")
async def nl_cast_play(
    request: Request,
    item_id: int,
    audio: Optional[int] = Query(None, ge=0, description="Jellyfin audio stream index"),
    subtitle: Optional[int] = Query(None, ge=-1, description="Jellyfin subtitle stream index to burn in; -1 = none"),
    start: float = Query(0.0, ge=0, description="Start position in seconds"),
    source: Optional[int] = Query(None, ge=1, description="media_sources.id: which episode of a show"),
) -> Dict[str, Any]:
    """/api/nl/play for a Chromecast: the cast profile (H.264/AAC), every URL absolute on the LAN
    (northern_lights.cast_base_url) and carrying a 6-hour token for this one item (?t=), so the
    Chromecast needs no cookies. Adds a `cast` block (content type, backdrop, poster). No Jellyfin key."""
    if not nl_cast.enabled:
        raise HTTPException(
            status_code=503,
            detail="Casting isn't set up: set northern_lights.cast_base_url to this server's LAN address.",
        )
    info = await _nl_play(request, item_id, audio, subtitle, start, None, source, "cast")
    return cast_payload(info, nl_cast, nl_cast_tokens)


def _stream_for_viewer(item, audio, subtitle, start, profile, prefer, max_kbps=None, device="This browser"):
    if prefer and not (prefer.get("audio_lang") or prefer.get("subtitles_on")):
        prefer = None
    if prefer:
        return jellyfin_playback.stream_for(item, audio, subtitle, start, profile, prefer=prefer, max_kbps=max_kbps, device=device)
    return jellyfin_playback.stream_for(item, audio, subtitle, start, profile, max_kbps=max_kbps, device=device)


def _apply_resume_rule(viewer: Viewer, info: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Resume from the viewer's own progress; for Both the further of the two. Adds `resume_note`
    ("You're at 42:10, Sam is at 38:00") when they differ by more than two minutes. Jellyfin trouble
    for the other person never blocks playback. Returns each person's {position, played} (the pact question
    reads it too)."""
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
    return states


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
    # M8: a cast stream token (?t=) narrows the request to the one item the token was made for. A bad,
    # expired, forged, or mismatched token is a 403; the token never goes on to Jellyfin.
    cast_token: Optional[str] = None
    tokens = token_values(request.url.query)
    if tokens:
        ok, _why = (False, "") if len(tokens) != 1 else nl_cast_tokens.check(tokens[0], request.method, clean)
        if not ok:
            raise HTTPException(status_code=403, detail="This link isn't valid.")
        cast_token = tokens[0]
    jf = jellyfin_service
    if not jf.configured:
        raise HTTPException(status_code=503, detail="Jellyfin is not configured.")

    upstream_url = f"{jf.base_url}/{quote(clean, safe='/')}"
    query = strip_api_key("?" + request.url.query)[1:] if request.url.query else ""
    if cast_token:
        query = strip_param(query, "t")
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
        text = rewrite_playlist(body.decode("utf-8", errors="replace"), cast_token)
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
    # M9: which title this is (the page knows), the length and whether the player reached the end.
    media_item_id: Optional[int] = Field(None, ge=1)
    source_id: Optional[int] = Field(None, ge=1)
    duration_seconds: Optional[float] = Field(None, ge=0)
    ended: bool = False


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
    # M9a: "Both of us" finishing a title is the one thing that marks it as seen together.
    if (
        req.event == "stop" and req.media_item_id and viewer.is_both and nl_together.together_possible(nl_people.people)
        and nl_together.is_finished(req.position_seconds, req.duration_seconds, req.ended)
    ):
        await asyncio.to_thread(db.mark_seen_together, req.media_item_id, req.source_id)
    return {"success": True}


class NLReactBody(BaseModel):
    reaction: str


def _react_profiles(viewer: Viewer) -> List[int]:
    return nl_reactions.profiles_to_rate(viewer.profile_ids, viewer.implicit)


@app.get("/api/nl/react/{item_id}")
async def nl_react_get(item_id: int, request: Request) -> Dict[str, Any]:
    """W10: should the player ask "How was it?" now. `ask` is false for an unknown title, a viewer with no
    profile, or when everyone at the screen already gave this title a verdict of their own."""
    viewer = _nl_viewer_or_default(request)

    def work() -> Dict[str, Any]:
        item = db.get_item_by_id(item_id)
        profiles = _react_profiles(viewer)
        asked = bool(item and profiles) and not all(
            nl_reactions.has_own_verdict(db.get_rating_details(item_id, pid)) for pid in profiles
        )
        return {
            "ask": asked,
            "text": nl_reactions.question(item["title"], viewer.is_both) if item else "",
            "options": [{"id": r["id"], "label": r["label"]} for r in nl_reactions.REACTIONS],
        }

    return await asyncio.to_thread(work)


@app.post("/api/nl/react/{item_id}")
async def nl_react_post(item_id: int, body: NLReactBody, request: Request) -> Dict[str, Any]:
    """W10: records the answer as a verdict for the profile(s) at the screen ("Both of us" rates for both)."""
    viewer = _nl_viewer_or_default(request)
    verdict = nl_reactions.verdict_for(body.reaction)
    if verdict is None:
        raise HTTPException(status_code=400, detail="Unknown reaction.")
    profiles = _react_profiles(viewer)
    if not profiles:
        raise HTTPException(status_code=400, detail="No profile to rate for.")

    def work() -> bool:
        if not db.get_item_by_id(item_id):
            return False
        for pid in profiles:
            db.rate_item(item_id, profile_id=pid, status="seen", verdict=verdict)
        return True

    if not await asyncio.to_thread(work):
        raise HTTPException(status_code=404, detail="That title isn't in the library.")
    return {"success": True, "verdict": verdict, "profiles": len(profiles)}


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
async def nl_library(
    request: Request,
    type: str = Query("movie", pattern="^(movie|tv)$"),
    unwatched: bool = Query(False, description="only titles nobody at the screen has watched"),
) -> Dict[str, Any]:
    """Cards for every movie or show that has a file. Movies partly watched carry a progress
    bar, the latest additions a "New" badge. `unwatched` (M7b) keeps the titles nobody at the screen has
    watched; `unwatched_applied` says whether Jellyfin could tell. Works with Jellyfin down (no progress then)."""
    viewer = _nl_viewer_or_default(request)
    return await asyncio.to_thread(_as_user, viewer.jellyfin_users[0], nl_browse.library_page, type, viewer.jellyfin_users, unwatched)


@app.get("/api/nl/home")
async def nl_home_data(request: Request) -> Dict[str, Any]:
    """Home: Continue Watching (Jellyfin Resume of the viewer), Recently Added, Recently Watched and the
    newest movies and shows. Titles this viewer hid (M7b) are left out of every row except Continue Watching."""
    viewer = _nl_viewer_or_default(request)
    hidden = await asyncio.to_thread(db.get_hidden_ids, viewer.key)
    return await asyncio.to_thread(_as_user, viewer.jellyfin_users[0], nl_browse.home, viewer.jellyfin_users, hidden)


class NLHiddenBody(BaseModel):
    hidden: bool


@app.get("/api/nl/hidden/{item_id}")
async def nl_hidden_get(item_id: int, request: Request) -> Dict[str, Any]:
    """Has this viewer hidden the title from the home rows?"""
    viewer = _nl_viewer_or_default(request)
    return {"hidden": item_id in await asyncio.to_thread(db.get_hidden_ids, viewer.key)}


@app.put("/api/nl/hidden/{item_id}")
async def nl_hidden_set(item_id: int, body: NLHiddenBody, request: Request) -> Dict[str, Any]:
    """"Hide from home" / "Show on home again" for the person at the screen ("Both of us" has its own list)."""
    viewer = _nl_viewer_or_default(request)
    try:
        value = await asyncio.to_thread(db.set_hidden, viewer.key, item_id, body.hidden)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"hidden": value}


@app.get("/api/nl/title/{item_id}")
async def nl_title_data(item_id: int, request: Request) -> Dict[str, Any]:
    viewer = _nl_viewer_or_default(request)
    data = await asyncio.to_thread(_as_user, viewer.jellyfin_users[0], nl_browse.title, item_id, viewer.jellyfin_users)
    if not data:
        raise HTTPException(status_code=404, detail="That title isn't in the library.")
    data["hidden"] = item_id in await asyncio.to_thread(db.get_hidden_ids, viewer.key)
    together = nl_together.together_possible(nl_people.people)
    data["together"] = bool(together and data.get("media_type") != "tv" and 0 in await asyncio.to_thread(db.get_seen_together, item_id))
    return data


@app.get("/api/nl/title/{item_id}/extras")
async def nl_title_extras(item_id: int, request: Request) -> Dict[str, Any]:
    """Cast & crew, the Trailer (a YouTube key + address, never a TMDB or Jellyfin key) and Tech details.
    Each part is just empty when its source (Jellyfin, TMDB) is away; only an unknown title is an error."""
    viewer = _nl_viewer_or_default(request)
    data = await asyncio.to_thread(_as_user, viewer.jellyfin_users[0], nl_extras.title_extras, item_id)
    if not data:
        raise HTTPException(status_code=404, detail="That title isn't in the library.")
    return data


@app.get("/api/nl/title/{item_id}/more")
async def nl_title_more(item_id: int, request: Request) -> Dict[str, Any]:
    """"More like this": library titles close to this one (TMDB similar, the recommender's vibe index, shared
    genres). `items` may be empty; it degrades part by part and never fails for a missing source."""
    viewer = _nl_viewer_or_default(request)
    data = await asyncio.to_thread(_as_user, viewer.jellyfin_users[0], nl_extras.more_like_this, item_id)
    if data is None:
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
    await asyncio.to_thread(_add_together_to_show, data, item_id, viewer)
    data["autoplay"] = await asyncio.to_thread(_autoplay_for, item_id, viewer, data.get("autoplay"))
    return data


def _add_together_to_show(data: Dict[str, Any], item_id: int, viewer: Viewer) -> None:
    """M9a: the pact switch, the episodes seen together and (for the person-specific "Mark unwatched")
    who can be reset. With fewer than two people configured none of it is offered."""
    possible = nl_together.together_possible(nl_people.people)
    seen = db.get_seen_together(item_id) if possible else set()
    for season in data.get("seasons") or []:
        for ep in season.get("episodes") or []:
            ep["together"] = ep["source_id"] in seen
    data["together_count"] = len(seen)
    data["pact"] = {"available": possible, "together": bool(possible and db.get_pact(item_id))}
    data["unwatch_people"] = _unwatch_choices(viewer)
    data["hide_spoilers"] = bool(_nl_prefs_row(viewer).get("hide_spoilers"))   # M9c


def _unwatch_choices(viewer: Viewer) -> List[Dict[str, str]]:
    """Whom "Mark unwatched" can reset from this screen: the one person, or each of the two on "Both of us".
    Empty list = the implicit single viewer (no person to name)."""
    return [{"id": p.id, "name": p.name} for p in viewer.people]


class NLPactBody(BaseModel):
    together: bool


@app.get("/api/nl/show/{item_id}/pact")
async def nl_show_pact_get(item_id: int) -> Dict[str, Any]:
    """Is "We watch this together" on for this show? Only offered with two or more people configured."""
    possible = nl_together.together_possible(nl_people.people)
    return {"available": possible, "together": bool(possible and await asyncio.to_thread(db.get_pact, item_id))}


@app.put("/api/nl/show/{item_id}/pact")
async def nl_show_pact_set(item_id: int, body: NLPactBody) -> Dict[str, Any]:
    """Switches the watch-together pact for a show on or off (it belongs to the pair, not to one person)."""
    if not nl_together.together_possible(nl_people.people):
        raise HTTPException(status_code=400, detail="Watching together needs two people in the config.")
    try:
        value = await asyncio.to_thread(db.set_pact, item_id, body.together)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"available": True, "together": value}


class NLUnwatchBody(BaseModel):
    source_id: Optional[int] = None   # which episode of a show
    person: Optional[str] = None      # whose data; needed on "Both of us"


@app.post("/api/nl/unwatch/{item_id}")
async def nl_unwatch(item_id: int, body: NLUnwatchBody, request: Request) -> Dict[str, Any]:
    """U6, "Mark unwatched for me": resets the played flag and resume position of ONE person's Jellyfin
    user for an episode or movie. On "Both of us" `person` says whose; nobody else's data is touched. The
    together mark of that title goes too, since it is no longer seen by both."""
    viewer = _nl_viewer_or_default(request)
    if viewer.implicit:
        person_user: Optional[str] = None
        person_id = None
    else:
        wanted = (body.person or "").strip().lower() or (viewer.primary.id if not viewer.is_both else "")
        if not wanted:
            raise HTTPException(status_code=400, detail="Say whose watched state to reset.")
        person = next((p for p in viewer.people if p.id == wanted), None)
        if person is None:
            raise HTTPException(status_code=403 if nl_people.get(wanted) else 400, detail="That person isn't at the screen.")
        person_user, person_id = person.jellyfin_user_id, person.id
    item = _nl_subtitle_item(item_id, body.source_id)
    try:
        jf_id = await asyncio.to_thread(jellyfin_playback._subtitle_item_id, item)
        await asyncio.to_thread(_as_user, person_user, jellyfin_playback.reset_user_data, jf_id)
    except ItemNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except JellyfinUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    await asyncio.to_thread(db.clear_seen_together, item_id, item["sources"][0]["id"] if item.get("media_type") == "tv" else 0)
    return {"success": True, "person": person_id}


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


class NLSkipIntro(BaseModel):
    skip_intro: bool


@app.get("/api/nl/show/{item_id}/skip-intro")
async def nl_show_skip_intro_get(item_id: int, request: Request) -> Dict[str, Any]:
    """Does this viewer always skip this show's intro? (per viewer, off by default)"""
    viewer = _nl_viewer_or_default(request)
    return {"skip_intro": await asyncio.to_thread(db.get_show_skip_intro, item_id, viewer.key)}


@app.put("/api/nl/show/{item_id}/skip-intro")
async def nl_show_skip_intro_set(item_id: int, body: NLSkipIntro, request: Request) -> Dict[str, Any]:
    """The per-show "always skip intro" switch of the person at the screen (Both of us has its own)."""
    viewer = _nl_viewer_or_default(request)
    try:
        value = await asyncio.to_thread(db.set_show_skip_intro, item_id, viewer.key, body.skip_intro)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"skip_intro": value}


def _player_extras_for(item: Dict[str, Any]) -> Dict[str, Any]:
    """Trickplay, chapters and skip segments of one title or episode, read from Jellyfin here. Any
    Jellyfin trouble leaves the parts empty: these features simply don't show."""
    try:
        jf_id = jellyfin_playback._subtitle_item_id(item)
    except (ItemNotFound, JellyfinUnavailable):
        return empty_extras()
    return player_extras(jellyfin_playback, jf_id)


@app.get("/api/nl/player-extras/{item_id}")
async def nl_player_extras(
    request: Request,
    item_id: int,
    source: Optional[int] = Query(None, ge=1, description="media_sources.id: which episode of a show"),
) -> Dict[str, Any]:
    """What the player draws over the picture, all read here (the Jellyfin key never leaves):
    `trickplay` (seek-bar thumbnail sheets: geometry + a /nl/jf/ URL template, or null), `chapters`
    ([{start_seconds, name}]) and `segments` ([{type intro|outro, start_seconds, end_seconds}], from
    Jellyfin's MediaSegments, e.g. the Intro Skipper plugin). `skip_intro`: this viewer's switch for the show.
    Jellyfin down, an old Jellyfin or a missing plugin only leave the parts empty; never an error."""
    viewer = _nl_viewer_or_default(request)
    item = _nl_subtitle_item(item_id, source)
    extras = await asyncio.to_thread(_as_user, viewer.jellyfin_users[0], _player_extras_for, item)
    is_show = item.get("media_type") == "tv"
    extras["skip_intro"] = bool(is_show and await asyncio.to_thread(db.get_show_skip_intro, item_id, viewer.key))
    extras["is_show"] = is_show
    return extras


@app.get("/api/nl/next/{item_id}")
async def nl_next_episode(request: Request, item_id: int, source: int = Query(..., ge=1)) -> Dict[str, Any]:
    """The episode after `source` for the player's next-episode card (has_next false at the end)."""
    viewer = _nl_viewer_or_default(request)
    data = await asyncio.to_thread(_as_user, viewer.jellyfin_users[0], nl_browse.next_episode, item_id, source)
    if isinstance(data, dict) and "autoplay" in data:
        data["autoplay"] = await asyncio.to_thread(_autoplay_for, item_id, viewer, data["autoplay"])
    return data


def _autoplay_for(item_id: int, viewer: Viewer, fallback: Any) -> bool:
    """Start the next episode by itself? The show's own switch if anyone ever set it, else this viewer's default
    (M10c, H5; on unless they turned it off)."""
    own = db.get_show_autoplay_row(item_id)
    if own is not None:
        return own
    return bool(_nl_effective_prefs(viewer).autoplay)


@app.get("/api/nl/search")
async def nl_search(request: Request, q: str = Query("", max_length=100), limit: int = Query(20, ge=1, le=40)) -> Dict[str, Any]:
    """Movies, shows and episodes matching the text. Each result is Ready (in the library,
    with a link) or Not in library yet (with a link to the Download tab of the main app)."""
    viewer = _nl_viewer_or_default(request)
    results = await asyncio.to_thread(_as_user, viewer.jellyfin_users[0], nl_browse.search, q, limit)
    return {"query": q, "results": results}


# --- Northern Lights picks for two (M6) ---
class NLWatchlistAdd(BaseModel):
    media_item_id: int
    scope: str = "shared"   # "shared" (default) or "me" (Just me)


class NLInterestBody(BaseModel):
    state: Optional[str] = None   # yes | maybe | pass | null (clear)
    person: Optional[str] = None  # whose answer; needed on "Both of us"


def _picks_error(e: PicksError) -> HTTPException:
    return HTTPException(status_code=e.status, detail=str(e))


@app.get("/api/nl/watchlist")
async def nl_watchlist(request: Request, max_runtime: Optional[str] = None) -> Dict[str, Any]:
    """The watchlist for the viewer: titles both said yes to first, then one yes, then the rest, passed
    last. `max_runtime` (minutes, 90 or 120 in the chips) hides longer movies and shows."""
    viewer = _nl_viewer_or_default(request)
    return await asyncio.to_thread(nl_picks.watchlist, viewer, parse_max_runtime(max_runtime))


@app.get("/api/nl/watchlist/state")
async def nl_watchlist_state(request: Request) -> Dict[str, Any]:
    """What a card needs to paint its watchlist button and yes / maybe / pass: listed titles and each
    person's answers, plus who the people are and whether "Just me" is offered."""
    viewer = _nl_viewer_or_default(request)
    return await asyncio.to_thread(nl_picks.state, viewer)


@app.post("/api/nl/watchlist")
async def nl_watchlist_add(body: NLWatchlistAdd, request: Request) -> Dict[str, Any]:
    """Adds a title: shared by default (credited to everyone at the screen), or scope "me" for Just me."""
    viewer = _nl_viewer_or_default(request)
    try:
        return await asyncio.to_thread(nl_picks.add, viewer, body.media_item_id, body.scope)
    except PicksError as e:
        raise _picks_error(e) from e


@app.delete("/api/nl/watchlist/{item_id}")
async def nl_watchlist_remove(item_id: int, request: Request) -> Dict[str, Any]:
    """Takes a title off the watchlist (the shared list for both, plus the viewer's own Just me)."""
    viewer = _nl_viewer_or_default(request)
    return await asyncio.to_thread(nl_picks.remove, viewer, item_id)


@app.put("/api/nl/interest/{item_id}")
async def nl_interest_set(item_id: int, body: NLInterestBody, request: Request) -> Dict[str, Any]:
    """One person's yes / maybe / pass on a title (state null clears it)."""
    viewer = _nl_viewer_or_default(request)
    try:
        return await asyncio.to_thread(nl_picks.set_interest, viewer, item_id, body.state, body.person)
    except PicksError as e:
        raise _picks_error(e) from e


@app.get("/api/nl/picks")
async def nl_picks_row(request: Request, max_runtime: Optional[str] = None) -> Dict[str, Any]:
    """The home row "For both of us" ("Picks for you" with one person): the recommender in co-view mode
    for the viewer(s), library titles only, with a one-line reason per card."""
    viewer = _nl_viewer_or_default(request)
    return await asyncio.to_thread(nl_picks.picks, viewer, parse_max_runtime(max_runtime))


# --- Northern Lights choosing for two (M9b) ---
class NLChooseBody(BaseModel):
    mood: Optional[int] = None
    genre: Optional[str] = Field(None, max_length=40)
    max_minutes: Optional[int] = None
    exclude: List[int] = Field(default_factory=list, max_length=60)
    mode: str = "pick"             # "pick" or "yeses" (a random title both said yes to)
    tz: Optional[int] = None       # the browser's UTC offset in minutes east of UTC


class NLVetoBody(BaseModel):
    kind: str                      # "tonight" or "never"
    person: Optional[str] = None   # whose "never"; needed on "Both of us"
    tz: Optional[int] = None


@app.get("/api/nl/choose/options")
async def nl_choose_options(request: Request, tz: Optional[str] = None) -> Dict[str, Any]:
    """What Choose for us offers: the recommender's moods, the shelf's genres, the length chips, what today's
    weekday and time of day suggest, whose turn it is to choose, and how many titles both said yes to."""
    viewer = _nl_viewer_or_default(request)
    return await asyncio.to_thread(nl_choose.options, viewer, clean_tz(tz))


@app.post("/api/nl/choose")
async def nl_choose_pick(body: NLChooseBody, request: Request) -> Dict[str, Any]:
    """One big pick for the viewer(s). Wishes (mood, genre, length) filter; the weekday and time of day fill
    in a mood and length when none was chosen; `exclude` is what "Another" has already shown; mode "yeses" is a
    random title both said yes to. `item` is null with a `note` when nothing fits."""
    viewer = _nl_viewer_or_default(request)
    try:
        return await asyncio.to_thread(
            nl_choose.choose, viewer, body.mood, body.genre, body.max_minutes, body.exclude, body.mode, clean_tz(body.tz)
        )
    except PicksError as e:
        raise _picks_error(e) from e


@app.post("/api/nl/choose/chosen/{item_id}")
async def nl_choose_chosen(item_id: int, request: Request) -> Dict[str, Any]:
    """The pick was played: remember who chose, so the next time says "Your turn, <the other person>"."""
    viewer = _nl_viewer_or_default(request)
    try:
        return await asyncio.to_thread(nl_choose.chosen, viewer, item_id)
    except PicksError as e:
        raise _picks_error(e) from e


@app.get("/api/nl/vetoes")
async def nl_vetoes_list(request: Request) -> Dict[str, Any]:
    """The titles off the table for this screen: "Not tonight" (until tomorrow 05:00) and each person's "Never"."""
    viewer = _nl_viewer_or_default(request)
    return await asyncio.to_thread(nl_choose.vetoes, viewer)


@app.post("/api/nl/veto/{item_id}")
async def nl_veto_set(item_id: int, body: NLVetoBody, request: Request) -> Dict[str, Any]:
    """"Not tonight" hides a title until tomorrow 05:00 local; "Never" hides it for that person. Both leave it
    out of Choose for us and the home row; the library, search and watchlist still show it."""
    viewer = _nl_viewer_or_default(request)
    try:
        return await asyncio.to_thread(nl_choose.veto, viewer, item_id, body.kind, body.person, clean_tz(body.tz))
    except PicksError as e:
        raise _picks_error(e) from e


@app.delete("/api/nl/veto/{item_id}")
async def nl_veto_clear(item_id: int, request: Request, kind: str = Query("never"), person: Optional[str] = None) -> Dict[str, Any]:
    """Bring a title back (undo "Not tonight" or "Never")."""
    viewer = _nl_viewer_or_default(request)
    try:
        return await asyncio.to_thread(nl_choose.unveto, viewer, item_id, kind, person)
    except PicksError as e:
        raise _picks_error(e) from e


# --- Northern Lights downloads (M10a): the same downloader and store as the old Download tab ---
def _nl_downloads_payload() -> Dict[str, Any]:
    snapshot = dict(downloads_store)
    briefs = db.get_download_briefs([i for i in snapshot if isinstance(i, int)])
    return nl_downloads.build_list(
        snapshot,
        download_requests,
        lambda i: briefs.get(i),
        lambda i: bool((briefs.get(i) or {}).get("has_file")),
    )


@app.get("/api/nl/downloads")
async def nl_downloads_list() -> Dict[str, Any]:
    """Every download the downloader knows: queued, downloading (speed and time left), importing, ready
    (with a Play link) or failed (with a plain-words reason and Retry). No magnet links, no file paths."""
    return await asyncio.to_thread(_nl_downloads_payload)


@app.post("/api/nl/downloads/{item_id}/retry")
async def nl_downloads_retry(item_id: int) -> Dict[str, Any]:
    """Starts a failed or stopped download again from the link it was first started with."""
    entry = downloads_store.get(item_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="That download isn't in the list any more.")
    if nl_downloads.state_of(entry) not in nl_downloads.RETRY_STATES:
        raise HTTPException(status_code=409, detail="That download is not stopped, so there is nothing to retry.")
    req = download_requests.get(item_id)
    if req is None:
        raise HTTPException(
            status_code=404,
            detail="The app was restarted since this started, so it no longer has the link. Search for the title again.",
        )
    downloads_store[item_id] = {"movie_id": item_id, "status": "connecting", "progress": 0.0, "error": None,
                                "name": req["title"], "media_type": req["media_type"]}
    asyncio.create_task(
        torrent_downloader.download(req["magnet"], item_id, req["title"], media_type=req["media_type"])
    )
    return {"success": True, "id": item_id}


@app.delete("/api/nl/downloads/{item_id}")
async def nl_downloads_dismiss(item_id: int) -> Dict[str, Any]:
    """Clears a failed, stopped or finished download from the list (the record only; files are never touched)."""
    entry = downloads_store.get(item_id)
    if entry is None:
        return {"success": True}
    if nl_downloads.state_of(entry) not in nl_downloads.DISMISS_STATES:
        raise HTTPException(status_code=409, detail="That download is still running.")
    downloads_store.pop(item_id, None)
    download_requests.forget(item_id)
    return {"success": True}


# --- Northern Lights health and admin (M10b): only what the existing status endpoints already show ---
def _host_only(url: Optional[str]) -> Optional[str]:
    """Jellyfin's address as host:port, with no scheme, user name or path."""
    try:
        parts = urlsplit(url or "")
        if not parts.hostname:
            return None
        return parts.hostname + (f":{parts.port}" if parts.port else "")
    except ValueError:
        return None


def _nl_admin_payload() -> Dict[str, Any]:
    diag = jellyfin_service.diagnostics()
    disk = dict(nl_health_state.disk_state())
    disk["media_used_bytes"] = nl_health_state.media_used()
    return {
        "health": nl_health_state.status(),
        "jellyfin": {
            "configured": bool(diag.get("configured")),
            "server_online": bool(diag.get("server_online")),
            "host": _host_only(diag.get("base_url")),
            "settings_present": {k: bool(v) for k, v in (diag.get("env") or {}).items()},   # yes / no only, never the values
            "realtime_monitor": diag.get("realtime_monitor"),
            "libraries": [
                {"name": lib.get("name"), "type": lib.get("collection_type"), "locations": lib.get("locations") or [],
                 "realtime_monitor": lib.get("realtime_monitor")}
                for lib in (diag.get("libraries") or [])
            ],
        },
        "disk": disk,
        "tools": nl_health.tool_links(config),
    }


@app.get("/api/nl/health")
async def nl_health_status() -> Dict[str, Any]:
    """The compact header status: Jellyfin reachable, free disk space, download queue size. Cheap: checks are
    cached for a few seconds. Carries no address, key or path."""
    return await asyncio.to_thread(nl_health_state.status)


@app.get("/api/nl/admin")
async def nl_admin_data() -> Dict[str, Any]:
    """The admin screen's data: the status above, Jellyfin's diagnostics (libraries and where they point),
    disk use against the limits, and the Jellystat / Uptime Kuma / Dozzle links that are configured."""
    return await asyncio.to_thread(_nl_admin_payload)


@app.post("/api/nl/admin/repair")
async def nl_admin_repair() -> Dict[str, Any]:
    """Library repair (the same action as POST /api/jellyfin/repair), answered with a short summary only."""
    report = await asyncio.to_thread(jellyfin_service.repair_libraries)
    nl_health_state.forget()
    return {
        "ok": not report.get("error") and all(
            lib.get("result") in ("already-attached", "created", "path-added", "recreated") for lib in report.get("libraries", [])
        ),
        "error": report.get("error"),
        "libraries": [
            {"library": lib.get("library"), "result": lib.get("result") or lib.get("error"), "hint": lib.get("hint")}
            for lib in report.get("libraries", [])
        ],
        "refresh_triggered": bool(report.get("refresh_triggered")),
    }


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


@app.get("/nl/watchlist", response_class=HTMLResponse)
async def nl_watchlist_page(request: Request) -> Any:
    return _nl_page(request, "nl/watchlist.html", {"active": "watchlist"})


@app.get("/nl/choose", response_class=HTMLResponse)
async def nl_choose_page(request: Request) -> Any:
    return _nl_page(request, "nl/choose.html", {"active": "choose"})


@app.get("/nl/downloads", response_class=HTMLResponse)
async def nl_downloads_page(request: Request) -> Any:
    return _nl_page(request, "nl/downloads.html", {"active": "downloads"})


@app.get("/nl/admin", response_class=HTMLResponse)
async def nl_admin_page(request: Request) -> Any:
    """Web only: there is deliberately no TV version and no link from the TV pages."""
    return _nl_page(request, "nl/admin.html", {"active": "admin"})


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


@app.get("/nl/tv/watchlist", response_class=HTMLResponse)
async def nl_tv_watchlist(request: Request) -> Any:
    return _nl_page(request, "nl/tv/watchlist.html", {"page": "watchlist"}, tv=True)


@app.get("/nl/tv/choose", response_class=HTMLResponse)
async def nl_tv_choose(request: Request) -> Any:
    return _nl_page(request, "nl/tv/choose.html", {"page": "choose"}, tv=True)


@app.get("/nl/tv/downloads", response_class=HTMLResponse)
async def nl_tv_downloads(request: Request) -> Any:
    return _nl_page(request, "nl/tv/downloads.html", {"page": "downloads"}, tv=True)


@app.get("/nl/tv/me", response_class=HTMLResponse)
async def nl_tv_me(request: Request) -> Any:
    return _nl_page(request, "nl/tv/me.html", {"page": "me"}, tv=True)


# --- Northern Lights phone keyboard for TV search (M8b) ---
class NLTypeSession(BaseModel):
    previous_key: Optional[str] = Field(None, max_length=64)


class NLTypeText(BaseModel):
    text: str = Field("", max_length=1000)   # the real limit (100 characters) is applied by services/nl_type.py
    submit: bool = False


def _client_address(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _type_error(e: TypingError) -> JSONResponse:
    status, body = describe_type_error(e)
    headers = {"Retry-After": str(body["retry_after"])} if "retry_after" in body else None
    return JSONResponse(status_code=status, content=body, headers=headers)


@app.post("/api/nl/type/session")
async def nl_type_session(request: Request, body: NLTypeSession = Body(default_factory=NLTypeSession)) -> Any:
    """The TV search page asks for a typing code. Returns {code, key, expires_in}: the code goes on the screen (and in
    the QR), the key stays on the TV (it is how only that TV reads what the phone types). With the old key the old
    session is replaced: one session per TV."""
    try:
        return nl_type.create(_client_address(request), body.previous_key)
    except TypingError as e:
        return _type_error(e)


@app.get("/api/nl/type/{code}/poll")
async def nl_type_poll(
    code: str, request: Request, after: int = Query(0, ge=0), x_nl_type_key: Optional[str] = Header(None),
) -> Any:
    """The TV reads the latest phone text. Needs the session key (header X-NL-Type-Key)."""
    try:
        return nl_type.poll(code, x_nl_type_key, _client_address(request), after)
    except TypingError as e:
        return _type_error(e)


@app.delete("/api/nl/type/{code}")
async def nl_type_close(code: str, request: Request, x_nl_type_key: Optional[str] = Header(None)) -> Any:
    """The TV leaves the search page: its code stops working."""
    try:
        nl_type.close(code, x_nl_type_key, _client_address(request))
    except TypingError as e:
        return _type_error(e)
    return {"ok": True}


@app.post("/api/nl/type/{code}")
async def nl_type_send(code: str, body: NLTypeText, request: Request) -> Any:
    """The phone's text for the TV (the whole text each time, so a missed update heals itself). 404 once the code is
    gone, 422 for more than 100 characters, 429 when going too fast."""
    try:
        return nl_type.send(code, body.text, _client_address(request), body.submit)
    except TypingError as e:
        return _type_error(e)


def _type_page(request: Request, code: Optional[str], message: str = "", status: int = 200) -> Any:
    resp = templates.TemplateResponse(request=request, name="nl/type.html", context={"code": code or "", "message": message}, status_code=status)
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Referrer-Policy"] = "no-referrer"   # the code is in the address
    return resp


@app.get("/nl/type", response_class=HTMLResponse)
async def nl_type_enter(request: Request, code: Optional[str] = Query(None, max_length=32)) -> Any:
    """The phone page without a code: type the 6 characters from the TV (a code in the address goes on to its page)."""
    if code is not None:
        norm = normalise_code(code)
        if norm:
            return RedirectResponse(f"/nl/type/{norm}", status_code=302)
        return _type_page(request, None, "That code doesn't look right. It has 6 letters and digits.", 400)
    return _type_page(request, None)


@app.get("/nl/type/{code}", response_class=HTMLResponse)
async def nl_type_page(code: str, request: Request) -> Any:
    """The phone's keyboard page for one TV. No cookies, no who's-watching: the code is the only credential."""
    try:
        norm = nl_type.check(code, _client_address(request))
    except TypingError as e:
        return _type_page(request, None, e.message, e.status)
    return _type_page(request, norm)


@app.get("/nl/cast/receiver", response_class=HTMLResponse)
async def nl_cast_receiver(request: Request) -> Any:
    """The custom Chromecast Web Receiver (M8). Runs on the Chromecast, so it never has the viewer cookie and
    never calls our API: everything it shows arrives in the load request from the sender, and the artwork URL
    carries a stream token."""
    resp = templates.TemplateResponse(request=request, name="nl/cast_receiver.html", context={})
    resp.headers["Cache-Control"] = "no-store"
    return resp


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
        download_requests.remember(movie_id, req.magnet_link, clean_movie_title, req.media_type)
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