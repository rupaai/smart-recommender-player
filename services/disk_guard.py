"""Refuses downloads that would overfill the media disk.

Two limits, both from `torrent:` in config.yaml:
  max_media_gb   cap on the media folders + download folder together (default 100; 0 = no cap)
  min_free_gb    free space that must remain on the disk afterwards (default 20; 0 = off)

In-progress downloads count at their full size: what they already wrote is in the
folder total, and what they still have to write is added on top.
"""

from __future__ import annotations

import os
import shutil
from typing import Any, Dict, Iterable, List, Optional

GB = 1024 ** 3
DEFAULT_MAX_MEDIA_GB = 100.0
DEFAULT_MIN_FREE_GB = 20.0


def folder_size(path: str) -> int:
    """Total bytes of regular files under `path` (0 if it doesn't exist)."""
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total


def media_bytes(folders: Iterable[str]) -> int:
    """Size of the given folders, counting a folder nested inside another once."""
    roots: List[str] = []
    for f in sorted({os.path.abspath(p) for p in folders if p}):
        if not any(f == r or f.startswith(r + os.sep) for r in roots):
            roots.append(f)
    return sum(folder_size(r) for r in roots)


def pending_bytes(downloads: Dict[Any, Dict[str, Any]], exclude_id: Any = None) -> int:
    """Bytes other active downloads still have to write."""
    pending = 0
    for key, d in downloads.items():
        if key == exclude_id or d.get("status") in ("error", "completed", "cancelled"):
            continue
        pending += max(0, int(d.get("total_size") or 0) - int(d.get("downloaded") or 0))
    return pending


def check_download(
    config: Dict[str, Any],
    incoming_bytes: int,
    downloads: Optional[Dict[Any, Dict[str, Any]]] = None,
    exclude_id: Any = None,
) -> Optional[str]:
    """Returns a plain-English reason to refuse, or None if the download fits."""
    tcfg = config.get("torrent", {}) or {}
    max_gb = float(tcfg.get("max_media_gb", DEFAULT_MAX_MEDIA_GB) or 0)
    min_free_gb = float(tcfg.get("min_free_gb", DEFAULT_MIN_FREE_GB) or 0)
    download_dir = tcfg.get("download_dir", "./downloads")
    folders = list(config.get("media_folders", []) or []) + [download_dir]

    other = pending_bytes(downloads or {}, exclude_id)
    if max_gb > 0:
        used = media_bytes(folders)
        projected = used + other + incoming_bytes
        if projected > max_gb * GB:
            return (
                f"Not enough room: your media folder holds {used / GB:.1f} GB and this download "
                f"is {incoming_bytes / GB:.1f} GB, which goes over the {max_gb:g} GB limit "
                f"(torrent.max_media_gb)."
            )
    if min_free_gb > 0:
        probe = download_dir if os.path.isdir(download_dir) else os.path.dirname(os.path.abspath(download_dir))
        free = shutil.disk_usage(probe).free
        if free - other - incoming_bytes < min_free_gb * GB:
            return (
                f"Not enough room: only {free / GB:.1f} GB is free on the disk and at least "
                f"{min_free_gb:g} GB must stay free (torrent.min_free_gb)."
            )
    return None
