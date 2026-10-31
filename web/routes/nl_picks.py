"""
Northern Lights picks for two: the watchlist, yes / maybe / pass, the "For both of us" row,
Choose for us and vetoes.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Request

from services.nl_choose import clean_tz
from services.nl_picks import PicksError, parse_max_runtime
from web import state
from web.helpers import _nl_viewer_or_default
from web.models import NLChooseBody, NLInterestBody, NLVetoBody, NLWatchlistAdd

router = APIRouter()


def _picks_error(e: PicksError) -> HTTPException:
    return HTTPException(status_code=e.status, detail=str(e))


@router.get("/api/nl/watchlist")
async def nl_watchlist(request: Request, max_runtime: Optional[str] = None) -> Dict[str, Any]:
    """The watchlist for the viewer: titles both said yes to first, then one yes, then the rest, passed
    last. `max_runtime` (minutes, 90 or 120 in the chips) hides longer movies and shows."""
    viewer = _nl_viewer_or_default(request)
    return await asyncio.to_thread(state.nl_picks.watchlist, viewer, parse_max_runtime(max_runtime))


@router.get("/api/nl/watchlist/state")
async def nl_watchlist_state(request: Request) -> Dict[str, Any]:
    """What a card needs to paint its watchlist button and yes / maybe / pass: listed titles and each
    person's answers, plus who the people are and whether "Just me" is offered."""
    viewer = _nl_viewer_or_default(request)
    return await asyncio.to_thread(state.nl_picks.state, viewer)


@router.post("/api/nl/watchlist")
async def nl_watchlist_add(body: NLWatchlistAdd, request: Request) -> Dict[str, Any]:
    """Adds a title: shared by default (credited to everyone at the screen), or scope "me" for Just me."""
    viewer = _nl_viewer_or_default(request)
    try:
        return await asyncio.to_thread(state.nl_picks.add, viewer, body.media_item_id, body.scope)
    except PicksError as e:
        raise _picks_error(e) from e


@router.delete("/api/nl/watchlist/{item_id}")
async def nl_watchlist_remove(item_id: int, request: Request) -> Dict[str, Any]:
    """Takes a title off the watchlist (the shared list for both, plus the viewer's own Just me)."""
    viewer = _nl_viewer_or_default(request)
    return await asyncio.to_thread(state.nl_picks.remove, viewer, item_id)


@router.put("/api/nl/interest/{item_id}")
async def nl_interest_set(item_id: int, body: NLInterestBody, request: Request) -> Dict[str, Any]:
    """One person's yes / maybe / pass on a title (state null clears it)."""
    viewer = _nl_viewer_or_default(request)
    try:
        return await asyncio.to_thread(state.nl_picks.set_interest, viewer, item_id, body.state, body.person)
    except PicksError as e:
        raise _picks_error(e) from e


@router.get("/api/nl/picks")
async def nl_picks_row(request: Request, max_runtime: Optional[str] = None) -> Dict[str, Any]:
    """The home row "For both of us" ("Picks for you" with one person): the recommender in co-view mode
    for the viewer(s), library titles only, with a one-line reason per card."""
    viewer = _nl_viewer_or_default(request)
    return await asyncio.to_thread(state.nl_picks.picks, viewer, parse_max_runtime(max_runtime))


@router.get("/api/nl/choose/options")
async def nl_choose_options(request: Request, tz: Optional[str] = None) -> Dict[str, Any]:
    """What Choose for us offers: the recommender's moods, the shelf's genres, the length chips, what today's
    weekday and time of day suggest, whose turn it is to choose, and how many titles both said yes to."""
    viewer = _nl_viewer_or_default(request)
    return await asyncio.to_thread(state.nl_choose.options, viewer, clean_tz(tz))


@router.post("/api/nl/choose")
async def nl_choose_pick(body: NLChooseBody, request: Request) -> Dict[str, Any]:
    """One big pick for the viewer(s). Wishes (mood, genre, length) filter; the weekday and time of day fill
    in a mood and length when none was chosen; `exclude` is what "Another" has already shown; mode "yeses" is a
    random title both said yes to. `item` is null with a `note` when nothing fits."""
    viewer = _nl_viewer_or_default(request)
    try:
        return await asyncio.to_thread(
            state.nl_choose.choose, viewer, body.mood, body.genre, body.max_minutes, body.exclude, body.mode, clean_tz(body.tz)
        )
    except PicksError as e:
        raise _picks_error(e) from e


@router.post("/api/nl/choose/chosen/{item_id}")
async def nl_choose_chosen(item_id: int, request: Request) -> Dict[str, Any]:
    """The pick was played: remember who chose, so the next time says "Your turn, <the other person>"."""
    viewer = _nl_viewer_or_default(request)
    try:
        return await asyncio.to_thread(state.nl_choose.chosen, viewer, item_id)
    except PicksError as e:
        raise _picks_error(e) from e


@router.get("/api/nl/vetoes")
async def nl_vetoes_list(request: Request) -> Dict[str, Any]:
    """The titles off the table for this screen: "Not tonight" (until tomorrow 05:00) and each person's "Never"."""
    viewer = _nl_viewer_or_default(request)
    return await asyncio.to_thread(state.nl_choose.vetoes, viewer)


@router.post("/api/nl/veto/{item_id}")
async def nl_veto_set(item_id: int, body: NLVetoBody, request: Request) -> Dict[str, Any]:
    """"Not tonight" hides a title until tomorrow 05:00 local; "Never" hides it for that person. Both leave it
    out of Choose for us and the home row; the library, search and watchlist still show it."""
    viewer = _nl_viewer_or_default(request)
    try:
        return await asyncio.to_thread(state.nl_choose.veto, viewer, item_id, body.kind, body.person, clean_tz(body.tz))
    except PicksError as e:
        raise _picks_error(e) from e


@router.delete("/api/nl/veto/{item_id}")
async def nl_veto_clear(item_id: int, request: Request, kind: str = Query("never"), person: Optional[str] = None) -> Dict[str, Any]:
    """Bring a title back (undo "Not tonight" or "Never")."""
    viewer = _nl_viewer_or_default(request)
    try:
        return await asyncio.to_thread(state.nl_choose.unveto, viewer, item_id, kind, person)
    except PicksError as e:
        raise _picks_error(e) from e
