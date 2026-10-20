"""Installs model bundles sent by the Mac (P6.2 of the old deployment plan).

A bundle is a .tar.gz with `manifest.json` at its root plus `tiger/`, `vibe/` and an
optional `db_patch.jsonl`. Layout on the server (next to MODELS_DIR, which points at the
`current` link):

    models/releases/<bundle_id>/     the last few bundles, unpacked
    models/current -> releases/<bundle_id>

Installing checks everything before the app can see it: file checksums, the evaluation
gate, that every item id in the model exists in the database, and that the model loads and
answers once. Only then is the database patch applied and `current` repointed (a new
symlink swapped in with os.replace, so there is never a moment with no model).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tarfile
import time
from typing import Any, Callable, Dict, List, Optional

from services.catalogue_patch import apply_patch

KEEP_RELEASES = 3
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
MAX_UNPACKED_BYTES = 2 * 1024**3


class BundleError(ValueError):
    """The bundle was rejected. Nothing was installed. `status` is the HTTP code to answer with."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def releases_dir(current_link: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(current_link)), "releases")


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def current_release(current_link: str) -> Optional[str]:
    """bundle_id (folder name) that `current` points at, or None."""
    try:
        return os.path.basename(os.readlink(current_link).rstrip("/"))
    except OSError:
        return None


def list_releases(current_link: str) -> List[str]:
    root = releases_dir(current_link)
    if not os.path.isdir(root):
        return []
    names = [n for n in os.listdir(root) if os.path.isdir(os.path.join(root, n)) and not n.startswith(".")]
    return sorted(names, key=lambda n: os.path.getmtime(os.path.join(root, n)))


def read_manifest(current_link: str) -> Dict[str, Any]:
    try:
        with open(os.path.join(current_link, "manifest.json"), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _unpack(archive: str, dest: str) -> None:
    """Extracts regular files only, refusing paths that escape `dest` and absurd sizes."""
    os.makedirs(dest)
    total = 0
    try:
        with tarfile.open(archive, "r:gz") as tar:
            for m in tar.getmembers():
                name = os.path.normpath(m.name)
                if name.startswith("..") or os.path.isabs(name):
                    raise BundleError(f"unsafe path in bundle: {m.name}")
                if m.isdir():
                    continue
                if not m.isfile():
                    raise BundleError(f"bundle holds something that is not a plain file: {m.name}")
                total += m.size
                if total > MAX_UNPACKED_BYTES:
                    raise BundleError("bundle is far larger than a model should be")
                target = os.path.join(dest, name)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                src = tar.extractfile(m)
                assert src is not None
                with open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
    except (tarfile.TarError, EOFError, OSError) as e:
        raise BundleError(f"could not read the bundle: {e}") from e


def _verify(folder: str) -> Dict[str, Any]:
    try:
        with open(os.path.join(folder, "manifest.json"), "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, ValueError) as e:
        raise BundleError(f"manifest.json missing or unreadable: {e}") from e
    bundle_id = manifest.get("bundle_id")
    if not isinstance(bundle_id, str) or not _ID_RE.match(bundle_id):
        raise BundleError("manifest bundle_id is missing or has odd characters")
    if manifest.get("gate") != "passed":
        raise BundleError(f"the evaluation gate did not pass (gate={manifest.get('gate')!r})")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise BundleError("manifest lists no files")
    for rel, digest in files.items():
        path = os.path.normpath(os.path.join(folder, rel))
        if not path.startswith(os.path.abspath(folder) + os.sep) or not os.path.isfile(path):
            raise BundleError(f"manifest names a file that is not in the bundle: {rel}")
        if _sha256(path) != digest:
            raise BundleError(f"checksum mismatch for {rel}")
    listed = {os.path.normpath(r) for r in files} | {"manifest.json"}
    for dp, _, fns in os.walk(folder):
        for fn in fns:
            rel = os.path.relpath(os.path.join(dp, fn), folder)
            if rel not in listed:
                raise BundleError(f"bundle holds a file the manifest does not list: {rel}")
    return manifest


def _check_ids(folder: str, db_path: str) -> None:
    ids: set = set()
    sem = os.path.join(folder, "tiger", "semantic_ids.json")
    if os.path.exists(sem):
        with open(sem, "r", encoding="utf-8") as f:
            ids |= {int(k) for k in (json.load(f).get("items") or {})}
    vib = os.path.join(folder, "vibe", "item_ids.json")
    if os.path.exists(vib):
        with open(vib, "r", encoding="utf-8") as f:
            ids |= {int(i) for i in json.load(f)}
    if not ids:
        raise BundleError("bundle has neither tiger/semantic_ids.json nor vibe/item_ids.json")
    conn = sqlite3.connect(db_path)
    try:
        have = {r[0] for r in conn.execute("SELECT id FROM media_items")}
    finally:
        conn.close()
    missing = sorted(ids - have)
    if missing:
        raise BundleError(
            f"{len(missing)} item id(s) in the model are not in the database, e.g. {missing[:5]}. "
            "The Mac trained on a database that is not this server's."
        )


def smoke_test(folder: str, db_path: str) -> None:
    """Loads the model from `folder` as the app would and asks it one question."""
    from database import DatabaseManager
    from services.generative_retriever import GenerativeRetriever
    from services.vibe_index import VibeIndex

    if os.path.isdir(os.path.join(folder, "tiger")):
        retriever = GenerativeRetriever(DatabaseManager(db_path), {})
        retriever.artifact_dir = os.path.join(folder, "tiger")
        if not retriever.is_ready():
            raise BundleError("smoke test: the TIGER model did not load")
        if not retriever.predict_top_tuples([[0, 0, 0]]):
            raise BundleError("smoke test: the TIGER model returned no candidates")
    if os.path.isdir(os.path.join(folder, "vibe")):
        index = VibeIndex(os.path.join(folder, "vibe"))
        if not index.is_ready():
            raise BundleError(f"smoke test: the vibe index did not load ({index.status().get('error')})")
        if index.encode_query("a quiet, funny film") is None:
            raise BundleError("smoke test: the vibe index could not encode a query")


def _point_current(current_link: str, bundle_id: str) -> None:
    """Atomically points `current` at releases/<bundle_id> (a relative link, valid in any container)."""
    parent = os.path.dirname(os.path.abspath(current_link))
    if os.path.isdir(current_link) and not os.path.islink(current_link):
        # An old plain folder: keep it as a release so nothing is lost.
        legacy = os.path.join(releases_dir(current_link), f"legacy-{int(time.time())}")
        os.makedirs(os.path.dirname(legacy), exist_ok=True)
        os.rename(current_link, legacy)
    tmp = os.path.join(parent, f".current.{os.getpid()}.tmp")
    if os.path.lexists(tmp):
        os.remove(tmp)
    os.symlink(os.path.join("releases", bundle_id), tmp)
    os.replace(tmp, current_link)


def _prune(current_link: str, keep: int = KEEP_RELEASES) -> None:
    root = releases_dir(current_link)
    live = current_release(current_link)
    names = list_releases(current_link)
    for name in names[:-keep] if len(names) > keep else []:
        if name != live:
            shutil.rmtree(os.path.join(root, name), ignore_errors=True)


def _swap_and_reload(current_link: str, bundle_id: str, previous: Optional[str],
                     reload_models: Callable[[], None]) -> None:
    _point_current(current_link, bundle_id)
    try:
        reload_models()
    except Exception as e:  # noqa: BLE001 - any failure means go back
        if previous:
            _point_current(current_link, previous)
            try:
                reload_models()
            except Exception:  # noqa: BLE001
                pass
        raise BundleError(f"the new model failed to load, so the old one was restored: {e}", 500) from e


def install_bundle(archive: str, *, db_path: str, current_link: str,
                   reload_models: Callable[[], None]) -> Dict[str, Any]:
    """Verifies and installs a bundle. Returns its manifest. Raises BundleError."""
    root = releases_dir(current_link)
    os.makedirs(root, exist_ok=True)
    staging = os.path.join(root, f".incoming-{os.getpid()}-{int(time.time())}")
    try:
        _unpack(archive, staging)
        manifest = _verify(staging)
        bundle_id = manifest["bundle_id"]
        final = os.path.join(root, bundle_id)
        if os.path.exists(final):
            raise BundleError(f"bundle {bundle_id} is already installed", 409)
        _check_ids(staging, db_path)
        smoke_test(staging, db_path)
        os.rename(staging, final)
        os.utime(final)  # releases are ordered by age; this one is the newest
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    previous = current_release(current_link)
    try:
        patch = os.path.join(final, "db_patch.jsonl")
        if os.path.exists(patch):
            with open(patch, "r", encoding="utf-8") as f:
                apply_patch(db_path, f)
        _swap_and_reload(current_link, bundle_id, previous, reload_models)
    except Exception:
        # Keep the folder only if it is what `current` points at.
        if current_release(current_link) != bundle_id:
            shutil.rmtree(final, ignore_errors=True)
        raise
    _prune(current_link)
    return manifest


def rollback(current_link: str, reload_models: Callable[[], None]) -> str:
    """Points `current` at the release before it and reloads. Returns the new bundle_id."""
    live = current_release(current_link)
    names = list_releases(current_link)
    if live not in names or names.index(live) == 0:
        raise BundleError("there is no earlier model release to go back to", 409)
    target = names[names.index(live) - 1]
    _swap_and_reload(current_link, target, live, reload_models)
    return target
