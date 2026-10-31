"""
Typing a TV search on the phone (/api/nl/type, /nl/type), the Chromecast receiver page
and the player page (/nl/play/{id}).
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Body, Header, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from services.nl_type import TypingError, normalise_code
from services.nl_type import describe as describe_type_error
from web import state
from web.helpers import _nl_page
from web.models import NLTypeSession, NLTypeText

router = APIRouter()


def _client_address(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _type_error(e: TypingError) -> JSONResponse:
    status, body = describe_type_error(e)
    headers = {"Retry-After": str(body["retry_after"])} if "retry_after" in body else None
    return JSONResponse(status_code=status, content=body, headers=headers)


@router.post("/api/nl/type/session")
async def nl_type_session(request: Request, body: NLTypeSession = Body(default_factory=NLTypeSession)) -> Any:
    """The TV search page asks for a typing code. Returns {code, key, expires_in}: the code goes on the screen (and in
    the QR), the key stays on the TV (it is how only that TV reads what the phone types). With the old key the old
    session is replaced: one session per TV."""
    try:
        return state.nl_type.create(_client_address(request), body.previous_key)
    except TypingError as e:
        return _type_error(e)


@router.get("/api/nl/type/{code}/poll")
async def nl_type_poll(
    code: str, request: Request, after: int = Query(0, ge=0), x_nl_type_key: Optional[str] = Header(None),
) -> Any:
    """The TV reads the latest phone text. Needs the session key (header X-NL-Type-Key)."""
    try:
        return state.nl_type.poll(code, x_nl_type_key, _client_address(request), after)
    except TypingError as e:
        return _type_error(e)


@router.delete("/api/nl/type/{code}")
async def nl_type_close(code: str, request: Request, x_nl_type_key: Optional[str] = Header(None)) -> Any:
    """The TV leaves the search page: its code stops working."""
    try:
        state.nl_type.close(code, x_nl_type_key, _client_address(request))
    except TypingError as e:
        return _type_error(e)
    return {"ok": True}


@router.post("/api/nl/type/{code}")
async def nl_type_send(code: str, body: NLTypeText, request: Request) -> Any:
    """The phone's text for the TV (the whole text each time, so a missed update heals itself). 404 once the code is
    gone, 422 for more than 100 characters, 429 when going too fast."""
    try:
        return state.nl_type.send(code, body.text, _client_address(request), body.submit)
    except TypingError as e:
        return _type_error(e)


def _type_page(request: Request, code: Optional[str], message: str = "", status: int = 200) -> Any:
    resp = state.templates.TemplateResponse(request=request, name="nl/type.html", context={"code": code or "", "message": message}, status_code=status)
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Referrer-Policy"] = "no-referrer"   # the code is in the address
    return resp


@router.get("/nl/type", response_class=HTMLResponse)
async def nl_type_enter(request: Request, code: Optional[str] = Query(None, max_length=32)) -> Any:
    """The phone page without a code: type the 6 characters from the TV (a code in the address goes on to its page)."""
    if code is not None:
        norm = normalise_code(code)
        if norm:
            return RedirectResponse(f"/nl/type/{norm}", status_code=302)
        return _type_page(request, None, "That code doesn't look right. It has 6 letters and digits.", 400)
    return _type_page(request, None)


@router.get("/nl/type/{code}", response_class=HTMLResponse)
async def nl_type_page(code: str, request: Request) -> Any:
    """The phone's keyboard page for one TV. No cookies, no who's-watching: the code is the only credential."""
    try:
        norm = state.nl_type.check(code, _client_address(request))
    except TypingError as e:
        return _type_page(request, None, e.message, e.status)
    return _type_page(request, norm)


@router.get("/nl/cast/receiver", response_class=HTMLResponse)
async def nl_cast_receiver(request: Request) -> Any:
    """The custom Chromecast Web Receiver (M8). Runs on the Chromecast, so it never has the viewer cookie and
    never calls our API: everything it shows arrives in the load request from the sender, and the artwork URL
    carries a stream token."""
    resp = state.templates.TemplateResponse(request=request, name="nl/cast_receiver.html", context={})
    resp.headers["Cache-Control"] = "no-store"
    return resp


@router.get("/nl/play/{item_id}", response_class=HTMLResponse)
async def nl_play_page(item_id: int, request: Request) -> Any:
    """Minimal full-screen player page for one movie or episode."""
    return _nl_page(request, "nl/player.html", {"item_id": item_id},
                    "tv" if request.query_params.get("tv") == "1" else "web")
