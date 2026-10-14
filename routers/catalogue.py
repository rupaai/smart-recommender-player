# ruff: noqa: F821 -- endpoint annotations resolve from the injected app dependency namespace.
"""Catalogue API routes and endpoint handlers."""

from fastapi import APIRouter

_ANNOTATION_NAMES = (
    "Any",
    "Dict",
    "MediaCleanupRequest",
    "Optional",
    "TmdbImportRequest",
)
ROUTES = [
    ("GET", "/api/media/files", "list_media_files"),
    ("GET", "/api/origins", "list_origins"),
    ("GET", "/api/catalogue/search", "search_catalogue"),
    ("GET", "/api/catalogue/tmdb-search", "search_tmdb"),
    ("POST", "/api/catalogue/tmdb-import", "import_from_tmdb"),
    ("POST", "/api/media/cleanup", "cleanup_media"),
    ("GET", "/api/db/sources", "list_db_sources"),
    ("POST", "/api/db/sources/prune", "prune_stale_db_sources"),
    ("POST", "/api/sync", "sync_catalogue"),
]


def build_router(dependencies):
    """Build the catalogue router with live app dependencies."""
    globals().update({name: dependencies[name] for name in _ANNOTATION_NAMES})
    router = APIRouter(tags=["catalogue"])

    async def list_media_files() -> "Dict[str, Any]":
        """Read-only listing of every video file under the configured media roots.

        Diagnostic helper (e.g. verifying the organizer's folder layout):
        returns per-root absolute file paths with sizes and mtimes.
        """
        VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".webm"}

        def _scan() -> "Dict[str, Any]":
            out = {"roots": {}}
            for root_dir in dependencies["config"].get("media_folders", []) or []:
                files = []
                if dependencies["os"].path.isdir(root_dir):
                    for dirpath, _dirnames, filenames in dependencies["os"].walk(
                        root_dir
                    ):
                        for fname in filenames:
                            if (
                                dependencies["os"].path.splitext(fname)[1].lower()
                                in VIDEO_EXTS
                            ):
                                fpath = dependencies["os"].path.join(dirpath, fname)
                                try:
                                    st = dependencies["os"].stat(fpath)
                                    files.append(
                                        {
                                            "path": fpath,
                                            "size": st.st_size,
                                            "mtime": st.st_mtime,
                                        }
                                    )
                                except OSError:
                                    continue
                files.sort(key=lambda f: dependencies["f"]["path"])
                out["roots"][root_dir] = {
                    "exists": dependencies["os"].path.isdir(root_dir),
                    "files": files,
                }
            return out

        return await dependencies["asyncio"].to_thread(_scan)

    async def list_origins():
        """Countries of origin present in the catalogue, with counts.

        Only regions actually imported (utils/backfill_origin_country.py) appear,
        so the UI never offers a region with nothing to show. Feeds the "From"
        chips, which write an origin phrase into the free-text query — origin is a
        place, so it cannot be expressed as a genre.
        """
        with dependencies["db"].get_connection() as conn:
            rows = conn.execute(
                "SELECT origin_country, COUNT(*) AS n FROM media_items WHERE origin_country IS NOT NULL AND origin_country != '' GROUP BY origin_country ORDER BY n DESC;"
            ).fetchall()
        counts = {}
        for raw, n in rows:
            for code in (raw or "").split(","):
                code = code.strip().upper()
                if code:
                    counts[code] = counts.get(code, 0) + n
        labelled = [
            {
                "code": code,
                "name": dependencies["ORIGIN_LABELS"].get(code, code),
                "count": counts[code],
            }
            for code in sorted(counts, key=lambda c: -counts[c])
            if counts[code] >= 20
        ]
        groups = []
        for label, members in dependencies["ORIGIN_GROUPS"].items():
            total = sum((counts.get(c, 0) for c in members))
            if total >= 20:
                groups.append(
                    {
                        "code": label,
                        "name": label,
                        "count": total,
                        "countries": list(members),
                    }
                )
        return {"origins": groups + labelled}

    async def search_catalogue(
        q: "str" = dependencies["Query"](
            ..., min_length=1, description="Title text; 2+ characters"
        ),
        media_type: "str" = dependencies["Query"](
            default="all", description="movie | tv | all"
        ),
        limit: "int" = dependencies["Query"](default=10, ge=1, le=25),
        profile_id: "Optional[int]" = dependencies["Query"](
            default=None, description="Adds this profile's own verdict"
        ),
    ) -> "Dict[str, Any]":
        """Search-to-rate (design doc §1): accent- and punctuation-insensitive title
        search over the catalogue, exact matches first, then by vote count."""
        mtype = media_type if isinstance(media_type, str) else "all"
        pid = profile_id if isinstance(profile_id, int) else None
        lim = limit if isinstance(limit, int) else 10
        items = await dependencies["asyncio"].to_thread(
            dependencies["catalogue_search"].search, q, mtype, lim, pid
        )
        return {
            "query": q,
            "items": dependencies["_with_on_list"](
                [dependencies["_with_co_view"](it) for it in items]
            ),
        }

    async def search_tmdb(
        q: "str" = dependencies["Query"](..., min_length=2), media_type: "str" = "all"
    ) -> "Dict[str, Any]":
        """Fallback when a title isn't in the catalogue. Needs TMDB_API_KEY + internet."""
        if not dependencies["api_key"]:
            raise dependencies["HTTPException"](
                status_code=503, detail="TMDB_API_KEY is not set on the server."
            )
        try:
            items = await dependencies["asyncio"].to_thread(
                dependencies["tmdb_search"],
                dependencies["api_key"],
                dependencies["db"],
                q,
                media_type,
            )
        except RuntimeError as e:
            raise dependencies["HTTPException"](status_code=502, detail=str(e)) from e
        return {"query": q, "items": items}

    async def import_from_tmdb(
        req: "TmdbImportRequest", profile_id: "Optional[int]" = None
    ) -> "Dict[str, Any]":
        """Adds one TMDB title to the catalogue and returns it ready to rate."""
        if not dependencies["api_key"]:
            raise dependencies["HTTPException"](
                status_code=503, detail="TMDB_API_KEY is not set on the server."
            )
        try:
            item_id = await dependencies["asyncio"].to_thread(
                dependencies["tmdb_import"],
                dependencies["api_key"],
                dependencies["db"],
                req.media_type,
                req.tmdb_id,
            )
        except ValueError as e:
            raise dependencies["HTTPException"](status_code=400, detail=str(e)) from e
        except RuntimeError as e:
            raise dependencies["HTTPException"](status_code=502, detail=str(e)) from e
        dependencies["catalogue_search"].invalidate()
        item = dependencies["db"].get_item_by_id(item_id) or {"id": item_id}
        item["in_library"] = bool(item.pop("sources", None))
        return dependencies["_with_co_view"](item)

    async def cleanup_media(req: "MediaCleanupRequest") -> "Dict[str, Any]":
        """Deletes the given media files/trees (guarded), removes their DB rows,
        prunes orphaned stub items, and triggers a Jellyfin refresh.

        Guardrails: every path must be absolute AND inside a configured media
        root (media_folders). Refuses anything else — never touches other paths.
        """
        roots = {
            dependencies["os"].path.abspath(r)
            for r in dependencies["config"].get("media_folders", []) or []
        }

        def _guarded(path: "str") -> "Optional[str]":
            ap = dependencies["os"].path.abspath(path)
            if any(
                (ap == r or ap.startswith(r + dependencies["os"].sep) for r in roots)
            ):
                return ap
            return None

        report = {
            "deleted_paths": [],
            "skipped_paths": [],
            "removed_sources": 0,
            "pruned_items": [],
            "jellyfin_refresh": False,
        }

        def _do_cleanup() -> "Dict[str, Any]":
            for raw_path in req.paths:
                ap = _guarded(raw_path)
                if ap is None:
                    report["skipped_paths"].append(f"outside media roots: {raw_path}")
                    continue
                if dependencies["os"].path.isdir(ap):
                    dependencies["shutil"].rmtree(ap, ignore_errors=True)
                elif dependencies["os"].path.isfile(ap):
                    dependencies["os"].remove(ap)
                report["deleted_paths"].append(ap)
            file_paths = [
                p
                for p in report["deleted_paths"]
                if not dependencies["os"].path.exists(p)
            ]
            report["removed_sources"] = dependencies["db"].remove_source_files(
                file_paths
            )
            if req.item_ids:
                with dependencies["db"].get_connection() as conn:
                    for item_id in req.item_ids:
                        cur = conn.cursor()
                        cur.execute(
                            "SELECT COUNT(*) AS n FROM watch_history WHERE media_item_id = ?;",
                            (item_id,),
                        )
                        if cur.fetchone()["n"] == 0:
                            cur.execute(
                                "DELETE FROM media_items WHERE id = ?;", (item_id,)
                            )
                            report["pruned_items"].append(str(item_id))
                    conn.commit()
            report["pruned_items"] += dependencies["db"].prune_orphan_stub_items()
            try:
                resp = dependencies["jellyfin_service"].session.post(
                    f"{dependencies['jellyfin_service'].base_url}/Library/Refresh",
                    timeout=10,
                )
                report["jellyfin_refresh"] = resp.status_code in (200, 204)
            except Exception as e:
                report["jellyfin_refresh_error"] = str(e)
            return report

        return await dependencies["asyncio"].to_thread(_do_cleanup)

    async def list_db_sources() -> "Dict[str, Any]":
        """Read-only dump of media_sources rows + their parent item titles.

        Diagnostic helper for spotting stale source rows pointing at deleted files.
        """

        def _dump() -> "Dict[str, Any]":
            with dependencies["db"].get_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    "\n                SELECT s.id, s.file_path, s.source_type, s.file_size,\n                       s.season_number, s.episode_number,\n                       s.media_item_id, i.title, i.media_type, i.tmdb_id\n                FROM media_sources s\n                LEFT JOIN media_items i ON i.id = s.media_item_id\n                ORDER BY s.id;\n                "
                )
                rows = [dict(r) for r in cur.fetchall()]
            for r in rows:
                r["file_exists"] = dependencies["os"].path.isfile(r["file_path"])
            return {"count": len(rows), "rows": rows}

        return await dependencies["asyncio"].to_thread(_dump)

    async def prune_stale_db_sources() -> "Dict[str, Any]":
        """Removes media_sources rows whose file_path no longer exists on disk,
        then prunes orphaned stub items. Never deletes files."""

        def _prune() -> "Dict[str, Any]":
            removed = []
            with dependencies["db"].get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT id, file_path FROM media_sources;")
                for row in cur.fetchall():
                    if not dependencies["os"].path.isfile(row["file_path"]):
                        removed.append(row["file_path"])
                if removed:
                    dependencies["db"].remove_source_files(removed)
            pruned = dependencies["db"].prune_orphan_stub_items()
            return {"removed_source_rows": removed, "pruned_items": pruned}

        return await dependencies["asyncio"].to_thread(_prune)

    async def sync_catalogue() -> "Dict[str, Any]":
        """Scans media folders and enriches new items with TMDB metadata."""
        folders = dependencies["config"].get("media_folders", ["./sample_library"])
        extensions = dependencies["config"].get("supported_extensions", None)
        scanner = dependencies["LocalFileScanner"](
            folders=folders,
            db_manager=dependencies["db"],
            supported_extensions=extensions,
        )
        scan_summary = scanner.scan()
        enr_summary = {"total": 0, "enriched": 0, "failed": 0}
        if dependencies["api_key"]:
            enricher = dependencies["TMDBEnricher"](
                api_key=dependencies["api_key"],
                db_manager=dependencies["db"],
                region=dependencies["config"].get("region", "GB"),
            )
            enr_summary = enricher.enrich()
        return {"scan": scan_summary, "enrich": enr_summary}

    _handlers = {
        "list_media_files": list_media_files,
        "list_origins": list_origins,
        "search_catalogue": search_catalogue,
        "search_tmdb": search_tmdb,
        "import_from_tmdb": import_from_tmdb,
        "cleanup_media": cleanup_media,
        "list_db_sources": list_db_sources,
        "prune_stale_db_sources": prune_stale_db_sources,
        "sync_catalogue": sync_catalogue,
    }
    for method, path, handler_name, *rest in ROUTES:
        options = dict(rest[0]) if rest else {}
        if options.get("response_class") == "HTMLResponse":
            options["response_class"] = dependencies["HTMLResponse"]
        router.add_api_route(path, _handlers[handler_name], methods=[method], **options)
    for name, handler in _handlers.items():
        dependencies[name] = handler
    return router
