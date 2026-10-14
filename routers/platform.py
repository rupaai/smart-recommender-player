# ruff: noqa: F821 -- endpoint annotations resolve from the injected app dependency namespace.
"""Platform API routes and endpoint handlers."""

from fastapi import APIRouter

_ANNOTATION_NAMES = ("Any", "Dict", "List")
ROUTES = [
    ("GET", "/healthz", "healthz"),
    ("GET", "/api/status", "get_status"),
    ("GET", "/api/moods", "get_moods"),
    ("GET", "/api/nl/health", "nl_health_status"),
    ("GET", "/api/nl/admin", "nl_admin_data"),
    ("POST", "/api/nl/admin/repair", "nl_admin_repair"),
]


def build_router(dependencies):
    """Build the platform router with live app dependencies."""
    globals().update({name: dependencies[name] for name in _ANNOTATION_NAMES})
    router = APIRouter(tags=["platform"])

    async def healthz() -> "Any":
        """Liveness/readiness: git SHA, model bundle, DB schema version. 503 if the DB cannot be read."""
        try:
            with dependencies["db"].get_connection() as conn:
                conn.execute("SELECT 1").fetchone()
            schema = dependencies["db"].get_schema_version()
        except Exception as e:
            return dependencies["JSONResponse"](
                status_code=503, content={"ok": False, "error": str(e)}
            )
        return {
            "ok": True,
            "git_sha": dependencies["os"].getenv("GIT_SHA", "dev"),
            "model_bundle": dependencies["_model_bundle_id"](),
            "db_schema": schema,
        }

    async def get_status() -> "Dict[str, Any]":
        """Returns system status including Jellyfin online state and catalogue statistics."""
        local_ip = dependencies["get_local_ip"]()
        jf_online = dependencies["is_jellyfin_online"]()
        stats = dependencies["db"].get_stats()
        return {
            "jellyfin_online": jf_online,
            "jellyfin_url": f"http://{local_ip}:8096",
            "local_ip": local_ip,
            "stats": stats,
        }

    async def get_moods() -> "List[Dict[str, Any]]":
        """Returns available mood options including sub-moods for the interactive picker."""
        moods_list = []
        for mood_id, info in dependencies["MOOD_MAPPINGS"].items():
            moods_list.append(
                {
                    "id": mood_id,
                    "name": info["name"],
                    "vibe": info.get("vibe", ""),
                    "genres": info["genres"],
                    "submoods": info.get("submoods", []),
                }
            )
        return moods_list

    async def nl_health_status() -> "Dict[str, Any]":
        """The compact header status: Jellyfin reachable, free disk space, download queue size. Cheap: checks are
        cached for a few seconds. Carries no address, key or path."""
        return await dependencies["asyncio"].to_thread(
            dependencies["nl_health_state"].status
        )

    async def nl_admin_data() -> "Dict[str, Any]":
        """The admin screen's data: the status above, Jellyfin's diagnostics (libraries and where they point),
        disk use against the limits, and the Jellystat / Uptime Kuma / Dozzle links that are configured."""
        return await dependencies["asyncio"].to_thread(
            dependencies["_nl_admin_payload"]
        )

    async def nl_admin_repair() -> "Dict[str, Any]":
        """Library repair (the same action as POST /api/jellyfin/repair), answered with a short summary only."""
        report = await dependencies["asyncio"].to_thread(
            dependencies["jellyfin_service"].repair_libraries
        )
        dependencies["nl_health_state"].forget()
        return {
            "ok": not report.get("error")
            and all(
                (
                    lib.get("result")
                    in ("already-attached", "created", "path-added", "recreated")
                    for lib in report.get("libraries", [])
                )
            ),
            "error": report.get("error"),
            "libraries": [
                {
                    "library": lib.get("library"),
                    "result": lib.get("result") or lib.get("error"),
                    "hint": lib.get("hint"),
                }
                for lib in report.get("libraries", [])
            ],
            "refresh_triggered": bool(report.get("refresh_triggered")),
        }

    _handlers = {
        "healthz": healthz,
        "get_status": get_status,
        "get_moods": get_moods,
        "nl_health_status": nl_health_status,
        "nl_admin_data": nl_admin_data,
        "nl_admin_repair": nl_admin_repair,
    }
    for method, path, handler_name, *rest in ROUTES:
        options = dict(rest[0]) if rest else {}
        if options.get("response_class") == "HTMLResponse":
            options["response_class"] = dependencies["HTMLResponse"]
        router.add_api_route(path, _handlers[handler_name], methods=[method], **options)
    for name, handler in _handlers.items():
        dependencies[name] = handler
    return router
