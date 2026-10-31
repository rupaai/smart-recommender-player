"""
The home page of the original app, /healthz, server status and Jellyfin library checks.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Dict, Optional

from services.app_version import homelab_version

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from services import paths as model_paths
from web import state
from web.helpers import is_jellyfin_online, jellyfin_link_base

router = APIRouter()


# --- Routes ---
@router.get("/", response_class=HTMLResponse)
async def serve_index(request: Request) -> Any:
    """Serves the mobile-first PWA interface."""
    return state.templates.TemplateResponse(request=request, name="index.html", context={})


def _model_bundle_id() -> Optional[str]:
    """bundle_id from <MODELS_DIR>/manifest.json, or None if absent or unreadable."""
    try:
        with open(os.path.join(model_paths.models_dir(), "manifest.json"), "r", encoding="utf-8") as f:
            bundle_id = json.load(f).get("bundle_id")
        return str(bundle_id) if bundle_id is not None else None
    except (OSError, ValueError, AttributeError):
        return None


@router.get("/healthz")
async def healthz() -> Any:
    """Liveness/readiness: git SHA, Homelab version, model bundle, DB schema version. 503 if the DB cannot be read or written."""
    try:
        with state.db.get_connection() as conn:
            # Rewriting the schema version in a transaction we roll back proves the file is writable.
            # A read-only database (wrong owner after a move) passes a plain read but fails here.
            conn.execute("BEGIN IMMEDIATE")
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            conn.execute(f"PRAGMA user_version = {int(version)}")
            conn.execute("ROLLBACK")
        schema = state.db.get_schema_version()
    except Exception as e:  # noqa: BLE001 - any DB failure means not ready
        return JSONResponse(status_code=503, content={"ok": False, "error": str(e)})
    return {
        "ok": True,
        "git_sha": os.getenv("GIT_SHA", "dev"),
        "version": homelab_version(),
        "model_bundle": _model_bundle_id(),
        "db_schema": schema,
    }


@router.get("/api/status")
async def get_status(request: Request) -> Dict[str, Any]:
    """Returns system status including Jellyfin online state and catalogue statistics."""
    jf_online = is_jellyfin_online()
    stats = state.db.get_stats()
    return {
        "jellyfin_online": jf_online,
        "jellyfin_url": jellyfin_link_base(request),
        "stats": stats,
    }


@router.get("/api/jellyfin/status")
async def get_jellyfin_status() -> Dict[str, Any]:
    """Jellyfin diagnostics: server online, libraries (types + locations), env config."""
    return await asyncio.to_thread(state.jellyfin_service.diagnostics)


@router.post("/api/jellyfin/repair")
async def repair_jellyfin_libraries() -> Dict[str, Any]:
    """Attaches missing paths to Movies/TV Shows libraries, forces realtime
    monitoring, recreates EMPTY libraries as a fallback, then refreshes."""
    return await asyncio.to_thread(state.jellyfin_service.repair_libraries)
