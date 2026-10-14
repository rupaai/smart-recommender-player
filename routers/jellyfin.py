"""Jellyfin diagnostics and playback transport routes.

The router receives the application's live dependency mapping at startup. This
keeps integration state owned by the composition root while preserving runtime
replacement of services in tests and local development.
"""

from typing import Any, Dict, Optional
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse


def build_router(dependencies):
    """Build the Jellyfin router against the app's live dependency mapping."""
    router = APIRouter(tags=["playback"])

    @router.get("/api/jellyfin/status")
    async def get_jellyfin_status() -> Dict[str, Any]:
        """Jellyfin diagnostics: server online, libraries (types + locations), env config."""
        return await dependencies["asyncio"].to_thread(
            dependencies["jellyfin_service"].diagnostics
        )

    @router.post("/api/jellyfin/repair")
    async def repair_jellyfin_libraries() -> Dict[str, Any]:
        """Attach missing paths to Jellyfin libraries and refresh them."""
        return await dependencies["asyncio"].to_thread(
            dependencies["jellyfin_service"].repair_libraries
        )

    @router.get("/api/jellyfin/repair")
    async def repair_jellyfin_libraries_get(apply: int = 0) -> Dict[str, Any]:
        """Report repair actions, or apply them when ``?apply=1`` is supplied."""
        service = dependencies["jellyfin_service"]
        if not apply:
            diag = await dependencies["asyncio"].to_thread(service.diagnostics)
            return {
                "mode": "dry-run",
                "note": "call /api/jellyfin/repair (POST) or ?apply=1 to fix libraries",
                "current_state": diag,
            }
        return await dependencies["asyncio"].to_thread(service.repair_libraries)

    @router.get("/api/watch/current")
    async def get_current_watch() -> Dict[str, Any]:
        """Return the active watching session, or an empty object."""
        active = dependencies["db"].get_active_watch()
        return {"active": active}

    @router.get("/api/stream/{item_id}")
    async def stream_media(item_id: int) -> Dict[str, Any]:
        """Return the Jellyfin playback URL for the selected movie or episode."""
        item = dependencies["db"].get_item_by_id(item_id)
        if not item or not item.get("sources"):
            raise HTTPException(status_code=404, detail="Media file not found.")
        file_path = item["sources"][0]["file_path"]
        if not dependencies["os"].path.exists(file_path):
            raise HTTPException(status_code=404, detail="Video file not found on disk.")

        local_ip = dependencies["get_local_ip"]()
        file_name = dependencies["os"].path.basename(file_path)
        jellyfin_url = (
            f"http://{local_ip}:8096/web/index.html#!/details?filename={file_name}"
        )
        return {"jellyfin_url": jellyfin_url}

    @router.get("/nl/jf/{path:path}")
    async def nl_jellyfin_proxy(path: str, request: Request) -> Response:
        """Proxy allowlisted Jellyfin media and artwork while keeping its key private."""
        clean = dependencies["safe_proxy_path"](path)
        if clean is None:
            raise HTTPException(status_code=404, detail="Not found.")

        cast_token: Optional[str] = None
        tokens = dependencies["token_values"](request.url.query)
        if tokens:
            cast_tokens = dependencies["nl_cast_tokens"]
            ok, _why = (
                (False, "")
                if len(tokens) != 1
                else cast_tokens.check(tokens[0], request.method, clean)
            )
            if not ok:
                raise HTTPException(status_code=403, detail="This link isn't valid.")
            cast_token = tokens[0]

        jf = dependencies["jellyfin_service"]
        if not jf.configured:
            raise HTTPException(status_code=503, detail="Jellyfin is not configured.")

        upstream_url = f"{jf.base_url}/{quote(clean, safe='/')}"
        query = (
            dependencies["strip_api_key"]("?" + request.url.query)[1:]
            if request.url.query
            else ""
        )
        if cast_token:
            query = dependencies["strip_param"](query, "t")
        if query:
            upstream_url += "?" + query
        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() in dependencies["_PROXY_REQUEST_HEADERS"]
        }
        headers["Accept"] = "*/*"
        headers["Accept-Encoding"] = "identity"
        try:
            upstream = await dependencies["run_in_threadpool"](
                lambda: jf.session.get(
                    upstream_url,
                    headers=headers,
                    stream=True,
                    timeout=(5, 60),
                    allow_redirects=False,
                )
            )
        except dependencies["requests"].RequestException as exc:
            raise HTTPException(
                status_code=502, detail="Jellyfin could not be reached."
            ) from exc

        content_type = upstream.headers.get("content-type", "")
        if "mpegurl" in content_type.lower() or clean.lower().endswith(".m3u8"):
            try:
                body = await dependencies["run_in_threadpool"](
                    dependencies["_read_limited"],
                    upstream,
                    dependencies["_MAX_PLAYLIST_BYTES"] + 1,
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=502, detail="Jellyfin playlist could not be read."
                ) from exc
            finally:
                upstream.close()
            if len(body) > dependencies["_MAX_PLAYLIST_BYTES"]:
                raise HTTPException(status_code=502, detail="Playlist too large.")
            text = dependencies["rewrite_playlist"](
                body.decode("utf-8", errors="replace"), cast_token
            )
            return Response(
                content=text,
                status_code=upstream.status_code,
                media_type="application/vnd.apple.mpegurl",
                headers={"Cache-Control": "no-store"},
            )

        out_headers = {
            key: value
            for key, value in upstream.headers.items()
            if key.lower() in dependencies["_PROXY_RESPONSE_HEADERS"]
        }
        if dependencies["is_image_path"](clean) and upstream.status_code == 200:
            out_headers["Cache-Control"] = "private, max-age=86400"

        async def body_iter():
            try:
                async for chunk in dependencies["iterate_in_threadpool"](
                    upstream.iter_content(chunk_size=64 * 1024)
                ):
                    yield chunk
            finally:
                upstream.close()

        return StreamingResponse(
            body_iter(), status_code=upstream.status_code, headers=out_headers
        )

    return router
