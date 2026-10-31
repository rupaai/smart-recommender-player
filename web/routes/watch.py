"""
The original app's watching flow (start / finish / cancel), history, the Watch later list
and the two rating profiles.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request

from services import verdicts as vd
from web import state
from web.helpers import _log_outcome, _nl_viewer, jellyfin_link_base
from web.models import (
    CancelWatchRequest,
    FinishWatchRequest,
    RenameProfileRequest,
    StartWatchRequest,
    WatchLaterRequest,
)

router = APIRouter()


@router.get("/api/watch/current")
async def get_current_watch() -> Dict[str, Any]:
    """Returns active watching movie session, or empty object."""
    active = state.db.get_active_watch()
    return {"active": active}


@router.get("/api/stream/{item_id}")
async def stream_media(item_id: int, request: Request) -> Dict[str, Any]:
    """Returns the Jellyfin playback URL for the selected movie or episode."""
    item = state.db.get_item_by_id(item_id)
    if not item or not item.get("sources"):
        raise HTTPException(status_code=404, detail="Media file not found.")
    file_path = item["sources"][0]["file_path"]
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Video file not found on disk.")

    file_name = os.path.basename(file_path)
    jellyfin_url = f"{jellyfin_link_base(request)}/web/index.html#!/details?filename={file_name}"
    return {"jellyfin_url": jellyfin_url}


@router.post("/api/watch/start")
async def start_watch(req: StartWatchRequest) -> Dict[str, Any]:
    """Begins watching a movie session."""
    session_id = state.db.start_active_watch(req.media_item_id)
    _log_outcome(req.media_item_id, "played")
    active = state.db.get_active_watch()
    return {"success": True, "session_id": session_id, "active": active}


@router.post("/api/watch/finish")
async def finish_watch(req: FinishWatchRequest, request: Request) -> Dict[str, Any]:
    """Completes watch session with a verdict (or a legacy numeric rating) and notes.
    With who's-watching set up, the watch and rating are logged for the viewer's recommender
    profile(s) ("Both of us" = both) instead of always landing on profile 1."""
    try:
        verdict = vd.validate_verdict(req.verdict)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    with state.db.get_connection() as conn:
        row = conn.execute(
            "SELECT media_item_id FROM active_watch_sessions WHERE id = ?;", (req.session_id,)
        ).fetchone()
    viewer = _nl_viewer(request) if state.nl_people.configured else None
    profile_ids = req.profile_ids or (viewer.profile_ids if viewer else [])
    if verdict is not None and not profile_ids:
        if state.nl_people.configured:
            raise HTTPException(status_code=400, detail="No profile to rate for.")
        profile_ids = [1]
    if profile_ids:
        try:
            done = state.db.mark_watch_done_for_profiles(
                req.session_id, profile_ids, req.user_rating, req.notes, verdict=verdict
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        record_id = req.session_id if done else None
    else:
        record_id = state.db.finish_active_watch(
            session_id=req.session_id,
            user_rating=req.user_rating,
            notes=req.notes,
        )
    if not record_id:
        raise HTTPException(status_code=404, detail="Active session not found.")
    logged_rating = vd.legacy_rating(verdict) if verdict is not None else req.user_rating
    if logged_rating and row:
        _log_outcome(row["media_item_id"], "rated", logged_rating)
    if row:
        try:
            state.db.mark_watch_later_done(row["media_item_id"], "watched")
        except Exception as e:  # noqa: BLE001
            print(f"[WatchLater] could not mark {row['media_item_id']} done: {e}", flush=True)
    return {"success": True, "record_id": record_id}


@router.post("/api/watch/cancel")
async def cancel_watch(req: CancelWatchRequest) -> Dict[str, Any]:
    """Cancels an active watch session."""
    state.db.cancel_active_watch(req.session_id)
    return {"success": True}


@router.get("/api/history")
async def get_history() -> Dict[str, Any]:
    """Retrieves watch history and learned taste profile."""
    history = state.db.get_watch_history(limit=50)
    taste = state.db.get_user_taste_profile(min_rating=7.0)
    return {
        "history": history,
        "taste_profile": taste,
    }


# ---------------------------------------------------------------------------
# Watch later (shared list, schema v11)
# ---------------------------------------------------------------------------
@router.get("/api/watch-later")
async def get_watch_later(
    include_done: bool = False,
    media_type: Optional[str] = None,
    added_by: Optional[int] = None,
) -> Dict[str, Any]:
    """The shared list, one entry per title, newest add first."""
    items = await asyncio.to_thread(
        state.db.get_watch_later, include_done=include_done, media_type=media_type or None, added_by=added_by
    )
    return {"items": items, "count": len(items)}


@router.post("/api/watch-later")
async def add_watch_later(req: WatchLaterRequest) -> Dict[str, Any]:
    """Puts a title on the list for one person; returns the grouped entry."""
    try:
        return await asyncio.to_thread(state.db.add_to_watch_later, req.media_item_id, req.profile_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.delete("/api/watch-later/{media_item_id}")
async def remove_watch_later(media_item_id: int, profile_id: int) -> Dict[str, Any]:
    """Takes one person's add off the list."""
    removed = await asyncio.to_thread(state.db.remove_from_watch_later, media_item_id, profile_id)
    return {"removed": bool(removed)}


@router.post("/api/watch-later/{media_item_id}/done")
async def done_watch_later(media_item_id: int) -> Dict[str, Any]:
    """Marks a title on the list as watched (the Later tab's "Watched it")."""
    n = await asyncio.to_thread(state.db.mark_watch_later_done, media_item_id, "watched")
    return {"done": n}


@router.post("/api/watch-later/{media_item_id}/undo")
async def undo_watch_later(media_item_id: int) -> Dict[str, Any]:
    """Brings a title marked done back onto the list."""
    n = await asyncio.to_thread(state.db.undo_watch_later_done, media_item_id)
    return {"reopened": n}


@router.get("/api/profiles")
async def get_profiles() -> List[Dict[str, Any]]:
    """Lists the co-view rating profiles (Labiba and Gabriele) with live stats."""
    return state.db.get_profiles()


@router.put("/api/profiles/{profile_id}")
async def rename_profile(profile_id: int, req: RenameProfileRequest) -> Dict[str, Any]:
    """Renames a rating profile. 400 on unknown id or a duplicate name."""
    if not state.db.update_profile_name(profile_id, req.name):
        raise HTTPException(status_code=400, detail="Unknown profile id or duplicate name.")
    return {"ok": True, "profiles": state.db.get_profiles()}
