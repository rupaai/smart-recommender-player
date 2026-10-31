"""
Northern Lights downloads list (Retry, dismiss), the header status and the admin page data.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException

from services import nl_downloads, nl_health
from services.torrent_service import downloads_store
from web import state

router = APIRouter()


# --- Northern Lights downloads (M10a): the same downloader and store as the old Download tab ---
def _nl_downloads_payload() -> Dict[str, Any]:
    snapshot = dict(downloads_store)
    movie_ids = {
        entry.get("movie_id", download_id)
        for download_id, entry in snapshot.items()
        if isinstance(entry, dict) and isinstance(entry.get("movie_id", download_id), int)
    }
    briefs = state.db.get_download_briefs(list(movie_ids))
    return nl_downloads.build_list(
        snapshot,
        state.download_requests,
        lambda i: briefs.get(i),
        lambda i: bool((briefs.get(i) or {}).get("has_file")),
    )


@router.get("/api/nl/downloads")
async def nl_downloads_list() -> Dict[str, Any]:
    """Every download the downloader knows: queued, downloading (speed and time left), importing, ready
    (with a Play link) or failed (with a plain-words reason and Retry). No magnet links, no file paths."""
    return await asyncio.to_thread(_nl_downloads_payload)


@router.post("/api/nl/downloads/{download_id}/retry")
async def nl_downloads_retry(download_id: str) -> Dict[str, Any]:
    """Starts a failed or stopped download again from the link it was first started with."""
    key: Any = download_id
    numeric_id = str(download_id).isdigit()
    if key not in downloads_store and numeric_id and int(download_id) in downloads_store:
        key = int(download_id)  # compatibility with pre-copy-ID in-memory entries
    entry = downloads_store.get(key)
    if entry is None:
        raise HTTPException(status_code=404, detail="That download isn't in the list any more.")
    if nl_downloads.state_of(entry) not in nl_downloads.RETRY_STATES:
        raise HTTPException(status_code=409, detail="That download is not stopped, so there is nothing to retry.")
    req = state.download_requests.get(key)
    if req is None:
        raise HTTPException(
            status_code=404,
            detail="The app was restarted since this started, so it no longer has the link. Search for the title again.",
        )
    movie_id = req.get("movie_id")
    if not isinstance(movie_id, int):
        movie_id = entry.get("movie_id", key)
    downloads_store[key] = {
        "download_id": key,
        "movie_id": movie_id,
        "status": "connecting",
        "progress": 0.0,
        "error": None,
        "name": req["title"],
        "media_type": req["media_type"],
        "created_at": time.time(),
    }
    state.torrent_downloader.cancelled_tasks.discard(key)
    state.download_requests.remember(key, req["magnet"], req["title"], req["media_type"], movie_id=movie_id)
    asyncio.create_task(state.run_download(str(key), movie_id, req["magnet"], req["title"], req["media_type"]))
    return {"success": True, "id": key}


@router.delete("/api/nl/downloads/{download_id}")
async def nl_downloads_dismiss(download_id: str) -> Dict[str, Any]:
    """Clears a failed, stopped or finished download from the list (the record only; files are never touched)."""
    key: Any = download_id
    numeric_id = str(download_id).isdigit()
    if key not in downloads_store and numeric_id and int(download_id) in downloads_store:
        key = int(download_id)
    entry = downloads_store.get(key)
    if entry is None:
        return {"success": True}
    if nl_downloads.state_of(entry) not in nl_downloads.DISMISS_STATES:
        raise HTTPException(status_code=409, detail="That download is still running.")
    downloads_store.pop(key, None)
    state.download_requests.forget(key)
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
    diag = state.jellyfin_service.diagnostics()
    disk = dict(state.nl_health_state.disk_state())
    disk["media_used_bytes"] = state.nl_health_state.media_used()
    return {
        "health": state.nl_health_state.status(),
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
        "tools": nl_health.tool_links(state.config),
        "cleanup": state.media_cleanup.public(),
    }


@router.get("/api/nl/health")
async def nl_health_status() -> Dict[str, Any]:
    """The compact header status: Jellyfin reachable, free disk space, download queue size. Cheap: checks are
    cached for a few seconds. Carries no address, key or path."""
    return await asyncio.to_thread(state.nl_health_state.status)


@router.get("/api/nl/admin")
async def nl_admin_data() -> Dict[str, Any]:
    """The admin screen's data: the status above, Jellyfin's diagnostics (libraries and where they point),
    disk use against the limits, and the Jellystat / Uptime Kuma / Dozzle links that are configured."""
    return await asyncio.to_thread(_nl_admin_payload)


@router.post("/api/nl/admin/repair")
async def nl_admin_repair() -> Dict[str, Any]:
    """Library repair (the same action as POST /api/jellyfin/repair), answered with a short summary only."""
    report = await asyncio.to_thread(state.jellyfin_service.repair_libraries)
    state.nl_health_state.forget()
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
