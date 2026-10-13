"""Health and admin for the Northern Lights app (M10b, H3 and H4).

H3, the compact status in the header: is Jellyfin reachable, how much disk is free, how many downloads are
queued. H4, the admin screen: the same plus Jellyfin's diagnostics, library repair and links to Jellystat,
Uptime Kuma and Dozzle when they are configured.

What this module may show is exactly what the existing status endpoints already show (`/api/jellyfin/status`,
`services/disk_guard.py`, the downloader's store). Never an API key, a token or a file path outside the
configured media folders. Links come from configuration, are checked to be plain http(s) addresses and are
left out when they are not.
"""

from __future__ import annotations

import os
import shutil
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from services import disk_guard
from services import nl_downloads

GB = disk_guard.GB
JELLYFIN_TTL = 15.0      # seconds a Jellyfin check is reused (every page of every viewer asks)
DISK_TTL = 30.0
MEDIA_TTL = 120.0        # the folder walk behind "media used" is only done for the admin screen
WARN_FREE_GB_FALLBACK = 10.0

TOOLS = (
    ("jellystat", "Jellystat", "NL_JELLYSTAT_URL"),
    ("uptime_kuma", "Uptime Kuma", "NL_UPTIME_KUMA_URL"),
    ("dozzle", "Dozzle", "NL_DOZZLE_URL"),
)
MAX_URL_CHARS = 300


# ------------------------------------------------------------------ links

def clean_http_url(value: Any) -> Optional[str]:
    """The address if it is a plain http(s) URL with a host, no credentials, no spaces or control characters
    and a sensible length; else None. Nothing else (javascript:, data:, file:, //host) is ever linked."""
    if not isinstance(value, str):
        return None
    url = value.strip()
    if not url or len(url) > MAX_URL_CHARS or any(ord(c) < 33 or ord(c) == 127 for c in url):
        return None
    try:
        parts = urlsplit(url)
        host = parts.hostname
        parts.port  # noqa: B018 - raises ValueError on a bad port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not host:
        return None
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        return None   # credentials inside a link would be shown to everyone who can open the page
    return url


def tool_links(config: Optional[Dict[str, Any]], env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """{links: [{id, name, url}], ignored: [name]} from `northern_lights.tools` (keys jellystat, uptime_kuma,
    dozzle) in the config, or the NL_JELLYSTAT_URL / NL_UPTIME_KUMA_URL / NL_DOZZLE_URL environment variables
    (the environment wins). An unset tool has no link at all; a set but invalid one is listed in `ignored` by name
    only (its text is never echoed)."""
    env = os.environ if env is None else env
    section = ((config or {}).get("northern_lights") or {})
    tools = section.get("tools") if isinstance(section, dict) else None
    tools = tools if isinstance(tools, dict) else {}
    links: List[Dict[str, str]] = []
    ignored: List[str] = []
    for key, name, env_name in TOOLS:
        raw = env.get(env_name) or tools.get(key)
        if raw in (None, ""):
            continue
        url = clean_http_url(raw)
        if url:
            links.append({"id": key, "name": name, "url": url})
        else:
            ignored.append(name)
    return {"links": links, "ignored": ignored}


# ------------------------------------------------------------------ Jellyfin

def check_jellyfin(svc: Any) -> Dict[str, Any]:
    """Is Jellyfin reachable, and does our key still work? Two quick GETs through the same session the app
    uses (the public info page, then the library list). Never raises; never returns the URL or the key."""
    out = {"configured": bool(getattr(svc, "configured", False)), "reachable": False, "authorised": None}
    if not out["configured"]:
        return out
    base = svc.base_url
    try:
        resp = svc.session.get(f"{base}/System/Info/Public", timeout=3)
        out["reachable"] = getattr(resp, "status_code", 0) == 200
    except Exception:  # noqa: BLE001 - any failure means "not reachable"
        return out
    if not out["reachable"]:
        return out
    try:
        resp = svc.session.get(f"{base}/Library/VirtualFolders", timeout=4)
        code = getattr(resp, "status_code", 0)
        out["authorised"] = False if code in (401, 403) else (True if code == 200 else None)
    except Exception:  # noqa: BLE001
        out["authorised"] = None
    return out


# ------------------------------------------------------------------ disk

def _probe_dir(config: Dict[str, Any]) -> str:
    tcfg = (config.get("torrent") or {})
    download_dir = tcfg.get("download_dir", "./downloads")
    if os.path.isdir(download_dir):
        return download_dir
    parent = os.path.dirname(os.path.abspath(download_dir))
    while parent and not os.path.isdir(parent) and parent != os.path.dirname(parent):
        parent = os.path.dirname(parent)
    return parent or "."


def disk_summary(config: Dict[str, Any]) -> Dict[str, Any]:
    """Free space on the disk that holds the downloads and the media, against the disk guard's limits
    (`torrent.min_free_gb`, `torrent.max_media_gb`). `level` is ok, warn (under twice the minimum) or bad
    (under the minimum: new downloads are refused)."""
    tcfg = (config.get("torrent") or {})
    try:
        min_free = float(tcfg.get("min_free_gb", disk_guard.DEFAULT_MIN_FREE_GB) or 0)
        max_media = float(tcfg.get("max_media_gb", disk_guard.DEFAULT_MAX_MEDIA_GB) or 0)
    except (TypeError, ValueError):
        min_free, max_media = disk_guard.DEFAULT_MIN_FREE_GB, disk_guard.DEFAULT_MAX_MEDIA_GB
    try:
        usage = shutil.disk_usage(_probe_dir(config))
        free, total = int(usage.free), int(usage.total)
    except OSError:
        return {"known": False, "level": "warn", "free_bytes": None, "total_bytes": None,
                "min_free_gb": min_free, "max_media_gb": max_media}
    warn_at = (min_free * 2 if min_free > 0 else WARN_FREE_GB_FALLBACK) * GB
    level = "ok"
    if min_free > 0 and free < min_free * GB:
        level = "bad"
    elif free < warn_at:
        level = "warn"
    return {"known": True, "level": level, "free_bytes": free, "total_bytes": total,
            "min_free_gb": min_free, "max_media_gb": max_media}


# ------------------------------------------------------------------ the snapshot

class Health:
    """Builds the header status and the admin payload. Checks are cached for a few seconds so many pages and
    viewers do not each walk to Jellyfin or the disk. `clock` is replaceable for tests."""

    def __init__(self, config: Dict[str, Any], jellyfin: Any, downloads: Dict[int, Dict[str, Any]],
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.config = config
        self.jellyfin = jellyfin
        self.downloads = downloads
        self.clock = clock
        self._lock = threading.Lock()
        self._cache: Dict[str, Tuple[float, Any]] = {}

    def _cached(self, key: str, ttl: float, make: Callable[[], Any]) -> Any:
        now = self.clock()
        with self._lock:
            hit = self._cache.get(key)
            if hit and now - hit[0] < ttl:
                return hit[1]
        value = make()
        with self._lock:
            self._cache[key] = (self.clock(), value)
        return value

    def forget(self) -> None:
        with self._lock:
            self._cache.clear()

    def queue(self) -> Dict[str, int]:
        states = [{"state": nl_downloads.state_of(e)} for e in list(self.downloads.values()) if isinstance(e, dict)]
        c = nl_downloads.counts(states)
        return {"active": c["active"], "downloading": c["downloading"], "queued": c["queued"],
                "importing": c["importing"], "failed": c["failed"]}

    def jellyfin_state(self) -> Dict[str, Any]:
        raw = self._cached("jellyfin", JELLYFIN_TTL, lambda: check_jellyfin(self.jellyfin))
        ok = bool(raw["configured"] and raw["reachable"] and raw["authorised"] is not False)
        if not raw["configured"]:
            label = "Jellyfin is not set up"
        elif not raw["reachable"]:
            label = "Jellyfin is not answering"
        elif raw["authorised"] is False:
            label = "Jellyfin refused our key"
        else:
            label = "Jellyfin is up"
        return {"ok": ok, "configured": raw["configured"], "reachable": raw["reachable"],
                "authorised": raw["authorised"], "label": label}

    def disk_state(self) -> Dict[str, Any]:
        return self._cached("disk", DISK_TTL, lambda: disk_summary(self.config))

    def status(self) -> Dict[str, Any]:
        """The compact header status."""
        jf, disk, queue = self.jellyfin_state(), self.disk_state(), self.queue()
        level = "ok"
        if not jf["ok"] or disk["level"] == "bad":
            level = "bad"
        elif disk["level"] == "warn" or queue["failed"]:
            level = "warn"
        return {"level": level, "jellyfin": jf, "disk": disk, "queue": queue}

    def media_used(self) -> Optional[int]:
        """Bytes in the media folders and the download folder (the disk guard's own measure). Cached: it walks
        the folders."""
        tcfg = (self.config.get("torrent") or {})
        folders = list(self.config.get("media_folders", []) or []) + [tcfg.get("download_dir", "./downloads")]

        def make() -> Optional[int]:
            try:
                return int(disk_guard.media_bytes(folders))
            except OSError:
                return None
        return self._cached("media", MEDIA_TTL, make)
