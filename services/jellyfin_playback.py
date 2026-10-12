"""
Jellyfin playback (services/jellyfin_playback.py)

Northern Lights M0: turns one of our media_items into something a browser can play.
- find_item_id(file_path): our file path -> Jellyfin item id (cached, refreshed once on a miss)
- playback_info(jf_id, profile): asks Jellyfin how to play it for a DeviceProfile
- stream_for(item): the URL (always under /nl/jf/, our proxy) plus how it is played

The browser never talks to Jellyfin and never sees the API key: URLs handed out here
point at our own proxy, and the pure helpers below strip any key from URLs and playlists.
"""

from __future__ import annotations

import posixpath
import re
import threading
from typing import Any, Dict, Optional
from urllib.parse import unquote

from services.device_profiles import BROWSER_PROFILE
from services.jellyfin_service import JellyfinService

PROXY_PREFIX = "/nl/jf/"
MAX_STREAMING_BITRATE = 20_000_000
_KEY_PARAMS = {"api_key", "apikey", "x-emby-token"}
_URI_ATTR = re.compile(r'URI="([^"]*)"')


class JellyfinUnavailable(Exception):
    """Jellyfin is not configured or could not be reached (HTTP 503)."""


class ItemNotFound(Exception):
    """Jellyfin does not know this file (HTTP 404)."""


class NotPlayable(Exception):
    """Jellyfin has no way to play this item for the browser profile (HTTP 415)."""


# ---------------------------------------------------------------- pure helpers


def strip_api_key(url: str) -> str:
    """Remove api_key= / ApiKey= query parameters (any case); keep everything else as is."""
    if "?" not in url:
        return url
    base, _, rest = url.partition("?")
    query, hash_sep, fragment = rest.partition("#")
    kept = [
        part
        for part in query.split("&")
        if part and part.split("=", 1)[0].lower() not in _KEY_PARAMS
    ]
    out = base + ("?" + "&".join(kept) if kept else "")
    return out + (hash_sep + fragment if hash_sep else "")


def rewrite_playlist(text: str) -> str:
    """Strip API keys from an .m3u8 body: URI lines, and URI="..." attributes in # tags.
    Other tag lines are left alone."""
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            lines.append(line)
        elif stripped.startswith("#"):
            lines.append(_URI_ATTR.sub(lambda m: 'URI="%s"' % strip_api_key(m.group(1)), line))
        else:
            lines.append(strip_api_key(stripped))
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def safe_proxy_path(path: str) -> Optional[str]:
    """Return the path if the proxy may forward it, else None.

    Only paths under videos/ or Videos/ pass. Anything with '..' (also percent-encoded,
    or double-encoded), backslashes, control characters or empty segments is refused.
    """
    decoded = path
    for _ in range(4):  # defeat %2e%2e and %252e%252e
        nxt = unquote(decoded)
        if nxt == decoded:
            break
        decoded = nxt
    else:
        return None
    if ".." in decoded or "\\" in decoded or "//" in decoded:
        return None
    if any(ord(c) < 32 or ord(c) == 127 for c in decoded):
        return None
    if "?" in decoded or "#" in decoded:
        return None
    if posixpath.normpath(decoded) != decoded.rstrip("/"):
        return None
    if not (decoded.startswith("videos/") or decoded.startswith("Videos/")):
        return None
    return path


def proxy_url(jellyfin_path: str) -> str:
    """Our proxy URL for a Jellyfin-relative path (with query), with any API key removed."""
    return PROXY_PREFIX + strip_api_key(jellyfin_path).lstrip("/")


# --------------------------------------------------------------------- service


class JellyfinPlayback:
    def __init__(self, jellyfin_service: JellyfinService):
        self.jf = jellyfin_service
        self._ids: Dict[str, str] = {}
        self._lock = threading.Lock()

    def _require(self) -> None:
        if not self.jf.configured or not self.jf.user_id:
            raise JellyfinUnavailable(
                "Jellyfin is not configured (set JELLYFIN_URL, JELLYFIN_API_KEY and JELLYFIN_USER_ID in .env)."
            )

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        self._require()
        try:
            resp = self.jf.session.request(method, f"{self.jf.base_url}{path}", timeout=15, **kwargs)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:  # requests errors, bad JSON
            raise JellyfinUnavailable(f"Jellyfin could not be reached ({type(e).__name__}).") from e

    def _refresh_ids(self) -> None:
        data = self._request(
            "GET",
            "/Items",
            params={
                "Recursive": "true",
                "IncludeItemTypes": "Movie,Episode",
                "Fields": "Path",
                "UserId": self.jf.user_id,
            },
        )
        self._ids = {
            it["Path"]: it["Id"] for it in (data.get("Items") or []) if it.get("Path") and it.get("Id")
        }

    def find_item_id(self, file_path: str) -> Optional[str]:
        """Jellyfin id for one of our file paths, or None. Refreshes the cache once on a miss."""
        with self._lock:
            if file_path in self._ids:
                return self._ids[file_path]
            self._refresh_ids()
            return self._ids.get(file_path)

    def playback_info(self, jf_id: str, profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        body = {
            "DeviceProfile": profile or BROWSER_PROFILE,
            "MaxStreamingBitrate": MAX_STREAMING_BITRATE,
            "EnableDirectPlay": True,
            "EnableDirectStream": True,
            "EnableTranscoding": True,
            "AutoOpenLiveStream": False,
        }
        return self._request(
            "POST", f"/Items/{jf_id}/PlaybackInfo", params={"UserId": self.jf.user_id}, json=body
        )

    def stream_for(self, item: Dict[str, Any]) -> Dict[str, Any]:
        sources = item.get("sources") or []
        if not sources:
            raise ItemNotFound("This item has no media file.")
        jf_id = self.find_item_id(sources[0]["file_path"])
        if not jf_id:
            raise ItemNotFound("Jellyfin does not know this file yet.")
        info = self.playback_info(jf_id)
        media_sources = info.get("MediaSources") or []
        if info.get("ErrorCode") or not media_sources:
            raise NotPlayable(f"Jellyfin cannot play this file ({info.get('ErrorCode') or 'no media source'}).")
        ms = media_sources[0]
        session_id = info.get("PlaySessionId") or ""
        source_id = ms.get("Id") or jf_id
        transcoding_url = ms.get("TranscodingUrl")

        if ms.get("SupportsDirectPlay") or (ms.get("SupportsDirectStream") and not transcoding_url):
            play_method = "DirectPlay" if ms.get("SupportsDirectPlay") else "DirectStream"
            url = proxy_url(
                f"Videos/{jf_id}/stream?static=true&MediaSourceId={source_id}&PlaySessionId={session_id}"
            )
            kind = "file"
        elif transcoding_url:
            play_method = "DirectStream" if ms.get("SupportsDirectStream") else "Transcode"
            url = proxy_url(transcoding_url)
            kind = "hls"
        else:
            raise NotPlayable("Jellyfin offered no way to play this file in a browser.")

        ticks = ms.get("RunTimeTicks")
        return {
            "url": url,
            "kind": kind,
            "play_method": play_method,
            "play_session_id": session_id,
            "media_source_id": source_id,
            "jellyfin_id": jf_id,
            "container": ms.get("Container"),
            "duration_seconds": (ticks / 10_000_000) if ticks else None,
            "title": item.get("title"),
        }
