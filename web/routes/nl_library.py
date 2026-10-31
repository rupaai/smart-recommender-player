"""
Northern Lights library: home rows, title and show pages, reactions, watched marks,
hidden titles, watch-together pacts, show settings, player extras, next episode, search.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request

from services import nl_reactions, nl_together
from services.jellyfin_playback import (
    ItemNotFound,
    JellyfinUnavailable,
    valid_jellyfin_id,
)
from services.nl_people import Viewer
from services.nl_player_extras import empty_extras, player_extras
from web import state
from web.helpers import (
    _as_user,
    _nl_effective_prefs,
    _nl_prefs_row,
    _nl_subtitle_item,
    _nl_viewer_or_default,
    _report_to_users,
)
from web.models import (
    NLHiddenBody,
    NLPactBody,
    NLReactBody,
    NLShowPrefs,
    NLSkipIntro,
    NLUnwatchBody,
)

router = APIRouter()


def _react_profiles(viewer: Viewer) -> List[int]:
    return nl_reactions.profiles_to_rate(viewer.profile_ids, viewer.implicit)


@router.get("/api/nl/react/{item_id}")
async def nl_react_get(item_id: int, request: Request) -> Dict[str, Any]:
    """W10: should the player ask "How was it?" now. `ask` is false for an unknown title, a viewer with no
    profile, or when everyone at the screen already gave this title a verdict of their own."""
    viewer = _nl_viewer_or_default(request)

    def work() -> Dict[str, Any]:
        item = state.db.get_item_by_id(item_id)
        profiles = _react_profiles(viewer)
        asked = bool(item and profiles) and not all(
            nl_reactions.has_own_verdict(state.db.get_rating_details(item_id, pid)) for pid in profiles
        )
        return {
            "ask": asked,
            "text": nl_reactions.question(item["title"], viewer.is_both) if item else "",
            "options": [{"id": r["id"], "label": r["label"]} for r in nl_reactions.REACTIONS],
        }

    return await asyncio.to_thread(work)


@router.post("/api/nl/react/{item_id}")
async def nl_react_post(item_id: int, body: NLReactBody, request: Request) -> Dict[str, Any]:
    """W10: records the answer as a verdict for the profile(s) at the screen ("Both of us" rates for both)."""
    viewer = _nl_viewer_or_default(request)
    verdict = nl_reactions.verdict_for(body.reaction)
    if verdict is None:
        raise HTTPException(status_code=400, detail="Unknown reaction.")
    profiles = _react_profiles(viewer)
    if not profiles:
        raise HTTPException(status_code=400, detail="No profile to rate for.")

    def work() -> bool:
        if not state.db.get_item_by_id(item_id):
            return False
        for pid in profiles:
            state.db.rate_item(item_id, profile_id=pid, status="seen", verdict=verdict)
        return True

    if not await asyncio.to_thread(work):
        raise HTTPException(status_code=404, detail="That title isn't in the library.")
    return {"success": True, "verdict": verdict, "profiles": len(profiles)}


async def _nl_set_played(jellyfin_id: str, played: bool, request: Request) -> Dict[str, Any]:
    if not valid_jellyfin_id(jellyfin_id):
        raise HTTPException(status_code=400, detail="Bad item id.")
    viewer = _nl_viewer_or_default(request)
    try:
        await asyncio.to_thread(
            _report_to_users, viewer.jellyfin_users, lambda user: state.jellyfin_playback.set_played(jellyfin_id, played)
        )
    except JellyfinUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {"success": True, "played": played}


@router.post("/api/nl/played/{jellyfin_id}")
async def nl_mark_played(jellyfin_id: str, request: Request) -> Dict[str, Any]:
    """Mark a title as watched in Jellyfin (for both users when "Both of us")."""
    return await _nl_set_played(jellyfin_id, True, request)


@router.delete("/api/nl/played/{jellyfin_id}")
async def nl_mark_unplayed(jellyfin_id: str, request: Request) -> Dict[str, Any]:
    """Mark a title as not watched in Jellyfin."""
    return await _nl_set_played(jellyfin_id, False, request)


@router.get("/api/nl/library")
async def nl_library(
    request: Request,
    type: str = Query("movie", pattern="^(movie|tv)$"),
    unwatched: bool = Query(False, description="only titles nobody at the screen has watched"),
) -> Dict[str, Any]:
    """Cards for every movie or show that has a file. Movies partly watched carry a progress
    bar, the latest additions a "New" badge. `unwatched` (M7b) keeps the titles nobody at the screen has
    watched; `unwatched_applied` says whether Jellyfin could tell. Works with Jellyfin down (no progress then)."""
    viewer = _nl_viewer_or_default(request)
    return await asyncio.to_thread(_as_user, viewer.jellyfin_users[0], state.nl_browse.library_page, type, viewer.jellyfin_users, unwatched)


@router.get("/api/nl/home")
async def nl_home_data(request: Request) -> Dict[str, Any]:
    """Home: Continue Watching (Jellyfin Resume of the viewer), Recently Added, Recently Watched and the
    newest movies and shows. Titles this viewer hid (M7b) are left out of every row except Continue Watching."""
    viewer = _nl_viewer_or_default(request)
    hidden = await asyncio.to_thread(state.db.get_hidden_ids, viewer.key)
    return await asyncio.to_thread(_as_user, viewer.jellyfin_users[0], state.nl_browse.home, viewer.jellyfin_users, hidden)


@router.get("/api/nl/hidden/{item_id}")
async def nl_hidden_get(item_id: int, request: Request) -> Dict[str, Any]:
    """Has this viewer hidden the title from the home rows?"""
    viewer = _nl_viewer_or_default(request)
    return {"hidden": item_id in await asyncio.to_thread(state.db.get_hidden_ids, viewer.key)}


@router.put("/api/nl/hidden/{item_id}")
async def nl_hidden_set(item_id: int, body: NLHiddenBody, request: Request) -> Dict[str, Any]:
    """"Hide from home" / "Show on home again" for the person at the screen ("Both of us" has its own list)."""
    viewer = _nl_viewer_or_default(request)
    try:
        value = await asyncio.to_thread(state.db.set_hidden, viewer.key, item_id, body.hidden)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"hidden": value}


@router.get("/api/nl/title/{item_id}")
async def nl_title_data(item_id: int, request: Request) -> Dict[str, Any]:
    viewer = _nl_viewer_or_default(request)
    data = await asyncio.to_thread(_as_user, viewer.jellyfin_users[0], state.nl_browse.title, item_id, viewer.jellyfin_users)
    if not data:
        raise HTTPException(status_code=404, detail="That title isn't in the library.")
    data["hidden"] = item_id in await asyncio.to_thread(state.db.get_hidden_ids, viewer.key)
    together = nl_together.together_possible(state.nl_people.people)
    data["together"] = bool(together and data.get("media_type") != "tv" and 0 in await asyncio.to_thread(state.db.get_seen_together, item_id))
    return data


@router.get("/api/nl/title/{item_id}/extras")
async def nl_title_extras(item_id: int, request: Request) -> Dict[str, Any]:
    """Cast & crew, the Trailer (a YouTube key + address, never a TMDB or Jellyfin key) and Tech details.
    Each part is just empty when its source (Jellyfin, TMDB) is away; only an unknown title is an error."""
    viewer = _nl_viewer_or_default(request)
    data = await asyncio.to_thread(_as_user, viewer.jellyfin_users[0], state.nl_extras.title_extras, item_id)
    if not data:
        raise HTTPException(status_code=404, detail="That title isn't in the library.")
    return data


@router.get("/api/nl/title/{item_id}/more")
async def nl_title_more(item_id: int, request: Request) -> Dict[str, Any]:
    """"More like this": library titles close to this one (TMDB similar, the recommender's vibe index, shared
    genres). `items` may be empty; it degrades part by part and never fails for a missing source."""
    viewer = _nl_viewer_or_default(request)
    data = await asyncio.to_thread(_as_user, viewer.jellyfin_users[0], state.nl_extras.more_like_this, item_id)
    if data is None:
        raise HTTPException(status_code=404, detail="That title isn't in the library.")
    return data


@router.get("/api/nl/show/{item_id}")
async def nl_show_data(item_id: int, request: Request) -> Dict[str, Any]:
    """A show's seasons and episodes with watched state, and its Next up episode (the viewer's;
    for "Both of us" the first person's)."""
    viewer = _nl_viewer_or_default(request)
    data = await asyncio.to_thread(_as_user, viewer.jellyfin_users[0], state.nl_browse.show, item_id)
    if not data:
        raise HTTPException(status_code=404, detail="That show isn't in the library.")
    await asyncio.to_thread(_add_together_to_show, data, item_id, viewer)
    data["autoplay"] = await asyncio.to_thread(_autoplay_for, item_id, viewer, data.get("autoplay"))
    return data


def _add_together_to_show(data: Dict[str, Any], item_id: int, viewer: Viewer) -> None:
    """M9a: the pact switch, the episodes seen together and (for the person-specific "Mark unwatched")
    who can be reset. With fewer than two people configured none of it is offered."""
    possible = nl_together.together_possible(state.nl_people.people)
    seen = state.db.get_seen_together(item_id) if possible else set()
    for season in data.get("seasons") or []:
        for ep in season.get("episodes") or []:
            ep["together"] = ep["source_id"] in seen
    data["together_count"] = len(seen)
    data["pact"] = {"available": possible, "together": bool(possible and state.db.get_pact(item_id))}
    data["unwatch_people"] = _unwatch_choices(viewer)
    data["hide_spoilers"] = bool(_nl_prefs_row(viewer).get("hide_spoilers"))   # M9c


def _unwatch_choices(viewer: Viewer) -> List[Dict[str, str]]:
    """Whom "Mark unwatched" can reset from this screen: the one person, or each of the two on "Both of us".
    Empty list = the implicit single viewer (no person to name)."""
    return [{"id": p.id, "name": p.name} for p in viewer.people]


@router.get("/api/nl/show/{item_id}/pact")
async def nl_show_pact_get(item_id: int) -> Dict[str, Any]:
    """Is "We watch this together" on for this show? Only offered with two or more people configured."""
    possible = nl_together.together_possible(state.nl_people.people)
    return {"available": possible, "together": bool(possible and await asyncio.to_thread(state.db.get_pact, item_id))}


@router.put("/api/nl/show/{item_id}/pact")
async def nl_show_pact_set(item_id: int, body: NLPactBody) -> Dict[str, Any]:
    """Switches the watch-together pact for a show on or off (it belongs to the pair, not to one person)."""
    if not nl_together.together_possible(state.nl_people.people):
        raise HTTPException(status_code=400, detail="Watching together needs two people in the config.")
    try:
        value = await asyncio.to_thread(state.db.set_pact, item_id, body.together)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"available": True, "together": value}


@router.post("/api/nl/unwatch/{item_id}")
async def nl_unwatch(item_id: int, body: NLUnwatchBody, request: Request) -> Dict[str, Any]:
    """U6, "Mark unwatched for me": resets the played flag and resume position of ONE person's Jellyfin
    user for an episode or movie. On "Both of us" `person` says whose; nobody else's data is touched. The
    together mark of that title goes too, since it is no longer seen by both."""
    viewer = _nl_viewer_or_default(request)
    if viewer.implicit:
        person_user: Optional[str] = None
        person_id = None
    else:
        wanted = (body.person or "").strip().lower() or (viewer.primary.id if not viewer.is_both else "")
        if not wanted:
            raise HTTPException(status_code=400, detail="Say whose watched state to reset.")
        person = next((p for p in viewer.people if p.id == wanted), None)
        if person is None:
            raise HTTPException(status_code=403 if state.nl_people.get(wanted) else 400, detail="That person isn't at the screen.")
        person_user, person_id = person.jellyfin_user_id, person.id
    item = _nl_subtitle_item(item_id, body.source_id)
    try:
        jf_id = await asyncio.to_thread(state.jellyfin_playback._subtitle_item_id, item)
        await asyncio.to_thread(_as_user, person_user, state.jellyfin_playback.reset_user_data, jf_id)
    except ItemNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except JellyfinUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    await asyncio.to_thread(state.db.clear_seen_together, item_id, item["sources"][0]["id"] if item.get("media_type") == "tv" else 0)
    return {"success": True, "person": person_id}


@router.get("/api/nl/show/{item_id}/prefs")
async def nl_show_prefs_get(item_id: int) -> Dict[str, Any]:
    return {"autoplay": await asyncio.to_thread(state.db.get_show_autoplay, item_id)}


@router.put("/api/nl/show/{item_id}/prefs")
async def nl_show_prefs_set(item_id: int, prefs: NLShowPrefs) -> Dict[str, Any]:
    """Autoplay of the next episode, per show (on by default)."""
    try:
        value = await asyncio.to_thread(state.db.set_show_autoplay, item_id, prefs.autoplay)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"autoplay": value}


@router.get("/api/nl/show/{item_id}/skip-intro")
async def nl_show_skip_intro_get(item_id: int, request: Request) -> Dict[str, Any]:
    """Does this viewer always skip this show's intro? (per viewer, off by default)"""
    viewer = _nl_viewer_or_default(request)
    return {"skip_intro": await asyncio.to_thread(state.db.get_show_skip_intro, item_id, viewer.key)}


@router.put("/api/nl/show/{item_id}/skip-intro")
async def nl_show_skip_intro_set(item_id: int, body: NLSkipIntro, request: Request) -> Dict[str, Any]:
    """The per-show "always skip intro" switch of the person at the screen (Both of us has its own)."""
    viewer = _nl_viewer_or_default(request)
    try:
        value = await asyncio.to_thread(state.db.set_show_skip_intro, item_id, viewer.key, body.skip_intro)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"skip_intro": value}


def _player_extras_for(item: Dict[str, Any]) -> Dict[str, Any]:
    """Trickplay, chapters and skip segments of one title or episode, read from Jellyfin here. Any
    Jellyfin trouble leaves the parts empty: these features simply don't show."""
    try:
        jf_id = state.jellyfin_playback._subtitle_item_id(item)
    except (ItemNotFound, JellyfinUnavailable):
        return empty_extras()
    return player_extras(state.jellyfin_playback, jf_id)


@router.get("/api/nl/player-extras/{item_id}")
async def nl_player_extras(
    request: Request,
    item_id: int,
    source: Optional[int] = Query(None, ge=1, description="media_sources.id: which episode of a show"),
) -> Dict[str, Any]:
    """What the player draws over the picture, all read here (the Jellyfin key never leaves):
    `trickplay` (seek-bar thumbnail sheets: geometry + a /nl/jf/ URL template, or null), `chapters`
    ([{start_seconds, name}]) and `segments` ([{type intro|outro, start_seconds, end_seconds}], from
    Jellyfin's MediaSegments, e.g. the Intro Skipper plugin). `skip_intro`: this viewer's switch for the show.
    Jellyfin down, an old Jellyfin or a missing plugin only leave the parts empty; never an error."""
    viewer = _nl_viewer_or_default(request)
    item = _nl_subtitle_item(item_id, source)
    extras = await asyncio.to_thread(_as_user, viewer.jellyfin_users[0], _player_extras_for, item)
    is_show = item.get("media_type") == "tv"
    extras["skip_intro"] = bool(is_show and await asyncio.to_thread(state.db.get_show_skip_intro, item_id, viewer.key))
    extras["is_show"] = is_show
    return extras


@router.get("/api/nl/next/{item_id}")
async def nl_next_episode(request: Request, item_id: int, source: int = Query(..., ge=1)) -> Dict[str, Any]:
    """The episode after `source` for the player's next-episode card (has_next false at the end)."""
    viewer = _nl_viewer_or_default(request)
    data = await asyncio.to_thread(_as_user, viewer.jellyfin_users[0], state.nl_browse.next_episode, item_id, source)
    if isinstance(data, dict) and "autoplay" in data:
        data["autoplay"] = await asyncio.to_thread(_autoplay_for, item_id, viewer, data["autoplay"])
    return data


def _autoplay_for(item_id: int, viewer: Viewer, fallback: Any) -> bool:
    """Start the next episode by itself? The show's own switch if anyone ever set it, else this viewer's default
    (M10c, H5; on unless they turned it off)."""
    own = state.db.get_show_autoplay_row(item_id)
    if own is not None:
        return own
    return bool(_nl_effective_prefs(viewer).autoplay)


@router.get("/api/nl/search")
async def nl_search(request: Request, q: str = Query("", max_length=100), limit: int = Query(20, ge=1, le=40)) -> Dict[str, Any]:
    """Movies, shows and episodes matching the text. Each result is Ready (in the library,
    with a link) or Not in library yet (with a link to the Download tab of the main app)."""
    viewer = _nl_viewer_or_default(request)
    results = await asyncio.to_thread(_as_user, viewer.jellyfin_users[0], state.nl_browse.search, q, limit)
    return {"query": q, "results": results}
