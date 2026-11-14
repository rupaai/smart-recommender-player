"""
Northern Lights "Get it": search a film or show on TMDB, see every season of a show on one page, and download
the best pick or a copy you choose. Logic: services/nl_get_it.py. The copies and the download itself are the
New & popular ones (GET /api/nl/new-episodes/copies, POST /api/nl/new-episodes/download in web/routes/nl_new.py).
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Query

from web import state
from web.routes.nl_new import _downloading_ids

router = APIRouter()

NO_TMDB = "TMDB isn't set up on the server, so titles can't be looked up."


@router.get("/api/nl/get/search")
async def nl_get_search(q: str = Query("", max_length=120), kind: str = Query("all", pattern="^(all|movie|tv)$")) -> Dict[str, Any]:
    """Films and shows that match the text, each with what's already on the server."""
    if not state.api_key:
        raise HTTPException(status_code=503, detail=NO_TMDB)
    try:
        return await asyncio.to_thread(state.nl_get_it.search, q, kind)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/api/nl/get/item/{media_item_id}")
async def nl_get_for_item(media_item_id: int) -> Dict[str, Any]:
    """Where "Find a copy" on a catalogue title goes: its TMDB id, or its name to search for."""
    try:
        return await asyncio.to_thread(state.nl_get_it.for_item, media_item_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/api/nl/get/{kind}/{tmdb_id}")
async def nl_get_title(kind: str, tmdb_id: int) -> Dict[str, Any]:
    """A film with its download, or a show with every season: out, on the server, missing."""
    if kind not in ("movie", "tv") or tmdb_id < 1:
        raise HTTPException(status_code=404, detail="No such title.")
    if not state.api_key:
        raise HTTPException(status_code=503, detail=NO_TMDB)
    try:
        data = await asyncio.to_thread(state.nl_get_it.title, kind, tmdb_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    data["downloading"] = bool(data.get("media_item_id") in _downloading_ids())
    return data
