"""Downloads in the Northern Lights app (M10a, H1 and H2).

Pure helpers that turn the downloader's in-memory store (`services.torrent_service.downloads_store`, keyed by
the media item id) into the rows the Downloads screen draws:

    queued       connecting, checking files, fetching the torrent's details
    downloading  moving data (percent, speed, time left)
    importing    finished downloading; being moved into the library, or waiting for it to appear there
    ready        in the library: the row links straight to Play (a movie) or to the show page
    failed       an error; the row says what went wrong in plain words and offers Retry
    cancelled    stopped by hand; Retry is offered too

Nothing here talks to Jellyfin or the network. The magnet link of a download is kept server-side only
(`DownloadRequests`) so Retry can start it again; it is never put in a reply.
"""

from __future__ import annotations

import re
import threading
from typing import Any, Callable, Dict, List, Optional

MAX_REQUESTS = 200          # remembered magnet links; the oldest are dropped
MAX_ERROR_CHARS = 300
MAX_READY_SHOWN = 8         # finished downloads listed at once

STATE_LABELS = {
    "queued": "Waiting to start",
    "downloading": "Downloading",
    "importing": "Adding to your library",
    "ready": "Ready",
    "failed": "Failed",
    "cancelled": "Stopped",
}
# Retry makes sense for these; everything else is either running or done.
RETRY_STATES = ("failed", "cancelled")
# A row can be cleared from the list in these states (it is only a record).
DISMISS_STATES = ("failed", "cancelled", "ready")

_QUEUED_RAW = {
    "connecting", "idle", "queued", "queued_for_checking", "checking_files", "checking_resume_data",
    "allocating", "downloading_metadata", "checking",
}
_IMPORTING_RAW = {"organizing", "seeding", "finished"}


class DownloadRequests:
    """The magnet link, clean title and kind of each download that was started, so Retry can run it again.
    Held in memory only (a restart empties it, like the downloader's own store)."""

    def __init__(self) -> None:
        self._items: Dict[int, Dict[str, str]] = {}
        self._lock = threading.Lock()

    def remember(self, item_id: int, magnet: str, title: str, media_type: str) -> None:
        with self._lock:
            self._items.pop(item_id, None)
            self._items[item_id] = {"magnet": magnet, "title": title, "media_type": media_type}
            while len(self._items) > MAX_REQUESTS:
                self._items.pop(next(iter(self._items)))

    def get(self, item_id: int) -> Optional[Dict[str, str]]:
        with self._lock:
            row = self._items.get(item_id)
            return dict(row) if row else None

    def forget(self, item_id: int) -> None:
        with self._lock:
            self._items.pop(item_id, None)


def _raw_state(entry: Dict[str, Any]) -> str:
    raw = str(entry.get("status") or "").strip().lower()
    return raw.split(".")[-1]          # libtorrent can print "torrent_status.downloading"


def state_of(entry: Dict[str, Any]) -> str:
    """queued | downloading | importing | ready | failed | cancelled for one downloader entry.
    `ready` here means the downloader says it is done; `describe` also checks the library."""
    raw = _raw_state(entry)
    if raw == "cancelled":
        return "cancelled"
    if raw == "error" or entry.get("error"):
        return "failed"
    if raw == "completed":
        return "ready"
    if raw in _IMPORTING_RAW:
        return "importing"
    if raw == "downloading":
        # No size and no progress yet is still just starting.
        return "downloading" if (entry.get("total_size") or entry.get("progress")) else "queued"
    return "queued"


_PATH_LIKE = re.compile(r"(?:/[\w.\- ]+){2,}")


def friendly_error(message: Any, state: str = "failed") -> str:
    """The downloader's error in plain words. Paths are cut out (they mean nothing on a TV) and the text
    is capped; known causes get a clearer sentence."""
    if state == "cancelled":
        return "You stopped this download."
    text = " ".join(str(message or "").split())
    low = text.lower()
    if not text:
        return "The download stopped and the reason is unknown. Try again."
    if "not enough room" in low:
        return text[:MAX_ERROR_CHARS]
    if "too small" in low or "fake torrent" in low:
        return "That torrent is far too small to be the real file, so it was dropped. Pick another copy."
    if "prowlarr" in low and "magnet" in low:
        return "The search site's link could not be turned into a download. Pick another copy."
    if "timed out" in low or "timeout" in low:
        return "The download timed out. Try again."
    text = _PATH_LIKE.sub("a file", text)
    return text[:MAX_ERROR_CHARS]


def _num(value: Any) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return 0.0
    return n if n == n and n >= 0 else 0.0


def describe(
    item_id: int,
    entry: Dict[str, Any],
    request: Optional[Dict[str, str]] = None,
    brief: Optional[Dict[str, Any]] = None,
    in_library: bool = False,
) -> Dict[str, Any]:
    """One Downloads row. `request` is what was remembered at start (clean title, media type), `brief` the
    catalogue's {title, media_type} for the id, `in_library` whether the title has a playable file now."""
    state = state_of(entry)
    media_type = (request or {}).get("media_type") or (brief or {}).get("media_type") or entry.get("media_type") or "movie"
    media_type = "tv" if media_type == "tv" else "movie"
    title = (request or {}).get("title") or (brief or {}).get("title") or entry.get("name") or "Download"
    note = ""
    if state == "ready" and not in_library:
        state = "importing"
        note = "Waiting for the library to pick it up."
    percent = max(0.0, min(100.0, _num(entry.get("progress"))))
    if state in ("importing", "ready"):
        percent = 100.0
    eta = entry.get("eta_seconds")
    row: Dict[str, Any] = {
        "id": item_id,
        "title": title,
        "media_type": media_type,
        "state": state,
        "label": STATE_LABELS[state],
        "percent": round(percent, 1),
        "size_bytes": int(_num(entry.get("total_size"))),
        "downloaded_bytes": int(_num(entry.get("downloaded"))),
        "speed_bytes": int(_num(entry.get("download_speed"))) if state == "downloading" else 0,
        "eta_seconds": int(eta) if state == "downloading" and isinstance(eta, (int, float)) and eta >= 0 else None,
        "error": friendly_error(entry.get("error"), state) if state in RETRY_STATES else None,
        "note": note or None,
        "can_retry": state in RETRY_STATES and request is not None,
        "can_dismiss": state in DISMISS_STATES,
        "href": None,
        "play_href": None,
    }
    if state == "ready":
        if media_type == "tv":
            row["href"] = f"/nl/show/{item_id}"
        else:
            row["href"] = f"/nl/title/{item_id}"
            row["play_href"] = f"/nl/play/{item_id}"
    return row


_ORDER = {"downloading": 0, "importing": 1, "queued": 2, "failed": 3, "cancelled": 4, "ready": 5}


def counts(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    out = {k: 0 for k in STATE_LABELS}
    for r in rows:
        out[r["state"]] = out.get(r["state"], 0) + 1
    out["active"] = out["queued"] + out["downloading"] + out["importing"]
    out["total"] = len(rows)
    return out


def build_list(
    store: Dict[int, Dict[str, Any]],
    requests: DownloadRequests,
    brief_of: Callable[[int], Optional[Dict[str, Any]]],
    in_library_of: Callable[[int], bool],
) -> Dict[str, Any]:
    """Every download the downloader knows, active ones first, with counts for the header status."""
    rows: List[Dict[str, Any]] = []
    for item_id, entry in list(store.items()):
        if not isinstance(entry, dict):
            continue
        done = state_of(entry) == "ready"
        rows.append(describe(
            item_id, entry, requests.get(item_id), brief_of(item_id), in_library_of(item_id) if done else False,
        ))
    rows.sort(key=lambda r: (_ORDER.get(r["state"], 9), -r["id"]))
    # Finished downloads stay as a record until dismissed; show only the newest few.
    ready = [r for r in rows if r["state"] == "ready"]
    for r in ready[MAX_READY_SHOWN:]:
        rows.remove(r)
    return {"downloads": rows, "counts": counts(rows)}
