"""
Northern Lights "who's watching": the viewer cookie, the who pages and per-person settings.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from services.nl_people import (
    Prefs,
    Viewer,
    clean_prefs,
    safe_next,
)
from web import state
from web.helpers import _nl_choose, _nl_prefs_row, _nl_viewer, _nl_viewer_or_default

router = APIRouter()


@router.get("/api/nl/viewer")
async def nl_viewer_info(request: Request) -> Dict[str, Any]:
    """Who is watching: the choices for the who's-watching screen, the chosen viewer (null = not
    chosen yet) and the recommender profiles their ratings go to. Never carries Jellyfin ids."""
    viewer = _nl_viewer(request)
    return {
        "configured": state.nl_people.configured,
        "choices": state.nl_people.choices(),
        "viewer": viewer.public() if viewer else None,
        "profile_ids": viewer.profile_ids if viewer else [],
    }


@router.get("/nl/who", response_class=HTMLResponse)
async def nl_who_page(request: Request, next: Optional[str] = None) -> Any:
    """Who's watching? Without configured people there is nothing to choose: straight home."""
    if not state.nl_people.configured:
        return RedirectResponse("/nl/", status_code=302)
    return state.templates.TemplateResponse(request=request, name="nl/who.html", context={
        "active": "", "choices": state.nl_people.choices(), "next": safe_next(next, "/nl/"), "viewer": None,
        "people_configured": True, "tv": False, "choose_base": "/nl/who/",
    })


@router.get("/nl/who/{key}")
async def nl_who_choose(key: str, next: Optional[str] = None) -> Any:
    return _nl_choose(key, next, "web")


@router.get("/nl/tv/who", response_class=HTMLResponse)
async def nl_tv_who_page(request: Request, next: Optional[str] = None) -> Any:
    if not state.nl_people.configured:
        return RedirectResponse("/nl/tv/", status_code=302)
    return state.templates.TemplateResponse(request=request, name="nl/tv/who.html", context={
        "page": "who", "choices": state.nl_people.choices(), "next": safe_next(next, "/nl/tv/"), "viewer": None,
        "people_configured": True, "tv": True, "choose_base": "/nl/tv/who/", "surface": "tv", "root": "/nl/tv/",
    })


@router.get("/nl/tv/who/{key}")
async def nl_tv_who_choose(key: str, next: Optional[str] = None) -> Any:
    return _nl_choose(key, next, "tv")


def _nl_prefs_reply(viewer: Viewer) -> Dict[str, Any]:
    row = _nl_prefs_row(viewer)
    return {"viewer": viewer.public(), "prefs": Prefs.from_row(row).public(), "saved": bool(row)}


@router.get("/api/nl/prefs")
async def nl_prefs_get(request: Request) -> Dict[str, Any]:
    """The current viewer's language and subtitle prefs (audio language, subtitle language, subtitles on
    by default, the subtitle look, the subtitle search language). Per person, not per device."""
    viewer = _nl_viewer_or_default(request)
    return await asyncio.to_thread(_nl_prefs_reply, viewer)


@router.put("/api/nl/prefs")
async def nl_prefs_put(request: Request, body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Updates the fields sent (null / "" clears a language); the rest stay. 400 on a bad value."""
    viewer = _nl_viewer_or_default(request)
    try:
        fields = clean_prefs(body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await asyncio.to_thread(state.db.set_person_prefs, viewer.key, fields)
    return await asyncio.to_thread(_nl_prefs_reply, viewer)
