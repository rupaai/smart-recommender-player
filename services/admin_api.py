"""Admin endpoints the Mac's nightly job talks to (P6.1 and P6.2 of the old deployment plan).

Every route needs `Authorization: Bearer $ADMIN_TOKEN`. With ADMIN_TOKEN unset the whole
router answers 404, as if it did not exist.
"""

from __future__ import annotations

import hmac
import os
import sqlite3
import tempfile
import threading
from typing import Any, Callable, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask

from services import model_bundle
from services import paths as model_paths
from services.catalogue_patch import PatchError, apply_patch

_lock = threading.Lock()   # one bundle install / rollback at a time


def require_admin(request: Request) -> None:
    token = os.environ.get("ADMIN_TOKEN", "").strip()
    if not token:
        raise HTTPException(status_code=404, detail="Not Found")
    given = request.headers.get("authorization", "").strip()
    expected = f"Bearer {token}"
    if not hmac.compare_digest(given.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Bad admin token")


def _current_link() -> str:
    if not os.environ.get("MODELS_DIR"):
        raise HTTPException(status_code=409, detail="MODELS_DIR is not set; this server keeps no model releases")
    return model_paths.models_dir()


async def _save_body(request: Request, suffix: str) -> str:
    """Streams the request body to a temp file and returns its path."""
    fd, path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as out:
            async for chunk in request.stream():
                out.write(chunk)
    except BaseException:
        os.unlink(path)
        raise
    return path


def build_router(db_path_fn: Callable[[], str], reload_models: Callable[[], None]) -> APIRouter:
    router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])

    @router.get("/export")
    async def export() -> FileResponse:
        """A consistent copy of the database (SQLite online backup), for the Mac to back up and train on."""
        fd, tmp = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        src = sqlite3.connect(db_path_fn())
        dst = sqlite3.connect(tmp)
        try:
            src.backup(dst)
            max_id = dst.execute("SELECT COALESCE(MAX(id), 0) FROM media_items").fetchone()[0]
        finally:
            dst.close()
            src.close()
        return FileResponse(
            tmp, media_type="application/octet-stream", filename="catalogue_full.db",
            headers={"X-Media-Items-Max-Id": str(max_id)},
            background=BackgroundTask(os.unlink, tmp),
        )

    @router.post("/catalogue-patch")
    async def catalogue_patch(request: Request) -> Dict[str, Any]:
        """Body: JSON lines (see services/catalogue_patch.py). One transaction; returns counts."""
        path = await _save_body(request, ".jsonl")
        try:
            with open(path, "r", encoding="utf-8") as f:
                return apply_patch(db_path_fn(), f)
        except PatchError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        finally:
            os.unlink(path)

    @router.get("/model-status")
    async def model_status() -> Dict[str, Any]:
        link = _current_link()
        return {
            "current": model_bundle.current_release(link),
            "releases": model_bundle.list_releases(link),
            "manifest": model_bundle.read_manifest(link),
        }

    @router.post("/model-bundle")
    async def model_bundle_upload(request: Request) -> Any:
        """Body: the bundle .tar.gz, as raw bytes."""
        link = _current_link()
        path = await _save_body(request, ".tar.gz")
        try:
            with _lock:
                manifest = model_bundle.install_bundle(
                    path, db_path=db_path_fn(), current_link=link, reload_models=reload_models)
        except model_bundle.BundleError as e:
            return JSONResponse(status_code=e.status, content={"detail": str(e)})
        finally:
            os.unlink(path)
        return {"installed": manifest["bundle_id"], "metrics": manifest.get("metrics", {})}

    @router.post("/model-rollback")
    async def model_rollback() -> Any:
        link = _current_link()
        try:
            with _lock:
                target = model_bundle.rollback(link, reload_models)
        except model_bundle.BundleError as e:
            return JSONResponse(status_code=e.status, content={"detail": str(e)})
        return {"current": target}

    return router
