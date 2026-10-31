"""
The app's shared objects: settings, the database, Jellyfin, the recommenders,
downloads, Northern Lights services. Built once at start-up; routes use them as `state.<name>`
(tests swap them the same way).
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, Optional

# Secrets from Homelab (/run/secrets) go into the environment before anything reads it.
from services import app_config, homelab_secrets

homelab_secrets.load()

from dotenv import load_dotenv
from fastapi.templating import Jinja2Templates

from database import DatabaseManager
from services import nl_downloads, nl_health
from services import paths as model_paths
from services.catalogue_search import CatalogueSearch, _tmdb_get
from services.jellyfin_playback import JellyfinPlayback
from services.jellyfin_service import JellyfinService
from services.media_cleanup import MediaCleanup
from services.nl_browse import NLBrowse
from services.nl_cast import (
    CastConfig,
    CastConfigError,
    CastTokens,
    secret_from_env,
)
from services.nl_choose import NLChoose
from services.nl_people import (
    People,
    PeopleConfigError,
)
from services.nl_picks import NLPicks
from services.nl_polish_b import NLExtras
from services.nl_type import TypeChannels
from services.recommender_service import MOOD_MAPPINGS, RecommenderService
from services.taste_runtime import TasteRuntime
from services.torrent_service import (
    TorrentDownloader,
    TorrentSearcher,
    downloads_store,
)
from services.vibe_index import VibeIndex
from services.vibe_popup import SessionStore
from services.wishmatch_movies import EngineSwitch, WishmatchMovies
from services.wishmatch_movies.router import engine_name

load_dotenv()
api_key = os.getenv("TMDB_API_KEY", "").strip()

# Settings: config.yaml (or CONFIG_PATH) with your config.local.yaml laid on top.
CONFIG_PATH = app_config.config_path()
config: Dict[str, Any] = app_config.load_config(CONFIG_PATH)

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
    shortlist_size=recommender_cfg.get("shortlist_size", 500),
    final_pool=recommender_cfg.get("final_pool", 200),
    taste_formula=recommender_cfg.get("taste_formula", "average"),
    vibe_index=vibe_index,
    taste_runtime=taste_runtime,
)

# Wishmatch (PROJECT_HANDOVER.md §17) and the engine switch: recommender.engine picks legacy or wishmatch
# for every screen. The catalogue's mood tags are read on the first Wishmatch request, not at start-up.
wishmatch_movies = WishmatchMovies(db, MOOD_MAPPINGS)
recommender = EngineSwitch(engine_name(recommender_cfg), recommender_service, wishmatch_movies)


def reload_models() -> None:
    """Loads the model files again after the Mac publishes a new bundle (or one is rolled back)."""
    vibe_ok = vibe_index.reload()
    root = model_paths.models_dir()
    if os.path.isdir(os.path.join(root, "vibe")) and not vibe_ok:
        raise RuntimeError(f"the vibe index did not load: {vibe_index.status().get('error')}")


# Create torrent services
torrent_searcher = TorrentSearcher(config)
torrent_downloader = TorrentDownloader(config, db_manager=db)
download_requests = nl_downloads.DownloadRequests(db)   # what Retry and a restart need; the magnet link never leaves the server


async def run_download(download_id: str, movie_id: int, magnet: str, title: str, media_type: str) -> None:
    """Runs one download. Only when it ends (any way but the app stopping) is it marked finished, so a
    restart carries on with the unfinished ones (resume_downloads)."""
    await torrent_downloader.download(magnet, download_id, movie_id, title, media_type=media_type)
    await asyncio.to_thread(download_requests.finished, download_id)

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
# The rating profiles take the configured people's names (only while they still have a default name).
db.sync_profile_names({p.profile_id: p.name for p in nl_people.people if p.profile_id is not None})
if nl_people.configured and not jellyfin_service.user_id:
    jellyfin_service.user_id = nl_people.people[0].jellyfin_user_id  # library scans need some user

# Northern Lights casting (M8): where the Chromecast reaches this app and the signed stream tokens.
# NL_CAST_SECRET (if set) signs the tokens; otherwise a secret kept in a file next to the database does.
try:
    nl_cast = CastConfig.from_config(config)
except CastConfigError as _e:
    raise SystemExit(f"[Northern Lights] bad cast configuration: {_e}")
nl_cast_tokens = CastTokens(secret_from_env(), data_dir=os.path.dirname(os.path.abspath(db_path)))

# Northern Lights phone keyboard for TV search (M8b): short-lived codes held in memory only.
nl_type = TypeChannels()

# Northern Lights picks for two (M6): watchlist, yes / maybe / pass, and the "For both of us" row.
nl_picks = NLPicks(db, nl_browse, recommender, lambda: nl_people)

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


def _jellyfin_rescan() -> None:
    jellyfin_service.session.post(f"{jellyfin_service.base_url}/Library/Refresh", timeout=10)


# Watched cleanup: deletes what everyone watched more than `media_cleanup.days_after_watched` days ago.
media_cleanup = MediaCleanup(
    config, jellyfin_playback, lambda: [p.jellyfin_user_id for p in nl_people.people], db,
    [jellyfin_service.movie_dir, jellyfin_service.tv_dir], _jellyfin_rescan,
)

# Directories
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # the repo root
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(TEMPLATES_DIR, exist_ok=True)
templates = Jinja2Templates(directory=TEMPLATES_DIR)


# ---------------------------------------------------------------------------
# Questions pop-up ("guess the vibe", design doc §3, services/vibe_popup.py)
# ---------------------------------------------------------------------------
popup_sessions = SessionStore()


catalogue_search = CatalogueSearch(db)
nl_browse.catalogue = catalogue_search
