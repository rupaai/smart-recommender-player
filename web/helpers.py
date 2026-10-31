"""
Small helpers shared by several route files (who is watching, page rendering, Jellyfin checks).
"""

from __future__ import annotations

import os
import socket
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import requests
from fastapi import HTTPException, Request
from fastapi.responses import (
    RedirectResponse,
    Response,
)

from services.jellyfin_playback import JellyfinUnavailable
from services.nl_people import (
    BOTH,
    COOKIE_MAX_AGE,
    COOKIE_NAME,
    Prefs,
    Viewer,
    safe_next,
)
from web import state


def get_local_ip() -> str:
    """Discovers host machine's Wi-Fi / LAN IP address."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def jellyfin_link_base(request: Request) -> str:
    """Jellyfin's address as the browser sees it: the host the page was loaded from, port 8096.
    (The app's own address is the container's inside Docker, so it is no use in a link.)"""
    return f"http://{request.url.hostname or 'localhost'}:8096"


def is_jellyfin_online() -> bool:
    """Checks if Jellyfin is responding, at JELLYFIN_URL (the app cannot reach it as localhost in Docker)."""
    try:
        resp = requests.get(f"{os.getenv('JELLYFIN_URL', 'http://localhost:8096').rstrip('/')}/web/", timeout=1.5)
        return resp.status_code == 200
    except Exception:
        return False


def _with_on_list(items: Optional[List[Dict[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
    """Adds `on_list` (profile ids with the title on Watch later) to each item, one query for all.
    Never raises: on failure items get an empty list."""
    if not items:
        return items
    try:
        ids = [i["id"] for i in items if isinstance(i, dict) and i.get("id") is not None]
        found = state.db.get_watch_later_ids(ids) if ids else {}
    except Exception as e:  # noqa: BLE001
        print(f"[WatchLater] on_list lookup failed: {e}", flush=True)
        found = {}
    for i in items:
        if isinstance(i, dict):
            i["on_list"] = list(found.get(i.get("id"), []))
    return items


def _log_outcome(media_item_id: Optional[int], outcome: str, rating: Optional[float] = None) -> None:
    """Attributes a play / rating / download to the Discover impression that
    showed this title recently, if any. Never raises."""
    if not media_item_id:
        return
    try:
        state.db.record_outcome(int(media_item_id), outcome, rating=rating)
    except Exception as e:  # noqa: BLE001
        print(f"[Feedback] outcome logging failed: {e}", flush=True)


# --- Northern Lights who's watching (M4) ---
def _nl_viewer(request: Optional[Request]) -> Optional[Viewer]:
    """The viewer the nl_viewer cookie names; None = not chosen (or unknown). Always the implicit
    viewer when no people are configured."""
    header = request.headers.get("cookie") if request is not None else None
    return state.nl_people.parse_cookie(header)


def _nl_viewer_or_default(request: Optional[Request]) -> Viewer:
    """For API calls: the chosen viewer, else the first person (the pages ask before they get here)."""
    return _nl_viewer(request) or state.nl_people.default_viewer()


def _as_user(user: Optional[str], fn, *args):
    """Runs fn(*args) with Jellyfin calls made for this user (set inside the worker thread)."""
    with state.jellyfin_playback.as_user(user):
        return fn(*args)


def _nl_prefs_row(viewer: Viewer) -> Dict[str, Any]:
    """The prefs a viewer lives by: their own row. For "Both of us" the first person's, with anything
    set separately for Both laid over it."""
    if viewer.is_both:
        base = state.db.get_person_prefs(viewer.primary.id) or {} if viewer.primary else {}
        over = state.db.get_person_prefs(BOTH) or {}
        return {**base, **{k: v for k, v in over.items() if v is not None}}
    return state.db.get_person_prefs(viewer.key) or {}


def _nl_effective_prefs(viewer: Viewer) -> Prefs:
    return Prefs.from_row(_nl_prefs_row(viewer))


# Where each surface lives: the phone/web pages, the TV app, and the TV app in a laptop browser.
NL_SURFACE_ROOTS = {"web": "/nl/", "tv": "/nl/tv/", "laptop": "/player/"}


def _nl_page(request: Request, name: str, context: Dict[str, Any], surface: str = "web") -> Any:
    """A page of one surface (web, tv or laptop). With people configured and nobody chosen yet, the
    who's-watching screen of that surface first."""
    root = NL_SURFACE_ROOTS[surface]
    viewer = _nl_viewer(request)
    if viewer is None:
        here = request.url.path + (("?" + request.url.query) if request.url.query else "")
        return RedirectResponse(f"{root}who?next={quote(here, safe='')}", status_code=302)
    # "tv" stays true for the laptop: it uses the TV templates.
    ctx = {**context, "viewer": viewer.public(), "people_configured": state.nl_people.configured,
           "tv": surface != "web", "surface": surface, "root": root}
    return state.templates.TemplateResponse(request=request, name=name, context=ctx)


def _nl_choose(key: str, next_url: Optional[str], surface: str = "web") -> Response:
    root = NL_SURFACE_ROOTS[surface]
    target = safe_next(next_url, root)
    if not state.nl_people.configured or state.nl_people.viewer_for(key) is None:
        return RedirectResponse(f"{root}who?next={quote(target, safe='')}", status_code=302)
    resp = RedirectResponse(target, status_code=302)
    resp.set_cookie(COOKIE_NAME, key.lower(), max_age=COOKIE_MAX_AGE, path="/", samesite="lax", httponly=True)
    return resp


def _nl_subtitle_item(item_id: int, source: Optional[int]) -> Dict[str, Any]:
    """The title (one episode of a show when `source` is given) the subtitle calls are about."""
    item = state.db.get_nl_title(item_id)
    if not item or not item.get("sources"):
        raise HTTPException(status_code=404, detail="Media file not found.")
    chosen = item["sources"][0]
    if source is not None:
        chosen = next((s for s in item["sources"] if s["id"] == source), None)
        if chosen is None:
            raise HTTPException(status_code=404, detail="That episode isn't in the library.")
    return {**item, "sources": [chosen]}


def _report_to_users(users: List[Optional[str]], call) -> None:
    """Runs `call(user)` for every Jellyfin user ("Both of us" = two). One failing does not stop the
    other; it only raises (the first error) when none succeeded."""
    errors: List[JellyfinUnavailable] = []
    done = 0
    for user in users:
        try:
            with state.jellyfin_playback.as_user(user):
                call(user)
            done += 1
        except JellyfinUnavailable as e:
            errors.append(e)
    if errors and not done:
        raise errors[0]
    for e in errors:
        print(f"[NL] one viewer's Jellyfin report failed: {e}", flush=True)
