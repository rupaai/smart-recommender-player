"""
Northern Lights HTML pages: phone/web (/nl/), TV (/nl/tv/) and laptop (/player/).
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from services.nl_people import safe_next
from web import state
from web.helpers import _nl_choose, _nl_page

router = APIRouter()


@router.get("/nl/", response_class=HTMLResponse)
async def nl_home(request: Request) -> Any:
    return _nl_page(request, "nl/home.html", {"active": "home"})


@router.get("/nl/movies", response_class=HTMLResponse)
async def nl_movies_page(request: Request) -> Any:
    return _nl_page(request, "nl/browse.html", {"active": "movies", "kind": "movie", "heading": "Movies"})


@router.get("/nl/shows", response_class=HTMLResponse)
async def nl_shows_page(request: Request) -> Any:
    return _nl_page(request, "nl/browse.html", {"active": "shows", "kind": "tv", "heading": "Shows"})


@router.get("/nl/title/{item_id}", response_class=HTMLResponse)
async def nl_title_page(item_id: int, request: Request) -> Any:
    return _nl_page(request, "nl/title.html", {"active": "", "item_id": item_id})


@router.get("/nl/show/{item_id}", response_class=HTMLResponse)
async def nl_show_page(item_id: int, request: Request) -> Any:
    return _nl_page(request, "nl/show.html", {"active": "shows", "item_id": item_id})


@router.get("/nl/watchlist", response_class=HTMLResponse)
async def nl_watchlist_page(request: Request) -> Any:
    return _nl_page(request, "nl/watchlist.html", {"active": "watchlist"})


@router.get("/nl/choose", response_class=HTMLResponse)
async def nl_choose_page(request: Request) -> Any:
    return _nl_page(request, "nl/choose.html", {"active": "choose"})


@router.get("/nl/downloads", response_class=HTMLResponse)
async def nl_downloads_page(request: Request) -> Any:
    return _nl_page(request, "nl/downloads.html", {"active": "downloads"})


@router.get("/nl/admin", response_class=HTMLResponse)
async def nl_admin_page(request: Request) -> Any:
    """Web only: there is deliberately no TV version and no link from the TV pages."""
    return _nl_page(request, "nl/admin.html", {"active": "admin"})


@router.get("/nl/me", response_class=HTMLResponse)
async def nl_me_page(request: Request) -> Any:
    """The viewer's own settings: their languages and subtitles, and who's watching."""
    return _nl_page(request, "nl/me.html", {"active": "me"})


@router.get("/nl/tv/", response_class=HTMLResponse)
async def nl_tv_home(request: Request) -> Any:
    return _nl_page(request, "nl/tv/home.html", {"page": "home"}, "tv")


@router.get("/nl/tv/search", response_class=HTMLResponse)
async def nl_tv_search(request: Request) -> Any:
    return _nl_page(request, "nl/tv/search.html", {"page": "search"}, "tv")


@router.get("/nl/tv/title/{item_id}", response_class=HTMLResponse)
async def nl_tv_title(item_id: int, request: Request) -> Any:
    return _nl_page(request, "nl/tv/title.html", {"page": "title", "item_id": item_id}, "tv")


@router.get("/nl/tv/show/{item_id}", response_class=HTMLResponse)
async def nl_tv_show(item_id: int, request: Request) -> Any:
    return _nl_page(request, "nl/tv/show.html", {"page": "show", "item_id": item_id}, "tv")


@router.get("/nl/tv/watchlist", response_class=HTMLResponse)
async def nl_tv_watchlist(request: Request) -> Any:
    return _nl_page(request, "nl/tv/watchlist.html", {"page": "watchlist"}, "tv")


@router.get("/nl/tv/choose", response_class=HTMLResponse)
async def nl_tv_choose(request: Request) -> Any:
    return _nl_page(request, "nl/tv/choose.html", {"page": "choose"}, "tv")


@router.get("/nl/tv/downloads", response_class=HTMLResponse)
async def nl_tv_downloads(request: Request) -> Any:
    return _nl_page(request, "nl/tv/downloads.html", {"page": "downloads"}, "tv")


@router.get("/nl/tv/me", response_class=HTMLResponse)
async def nl_tv_me(request: Request) -> Any:
    return _nl_page(request, "nl/tv/me.html", {"page": "me"}, "tv")


# --- The TV app for a laptop browser at /player (same templates and tv.js as /nl/tv/) ---
@router.get("/player")
async def player_root() -> Any:
    return RedirectResponse("/player/", status_code=302)


@router.get("/player/", response_class=HTMLResponse)
async def player_home(request: Request) -> Any:
    return _nl_page(request, "nl/tv/home.html", {"page": "home"}, "laptop")


@router.get("/player/search", response_class=HTMLResponse)
async def player_search(request: Request) -> Any:
    return _nl_page(request, "nl/tv/search.html", {"page": "search"}, "laptop")


@router.get("/player/title/{item_id}", response_class=HTMLResponse)
async def player_title(item_id: int, request: Request) -> Any:
    return _nl_page(request, "nl/tv/title.html", {"page": "title", "item_id": item_id}, "laptop")


@router.get("/player/show/{item_id}", response_class=HTMLResponse)
async def player_show(item_id: int, request: Request) -> Any:
    return _nl_page(request, "nl/tv/show.html", {"page": "show", "item_id": item_id}, "laptop")


@router.get("/player/watchlist", response_class=HTMLResponse)
async def player_watchlist(request: Request) -> Any:
    return _nl_page(request, "nl/tv/watchlist.html", {"page": "watchlist"}, "laptop")


@router.get("/player/choose", response_class=HTMLResponse)
async def player_choose(request: Request) -> Any:
    return _nl_page(request, "nl/tv/choose.html", {"page": "choose"}, "laptop")


@router.get("/player/downloads", response_class=HTMLResponse)
async def player_downloads(request: Request) -> Any:
    return _nl_page(request, "nl/tv/downloads.html", {"page": "downloads"}, "laptop")


@router.get("/player/me", response_class=HTMLResponse)
async def player_me(request: Request) -> Any:
    return _nl_page(request, "nl/tv/me.html", {"page": "me"}, "laptop")


@router.get("/player/who", response_class=HTMLResponse)
async def player_who_page(request: Request, next: Optional[str] = None) -> Any:
    if not state.nl_people.configured:
        return RedirectResponse("/player/", status_code=302)
    return state.templates.TemplateResponse(request=request, name="nl/tv/who.html", context={
        "page": "who", "choices": state.nl_people.choices(), "next": safe_next(next, "/player/"), "viewer": None,
        "people_configured": True, "tv": True, "choose_base": "/player/who/", "surface": "laptop", "root": "/player/",
    })


@router.get("/player/who/{key}")
async def player_who_choose(key: str, next: Optional[str] = None) -> Any:
    return _nl_choose(key, next, "laptop")


@router.get("/player/play/{item_id}", response_class=HTMLResponse)
async def player_play_page(item_id: int, request: Request) -> Any:
    """The player page in laptop mode (?source= picks an episode)."""
    return _nl_page(request, "nl/player.html", {"item_id": item_id}, "laptop")
