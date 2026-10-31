"""
Northern Lights server: builds the FastAPI app from the route files in web/routes/
and starts it. The shared objects live in web/state.py; see web/README.md.
"""


from __future__ import annotations

import asyncio
import time

# Secrets from Homelab (/run/secrets) go into the environment before anything reads it.
from services import homelab_secrets
from services.app_version import app_version

homelab_secrets.load()

# Python logging prints one JSON line per record, with levels, so Homelab's Logs can read it.
# Needs no token. Called before anything else sets up logging.
import homelab.logs

homelab.logs.setup("INFO")

import uvicorn
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from services.admin_api import build_router as build_admin_router
from services.media_cleanup import FIRST_RUN_DELAY, RUN_EVERY
from services.torrent_service import (
    downloads_store,
)
from web import state
from web.helpers import get_local_ip
from web.routes import (
    core,
    discover,
    nl_admin,
    nl_library,
    nl_pages,
    nl_picks,
    nl_play,
    nl_type,
    nl_viewer,
    ratings,
    torrents,
    watch,
)

app = FastAPI(title="Smart Movie & TV Streamer & Recommender", version=app_version())

# Mac -> server admin routes (export, catalogue patch, model bundles); 404 unless ADMIN_TOKEN is set.
app.include_router(build_admin_router(lambda: state.db.db_path, state.reload_models))


@app.on_event("startup")
async def resume_downloads() -> None:
    """Starts again the downloads that were running when the app stopped, and removes staging folders
    that no stored download owns."""
    try:
        pending = await asyncio.to_thread(state.download_requests.unfinished)
        owned = await asyncio.to_thread(state.download_requests.download_ids)
        await asyncio.to_thread(state.torrent_downloader.remove_orphan_staging, owned)
        for r in pending:
            did = r["download_id"]
            downloads_store[did] = {"download_id": did, "movie_id": r["movie_id"], "status": "connecting",
                                    "progress": 0.0, "error": None, "name": r["title"],
                                    "media_type": r["media_type"], "created_at": time.time()}
            asyncio.create_task(state.run_download(did, r["movie_id"], r["magnet"], r["title"], r["media_type"]))
        if pending:
            print(f"[Downloader] Resuming {len(pending)} download(s) from before the restart.", flush=True)
    except Exception as e:
        print(f"[Downloader] Could not resume downloads ({e}); server continues.", flush=True)


@app.on_event("startup")
async def start_wishmatch() -> None:
    """Asks Ollama (if configured) to download Wishmatch's wish-reader model once, in the background."""
    try:
        state.wishmatch_movies.start()
    except Exception as e:  # noqa: BLE001
        print(f"[Wishmatch] Wish reader setup skipped ({e}); the word lookup still works.", flush=True)


@app.on_event("startup")
async def start_media_cleanup() -> None:
    """Once a day (the first time 10 minutes after start), when media_cleanup.enabled is on."""
    if not state.media_cleanup.public()["enabled"]:
        return

    async def loop() -> None:
        await asyncio.sleep(FIRST_RUN_DELAY)
        while True:
            await asyncio.to_thread(state.media_cleanup.run_once)
            await asyncio.sleep(RUN_EVERY)

    asyncio.create_task(loop())


@app.on_event("startup")
async def setup_jellyfin_libraries() -> None:
    """Idempotently ensures the dedicated Movies / TV Shows libraries exist.

    Additive-only and fully fault-tolerant: if Jellyfin is offline or not
    configured, the server still boots normally and setup is simply skipped.
    """
    try:
        result = await asyncio.to_thread(state.jellyfin_service.ensure_libraries)
        print(f"[Jellyfin] Libraries ensured: {result}", flush=True)
    except Exception as e:
        print(f"[Jellyfin] Library setup skipped ({e}) — server continues.", flush=True)

app.include_router(core.router)
app.include_router(discover.router)
app.include_router(watch.router)
app.include_router(nl_viewer.router)
app.include_router(nl_play.router)
app.include_router(nl_library.router)
app.include_router(nl_picks.router)
app.include_router(nl_admin.router)
app.include_router(nl_pages.router)
app.include_router(nl_type.router)
app.include_router(ratings.router)
app.include_router(torrents.router)

app.mount("/static", StaticFiles(directory=state.STATIC_DIR), name="static")


@app.middleware("http")
async def revalidate_static(request: Request, call_next):
    """Phones (iOS Safari, home-screen apps) and the TV app's WebView otherwise keep an old app.js for days,
    since StaticFiles sends no Cache-Control. no-cache still allows the cheap
    ETag check, so an unchanged file costs a 304."""
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


def run_server(host: str = "0.0.0.0", port: int = 8000) -> None:
    """Launches Uvicorn server for the application."""
    print("=" * 65)
    print(f"🎬 SMART MOVIE & TV STREAMER PWA SERVER RUNNING")
    print(f"📱 Access on your phone:   http://{get_local_ip()}:{port}")
    print(f"💻 Access on your laptop:  http://localhost:{port}")
    print(f"📺 Jellyfin Server:        http://{get_local_ip()}:8096")
    print("=" * 65)
    # log_config=None: uvicorn's own lines go through the JSON handler too, not its plain one.
    uvicorn.run("server:app", host=host, port=port, reload=False, log_config=None)


if __name__ == "__main__":
    run_server()
