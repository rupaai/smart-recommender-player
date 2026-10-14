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
from services.household_config import load_household, load_machine_config, merge_household
from services.catalogue_search import CatalogueSearch, _tmdb_get, tmdb_import, tmdb_search
from services.generative_retriever import GenerativeRetriever, route_recommend
from services.recommender_service import MOOD_MAPPINGS, RecommenderService
from services import paths as model_paths
from services import verdicts as vd
from services.taste_runtime import TasteRuntime
from services.admin_api import build_router as build_admin_router
from services.vibe_index import VibeIndex
from services import vibe_moods
from services.mood_popup import MoodSession
from services.vibe_popup import PopupSession, SessionStore, should_auto_ask, skip_reason  # noqa: F401 - PopupSession kept, unused by /api/discover/*
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
try:
    config = load_machine_config(config)
    config = merge_household(config, load_household())
except (OSError, ValueError, yaml.YAMLError) as _e:
    raise SystemExit(f"[Household] bad household configuration: {_e}")

db_path = os.getenv("DATABASE_PATH") or config.get("database_path", "catalogue.db")
# Folders the server layout expects (no-ops when they exist; the media folders are
# created by TorrentDownloader below).
os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
if os.getenv("MODELS_DIR"):
    os.makedirs(os.path.join(os.path.dirname(os.path.abspath(os.environ["MODELS_DIR"])), "releases"), exist_ok=True)
household_profiles = (config.get("household") or {}).get("profiles")
db = DatabaseManager(db_path, initial_profiles=household_profiles)
if household_profiles:
    db.sync_configured_profiles(household_profiles)
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
    shortlist_size=recommender_cfg.get("shortlist_size", 1500),
    final_pool=recommender_cfg.get("final_pool", 200),
    taste_formula=recommender_cfg.get("taste_formula", "average"),
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
    """Phones (iOS Safari, home-screen apps) and the TV app's WebView otherwise keep an old app.js for days,
    since StaticFiles sends no Cache-Control. no-cache still allows the cheap
    ETag check, so an unchanged file costs a 304."""
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

def _model_bundle_id() -> Optional[str]:
    """bundle_id from <MODELS_DIR>/manifest.json, or None if absent or unreadable."""
    try:
        with open(os.path.join(model_paths.models_dir(), "manifest.json"), "r", encoding="utf-8") as f:
            bundle_id = json.load(f).get("bundle_id")
        return str(bundle_id) if bundle_id is not None else None
    except (OSError, ValueError, AttributeError):
        return None

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
            recommender_service.finalize, popup.ranked(), req.page, effective_page_size,
            spread_exempt=_spread_exempt(popup))
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
        remember_shown=True,   # push down what Discover showed lately and nobody opened
        skip_session_id=req.session_id,
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

_MOOD_LABELS = {m["id"]: m["label"] for m in vibe_moods.MOODS}

def _mood_list(popup: Any, n: int = 3) -> Optional[List[Dict[str, Any]]]:
    """Top moods as [{id, label, p}] for the UI; None for sessions without a mood belief."""
    top = getattr(popup, "top_moods", None)
    if top is None:
        return None
    return [{"id": m, "label": _MOOD_LABELS.get(m, m), "p": round(p, 2)} for m, p in top(n)]

def _spread_exempt(popup: Any) -> set:
    """Genres a questions session asked for, which the genre spread leaves alone:
    the request's genre chips, plus Animation / Family once the answers point
    clearly at "Animated, any age" or "Fun for everyone"."""
    kwargs = (getattr(popup, "request", None) or {}).get("kwargs") or {}
    exempt = set(kwargs.get("genres_override") or [])
    belief = getattr(popup, "belief", None) or {}
    if belief.get("animated_any", 0.0) + belief.get("family_all", 0.0) >= 0.3:
        exempt |= {"Animation", "Family"}
    return exempt

def _popup_response(popup: Any, page_size: int, vibe: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    results = recommender_service.finalize(popup.ranked(), 1, page_size, spread_exempt=_spread_exempt(popup))
    results.update({"engine": "legacy", "session_id": popup.id, "question": popup.public_question()})
    moods = _mood_list(popup)
    if moods is not None:
        results["moods"] = moods
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

async def discover_start(req: DiscoverStartRequest) -> Dict[str, Any]:
    """First results plus, when asking is on, the first question (always)."""
    page_size = req.top_n if req.top_n is not None else req.page_size
    kwargs = _recommend_kwargs(req, page_size)
    res = await asyncio.to_thread(recommender_service.recommend, **{**kwargs, "page": 1}, return_pool=True)
    pool = res.get("pool") or []
    session_id = db.create_discover_session(
        profile=req.profile, media_type=req.media_type, query_text=req.query,
        request=req.model_dump(exclude={"session_id", "exclude_ids", "page", "page_size", "top_n"}),
        engine="legacy+popup",
    )
    popup = MoodSession(session_id, {**kwargs, "kwargs": kwargs}, pool, _cluster_labels(),
                        recommender_service.vibe_index, mood_weight=recommender_cfg.get("mood_weight"))
    if req.ask and pool:
        await asyncio.to_thread(popup.next_question, force=True)
    popup.done = popup.current is None
    popup_sessions.put(popup)
    out = await asyncio.to_thread(_popup_response, popup, page_size, res.get("vibe"))
    if req.ask:
        # MoodSession always asks A3 when there is a pool, so only an empty pool skips.
        out["question_skipped"] = None if popup.current is not None else ("no_results" if not pool else None)
    return out

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
    # (PopupSession's D1 "who's on the sofa" pool rebuild has no MoodSession equivalent: the mood
    # bank has no profile question, so the pool never changes during a mood session.)
    if popup.should_stop():
        popup.done = True
    else:
        await asyncio.to_thread(popup.next_question)
        popup.done = popup.current is None
    return await asyncio.to_thread(_popup_response, popup, kwargs.get("page_size") or 20)

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

async def record_feedback(req: FeedbackRequest) -> Dict[str, Any]:
    """Records what happened to a recommended title: opened (synopsis tapped),
    dismissed ("not this one"), downloaded, played or rated."""
    try:
        updated = db.record_outcome(req.media_item_id, req.outcome, req.session_id, req.rating)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"updated": updated}

async def feedback_stats() -> Dict[str, Any]:
    """How much training signal has been logged so far."""
    return db.get_feedback_stats()

async def vibe_status() -> Dict[str, Any]:
    """Whether the vibe index (models/vibe/, built on the Mac) is loaded."""
    index = recommender_service.vibe_index
    out = index.status() if index else {"ready": False}
    out["mood_data"] = await asyncio.to_thread(db.get_mood_data_counts)
    return out

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
    ctx = {**context, "viewer": viewer.public(), "people_configured": nl_people.configured,
           "tv": tv, "group_label": nl_people.group_label}
    return templates.TemplateResponse(request=request, name=name, context=ctx)

def _nl_choose(key: str, next_url: Optional[str], tv: bool) -> Response:
    default = "/nl/tv/" if tv else "/nl/"
    target = safe_next(next_url, default)
    if not nl_people.configured or nl_people.viewer_for(key) is None:
        return RedirectResponse(f"/nl/{'tv/' if tv else ''}who?next={quote(target, safe='')}", status_code=302)
    resp = RedirectResponse(target, status_code=302)
    resp.set_cookie(COOKIE_NAME, key.lower(), max_age=COOKIE_MAX_AGE, path="/", samesite="lax", httponly=True)
    return resp

def _nl_prefs_reply(viewer: Viewer) -> Dict[str, Any]:
    row = _nl_prefs_row(viewer)
    return {"viewer": viewer.public(), "prefs": Prefs.from_row(row).public(), "saved": bool(row)}

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

class NLSubtitleDownload(BaseModel):
    subtitle_id: str = Field(..., min_length=1, max_length=512)

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

class NLReactBody(BaseModel):
    reaction: str

def _react_profiles(viewer: Viewer) -> List[int]:
    return nl_reactions.profiles_to_rate(viewer.profile_ids, viewer.implicit)

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

class NLHiddenBody(BaseModel):
    hidden: bool

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

class NLUnwatchBody(BaseModel):
    source_id: Optional[int] = None   # which episode of a show
    person: Optional[str] = None      # whose data; needed on "Both of us"

class NLShowPrefs(BaseModel):
    autoplay: bool

class NLSkipIntro(BaseModel):
    skip_intro: bool

def _player_extras_for(item: Dict[str, Any]) -> Dict[str, Any]:
    """Trickplay, chapters and skip segments of one title or episode, read from Jellyfin here. Any
    Jellyfin trouble leaves the parts empty: these features simply don't show."""
    try:
        jf_id = jellyfin_playback._subtitle_item_id(item)
    except (ItemNotFound, JellyfinUnavailable):
        return empty_extras()
    return player_extras(jellyfin_playback, jf_id)

def _autoplay_for(item_id: int, viewer: Viewer, fallback: Any) -> bool:
    """Start the next episode by itself? The show's own switch if anyone ever set it, else this viewer's default
    (M10c, H5; on unless they turned it off)."""
    own = db.get_show_autoplay_row(item_id)
    if own is not None:
        return own
    return bool(_nl_effective_prefs(viewer).autoplay)

# --- Northern Lights picks for two (M6) ---
class NLWatchlistAdd(BaseModel):
    media_item_id: int
    scope: str = "shared"   # "shared" (default) or "me" (Just me)

class NLInterestBody(BaseModel):
    state: Optional[str] = None   # yes | maybe | pass | null (clear)
    person: Optional[str] = None  # whose answer; needed on "Both of us"

def _picks_error(e: PicksError) -> HTTPException:
    return HTTPException(status_code=e.status, detail=str(e))

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

async def nl_choose_options(request: Request, tz: Optional[str] = None) -> Dict[str, Any]:
    """What Choose for us offers: the recommender's moods, the shelf's genres, the length chips, what today's
    weekday and time of day suggest, whose turn it is to choose, and how many titles both said yes to."""
    viewer = _nl_viewer_or_default(request)
    return await asyncio.to_thread(nl_choose.options, viewer, clean_tz(tz))

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

async def nl_choose_chosen(item_id: int, request: Request) -> Dict[str, Any]:
    """The pick was played: remember who chose, so the next time says "Your turn, <the other person>"."""
    viewer = _nl_viewer_or_default(request)
    try:
        return await asyncio.to_thread(nl_choose.chosen, viewer, item_id)
    except PicksError as e:
        raise _picks_error(e) from e

async def nl_vetoes_list(request: Request) -> Dict[str, Any]:
    """The titles off the table for this screen: "Not tonight" (until tomorrow 05:00) and each person's "Never"."""
    viewer = _nl_viewer_or_default(request)
    return await asyncio.to_thread(nl_choose.vetoes, viewer)

async def nl_veto_set(item_id: int, body: NLVetoBody, request: Request) -> Dict[str, Any]:
    """"Not tonight" hides a title until tomorrow 05:00 local; "Never" hides it for that person. Both leave it
    out of Choose for us and the home row; the library, search and watchlist still show it."""
    viewer = _nl_viewer_or_default(request)
    try:
        return await asyncio.to_thread(nl_choose.veto, viewer, item_id, body.kind, body.person, clean_tz(body.tz))
    except PicksError as e:
        raise _picks_error(e) from e

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

def _type_page(request: Request, code: Optional[str], message: str = "", status: int = 200) -> Any:
    resp = templates.TemplateResponse(request=request, name="nl/type.html", context={"code": code or "", "message": message}, status_code=status)
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Referrer-Policy"] = "no-referrer"   # the code is in the address
    return resp

# ---------------------------------------------------------------------------
# Watch later (shared list, schema v11)
# ---------------------------------------------------------------------------

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

class PairAnswer(BaseModel):
    profile_id: int = 1
    a_id: int
    b_id: int
    choice: str  # a | b | same

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

def run_server(host: str = "0.0.0.0", port: int = 8000) -> None:
    """Launches Uvicorn server for the application."""
    print("=" * 65)
    print(f"🎬 SMART MOVIE & TV STREAMER PWA SERVER RUNNING")
    print(f"📱 Access on your phone:   http://{get_local_ip()}:{port}")
    print(f"💻 Access on your laptop:  http://localhost:{port}")
    print(f"📺 Jellyfin Server:        http://{get_local_ip()}:8096")
    print("=" * 65)
    uvicorn.run("server:app", host=host, port=port, reload=False)

# Route tables and extracted domain handlers live under routers/; server.py
# remains the composition root for startup and shared dependencies.
from routers import build_routers

for _router in build_routers(globals()):
    app.include_router(_router)

if __name__ == "__main__":
    run_server()
