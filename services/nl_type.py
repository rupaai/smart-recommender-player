"""
Northern Lights phone keyboard for TV search (services/nl_type.py), M8b

The TV search page asks for a session and shows its short code (and a QR of /nl/type/<code>). A phone that
opens that address types into a plain page and every change is posted here; the TV polls for the latest text.
Everything is in memory (a restart just means asking the TV for a new code).

- Codes: 6 characters from an alphabet without look-alikes (32^6, about a billion), made with `secrets`,
  valid for 10 minutes. One session per TV: the TV holds a random `key` (never shown, never in the QR) that
  only it can poll with, and asking for a new session with the old key replaces the old one. At most 16
  sessions live at once (the oldest goes first).
- Phones need only the code. Text is cleaned (control characters out) and limited to 100 characters.
- Rate limits (sliding windows, per code and per client address) keep guessing and floods cheap to refuse.

Pure: the clock and the random source are injectable, so it is tested without a server.
"""

from __future__ import annotations

import hmac
import re
import secrets
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, Optional, Tuple

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no I, O, 0, 1
CODE_LENGTH = 6
SESSION_TTL_SECONDS = 600
MAX_TEXT_LENGTH = 100
MAX_SESSIONS = 16

_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")
_CODE_CHARS = set(CODE_ALPHABET)


class TypingError(Exception):
    """Base for the refusals; `status` is the HTTP status the API answers with."""

    status = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class UnknownCode(TypingError):
    status = 404


class WrongKey(TypingError):
    status = 403


class BadText(TypingError):
    status = 422


class RateLimited(TypingError):
    status = 429

    def __init__(self, message: str, retry_after: int):
        super().__init__(message)
        self.retry_after = retry_after


def normalise_code(raw: Optional[str]) -> Optional[str]:
    """What someone typed -> the canonical code, or None. Case, spaces and dashes do not matter; O and 0, I and 1
    are not in the alphabet, so a code with them is simply not valid."""
    if not isinstance(raw, str):
        return None
    code = re.sub(r"[\s-]", "", raw).upper()
    if len(code) != CODE_LENGTH or any(c not in _CODE_CHARS for c in code):
        return None
    return code


def clean_text(raw: Any) -> str:
    """The text a phone sent, made safe for the search box. Raises BadText when it is not text or too long."""
    if not isinstance(raw, str):
        raise BadText("The text must be a string.")
    text = _CONTROL.sub("", raw)
    if len(text) > MAX_TEXT_LENGTH:
        raise BadText(f"That is too long (at most {MAX_TEXT_LENGTH} characters).")
    return text


class RateLimiter:
    """Sliding-window limits: `hit(key, limit, window)` raises RateLimited when `key` already used `limit` hits in
    the last `window` seconds. Keys are forgotten when their window is empty."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self._hits: Dict[str, Deque[float]] = {}

    def hit(self, key: str, limit: int, window: float, message: str = "Too many tries. Wait a moment.") -> None:
        now = self.clock()
        q = self._hits.setdefault(key, deque())
        while q and now - q[0] >= window:
            q.popleft()
        if len(q) >= limit:
            raise RateLimited(message, max(1, int(window - (now - q[0])) + 1))
        q.append(now)

    def check_full(self, key: str, limit: int, window: float, message: str) -> None:
        """Raises RateLimited when `key` is already at its limit, without counting a new hit."""
        now = self.clock()
        q = self._hits.get(key)
        if q is None:
            return
        while q and now - q[0] >= window:
            q.popleft()
        if len(q) >= limit:
            raise RateLimited(message, max(1, int(window - (now - q[0])) + 1))

    def sweep(self, window: float = 3600) -> None:
        now = self.clock()
        for key in [k for k, q in self._hits.items() if not q or now - q[-1] >= window]:
            del self._hits[key]


@dataclass
class _Session:
    code: str
    key: str
    created: float
    expires: float
    text: str = ""
    seq: int = 0            # bumps whenever the text changes
    submit_seq: int = 0     # bumps whenever the phone pressed Search
    phone_seen: float = 0.0
    sends: int = 0
    extra: Dict[str, Any] = field(default_factory=dict)


# Limits (hits, seconds). Generous for a person, tiny for a script.
LIMIT_CREATE_IP = (10, 60)        # new TV sessions per client address
LIMIT_SEND_CODE = (40, 10)        # phone updates per code (live typing is about 4 a second at most)
LIMIT_SEND_IP = (120, 60)         # phone updates per client address
LIMIT_BAD_CODE_IP = (15, 300)     # wrong or expired codes per client address: guessing is cheap to stop
LIMIT_POLL_CODE = (60, 10)        # TV polls per code (the page polls about once a second)


class TypeChannels:
    def __init__(self, clock: Callable[[], float] = time.monotonic, ttl: int = SESSION_TTL_SECONDS,
                 max_sessions: int = MAX_SESSIONS):
        self.clock = clock
        self.ttl = ttl
        self.max_sessions = max_sessions
        self.limits = RateLimiter(clock)
        self._sessions: Dict[str, _Session] = {}

    # -- housekeeping

    def _purge(self) -> None:
        now = self.clock()
        for code in [c for c, s in self._sessions.items() if s.expires <= now]:
            del self._sessions[code]
        self.limits.sweep()

    def active(self) -> int:
        self._purge()
        return len(self._sessions)

    def _find(self, code: Optional[str]) -> Optional[_Session]:
        self._purge()
        norm = normalise_code(code)
        return self._sessions.get(norm) if norm else None

    # -- the TV

    def create(self, client: str, previous_key: Optional[str] = None) -> Dict[str, Any]:
        """A new session for a TV. With the key of its old session, that one is replaced (one session per TV)."""
        self.limits.hit(f"create:{client}", *LIMIT_CREATE_IP, message="Too many new codes. Wait a minute.")
        self._purge()
        if previous_key:
            for code, s in list(self._sessions.items()):
                if hmac.compare_digest(s.key.encode(), str(previous_key).encode("utf-8", "replace")):
                    del self._sessions[code]
        while len(self._sessions) >= self.max_sessions:
            oldest = min(self._sessions.values(), key=lambda s: s.created)
            del self._sessions[oldest.code]
        for _ in range(50):
            code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
            if code not in self._sessions:
                break
        else:  # pragma: no cover - 50 collisions in 1e9 codes
            raise RateLimited("Try again.", 1)
        now = self.clock()
        session = _Session(code=code, key=secrets.token_urlsafe(18), created=now, expires=now + self.ttl)
        self._sessions[code] = session
        return {"code": code, "key": session.key, "expires_in": self.ttl, "max_length": MAX_TEXT_LENGTH}

    _BAD_MESSAGE = "Too many wrong codes. Wait a few minutes."

    def _bad(self, client: str) -> None:
        """A wrong code or key from this address counts against the guessing limit."""
        self.limits.hit(f"bad:{client}", *LIMIT_BAD_CODE_IP, message=self._BAD_MESSAGE)

    def _guard(self, client: str) -> None:
        """An address that has used up its wrong guesses gets no answers at all (not even for a right code)."""
        self.limits.check_full(f"bad:{client}", *LIMIT_BAD_CODE_IP, message=self._BAD_MESSAGE)

    def _owned(self, code: str, key: Optional[str], client: str) -> _Session:
        self._guard(client)
        s = self._find(code)
        if s is None:
            self._bad(client)
            raise UnknownCode("This code has expired.")
        if not key or not hmac.compare_digest(s.key.encode(), str(key).encode("utf-8", "replace")):
            self._bad(client)
            raise WrongKey("That isn't this TV's session.")
        return s

    def poll(self, code: str, key: Optional[str], client: str, after: int = 0) -> Dict[str, Any]:
        """What the TV reads: the latest text and counters. Only with the session's key."""
        s = self._owned(code, key, client)
        self.limits.hit(f"poll:{s.code}", *LIMIT_POLL_CODE)
        return {
            "seq": s.seq, "text": s.text, "changed": s.seq != after, "submit_seq": s.submit_seq,
            "phone_connected": s.sends > 0, "expires_in": max(0, int(s.expires - self.clock())),
        }

    def close(self, code: str, key: Optional[str], client: str) -> bool:
        s = self._owned(code, key, client)
        del self._sessions[s.code]
        return True

    # -- the phone

    def check(self, code: str, client: str) -> str:
        """Is this a live code? Wrong ones count against the client address (guessing). Returns the canonical code."""
        self._guard(client)
        s = self._find(code)
        if s is None:
            self._bad(client)
            raise UnknownCode("That code isn't valid any more. Ask the TV for a new one.")
        return s.code

    def send(self, code: str, text: Any, client: str, submit: bool = False) -> Dict[str, Any]:
        """The phone's text becomes the TV's search text. The code is the only credential."""
        cleaned = clean_text(text)
        norm = self.check(code, client)
        self.limits.hit(f"send:{norm}", *LIMIT_SEND_CODE)
        self.limits.hit(f"sendip:{client}", *LIMIT_SEND_IP)
        s = self._sessions[norm]
        s.sends += 1
        s.phone_seen = self.clock()
        if cleaned != s.text:
            s.text = cleaned
            s.seq += 1
        if submit:
            s.submit_seq += 1
        return {"ok": True, "seq": s.seq, "expires_in": max(0, int(s.expires - self.clock()))}


def describe(error: TypingError) -> Tuple[int, Dict[str, Any]]:
    """(status, JSON body) for an API error."""
    body: Dict[str, Any] = {"detail": error.message}
    if isinstance(error, RateLimited):
        body["retry_after"] = error.retry_after
    return error.status, body
