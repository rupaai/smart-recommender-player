# ruff: noqa: F821 -- endpoint annotations resolve from the injected app dependency namespace.
"""Downloads API routes and endpoint handlers."""

from fastapi import APIRouter

_ANNOTATION_NAMES = (
    "Any",
    "Dict",
    "Request",
    "TorrentCancelRequest",
    "TorrentDownloadRequest",
    "TorrentSearchRequest",
)
ROUTES = [
    ("GET", "/api/nl/downloads", "nl_downloads_list"),
    ("POST", "/api/nl/downloads/{item_id}/retry", "nl_downloads_retry"),
    ("DELETE", "/api/nl/downloads/{item_id}", "nl_downloads_dismiss"),
    ("GET", "/nl/downloads", "nl_downloads_page", {"response_class": "HTMLResponse"}),
    ("GET", "/nl/tv/downloads", "nl_tv_downloads", {"response_class": "HTMLResponse"}),
    ("POST", "/api/torrent/search", "search_torrents"),
    ("POST", "/api/torrent/download", "download_torrent"),
    ("POST", "/api/torrent/cancel", "cancel_torrent"),
    ("GET", "/api/torrent/status/{movie_id}", "get_torrent_status"),
]


def build_router(dependencies):
    """Build the downloads router with live app dependencies."""
    globals().update({name: dependencies[name] for name in _ANNOTATION_NAMES})
    router = APIRouter(tags=["downloads"])

    async def nl_downloads_list() -> "Dict[str, Any]":
        """Every download the downloader knows: queued, downloading (speed and time left), importing, ready
        (with a Play link) or failed (with a plain-words reason and Retry). No magnet links, no file paths."""
        return await dependencies["asyncio"].to_thread(
            dependencies["_nl_downloads_payload"]
        )

    async def nl_downloads_retry(item_id: "int") -> "Dict[str, Any]":
        """Starts a failed or stopped download again from the link it was first started with."""
        entry = dependencies["downloads_store"].get(item_id)
        if entry is None:
            raise dependencies["HTTPException"](
                status_code=404, detail="That download isn't in the list any more."
            )
        if (
            dependencies["nl_downloads"].state_of(entry)
            not in dependencies["nl_downloads"].RETRY_STATES
        ):
            raise dependencies["HTTPException"](
                status_code=409,
                detail="That download is not stopped, so there is nothing to retry.",
            )
        req = dependencies["download_requests"].get(item_id)
        if req is None:
            raise dependencies["HTTPException"](
                status_code=404,
                detail="The app was restarted since this started, so it no longer has the link. Search for the title again.",
            )
        dependencies["downloads_store"][item_id] = {
            "movie_id": item_id,
            "status": "connecting",
            "progress": 0.0,
            "error": None,
            "name": req["title"],
            "media_type": req["media_type"],
        }
        dependencies["asyncio"].create_task(
            dependencies["torrent_downloader"].download(
                req["magnet"], item_id, req["title"], media_type=req["media_type"]
            )
        )
        return {"success": True, "id": item_id}

    async def nl_downloads_dismiss(item_id: "int") -> "Dict[str, Any]":
        """Clears a failed, stopped or finished download from the list (the record only; files are never touched)."""
        entry = dependencies["downloads_store"].get(item_id)
        if entry is None:
            return {"success": True}
        if (
            dependencies["nl_downloads"].state_of(entry)
            not in dependencies["nl_downloads"].DISMISS_STATES
        ):
            raise dependencies["HTTPException"](
                status_code=409, detail="That download is still running."
            )
        dependencies["downloads_store"].pop(item_id, None)
        dependencies["download_requests"].forget(item_id)
        return {"success": True}

    async def nl_downloads_page(request: "Request") -> "Any":
        return dependencies["_nl_page"](
            request, "nl/downloads.html", {"active": "downloads"}
        )

    async def nl_tv_downloads(request: "Request") -> "Any":
        return dependencies["_nl_page"](
            request, "nl/tv/downloads.html", {"page": "downloads"}, tv=True
        )

    async def search_torrents(req: "TorrentSearchRequest") -> "Dict[str, Any]":
        """Searches for torrents matching the query with configurable sort order and media type (movies/shows)."""
        results = dependencies["torrent_searcher"].search(
            req.query,
            sort_by_size=req.sort_by_size,
            media_type=req.media_type,
            sort=req.sort,
        )
        return {"results": results}

    async def download_torrent(req: "TorrentDownloadRequest") -> "Dict[str, Any]":
        """Launches torrent download as a background task and returns immediately."""
        try:
            movie_id = req.movie_id
            clean_movie_title = dependencies["_clean_release_title"](req.movie_title)
            if not movie_id or movie_id <= 0:
                movie_id = dependencies["db"].get_or_create_media_item(
                    title=clean_movie_title, media_type=req.media_type
                )
            if req.movie_id and req.movie_id > 0:
                dependencies["_log_outcome"](req.movie_id, "downloaded")
            dependencies["download_requests"].remember(
                movie_id, req.magnet_link, clean_movie_title, req.media_type
            )
            dependencies["asyncio"].create_task(
                dependencies["torrent_downloader"].download(
                    req.magnet_link,
                    movie_id,
                    clean_movie_title,
                    media_type=req.media_type,
                )
            )
            return {
                "success": True,
                "movie_id": movie_id,
                "clean_title": clean_movie_title,
                "message": f"Download task started for {req.media_type}.",
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def cancel_torrent(req: "TorrentCancelRequest") -> "Dict[str, Any]":
        """Cancels an in-progress torrent download."""
        success = await dependencies["torrent_downloader"].cancel(req.movie_id)
        return {"success": success}

    async def get_torrent_status(movie_id: "int") -> "Dict[str, Any]":
        """Returns the current download progress for a given movie or TV show."""
        status_data = dependencies["downloads_store"].get(
            movie_id, {"status": "idle", "progress": 0.0, "movie_id": movie_id}
        )
        return status_data

    _handlers = {
        "nl_downloads_list": nl_downloads_list,
        "nl_downloads_retry": nl_downloads_retry,
        "nl_downloads_dismiss": nl_downloads_dismiss,
        "nl_downloads_page": nl_downloads_page,
        "nl_tv_downloads": nl_tv_downloads,
        "search_torrents": search_torrents,
        "download_torrent": download_torrent,
        "cancel_torrent": cancel_torrent,
        "get_torrent_status": get_torrent_status,
    }
    for method, path, handler_name, *rest in ROUTES:
        options = dict(rest[0]) if rest else {}
        if options.get("response_class") == "HTMLResponse":
            options["response_class"] = dependencies["HTMLResponse"]
        router.add_api_route(path, _handlers[handler_name], methods=[method], **options)
    for name, handler in _handlers.items():
        dependencies[name] = handler
    return router
