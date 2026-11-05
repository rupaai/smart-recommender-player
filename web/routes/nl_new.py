"""
Northern Lights "New & popular": new episodes of your shows and popular shows, with one-tap download.
The work (TMDB, taste, finding and picking a copy, starting the download) happens here on the server;
the pages only draw the lists and send the button press. Logic: services/nl_new_episodes.py.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional, Set

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from services import nl_downloads
from services.torrent_service import downloads_store
from web import state
from web.helpers import _nl_viewer_or_default
from web.routes.torrents import start_download

router = APIRouter()


class NewDownloadRequest(BaseModel):
    tmdb_id: int = Field(..., ge=1)
    season: int = Field(..., ge=1, le=200)
    episode: Optional[int] = Field(None, ge=1, le=2000)
    copy_id: Optional[str] = Field(None, max_length=40)


def _downloading_ids() -> Set[int]:
    """Our ids of shows with a download still running (so a card can say "Downloading")."""
    out: Set[int] = set()
    for key, entry in list(downloads_store.items()):
        if not isinstance(entry, dict):
            continue
        if nl_downloads.state_of(entry) in nl_downloads.RETRY_STATES + nl_downloads.DISMISS_STATES:
            continue
        movie_id = entry.get("movie_id", key)
        if isinstance(movie_id, int):
            out.add(movie_id)
    return out


def _mark_downloading(data: Dict[str, Any]) -> Dict[str, Any]:
    busy = _downloading_ids()
    out = dict(data)
    for key in ("yours", "popular"):
        out[key] = [{**c, "downloading": bool(c.get("media_item_id") in busy)} for c in data.get(key) or []]
    return out


@router.get("/api/nl/new-episodes")
async def nl_new_episodes(request: Request, fresh: bool = False) -> Dict[str, Any]:
    """New episodes of the viewer's shows ("yours") and popular shows still airing ("popular"). Each card
    has an `offer` (an episode or a whole season) for the Download button. `tmdb` is false when TMDB could
    not be reached (the page says so instead of showing an empty list)."""
    if not state.api_key:
        return {"enabled": False, "yours": [], "popular": [], "tmdb": False,
                "note": "TMDB isn't set up on the server, so new episodes can't be looked up."}
    viewer = _nl_viewer_or_default(request)
    data = await asyncio.to_thread(state.nl_new_episodes.lists, viewer.profile_ids, fresh)
    return _mark_downloading(data)


@router.get("/api/nl/new-episodes/copies")
async def nl_new_episode_copies(
    tmdb_id: int = Query(..., ge=1), season: int = Query(..., ge=1, le=200),
    episode: Optional[int] = Query(None, ge=1, le=2000),
) -> Dict[str, Any]:
    """Copies of one episode (or season), best first, for "Choose another copy". No magnet links."""
    try:
        copies = await asyncio.to_thread(state.nl_new_episodes.copies, tmdb_id, season, episode)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {"copies": copies}


@router.post("/api/nl/new-episodes/download")
async def nl_new_episode_download(body: NewDownloadRequest) -> Dict[str, Any]:
    """Finds the best copy (or uses the chosen `copy_id`) and starts it. Progress shows on Downloads."""
    try:
        picked = await asyncio.to_thread(state.nl_new_episodes.pick, body.tmdb_id, body.season, body.episode, body.copy_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    item = await asyncio.to_thread(state.db.get_item_by_id, picked["media_item_id"])
    title = ((item or {}).get("title") or picked["title"]).strip()
    download_id = start_download(picked["media_item_id"], picked["magnet"], title, "tv")
    state.nl_new_episodes.forget_lists()
    return {"success": True, "download_id": download_id, "media_item_id": picked["media_item_id"],
            "release": picked["release"], "message": f"Downloading {picked['release']}"}
