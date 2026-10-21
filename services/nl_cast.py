"""
Northern Lights casting (services/nl_cast.py), M8a

Three small pieces, all pure so they are tested without a server or Jellyfin:

1. CastConfig: where the Chromecast reaches this app (`northern_lights.cast_base_url`, a LAN
   address; the Chromecast is not on Tailscale) and which receiver app to launch
   (`northern_lights.cast_receiver_app_id`; empty = Google's Default Media Receiver).
2. CastTokens: short-lived signed stream tokens. A token is bound to ONE Jellyfin item and an
   expiry and signed with HMAC-SHA256 using a server secret (env NL_CAST_SECRET if set; otherwise a secret
   the app makes once and keeps in a file in its data folder, so cast links survive a restart. Only if that
   folder cannot be written is a random in-memory secret used). The secret is never
   derived from the Jellyfin key, and the Jellyfin key is never in a token.
3. cast_payload(): turns a normal play answer into the one the sender hands the Chromecast:
   absolute LAN URLs carrying the token, plus the content type and the artwork.

Token format: `v1.<expiry unix seconds>.<jellyfin item id>.<base64url signature>`; the signature covers
`v1|<expiry>|<normalised id>`. What a token opens (path_allowed) is derived from the id in code:
`videos/<id>/...` and `Videos/<id>/...` (the stream, HLS playlists and segments, subtitles, trickplay)
and `Items/<id>/Images/...` (artwork). The proxy applies this ON TOP of its normal allowlist.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, unquote, urlsplit

DEFAULT_RECEIVER_APP_ID = "CC1AD845"      # Google's Default Media Receiver
TOKEN_VERSION = "v1"
TOKEN_TTL_SECONDS = 6 * 60 * 60
MIN_SECRET_LENGTH = 16
SECRET_FILE = "nl_cast_secret"
TOKEN_PARAM = "t"

_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_APP_ID = re.compile(r"^[A-Za-z0-9]{4,32}$")


# ------------------------------------------------------------------ configuration


class CastConfigError(ValueError):
    pass


@dataclass(frozen=True)
class CastConfig:
    base_url: str = ""          # e.g. http://192.168.1.50:8000 (no trailing slash); "" = casting is off
    app_id: str = DEFAULT_RECEIVER_APP_ID

    @property
    def enabled(self) -> bool:
        return bool(self.base_url)

    @property
    def custom_receiver(self) -> bool:
        return self.app_id != DEFAULT_RECEIVER_APP_ID

    @classmethod
    def from_config(cls, config: Optional[Dict[str, Any]]) -> "CastConfig":
        """Reads `northern_lights.cast_base_url` and `northern_lights.cast_receiver_app_id`. A bad value is
        an error at start-up (a typo should not silently turn casting off)."""
        section = (config or {}).get("northern_lights") or {}
        base = section.get("cast_base_url")
        base = "" if base is None else str(base).strip()
        if base:
            parts = urlsplit(base)
            if parts.scheme not in ("http", "https") or not parts.hostname or parts.query or parts.fragment \
                    or parts.path.strip("/") or parts.username or parts.password:
                raise CastConfigError(
                    "northern_lights.cast_base_url must look like http://192.168.1.50:8000 "
                    "(scheme, host and optional port; no path)."
                )
            base = f"{parts.scheme}://{parts.netloc}"
        app_id = section.get("cast_receiver_app_id")
        app_id = "" if app_id is None else str(app_id).strip()
        if not app_id:
            app_id = DEFAULT_RECEIVER_APP_ID
        elif not _APP_ID.match(app_id):
            raise CastConfigError("northern_lights.cast_receiver_app_id must be the 8-character id from the Cast console.")
        return cls(base_url=base, app_id=app_id)

    def public(self) -> Dict[str, Any]:
        """What the page may know. Never the base URL's secrets (it has none) or the token secret."""
        return {"enabled": self.enabled, "app_id": self.app_id, "custom_receiver": self.custom_receiver}


# ------------------------------------------------------------------ tokens


def normalise_id(jellyfin_id: str) -> str:
    """Jellyfin ids are GUIDs that it accepts with or without dashes and in either case."""
    return (jellyfin_id or "").replace("-", "").lower()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_path(path: str) -> Optional[str]:
    """Percent-decodes (also double-encoded) a proxy path; None when it never settles."""
    decoded = path
    for _ in range(4):
        nxt = unquote(decoded)
        if nxt == decoded:
            return decoded
        decoded = nxt
    return None


def path_allowed(jellyfin_id: str, path: str) -> bool:
    """Is `path` (a /nl/jf/ path without the prefix) one of the paths a token for this item opens?"""
    decoded = decode_path(path)
    if decoded is None or not _ID.match(jellyfin_id or ""):
        return False
    parts = decoded.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return False
    want = normalise_id(jellyfin_id)
    if len(parts) >= 3 and parts[0] in ("videos", "Videos") and normalise_id(parts[1]) == want and parts[2]:
        return True
    if len(parts) >= 4 and parts[0] == "Items" and normalise_id(parts[1]) == want and parts[2] == "Images":
        return True
    return False


def _file_secret(data_dir: str) -> Optional[bytes]:
    """The secret in `<data_dir>/nl_cast_secret`, made once (random, mode 0600, written atomically) if missing.
    None when the folder cannot be used."""
    path = os.path.join(data_dir, SECRET_FILE)
    try:
        if not os.path.exists(path):
            tmp = f"{path}.{os.getpid()}.tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(secrets.token_urlsafe(32))
            os.replace(tmp, path)
        with open(path, encoding="utf-8") as f:
            value = f.read().strip()
        return value.encode("utf-8") if len(value) >= MIN_SECRET_LENGTH else None
    except OSError:
        return None


class CastTokens:
    """Mints and checks stream tokens. `secret` comes from the environment; without one the secret is read
    from (or first written to) a file in `data_dir`; without a usable folder it lives in memory only."""

    def __init__(self, secret: Optional[str] = None, clock=time.time, data_dir: Optional[str] = None):
        self.clock = clock
        self.from_env = False
        if secret is not None and secret.strip():
            if len(secret.strip()) < MIN_SECRET_LENGTH:
                print(
                    f"[NL] NL_CAST_SECRET is shorter than {MIN_SECRET_LENGTH} characters; ignoring it and using a "
                    "random secret (cast links stop working when the app restarts).",
                    flush=True,
                )
            else:
                self._secret = secret.strip().encode("utf-8")
                self.from_env = True
        if not self.from_env:
            self._secret = _file_secret(data_dir) if data_dir else None
            if self._secret is None:
                if data_dir:
                    print(f"[NL] Cannot use {os.path.join(data_dir, SECRET_FILE)}; using a secret kept in memory "
                          "(cast links stop working when the app restarts).", flush=True)
                self._secret = secrets.token_bytes(32)

    def _sign(self, expiry: int, norm_id: str) -> str:
        message = f"{TOKEN_VERSION}|{expiry}|{norm_id}".encode("ascii")
        return _b64(hmac.new(self._secret, message, hashlib.sha256).digest())

    def mint(self, jellyfin_id: str, ttl: int = TOKEN_TTL_SECONDS) -> str:
        if not _ID.match(jellyfin_id or ""):
            raise ValueError("bad item id")
        expiry = int(self.clock()) + int(ttl)
        norm = normalise_id(jellyfin_id)
        return f"{TOKEN_VERSION}.{expiry}.{norm}.{self._sign(expiry, norm)}"

    def check(self, token: str, method: str, path: str) -> Tuple[bool, str]:
        """(ok, reason). Order: shape, signature (constant time), expiry, then the request itself
        (GET only, and a path this token's item opens). Every refusal is a 403 in the proxy."""
        parts = (token or "").split(".")
        if len(parts) != 4 or parts[0] != TOKEN_VERSION or not parts[1].isdigit() or len(parts[1]) > 12 \
                or not _ID.match(parts[2]) or len(parts[3]) > 64:
            return False, "malformed"
        expiry, norm = int(parts[1]), parts[2]
        if not hmac.compare_digest(self._sign(expiry, norm).encode("ascii"), parts[3].encode("ascii", "replace")):
            return False, "bad signature"
        if self.clock() >= expiry:
            return False, "expired"
        if method != "GET":
            return False, "method"
        if not path_allowed(norm, path):
            return False, "wrong item or path"
        return True, "ok"


# ------------------------------------------------------------------ playlists and query strings


def append_param(url: str, name: str, value: str) -> str:
    """Adds name=value to a URL's query (before any #fragment)."""
    base, hash_sep, fragment = url.partition("#")
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}{name}={quote(value, safe='')}" + (hash_sep + fragment if hash_sep else "")


def strip_param(query: str, name: str) -> str:
    """Removes every `name=` parameter (any case) from a raw query string (no leading '?')."""
    kept = [p for p in query.split("&") if p and p.split("=", 1)[0].lower() != name.lower()]
    return "&".join(kept)


def token_values(query: str) -> List[str]:
    """All values of the `t` parameter (any case of the name) in a raw query string."""
    out = []
    for p in query.split("&"):
        key, _, value = p.partition("=")
        if key.lower() == TOKEN_PARAM:
            out.append(unquote(value))
    return out


# ------------------------------------------------------------------ what the sender hands the Chromecast


def content_type_for(stream: Dict[str, Any]) -> str:
    if stream.get("kind") == "hls":
        return "application/x-mpegURL"
    container = (stream.get("container") or "mp4").split(",")[0].strip().lower()
    return {"mkv": "video/x-matroska", "webm": "video/webm"}.get(container, "video/mp4")


def cast_payload(info: Dict[str, Any], cfg: CastConfig, tokens: CastTokens) -> Dict[str, Any]:
    """`info` is a normal /api/nl/play answer made with the cast profile (relative /nl/jf/ URLs). Returns it with
    every media, subtitle and artwork URL made absolute on the LAN and carrying a token for this item, plus a
    `cast` block: {content_type, backdrop, poster, receiver: {...}}. The Jellyfin key is not in `info` (the
    playback layer strips it) and is not added here."""
    jf_id = info.get("jellyfin_id")
    if not jf_id:
        raise ValueError("no Jellyfin item")
    token = tokens.mint(jf_id)

    def lan(relative: str) -> str:
        return append_param(cfg.base_url + relative, TOKEN_PARAM, token)

    out = dict(info)
    out["url"] = lan(info["url"])
    subs = []
    for track in info.get("subtitle_tracks") or []:
        t = dict(track)
        if t.get("url"):
            t["url"] = lan(t["url"])
        subs.append(t)
    out["subtitle_tracks"] = subs
    out["cast"] = {
        "content_type": content_type_for(info),
        "backdrop": lan(f"/nl/jf/Items/{jf_id}/Images/Backdrop"),
        "poster": lan(f"/nl/jf/Items/{jf_id}/Images/Primary"),
        "expires_in": TOKEN_TTL_SECONDS,
    }
    return out


def secret_from_env() -> Optional[str]:
    return os.getenv("NL_CAST_SECRET")
