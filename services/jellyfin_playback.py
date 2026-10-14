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

import contextlib
import contextvars
import copy
import os
import posixpath
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib.parse import quote, unquote

from services.device_profiles import BROWSER_PROFILE
from services import nl_picture, nl_settings
from services.nl_people import pick_audio_index, pick_subtitle_track
from services.jellyfin_service import JellyfinService

PROXY_PREFIX = "/nl/jf/"
MAX_STREAMING_BITRATE = 20_000_000
_KEY_PARAMS = {"api_key", "apikey", "x-emby-token"}
_URI_ATTR = re.compile(r'URI="([^"]*)"')
# Read-only artwork: Items/<id>/Images/<kind>[/<index>] and nothing else under Items/ (M2).
_IMAGE_PATH = re.compile(r"^Items/[A-Za-z0-9-]{1,64}/Images/(Primary|Backdrop|Thumb|Logo|Banner)(/[0-9]{1,2})?$")
# Seek-bar thumbnails (M7a): Videos/<id>/Trickplay/<tile width>/<sheet number>.jpg and nothing else under Trickplay.
# (The tiles.m3u8 playlist is not used: the server hands the page the geometry as JSON.)
_TRICKPLAY_PATH = re.compile(r"^Videos/[A-Za-z0-9-]{1,64}/Trickplay/[0-9]{2,4}/[0-9]{1,6}\.jpg$")


class JellyfinUnavailable(Exception):
    """Jellyfin is not configured or could not be reached (HTTP 503)."""


class ItemNotFound(Exception):
    """Jellyfin does not know this file (HTTP 404)."""


class NotPlayable(Exception):
    """Jellyfin has no way to play this item for the browser profile (HTTP 415)."""


class SubtitleError(Exception):
    """A subtitle search or download that Jellyfin could not do, in words for the viewer (M5).

    `code` is one of plugin_missing, plugin_unconfigured, limit, not_found, failed, bad_request;
    `status` is the HTTP status our API answers with."""

    def __init__(self, code: str, message: str, status: int):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


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


def append_token(url: str, token: str) -> str:
    """Adds t=<token> (a cast stream token, M8) to a URL's query, before any #fragment."""
    base, hash_sep, fragment = url.partition("#")
    out = f"{base}{'&' if '?' in base else '?'}t={quote(token, safe='')}"
    return out + (hash_sep + fragment if hash_sep else "")


def rewrite_playlist(text: str, token: Optional[str] = None) -> str:
    """Strip API keys from an .m3u8 body: URI lines, and URI="..." attributes in # tags.
    Other tag lines are left alone. With a cast `token` (M8) every URI also gets t=<token>: the
    Chromecast fetches the segments itself and a relative URI does not inherit the playlist's query."""

    def fix(uri: str) -> str:
        uri = strip_api_key(uri)
        return append_token(uri, token) if token and not uri.lower().startswith("data:") else uri

    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            lines.append(line)
        elif stripped.startswith("#"):
            lines.append(_URI_ATTR.sub(lambda m: 'URI="%s"' % fix(m.group(1)), line))
        else:
            lines.append(fix(stripped))
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def safe_proxy_path(path: str) -> Optional[str]:
    """Return the path if the proxy may forward it, else None.

    Only paths under videos/ or Videos/ pass, plus artwork (Items/<id>/Images/<kind>[/<n>],
    GET only; the proxy route has no other method). Anything with '..' (also percent-encoded,
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
    if decoded == path and _TRICKPLAY_PATH.match(path):
        return path
    if decoded.startswith("videos/") or decoded.startswith("Videos/"):
        return path
    if decoded == path and _IMAGE_PATH.match(path):  # artwork ids are plain, never percent-encoded
        return path
    return None


def is_image_path(path: str) -> bool:
    return bool(_IMAGE_PATH.match(path)) or bool(_TRICKPLAY_PATH.match(path))


def is_trickplay_path(path: str) -> bool:
    return bool(_TRICKPLAY_PATH.match(path))


def proxy_url(jellyfin_path: str) -> str:
    """Our proxy URL for a Jellyfin-relative path (with query), with any API key removed."""
    return PROXY_PREFIX + strip_api_key(jellyfin_path).lstrip("/")



# ------------------------------------------------------------- tracks (M1)

_JF_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_TICKS = 10_000_000

_LANGUAGES = {
    "eng": "English", "spa": "Spanish", "fra": "French", "fre": "French", "deu": "German",
    "ger": "German", "ita": "Italian", "por": "Portuguese", "jpn": "Japanese", "kor": "Korean",
    "zho": "Chinese", "chi": "Chinese", "rus": "Russian", "ara": "Arabic", "hin": "Hindi",
    "ben": "Bengali", "nld": "Dutch", "dut": "Dutch", "swe": "Swedish", "nor": "Norwegian",
    "dan": "Danish", "fin": "Finnish", "pol": "Polish", "tur": "Turkish", "hun": "Hungarian",
    "ces": "Czech", "cze": "Czech", "ell": "Greek", "gre": "Greek", "heb": "Hebrew",
    "tha": "Thai", "vie": "Vietnamese", "ind": "Indonesian", "ukr": "Ukrainian",
    "ron": "Romanian", "rum": "Romanian", "bul": "Bulgarian", "hrv": "Croatian", "srp": "Serbian",
    "fas": "Persian", "per": "Persian", "urd": "Urdu", "tam": "Tamil", "tel": "Telugu",
}
_AUDIO_CODECS = {
    "aac": "AAC", "ac3": "AC3", "eac3": "E-AC-3", "dts": "DTS", "truehd": "TrueHD", "flac": "FLAC",
    "opus": "Opus", "mp3": "MP3", "vorbis": "Vorbis", "pcm_s16le": "PCM", "pcm_s24le": "PCM",
}
_CHANNELS = {1: "Mono", 2: "Stereo", 6: "5.1", 8: "7.1"}
_IMAGE_SUB_CODECS = {
    "pgssub": "PGS", "pgs": "PGS", "hdmv_pgs_subtitle": "PGS", "dvdsub": "VobSub",
    "dvd_subtitle": "VobSub", "vobsub": "VobSub", "dvbsub": "DVB", "dvb_subtitle": "DVB", "xsub": "XSUB",
}


def valid_jellyfin_id(value: str) -> bool:
    return bool(value) and bool(_JF_ID.match(value))


def seconds_to_ticks(seconds: float) -> int:
    return int(round(max(0.0, float(seconds or 0)) * _TICKS))


def _language_name(stream: Dict[str, Any]) -> str:
    name = stream.get("DisplayLanguage")
    if name:
        return str(name)
    code = (stream.get("Language") or "").lower()
    if not code or code == "und":
        return "Unknown"
    return _LANGUAGES.get(code, code.upper())


def _is_image_subtitle(stream: Dict[str, Any]) -> bool:
    codec = (stream.get("Codec") or "").lower()
    if codec in _IMAGE_SUB_CODECS:
        return True
    if "IsTextSubtitleStream" in stream:
        return not stream["IsTextSubtitleStream"]
    return False


def audio_label(stream: Dict[str, Any]) -> str:
    parts = [_language_name(stream)]
    ch = stream.get("Channels")
    if ch:
        parts.append(_CHANNELS.get(int(ch), f"{ch}ch"))
    codec = (stream.get("Codec") or "").lower()
    if codec:
        parts.append(_AUDIO_CODECS.get(codec, codec.upper()))
    label = " \u00b7 ".join(parts)
    if "commentary" in (stream.get("Title") or "").lower():
        label += " (Commentary)"
    elif nl_picture.is_audio_description(stream):
        label += " (AD)"   # M10c, P24: a narrator describing the picture
    return label


def subtitle_label(stream: Dict[str, Any]) -> str:
    name = _language_name(stream)
    if stream.get("IsHearingImpaired"):
        name += " (SDH)"
    parts = [name]
    if stream.get("IsForced"):
        parts.append("Forced")
    if _is_image_subtitle(stream):
        parts.append(_IMAGE_SUB_CODECS.get((stream.get("Codec") or "").lower(), "Image"))
    return " \u00b7 ".join(parts)


_SUB_EXT = re.compile(r"Stream\.[A-Za-z0-9]+$")


def _subtitle_url(stream: Dict[str, Any], jf_id: str, source_id: str) -> str:
    """Proxied WebVTT URL for a text subtitle (API key stripped, only under Videos/)."""
    delivery = stream.get("DeliveryUrl")
    if delivery and stream.get("DeliveryMethod") == "External":
        rel = strip_api_key(delivery).lstrip("/")
        path, sep, query = rel.partition("?")
        # Jellyfin names the file after the subtitle's own format (Stream.srt, Stream.ass) when the device profile
        # lists it. Every player of ours reads WebVTT only (the TV app tells Media3 it is WebVTT), and Jellyfin
        # converts on the fly when asked for .vtt, so always ask for .vtt.
        path = _SUB_EXT.sub("Stream.vtt", path)
        if safe_proxy_path(path) is not None:
            return PROXY_PREFIX + path + sep + query
    return proxy_url(f"Videos/{jf_id}/{source_id}/Subtitles/{stream.get('Index')}/0/Stream.vtt")


def extract_tracks(media_source: Dict[str, Any], jf_id: str, source_id: str) -> Dict[str, list]:
    """Audio and subtitle tracks from MediaSources[n].MediaStreams, ready for the browser."""
    audio, subs = [], []
    for st in media_source.get("MediaStreams") or []:
        kind = st.get("Type")
        if kind not in ("Audio", "Subtitle") or st.get("Index") is None:
            continue
        track = {
            "index": st["Index"],
            "language": st.get("Language") or "",
            "display_title": st.get("DisplayTitle") or "",
            "codec": st.get("Codec") or "",
            "channels": st.get("Channels"),
            "is_default": bool(st.get("IsDefault")),
            "is_forced": bool(st.get("IsForced")),
            "is_hearing_impaired": bool(st.get("IsHearingImpaired")),
            "is_external": bool(st.get("IsExternal")),
            "delivery_method": st.get("DeliveryMethod") or "",
        }
        if kind == "Audio":
            track["label"] = audio_label(st)
            track["is_audio_description"] = nl_picture.is_audio_description(st)
            audio.append(track)
        else:
            image = _is_image_subtitle(st)
            track["label"] = subtitle_label(st)
            track["kind"] = "image" if image else "text"
            track["url"] = None if image else _subtitle_url(st, jf_id, source_id)
            subs.append(track)
    return {"audio_tracks": audio, "subtitle_tracks": subs}


# ------------------------------------------------------ remote subtitles (M5)

_SUB_LANGUAGE = re.compile(r"^[a-z]{3}$")
# Jellyfin's subtitle ids are "<provider>_<id>" (base64url-ish); never a path separator or a leading dot.
_SUB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.,:=+~-]{0,511}$")
MAX_SUBTITLE_RESULTS = 50
_LIMIT_WORDS = re.compile(r"download limit|rate.?limit|limit (?:reached|exceeded)|quota|too many requests|exceeded|throttl", re.I)
_SDH_WORDS = re.compile(r"\b(sdh|hearing[ ._-]?impaired)\b", re.I)
_FORCED_WORDS = re.compile(r"\bforced\b", re.I)

LIMIT_MESSAGE = "The daily subtitle download limit is reached. Try again tomorrow."
PLUGIN_MISSING_MESSAGE = (
    "Jellyfin has no subtitle plugin. Install OpenSubtitles in Jellyfin (Dashboard > Plugins) and sign in."
)
PLUGIN_UNCONFIGURED_MESSAGE = (
    "Jellyfin's subtitle plugin isn't signed in. Open Dashboard > Plugins > Open Subtitles and enter your account."
)


def _body_text(resp: Any) -> str:
    try:
        return str(resp.text or "")[:500]
    except Exception:
        return ""


def valid_language(value: Any) -> bool:
    """A three-letter lowercase ISO 639-2 code, e.g. eng. Anything else never reaches Jellyfin."""
    return isinstance(value, str) and bool(_SUB_LANGUAGE.match(value))


def valid_subtitle_id(value: Any) -> bool:
    return isinstance(value, str) and bool(_SUB_ID.match(value)) and ".." not in value


def language_name(code: str) -> str:
    return _LANGUAGES.get((code or "").lower(), (code or "").upper() or "Unknown")


def _to_float(value: Any) -> Optional[float]:
    try:
        return round(float(value), 1) if value is not None else None
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def parse_subtitle_results(data: Any) -> list:
    """Jellyfin's RemoteSubtitleInfo list -> what the page shows, best first.

    Hash-matched results (the file's own hash was found at the provider: in sync) come first, then
    the most downloaded. SDH and forced come from the flags when the plugin sets them, else from the name."""
    out = []
    for r in data if isinstance(data, list) else []:
        if not isinstance(r, dict) or not r.get("Id"):
            continue
        name = str(r.get("Name") or "")
        lang = str(r.get("ThreeLetterISOLanguageName") or "").lower()
        out.append({
            "id": str(r["Id"]),
            "name": name,
            "language": lang,
            "language_name": language_name(lang) if lang else "",
            "format": str(r.get("Format") or ""),
            "provider": str(r.get("ProviderName") or ""),
            "rating": _to_float(r.get("CommunityRating")),
            "downloads": _to_int(r.get("DownloadCount")),
            "hash_match": bool(r.get("IsHashMatch")),
            "hearing_impaired": bool(r.get("HearingImpaired")) or bool(_SDH_WORDS.search(name)),
            "forced": bool(r.get("Forced")) or bool(_FORCED_WORDS.search(name)),
        })
    out.sort(key=lambda x: (not x["hash_match"], -(x["downloads"] or 0), -(x["rating"] or 0.0)))
    return out[:MAX_SUBTITLE_RESULTS]


def subtitle_error_for(status: int, body: str, action: str) -> SubtitleError:
    """Map a Jellyfin failure (`action` is "search" or "download") to a friendly error.
    Plugin problems cannot always be told apart by status; the service refines a 5xx or a 404
    with a plugin check (see JellyfinPlayback._refine_subtitle_error)."""
    if status == 429 or (status >= 400 and _LIMIT_WORDS.search(body or "")):
        return SubtitleError("limit", LIMIT_MESSAGE, 429)
    if status in (401, 403):
        return SubtitleError("plugin_unconfigured", PLUGIN_UNCONFIGURED_MESSAGE, 409)
    if status == 404 and action == "download":
        return SubtitleError("not_found", "That subtitle isn't available any more. Search again.", 404)
    if status == 404:
        return SubtitleError("plugin_missing", PLUGIN_MISSING_MESSAGE, 409)
    verb = "search for" if action == "search" else "download"
    return SubtitleError("failed", f"Jellyfin couldn't {verb} subtitles right now ({status}).", 502)


# --------------------------------------------------------------------- service


class JellyfinPlayback:
    # The app and Jellyfin see the same paths (both mount media at /media); tests replace this.
    file_exists = staticmethod(os.path.exists)

    def __init__(self, jellyfin_service: JellyfinService):
        self.jf = jellyfin_service
        self._ids: Dict[str, str] = {}
        self._lock = threading.Lock()
        self._resolved_user: Optional[str] = None
        self._resolved_users: Dict[str, str] = {}
        self._as_user: contextvars.ContextVar = contextvars.ContextVar("nl_jellyfin_user", default=None)
        self._last_refresh: Optional[float] = None  # never scanned

    def _require(self) -> None:
        if not self.jf.configured or not (self.jf.user_id or self._as_user.get()):
            raise JellyfinUnavailable(
                "Jellyfin is not configured (set JELLYFIN_URL, JELLYFIN_API_KEY and JELLYFIN_USER_ID in .env)."
            )

    @contextlib.contextmanager
    def as_user(self, raw_user: Optional[str]):
        """Everything inside the block that asks for "the user" (progress, resume, UserData) uses this
        Jellyfin user (an id or a user name) instead of JELLYFIN_USER_ID. None = the default user.
        Set inside the worker thread that makes the calls, so concurrent requests never mix users."""
        token = self._as_user.set(raw_user or None)
        try:
            yield
        finally:
            self._as_user.reset(token)

    def resolve_user(self, raw: Optional[str], label: str = "JELLYFIN_USER_ID") -> str:
        """A configured Jellyfin user as Jellyfin's 32-hex id. A user *name* is accepted too and looked
        up once via GET /Users, since the id is easy to confuse with the login name."""
        raw = (raw or "").strip()
        if re.fullmatch(r"[0-9a-fA-F]{32}", raw.replace("-", "")):
            return raw
        cached = self._resolved_users.get(raw.lower())
        if cached:
            return cached
        users = self._request("GET", "/Users") or []
        for u in users:
            if str(u.get("Name", "")).lower() == raw.lower() and u.get("Id"):
                self._resolved_users[raw.lower()] = u["Id"]
                return u["Id"]
        raise JellyfinUnavailable(
            f"{label} ({raw!r}) is not a Jellyfin user id or user name. "
            "Find the id in Jellyfin: Dashboard > Users > the user (it's in the page address)."
        )

    def _user_id(self) -> str:
        """The Jellyfin user calls are made for: the as_user() override, else JELLYFIN_USER_ID."""
        override = self._as_user.get()
        if override:
            self._require()
            return self.resolve_user(override, "northern_lights.people jellyfin_user_id")
        if self._resolved_user:
            return self._resolved_user
        self._require()
        raw = (self.jf.user_id or "").strip()
        self._resolved_user = self.resolve_user(raw)
        return self._resolved_user

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        self._require()
        try:
            resp = self.jf.session.request(method, f"{self.jf.base_url}{path}", timeout=15, **kwargs)
        except Exception as e:  # connection refused, timeout, DNS
            raise JellyfinUnavailable(f"Jellyfin could not be reached ({type(e).__name__}).") from e
        if resp.status_code >= 400:
            raise JellyfinUnavailable(f"Jellyfin answered {resp.status_code} for {path.split('?')[0]}.")
        try:
            return resp.json()
        except ValueError as e:
            raise JellyfinUnavailable("Jellyfin sent a reply that isn't JSON.") from e

    def _refresh_ids(self) -> None:
        data = self._request(
            "GET",
            "/Items",
            params={
                "Recursive": "true",
                "IncludeItemTypes": "Movie,Episode",
                "Fields": "Path",
                "UserId": self._user_id(),
            },
        )
        self._ids = {
            it["Path"]: it["Id"] for it in (data.get("Items") or []) if it.get("Path") and it.get("Id")
        }
        self._last_refresh = time.monotonic()

    def known_ids(self, paths: Any, max_age: float = 60.0) -> Dict[str, str]:
        """{path: Jellyfin id} for the given paths. One library scan at most per `max_age`
        seconds, and only when a path is missing from the cache (a file Jellyfin does not
        know must not cause a scan on every page view)."""
        paths = list(paths)
        with self._lock:
            if any(p not in self._ids for p in paths) and (
                self._last_refresh is None or time.monotonic() - self._last_refresh >= max_age
            ):
                self._refresh_ids()
            return {p: self._ids[p] for p in paths if p in self._ids}

    def find_item_id(self, file_path: str) -> Optional[str]:
        """Jellyfin id for one of our file paths, or None. Refreshes the cache once on a miss."""
        with self._lock:
            if file_path in self._ids:
                return self._ids[file_path]
            self._refresh_ids()
            return self._ids.get(file_path)

    def playback_info(
        self,
        jf_id: str,
        profile: Optional[Dict[str, Any]] = None,
        audio_index: Optional[int] = None,
        subtitle_index: Optional[int] = None,
        start_seconds: float = 0.0,
        direct_play: bool = True,
        direct_stream: bool = True,
        max_bitrate: Optional[int] = None,
    ) -> Dict[str, Any]:
        """`max_bitrate` (bit/s, M10c) is the highest stream rate to allow, from the viewer's quality choice. It only ever
        lowers the profile's own ceiling; a file above it cannot direct play, so Jellyfin converts it."""
        profile = copy.deepcopy(profile or BROWSER_PROFILE)
        ceiling = int(profile.get("MaxStreamingBitrate") or MAX_STREAMING_BITRATE)
        if max_bitrate and max_bitrate < ceiling:
            ceiling = int(max_bitrate)
            profile["MaxStreamingBitrate"] = ceiling
            profile["MaxStaticBitrate"] = min(int(profile.get("MaxStaticBitrate") or ceiling), ceiling)
        body: Dict[str, Any] = {
            "DeviceProfile": profile,
            "MaxStreamingBitrate": ceiling if max_bitrate else MAX_STREAMING_BITRATE,
            "EnableDirectPlay": direct_play,
            "EnableDirectStream": direct_stream,
            "EnableTranscoding": True,
            "AutoOpenLiveStream": False,
        }
        params: Dict[str, Any] = {"UserId": self._user_id()}
        if audio_index is not None:
            body["AudioStreamIndex"] = params["AudioStreamIndex"] = audio_index
        if subtitle_index is not None:
            body["SubtitleStreamIndex"] = params["SubtitleStreamIndex"] = subtitle_index
            if subtitle_index >= 0:
                # Picture subtitles cannot go to the browser as text: ask Jellyfin to burn them in.
                profile["SubtitleProfiles"] = list(profile.get("SubtitleProfiles") or []) + [
                    {"Format": codec, "Method": "Encode"} for codec in sorted(_IMAGE_SUB_CODECS)
                ]
        if start_seconds and start_seconds > 0:
            body["StartTimeTicks"] = params["StartTimeTicks"] = seconds_to_ticks(start_seconds)
        return self._request("POST", f"/Items/{jf_id}/PlaybackInfo", params=params, json=body)

    def _send(self, method: str, path: str, **kwargs: Any) -> None:
        """A Jellyfin call whose reply we do not need (progress reports return 204)."""
        self._require()
        try:
            resp = self.jf.session.request(method, f"{self.jf.base_url}{path}", timeout=15, **kwargs)
            resp.raise_for_status()
        except Exception as e:
            raise JellyfinUnavailable(f"Jellyfin could not be reached ({type(e).__name__}).") from e

    def _send_status(self, method: str, path: str, **kwargs: Any) -> int:
        """Like _send but answers the HTTP status (a connection failure still raises JellyfinUnavailable)."""
        self._require()
        try:
            return int(self.jf.session.request(method, f"{self.jf.base_url}{path}", timeout=15, **kwargs).status_code)
        except Exception as e:
            raise JellyfinUnavailable(f"Jellyfin could not be reached ({type(e).__name__}).") from e

    def item_details(self, jf_id: str) -> Dict[str, Any]:
        """The Jellyfin item for our user (UserData, episode numbers). {} if it cannot be read."""
        try:
            return self._request("GET", f"/Users/{self._user_id()}/Items/{jf_id}") or {}
        except JellyfinUnavailable:
            return {}

    def media_segments(self, jf_id: str) -> Any:
        """`GET /MediaSegments/{id}` for Intro and Outro (Jellyfin 10.10+; the segments come from a provider such as
        the Intro Skipper plugin). {} when Jellyfin is old, has no provider, or can't be read: never raises."""
        if not valid_jellyfin_id(jf_id or ""):
            return {}
        try:
            self._require()
            resp = self.jf.session.request(
                "GET", f"{self.jf.base_url}/MediaSegments/{jf_id}",
                params={"includeSegmentTypes": ["Intro", "Outro"]}, timeout=10,
            )
            if resp.status_code != 200:
                return {}
            return resp.json()
        except Exception:  # noqa: BLE001 - segments are optional
            return {}

    def report_progress(
        self,
        event: str,
        jf_id: str,
        play_session_id: str,
        media_source_id: str,
        position_seconds: float,
        paused: bool = False,
        play_method: str = "DirectPlay",
        user: Optional[str] = None,
    ) -> None:
        """Tell Jellyfin where the viewer is: the session report as before M4 (Jellyfin's own housekeeping,
        e.g. ending a transcode). With `user` (a configured person's Jellyfin user, M4) the position is
        also written to that user's UserData, so "Both of us" can report one playback to two users.
        The user-scoped PlayingItems endpoints are not used: Jellyfin 10.11 ignores their userId and
        attributes them to the caller's session (the API key), not to the user."""
        paths = {
            "start": "/Sessions/Playing",
            "progress": "/Sessions/Playing/Progress",
            "stop": "/Sessions/Playing/Stopped",
        }
        if event not in paths:
            raise ValueError("event must be start, progress or stop")
        ticks = seconds_to_ticks(position_seconds)
        source = media_source_id or jf_id
        body = {
            "ItemId": jf_id,
            "MediaSourceId": source,
            "PlaySessionId": play_session_id,
            "PositionTicks": ticks,
            "IsPaused": bool(paused),
            "PlayMethod": play_method or "DirectPlay",
            "CanSeek": True,
        }
        self._send("POST", paths[event], json=body)
        if user and event != "start":
            uid = self.resolve_user(user, "northern_lights.people jellyfin_user_id")
            status = self._send_status(
                "POST",
                f"/UserItems/{jf_id}/UserData",
                params={"userId": uid},
                json={
                    "PlaybackPositionTicks": ticks,
                    "LastPlayedDate": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.0000000Z"),
                },
            )
            if status >= 400:
                raise JellyfinUnavailable(f"Jellyfin answered {status} when saving the position.")

    def set_played(self, jf_id: str, played: bool) -> None:
        if not valid_jellyfin_id(jf_id):
            raise ValueError("bad item id")
        self._send("POST" if played else "DELETE", f"/Users/{self._user_id()}/PlayedItems/{jf_id}")

    def reset_user_data(self, jf_id: str) -> None:
        """"Mark unwatched for me" (M9a): clears this one user's played flag and resume position for one
        item. Runs as the current `as_user` person; nobody else's data is touched. The same per-user
        calls the M4 code uses: DELETE /Users/{id}/PlayedItems/{item}, then the position is zeroed with
        POST /UserItems/{item}/UserData?userId= (Jellyfin 10.11)."""
        if not valid_jellyfin_id(jf_id):
            raise ValueError("bad item id")
        uid = self._user_id()
        self._send("DELETE", f"/Users/{uid}/PlayedItems/{jf_id}")
        status = self._send_status(
            "POST", f"/UserItems/{jf_id}/UserData", params={"userId": uid},
            json={"Played": False, "PlaybackPositionTicks": 0},
        )
        if status >= 400:
            raise JellyfinUnavailable(f"Jellyfin answered {status} when clearing the position.")

    # ------------------------------------------------------------ subtitles (M5)

    def _subtitle_item_id(self, item: Dict[str, Any]) -> str:
        sources = item.get("sources") or []
        if not sources:
            raise ItemNotFound("This item has no media file.")
        file_path = sources[0]["file_path"]
        if not self.file_exists(file_path):
            raise ItemNotFound("This movie's file isn't on disk any more (moved or deleted). Rescan the library.")
        jf_id = self.find_item_id(file_path)
        if not jf_id:
            raise ItemNotFound("Jellyfin does not know this file yet.")
        if not valid_jellyfin_id(jf_id):
            raise ItemNotFound("Jellyfin sent an item id we can't use.")
        return jf_id

    def _subtitle_call(self, method: str, path: str) -> Any:
        """One Jellyfin subtitle call; the caller reads the status. A dead connection is JellyfinUnavailable."""
        self._require()
        try:
            return self.jf.session.request(method, f"{self.jf.base_url}{path}", timeout=45)
        except Exception as e:
            raise JellyfinUnavailable(f"Jellyfin could not be reached ({type(e).__name__}).") from e

    def subtitle_plugin_active(self) -> Optional[bool]:
        """Is a subtitle provider plugin installed and active? None when Jellyfin won't say (GET /Plugins is admin only)."""
        try:
            resp = self._subtitle_call("GET", "/Plugins")
            if resp.status_code != 200:
                return None
            plugins = resp.json()
        except Exception:
            return None
        if not isinstance(plugins, list):
            return None
        for p in plugins:
            if isinstance(p, dict) and "subtitle" in str(p.get("Name") or "").lower():
                if str(p.get("Status") or "Active").lower() in ("active", "restart"):
                    return True
        return False

    def _refine_subtitle_error(self, err: SubtitleError) -> SubtitleError:
        """A failed call may just mean there is no subtitle plugin: say so when we can tell."""
        if err.code in ("failed", "plugin_missing") and self.subtitle_plugin_active() is False:
            return SubtitleError("plugin_missing", PLUGIN_MISSING_MESSAGE, 409)
        return err

    def search_subtitles(self, item: Dict[str, Any], language: str) -> Dict[str, Any]:
        """Remote subtitles for one title: {language, language_name, results}. Raises SubtitleError."""
        if not valid_language(language):
            raise SubtitleError("bad_request", "That language isn't valid.", 400)
        jf_id = self._subtitle_item_id(item)
        resp = self._subtitle_call("GET", f"/Items/{jf_id}/RemoteSearch/Subtitles/{language}")
        if resp.status_code >= 400:
            raise self._refine_subtitle_error(subtitle_error_for(resp.status_code, _body_text(resp), "search"))
        try:
            data = resp.json()
        except ValueError:
            data = []
        results = parse_subtitle_results(data)
        if not results and self.subtitle_plugin_active() is False:
            raise SubtitleError("plugin_missing", PLUGIN_MISSING_MESSAGE, 409)
        return {"language": language, "language_name": language_name(language), "results": results}

    def download_subtitle(self, item: Dict[str, Any], subtitle_id: str) -> None:
        """Ask Jellyfin to fetch one remote subtitle and save it next to the file. Raises SubtitleError."""
        if not valid_subtitle_id(subtitle_id):
            raise SubtitleError("bad_request", "That subtitle id isn't valid.", 400)
        jf_id = self._subtitle_item_id(item)
        resp = self._subtitle_call("POST", f"/Items/{jf_id}/RemoteSearch/Subtitles/{quote(subtitle_id, safe='')}")
        if resp.status_code >= 400:
            raise self._refine_subtitle_error(subtitle_error_for(resp.status_code, _body_text(resp), "download"))

    def _cannot_play_message(self, jf_id: str, ms: Optional[Dict[str, Any]], code: str, device: str) -> str:
        """The 415 text when Jellyfin has no way to play a file here (P20): names the codecs when they can be read,
        e.g. "This browser can't play this format (HEVC video, DTS sound) and Jellyfin has no way to convert it (NoCompatibleStream)."."""
        formats = nl_picture.formats_of(ms, None) if ms else {}
        if not formats.get("video") and not formats.get("audio"):
            try:
                details = self.item_details(jf_id)
                sources = details.get("MediaSources") or []
                streams = (details.get("MediaStreams") or []) or (sources[0].get("MediaStreams") if sources else []) or []
                formats = nl_picture.formats_of({"MediaStreams": streams}, None)
            except Exception:  # noqa: BLE001 - the codec names are a nicety
                formats = {}
        named = [f"{formats[k]} {w}" for k, w in (("video", "video"), ("audio", "sound")) if formats.get(k)]
        where = f" ({', '.join(named)})" if named else ""
        return f"{device} can't play this format{where} and Jellyfin has no way to convert it ({code})."

    def stream_for(
        self,
        item: Dict[str, Any],
        audio_index: Optional[int] = None,
        subtitle_index: Optional[int] = None,
        start_seconds: float = 0.0,
        profile: Optional[Dict[str, Any]] = None,
        prefer: Optional[Dict[str, Any]] = None,
        max_kbps: Optional[int] = None,
        device: str = "This browser",
    ) -> Dict[str, Any]:
        """`max_kbps` (M10c) is the viewer's quality choice (None = Auto): the highest stream rate to ask for.
        `device` ("This TV" / "This browser") is how the conversion note names the screen.
        `prefer` ({audio_lang, subtitle_lang, subtitles_on}, from the viewer's prefs, M4) picks the
        default tracks when the caller did not ask for any: audio in the preferred language, and a
        subtitle track when subtitles are on by default (picture subtitles are burned in from the start;
        a text track is reported as `default_subtitle_index` for the page to switch on)."""
        sources = item.get("sources") or []
        if not sources:
            raise ItemNotFound("This item has no media file.")
        file_path = sources[0]["file_path"]
        if not self.file_exists(file_path):
            # Jellyfin would answer every segment with a 500 (ffmpeg can't open the input).
            raise ItemNotFound(
                "This movie's file isn't on disk any more (moved or deleted). Rescan the library."
            )
        jf_id = self.find_item_id(file_path)
        if not jf_id:
            raise ItemNotFound("Jellyfin does not know this file yet.")

        cap = nl_settings.effective_cap_bps(int((profile or BROWSER_PROFILE).get("MaxStreamingBitrate") or 0) or None, max_kbps) if max_kbps else None

        def pinfo(audio, sub, start, **kw):
            return self.playback_info(jf_id, profile, audio, sub, start, max_bitrate=cap, **kw)

        def not_playable(info_, ms_=None):
            code = (info_ or {}).get("ErrorCode") or "no media source"
            return NotPlayable(self._cannot_play_message(jf_id, ms_, code, device))

        info = pinfo(audio_index, subtitle_index, start_seconds)
        media_sources = info.get("MediaSources") or []
        if info.get("ErrorCode") or not media_sources:
            raise not_playable(info)
        ms = media_sources[0]

        default_subtitle_index = -1
        if prefer and (audio_index is None or subtitle_index is None):
            first = extract_tracks(ms, jf_id, ms.get("Id") or jf_id)
            want_audio, want_sub = audio_index, subtitle_index
            if audio_index is None:
                pick = pick_audio_index(first["audio_tracks"], prefer.get("audio_lang"))
                if pick is not None and pick != ms.get("DefaultAudioStreamIndex"):
                    want_audio = pick
            if subtitle_index is None and prefer.get("subtitles_on"):
                track = pick_subtitle_track(first["subtitle_tracks"], prefer.get("subtitle_lang"))
                if track is not None:
                    if track.get("kind") == "image":
                        want_sub = track["index"]
                    else:
                        default_subtitle_index = track["index"]
            if want_audio != audio_index or want_sub != subtitle_index:
                audio_index, subtitle_index = want_audio, want_sub
                info = pinfo(audio_index, subtitle_index, start_seconds)
                media_sources = info.get("MediaSources") or []
                if info.get("ErrorCode") or not media_sources:
                    raise not_playable(info)
                ms = media_sources[0]

        # A non-default audio track or burned-in subtitles cannot come from the file itself:
        # make Jellyfin transcode (it still copies the video when the browser can take it).
        default_audio = ms.get("DefaultAudioStreamIndex")
        wants_other_audio = (
            audio_index is not None and default_audio is not None and audio_index != default_audio
        )
        wants_burn_in = subtitle_index is not None and subtitle_index >= 0
        if (wants_other_audio or wants_burn_in) and not ms.get("TranscodingUrl"):
            info = pinfo(audio_index, subtitle_index, start_seconds, direct_play=False)
            media_sources = info.get("MediaSources") or []
            if info.get("ErrorCode") or not media_sources:
                raise not_playable(info)
            ms = media_sources[0]
            if not ms.get("TranscodingUrl"):
                info = pinfo(audio_index, subtitle_index, start_seconds, direct_play=False, direct_stream=False)
                media_sources = info.get("MediaSources") or []
                if info.get("ErrorCode") or not media_sources:
                    raise not_playable(info)
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
            raise NotPlayable(self._cannot_play_message(jf_id, ms, "no stream offered", device))

        tracks = extract_tracks(ms, jf_id, source_id)
        if audio_index is not None:
            selected_audio = audio_index
        elif default_audio is not None:
            selected_audio = default_audio
        else:
            defaults = [t["index"] for t in tracks["audio_tracks"] if t["is_default"]]
            selected_audio = defaults[0] if defaults else (
                tracks["audio_tracks"][0]["index"] if tracks["audio_tracks"] else None
            )

        details = self.item_details(jf_id)
        user_data = details.get("UserData") or {}
        resume_ticks = user_data.get("PlaybackPositionTicks") or 0
        title = item.get("title")
        episode = None
        if details.get("Type") == "Episode":
            title = details.get("SeriesName") or title
            episode = {
                "season": details.get("ParentIndexNumber"),
                "episode": details.get("IndexNumber"),
                "name": details.get("Name"),
            }

        ticks = ms.get("RunTimeTicks")
        video_codec = next(
            (st.get("Codec") for st in (ms.get("MediaStreams") or []) if st.get("Type") == "Video"), None
        )
        formats = nl_picture.formats_of(ms, selected_audio)
        conversion = nl_picture.describe_conversion(
            play_method, nl_picture.transcode_reasons(transcoding_url), formats, device, cap_kbps=max_kbps
        )
        bitrate = ms.get("Bitrate")
        return {
            "audio_codec": formats["audio_codec"],
            "conversion": conversion,
            "quality": {
                "applied_kbps": int(max_kbps) if max_kbps else None,
                "source_kbps": int(bitrate / 1000) if isinstance(bitrate, (int, float)) and bitrate > 0 else None,
            },
            "url": url,
            "kind": kind,
            "play_method": play_method,
            "play_session_id": session_id,
            "media_source_id": source_id,
            "jellyfin_id": jf_id,
            "container": ms.get("Container"),
            "video_codec": video_codec,
            "duration_seconds": (ticks / _TICKS) if ticks else None,
            "title": title,
            "episode": episode,
            "audio_index": selected_audio,
            "subtitle_index": subtitle_index if subtitle_index is not None else -1,
            "default_subtitle_index": default_subtitle_index,
            "resume_seconds": resume_ticks / _TICKS,
            "played": bool(user_data.get("Played")),
            **tracks,
        }
