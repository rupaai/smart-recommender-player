"""
Northern Lights playback: what to play and how (/api/nl/play), casting, subtitles,
remembered tracks, the Jellyfin proxy (/nl/jf/, a strict allowlist) and progress reports.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional
from urllib.parse import quote

import requests
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import iterate_in_threadpool, run_in_threadpool
from fastapi.responses import (
    JSONResponse,
    Response,
    StreamingResponse,
)

from services import (
    nl_settings,
    nl_together,
    nl_track_memory,
)
from services.device_profiles import profile_for
from services.jellyfin_playback import (
    ItemNotFound,
    JellyfinUnavailable,
    NotPlayable,
    SubtitleError,
    is_image_path,
    rewrite_playlist,
    safe_proxy_path,
    strip_api_key,
    valid_jellyfin_id,
    valid_subtitle_id,
)
from services.nl_cast import (
    cast_payload,
    strip_param,
    token_values,
)
from services.nl_people import (
    Viewer,
    resume_summary,
)
from web import state
from web.helpers import (
    _as_user,
    _nl_effective_prefs,
    _nl_subtitle_item,
    _nl_viewer_or_default,
    _report_to_users,
)
from web.models import NLProgressRequest, NLSubtitleDownload, NLTrackChoice

router = APIRouter()


# --- Northern Lights player (M0): playback through our own proxy, never straight from Jellyfin ---
_PROXY_REQUEST_HEADERS = ("range", "if-range", "if-none-match", "if-modified-since")
_PROXY_RESPONSE_HEADERS = (
    "content-type", "content-length", "content-range", "accept-ranges", "etag", "last-modified",
)
_MAX_PLAYLIST_BYTES = 5 * 1024 * 1024


def _read_limited(resp: requests.Response, limit: int) -> bytes:
    """Reads at most `limit` bytes of a streamed response."""
    buf = bytearray()
    for chunk in resp.iter_content(chunk_size=64 * 1024):
        buf += chunk
        if len(buf) >= limit:
            break
    return bytes(buf)


@router.get("/api/nl/play/{item_id}")
async def nl_play_info(
    request: Request,
    item_id: int,
    audio: Optional[int] = Query(None, ge=0, description="Jellyfin audio stream index"),
    subtitle: Optional[int] = Query(None, ge=-1, description="Jellyfin subtitle stream index to burn in; -1 = none"),
    start: float = Query(0.0, ge=0, description="Start position in seconds"),
    caps: Optional[str] = Query(None, max_length=300, description="Codecs this browser can play, e.g. v=h264.hevc;a=aac;h=h264.aac"),
    source: Optional[int] = Query(None, ge=1, description="media_sources.id: which episode of a show (default: the first)"),
    client: Optional[str] = Query(None, pattern="^(tv|laptop)$", description="tv = the Android TV app (TV profile; caps ignored); laptop = the /player laptop app (original video; caps shape the profile)"),
    max_kbps: Optional[int] = Query(None, ge=0, le=200000, description="Quality for this viewing: 8000, 4000, 2000 or 1000 kbit/s; 0 = Auto; absent = the person's default"),
    allow_conversion: bool = Query(False, description="TV and laptop only: the viewer agreed to a converted copy because the original cannot be played"),
) -> Dict[str, Any]:
    """How to play one movie or episode: a proxied stream URL and its kind (file or hls),
    its audio and subtitle tracks, and where the viewer left off. For the viewer (who's watching):
    default tracks from their language prefs, and Resume from their own progress (the further of the
    two for "Both of us", with a note when they are in different places)."""
    return await _nl_play(request, item_id, audio, subtitle, start, caps, source, client, max_kbps, allow_conversion)


async def _nl_play(
    request: Request,
    item_id: int,
    audio: Optional[int],
    subtitle: Optional[int],
    start: float,
    caps: Optional[str],
    source: Optional[int],
    client: Optional[str],
    max_kbps: Optional[int] = None,
    allow_conversion: bool = False,
) -> Dict[str, Any]:
    viewer = _nl_viewer_or_default(request)
    prefs = _nl_effective_prefs(viewer)
    prefer = prefs.public()
    # M10c, P19: this viewing's quality (the Picture menu) wins, else the person's default; 0 = Auto.
    if max_kbps is None:
        quality = prefs.quality_kbps
    elif max_kbps == 0:
        quality = None
    elif nl_settings.valid_quality(max_kbps):
        quality = max_kbps
    else:
        raise HTTPException(status_code=400, detail="Quality must be 8000, 4000, 2000 or 1000 (kbit/s), or 0 for Auto.")
    if client == "cast":
        quality = None   # the Chromecast has its own profile ceiling
    if client == "laptop":
        # The laptop plays the original: the person's saved cap is ignored. A cap picked for this viewing is the viewer
        # choosing a converted copy, so it counts as agreeing to one.
        if max_kbps is None:
            quality = None
        elif quality:
            allow_conversion = True
    item = state.db.get_nl_title(item_id)
    if not item or not item.get("sources"):
        raise HTTPException(status_code=404, detail="Media file not found.")
    chosen = item["sources"][0]
    if source is not None:
        chosen = next((s for s in item["sources"] if s["id"] == source), None)
        if chosen is None:
            raise HTTPException(status_code=404, detail="That episode isn't in the library.")
    item = {**item, "sources": [chosen]}
    # The tracks this viewer (or anyone) last chose for this movie or show come back first.
    prefer = {**prefer, "memory": state.db.get_track_memory(viewer.key, item_id)}
    try:
        profile = profile_for(client, caps)
        info = await asyncio.to_thread(
            _as_user, viewer.jellyfin_users[0], _stream_for_viewer, item, audio, subtitle, start, profile, prefer,
            quality, {"tv": "This TV", "cast": "This Chromecast", "laptop": "This laptop"}.get(client or "", "This browser"),
            allow_conversion, client == "laptop" and not allow_conversion,
        )
        states: Dict[str, Dict[str, Any]] = {}
        if state.nl_people.configured and info.get("jellyfin_id"):
            states = await asyncio.to_thread(_apply_resume_rule, viewer, info)
    except ItemNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except JellyfinUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    except NotPlayable as e:
        # The reason only reached the screen before; keep it in the log so a refusal can be traced afterwards.
        print(f"[NL] play {item_id} ({client or 'browser'}) refused: {e}", flush=True)
        if e.can_convert:
            return JSONResponse(status_code=415, content={"detail": str(e), "can_convert": True})
        raise HTTPException(status_code=415, detail=str(e))
    memory = prefer["memory"]
    if memory.get("audio") or memory.get("subtitle"):
        # Remembered tracks (nl_track_memory): what was saved, and what this play starts with.
        print(f"[NL] play {item_id} ({client or 'browser'}, {viewer.key}) remembered audio={_track_note(memory.get('audio'))} "
              f"subtitle={_track_note(memory.get('subtitle'))} -> audio {info.get('audio_index')}"
              f" (order {info.get('audio_order')}), subtitle {info.get('default_subtitle_index')}", flush=True)
    info["quality"] = {**(info.get("quality") or {}), "default_kbps": prefs.quality_kbps, "options": nl_settings.quality_options()}
    info["media_item_id"] = item_id
    info["media_type"] = item.get("media_type") or "movie"
    info["source_id"] = chosen["id"]
    info["together_prompt"] = await asyncio.to_thread(_pact_prompt_for, viewer, item_id, item, chosen, states)
    return info


def _pact_prompt_for(viewer: Viewer, item_id: int, item: Dict[str, Any], chosen: Dict[str, Any], states) -> Optional[Dict[str, Any]]:
    """U5: the "You said you'd watch this together" question for this play, or None."""
    if item.get("media_type") != "tv" or not nl_together.together_possible(state.nl_people.people) or viewer.is_both:
        return None
    if not state.db.get_pact(item_id):
        return None
    return nl_together.pact_prompt(
        viewer, state.nl_people.people, states or {}, True, chosen["id"] in state.db.get_seen_together(item_id)
    )


@router.get("/api/nl/cast/config")
async def nl_cast_config() -> Dict[str, Any]:
    """Whether casting is set up (cast_base_url configured) and which receiver app the sender launches."""
    return state.nl_cast.public()


@router.get("/api/nl/cast/play/{item_id}")
async def nl_cast_play(
    request: Request,
    item_id: int,
    audio: Optional[int] = Query(None, ge=0, description="Jellyfin audio stream index"),
    subtitle: Optional[int] = Query(None, ge=-1, description="Jellyfin subtitle stream index to burn in; -1 = none"),
    start: float = Query(0.0, ge=0, description="Start position in seconds"),
    source: Optional[int] = Query(None, ge=1, description="media_sources.id: which episode of a show"),
) -> Dict[str, Any]:
    """/api/nl/play for a Chromecast: the cast profile (H.264/AAC), every URL absolute on the LAN
    (northern_lights.cast_base_url) and carrying a 6-hour token for this one item (?t=), so the
    Chromecast needs no cookies. Adds a `cast` block (content type, backdrop, poster). No Jellyfin key."""
    if not state.nl_cast.enabled:
        raise HTTPException(
            status_code=503,
            detail="Casting isn't set up: set northern_lights.cast_base_url to this server's LAN address.",
        )
    info = await _nl_play(request, item_id, audio, subtitle, start, None, source, "cast")
    return cast_payload(info, state.nl_cast, state.nl_cast_tokens)


def _stream_for_viewer(item, audio, subtitle, start, profile, prefer, max_kbps=None, device="This browser", allow_conversion=False, copy_video_only=False):
    memory = (prefer or {}).get("memory") or {}
    if prefer and not (prefer.get("audio_lang") or prefer.get("subtitles_on") or prefer.get("subtitle_lang")
                       or memory.get("audio") or memory.get("subtitle")):
        prefer = None
    if prefer:
        return state.jellyfin_playback.stream_for(item, audio, subtitle, start, profile, prefer=prefer, max_kbps=max_kbps, device=device, allow_conversion=allow_conversion, copy_video_only=copy_video_only)
    return state.jellyfin_playback.stream_for(item, audio, subtitle, start, profile, max_kbps=max_kbps, device=device, allow_conversion=allow_conversion, copy_video_only=copy_video_only)


def _apply_resume_rule(viewer: Viewer, info: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Resume from the viewer's own progress; for Both the further of the two. Adds `resume_note`
    ("You're at 42:10, Sam is at 38:00") when they differ by more than two minutes. Jellyfin trouble
    for the other person never blocks playback. Returns each person's {position, played} (the pact question
    reads it too)."""
    primary = viewer.primary
    states: Dict[str, Dict[str, Any]] = {}
    if primary is not None:
        states[primary.id] = {"position": info.get("resume_seconds") or 0.0, "played": info.get("played")}
    for person in state.nl_people.people:
        if person.id in states:
            continue
        try:
            with state.jellyfin_playback.as_user(person.jellyfin_user_id):
                details = state.jellyfin_playback.item_details(info["jellyfin_id"])
        except Exception:  # noqa: BLE001 - best effort
            continue
        data = (details or {}).get("UserData") or {}
        states[person.id] = {
            "position": (data.get("PlaybackPositionTicks") or 0) / 10_000_000,
            "played": bool(data.get("Played")),
        }
    summary = resume_summary(viewer, states, state.nl_people.people)
    if summary["positions"] or viewer.is_both:
        info["resume_seconds"] = summary["resume_seconds"]
        info["played"] = summary["played"]
    info["resume_note"] = summary["note"]
    return states


def _nl_subtitle_failure(e: Exception) -> JSONResponse:
    """Jellyfin's problems as short messages the page can show: {detail, code}."""
    if isinstance(e, SubtitleError):
        return JSONResponse(status_code=e.status, content={"detail": e.message, "code": e.code})
    if isinstance(e, ItemNotFound):
        return JSONResponse(status_code=404, content={"detail": str(e), "code": "not_found"})
    return JSONResponse(status_code=503, content={"detail": str(e), "code": "unavailable"})


@router.get("/api/nl/subtitles/{item_id}")
async def nl_subtitle_search(
    item_id: int,
    language: str = Query("eng", pattern="^[a-z]{3}$", description="Three-letter ISO 639-2 code, e.g. eng"),
    source: Optional[int] = Query(None, ge=1, description="media_sources.id: which episode of a show"),
) -> Any:
    """Search OpenSubtitles (through Jellyfin's plugin) for subtitles of one title or episode.
    Results are best first (file-hash matches, then most downloaded). The Jellyfin key stays here."""
    item = _nl_subtitle_item(item_id, source)
    try:
        found = await asyncio.to_thread(state.jellyfin_playback.search_subtitles, item, language)
    except (SubtitleError, ItemNotFound, JellyfinUnavailable) as e:
        return _nl_subtitle_failure(e)
    found["code"] = "ok" if found["results"] else "no_results"
    if not found["results"]:
        found["message"] = f"No subtitles found for {found['language_name']}."
    return found


@router.post("/api/nl/tracks/{item_id}")
async def nl_remember_tracks(request: Request, item_id: int, req: NLTrackChoice) -> Dict[str, Any]:
    """Remembers the audio and/or subtitle track the viewer just picked for this movie or show; the next
    /api/nl/play of the title starts with them (services/nl_track_memory.py)."""
    item = state.db.get_nl_title(item_id)
    if not item or not item.get("sources"):
        raise HTTPException(status_code=404, detail="Media file not found.")
    source_id = req.source if req.source is not None else item["sources"][0]["id"]
    if not any(s["id"] == source_id for s in item["sources"]):
        raise HTTPException(status_code=404, detail="That episode isn't in the library.")
    try:
        audio = nl_track_memory.clean_choice(req.audio, source_id)
        subtitle = nl_track_memory.clean_choice(req.subtitle, source_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if audio and audio.get("off"):
        raise HTTPException(status_code=400, detail="Audio can't be switched off.")
    if audio is None and subtitle is None:
        raise HTTPException(status_code=400, detail="Send an audio or a subtitle choice.")
    viewer = _nl_viewer_or_default(request)
    await asyncio.to_thread(state.db.set_track_memory, viewer.key, item_id, audio, subtitle)
    print(f"[NL] tracks saved for {item_id} ({viewer.key}): audio={_track_note(audio)} subtitle={_track_note(subtitle)}", flush=True)
    return {"success": True}


def _track_note(choice: Optional[Dict[str, Any]]) -> str:
    """A saved track in a log line: "off", "-" (not chosen) or index/language."""
    if not choice:
        return "-"
    return "off" if choice.get("off") else f"{choice.get('index')}/{choice.get('language') or '?'}"


@router.post("/api/nl/subtitles/{item_id}")
async def nl_subtitle_download(
    item_id: int,
    req: NLSubtitleDownload,
    source: Optional[int] = Query(None, ge=1),
) -> Any:
    """Have Jellyfin download one of the searched subtitles and save it with the file. The page then
    re-reads /api/nl/play for the new track."""
    if not valid_subtitle_id(req.subtitle_id):
        return JSONResponse(status_code=400, content={"detail": "That subtitle id isn't valid.", "code": "bad_request"})
    item = _nl_subtitle_item(item_id, source)
    try:
        await asyncio.to_thread(state.jellyfin_playback.download_subtitle, item, req.subtitle_id)
    except (SubtitleError, ItemNotFound, JellyfinUnavailable) as e:
        return _nl_subtitle_failure(e)
    return {"success": True}


# A Chromecast receiver page on another origin reads the cast stream. Only requests that carry a valid cast
# token get CORS headers (no cookies are involved), so no other page can read the proxy.
_CAST_CORS = {"Access-Control-Allow-Origin": "*"}


@router.options("/nl/jf/{path:path}")
async def nl_jellyfin_proxy_preflight(path: str, request: Request) -> Response:
    clean = safe_proxy_path(path)
    tokens = token_values(request.url.query)
    if clean is None or len(tokens) != 1 or not state.nl_cast_tokens.check(tokens[0], "GET", clean)[0]:
        raise HTTPException(status_code=404, detail="Not found.")
    return Response(status_code=204, headers={
        **_CAST_CORS,
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": request.headers.get("access-control-request-headers", "*"),
    })


@router.get("/nl/jf/{path:path}")
async def nl_jellyfin_proxy(path: str, request: Request) -> Response:
    """Streams Jellyfin video, HLS and artwork (Items/<id>/Images/...) through our app. GET only,
    allowlisted paths only; the API key stays here."""
    clean = safe_proxy_path(path)
    if clean is None:
        raise HTTPException(status_code=404, detail="Not found.")
    # M8: a cast stream token (?t=) narrows the request to the one item the token was made for. A bad,
    # expired, forged, or mismatched token is a 403; the token never goes on to Jellyfin.
    cast_token: Optional[str] = None
    tokens = token_values(request.url.query)
    if tokens:
        ok, _why = (False, "") if len(tokens) != 1 else state.nl_cast_tokens.check(tokens[0], request.method, clean)
        if not ok:
            raise HTTPException(status_code=403, detail="This link isn't valid.")
        cast_token = tokens[0]
    jf = state.jellyfin_service
    if not jf.configured:
        raise HTTPException(status_code=503, detail="Jellyfin is not configured.")

    upstream_url = f"{jf.base_url}/{quote(clean, safe='/')}"
    query = strip_api_key("?" + request.url.query)[1:] if request.url.query else ""
    if cast_token:
        query = strip_param(query, "t")
    if query:
        upstream_url += "?" + query
    headers = {k: v for k, v in request.headers.items() if k.lower() in _PROXY_REQUEST_HEADERS}
    headers["Accept"] = "*/*"
    headers["Accept-Encoding"] = "identity"
    try:
        upstream = await run_in_threadpool(
            lambda: jf.session.get(
                upstream_url, headers=headers, stream=True, timeout=(5, 60), allow_redirects=False
            )
        )
    except requests.RequestException:
        raise HTTPException(status_code=502, detail="Jellyfin could not be reached.")

    content_type = upstream.headers.get("content-type", "")
    if "mpegurl" in content_type.lower() or clean.lower().endswith(".m3u8"):
        try:
            body = await run_in_threadpool(_read_limited, upstream, _MAX_PLAYLIST_BYTES + 1)
        except Exception:
            raise HTTPException(status_code=502, detail="Jellyfin playlist could not be read.")
        finally:
            upstream.close()
        if len(body) > _MAX_PLAYLIST_BYTES:
            raise HTTPException(status_code=502, detail="Playlist too large.")
        text = rewrite_playlist(body.decode("utf-8", errors="replace"), cast_token)
        return Response(
            content=text,
            status_code=upstream.status_code,
            media_type="application/vnd.apple.mpegurl",
            headers={"Cache-Control": "no-store", **(_CAST_CORS if cast_token else {})},
        )

    out_headers = {k: v for k, v in upstream.headers.items() if k.lower() in _PROXY_RESPONSE_HEADERS}
    if cast_token:
        out_headers.update(_CAST_CORS)
    if is_image_path(clean) and upstream.status_code == 200:
        out_headers["Cache-Control"] = "private, max-age=86400"

    async def body_iter():
        try:
            async for chunk in iterate_in_threadpool(upstream.iter_content(chunk_size=64 * 1024)):
                yield chunk
        finally:
            upstream.close()  # also runs when the client disconnects and the stream is cancelled

    return StreamingResponse(body_iter(), status_code=upstream.status_code, headers=out_headers)


@router.post("/api/nl/progress")
async def nl_progress(req: NLProgressRequest, request: Request) -> Dict[str, Any]:
    """Tells Jellyfin where the viewer is (start, progress, stop) so it can offer 'Resume'.
    Each person's progress goes to their own Jellyfin user; "Both of us" reports to both."""
    if not valid_jellyfin_id(req.jellyfin_id):
        raise HTTPException(status_code=400, detail="Bad item id.")
    viewer = _nl_viewer_or_default(request)
    try:
        await asyncio.to_thread(
            _report_to_users, viewer.jellyfin_users,
            lambda user: state.jellyfin_playback.report_progress(
                req.event, req.jellyfin_id, req.play_session_id, req.media_source_id,
                req.position_seconds, req.paused, req.play_method, **({"user": user} if user else {}),
            ),
        )
    except JellyfinUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    # M9a: "Both of us" finishing a title is the one thing that marks it as seen together.
    if (
        req.event == "stop" and req.media_item_id and viewer.is_both and nl_together.together_possible(state.nl_people.people)
        and nl_together.is_finished(req.position_seconds, req.duration_seconds, req.ended)
    ):
        await asyncio.to_thread(state.db.mark_seen_together, req.media_item_id, req.source_id)
    return {"success": True}
