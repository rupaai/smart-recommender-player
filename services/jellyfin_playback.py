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

import copy
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


def _subtitle_url(stream: Dict[str, Any], jf_id: str, source_id: str) -> str:
    """Proxied WebVTT URL for a text subtitle (API key stripped, only under Videos/)."""
    delivery = stream.get("DeliveryUrl")
    if delivery and stream.get("DeliveryMethod") == "External":
        rel = strip_api_key(delivery).lstrip("/")
        if safe_proxy_path(rel.split("?", 1)[0]) is not None:
            return PROXY_PREFIX + rel
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
            audio.append(track)
        else:
            image = _is_image_subtitle(st)
            track["label"] = subtitle_label(st)
            track["kind"] = "image" if image else "text"
            track["url"] = None if image else _subtitle_url(st, jf_id, source_id)
            subs.append(track)
    return {"audio_tracks": audio, "subtitle_tracks": subs}


# --------------------------------------------------------------------- service


class JellyfinPlayback:
    def __init__(self, jellyfin_service: JellyfinService):
        self.jf = jellyfin_service
        self._ids: Dict[str, str] = {}
        self._lock = threading.Lock()
        self._resolved_user: Optional[str] = None

    def _require(self) -> None:
        if not self.jf.configured or not self.jf.user_id:
            raise JellyfinUnavailable(
                "Jellyfin is not configured (set JELLYFIN_URL, JELLYFIN_API_KEY and JELLYFIN_USER_ID in .env)."
            )

    def _user_id(self) -> str:
        """JELLYFIN_USER_ID as Jellyfin's 32-hex user id. A user *name* is accepted too and
        looked up once via GET /Users, since the id is easy to confuse with the login name."""
        if self._resolved_user:
            return self._resolved_user
        self._require()
        raw = (self.jf.user_id or "").strip()
        if re.fullmatch(r"[0-9a-fA-F]{32}", raw.replace("-", "")):
            self._resolved_user = raw
            return raw
        users = self._request("GET", "/Users") or []
        for u in users:
            if str(u.get("Name", "")).lower() == raw.lower() and u.get("Id"):
                self._resolved_user = u["Id"]
                return u["Id"]
        raise JellyfinUnavailable(
            f"JELLYFIN_USER_ID ({raw!r}) is not a Jellyfin user id or user name. "
            "Find the id in Jellyfin: Dashboard > Users > your user (it's in the page address)."
        )

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
    ) -> Dict[str, Any]:
        profile = copy.deepcopy(profile or BROWSER_PROFILE)
        body: Dict[str, Any] = {
            "DeviceProfile": profile,
            "MaxStreamingBitrate": MAX_STREAMING_BITRATE,
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

    def item_details(self, jf_id: str) -> Dict[str, Any]:
        """The Jellyfin item for our user (UserData, episode numbers). {} if it cannot be read."""
        try:
            return self._request("GET", f"/Users/{self._user_id()}/Items/{jf_id}") or {}
        except JellyfinUnavailable:
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
    ) -> None:
        paths = {
            "start": "/Sessions/Playing",
            "progress": "/Sessions/Playing/Progress",
            "stop": "/Sessions/Playing/Stopped",
        }
        if event not in paths:
            raise ValueError("event must be start, progress or stop")
        body = {
            "ItemId": jf_id,
            "MediaSourceId": media_source_id or jf_id,
            "PlaySessionId": play_session_id,
            "PositionTicks": seconds_to_ticks(position_seconds),
            "IsPaused": bool(paused),
            "PlayMethod": play_method or "DirectPlay",
            "CanSeek": True,
        }
        self._send("POST", paths[event], json=body)

    def set_played(self, jf_id: str, played: bool) -> None:
        if not valid_jellyfin_id(jf_id):
            raise ValueError("bad item id")
        self._send("POST" if played else "DELETE", f"/Users/{self._user_id()}/PlayedItems/{jf_id}")

    def stream_for(
        self,
        item: Dict[str, Any],
        audio_index: Optional[int] = None,
        subtitle_index: Optional[int] = None,
        start_seconds: float = 0.0,
        profile: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        sources = item.get("sources") or []
        if not sources:
            raise ItemNotFound("This item has no media file.")
        jf_id = self.find_item_id(sources[0]["file_path"])
        if not jf_id:
            raise ItemNotFound("Jellyfin does not know this file yet.")

        info = self.playback_info(jf_id, profile, audio_index, subtitle_index, start_seconds)
        media_sources = info.get("MediaSources") or []
        if info.get("ErrorCode") or not media_sources:
            raise NotPlayable(f"Jellyfin cannot play this file ({info.get('ErrorCode') or 'no media source'}).")
        ms = media_sources[0]

        # A non-default audio track or burned-in subtitles cannot come from the file itself:
        # make Jellyfin transcode (it still copies the video when the browser can take it).
        default_audio = ms.get("DefaultAudioStreamIndex")
        wants_other_audio = (
            audio_index is not None and default_audio is not None and audio_index != default_audio
        )
        wants_burn_in = subtitle_index is not None and subtitle_index >= 0
        if (wants_other_audio or wants_burn_in) and not ms.get("TranscodingUrl"):
            info = self.playback_info(jf_id, profile, audio_index, subtitle_index, start_seconds, direct_play=False)
            media_sources = info.get("MediaSources") or []
            if info.get("ErrorCode") or not media_sources:
                raise NotPlayable(f"Jellyfin cannot play this file ({info.get('ErrorCode') or 'no media source'}).")
            ms = media_sources[0]
            if not ms.get("TranscodingUrl"):
                info = self.playback_info(
                    jf_id, profile, audio_index, subtitle_index, start_seconds, direct_play=False, direct_stream=False
                )
                media_sources = info.get("MediaSources") or []
                if info.get("ErrorCode") or not media_sources:
                    raise NotPlayable("Jellyfin cannot switch tracks for this file.")
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
        return {
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
            "resume_seconds": resume_ticks / _TICKS,
            "played": bool(user_data.get("Played")),
            **tracks,
        }
