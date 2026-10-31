"""
Finding and downloading copies: torrent search, download, cancel and status.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Dict

from fastapi import APIRouter

from services import nl_downloads
from services.torrent_service import (
    _clean_release_title,
    downloads_store,
)
from web import state
from web.helpers import _log_outcome
from web.models import (
    TorrentCancelRequest,
    TorrentDownloadRequest,
    TorrentSearchRequest,
)

router = APIRouter()


@router.post("/api/torrent/search")
async def search_torrents(req: TorrentSearchRequest) -> Dict[str, Any]:
    """Searches for torrents matching the query with configurable sort order and media type (movies/shows)."""
    results = state.torrent_searcher.search(
        req.query,
        sort_by_size=req.sort_by_size,
        media_type=req.media_type,
        sort=req.sort,
    )
    return {"results": results}


@router.post("/api/torrent/download")
async def download_torrent(req: TorrentDownloadRequest) -> Dict[str, Any]:
    """Launches torrent download as a background task and returns immediately."""
    try:
        movie_id = req.movie_id
        catalogue_item = state.db.get_item_by_id(movie_id) if movie_id and movie_id > 0 else None
        # A linked download already has a canonical catalogue title and type.
        # Torrent names are only a fallback for unlinked downloads and new stubs.
        clean_movie_title = _clean_release_title(req.movie_title)
        media_type = req.media_type
        if catalogue_item:
            clean_movie_title = (catalogue_item.get("title") or clean_movie_title).strip()
            media_type = catalogue_item.get("media_type") or media_type
        if not movie_id or movie_id <= 0:
            movie_id = state.db.get_or_create_media_item(
                title=clean_movie_title,
                media_type=media_type,
            )

        if req.movie_id and req.movie_id > 0:
            _log_outcome(req.movie_id, "downloaded")
        download_id = uuid.uuid4().hex
        now = time.time()
        state.download_requests.remember(download_id, req.magnet_link, clean_movie_title, media_type, movie_id=movie_id)
        downloads_store[download_id] = {
            "download_id": download_id,
            "movie_id": movie_id,
            "name": clean_movie_title,
            "media_type": media_type,
            "status": "connecting",
            "progress": 0.0,
            "error": None,
            "created_at": now,
        }
        asyncio.create_task(state.run_download(download_id, movie_id, req.magnet_link, clean_movie_title, media_type))
        return {
            "success": True,
            "download_id": download_id,
            "movie_id": movie_id,
            "clean_title": clean_movie_title,
            "message": f"Download task started for {media_type}.",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/api/torrent/cancel")
async def cancel_torrent(req: TorrentCancelRequest) -> Dict[str, Any]:
    """Cancels an in-progress torrent download."""
    download_id = req.download_id
    if not download_id and req.movie_id is not None:
        matches = [
            key for key, entry in downloads_store.items()
            if isinstance(entry, dict)
            and entry.get("movie_id", key) == req.movie_id
            and nl_downloads.state_of(entry) not in nl_downloads.RETRY_STATES + nl_downloads.DISMISS_STATES
        ]
        if len(matches) == 1:
            download_id = str(matches[0])
    success = await state.torrent_downloader.cancel(download_id) if download_id else False
    return {"success": success}


@router.get("/api/torrent/status/{download_id}")
async def get_torrent_status(download_id: str) -> Dict[str, Any]:
    """Returns one copy's current download progress."""
    key: Any = download_id
    numeric_id = str(download_id).isdigit()
    if key not in downloads_store and numeric_id and int(download_id) in downloads_store:
        key = int(download_id)  # compatibility for old clients/tests
    status_data = downloads_store.get(key)
    if status_data is None and numeric_id:
        movie_id = int(download_id)
        matches = [entry for entry in downloads_store.values()
                   if isinstance(entry, dict) and entry.get("movie_id") == movie_id]
        if len(matches) == 1:
            status_data = matches[0]
        elif len(matches) > 1:
            return {"status": "ambiguous", "progress": 0.0, "movie_id": movie_id}
    if status_data is None:
        legacy_movie_id = int(download_id) if numeric_id else None
        status_data = {"status": "idle", "progress": 0.0, "movie_id": legacy_movie_id}
    return status_data
