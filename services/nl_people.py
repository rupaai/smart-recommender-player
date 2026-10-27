"""
Northern Lights: who's watching (services/nl_people.py), M4

The people come from configuration (config.yaml `northern_lights.people`, or the env var
NL_PEOPLE holding the same list as JSON). Each person has an id (the cookie value), a label,
the Jellyfin user that keeps their progress, and the recommender profile their ratings go to.

    northern_lights:
      people:
        - {id: labiba,  name: Labiba,  jellyfin_user_id: <id or user name>, profile_id: 1}
        - {id: partner, name: <their name>, jellyfin_user_id: <id or user name>, profile_id: 2}

No passwords: the `nl_viewer` cookie is a plain choice (a person's id, or "both"). A cookie value
that is not a configured person is treated as "not chosen". With no people configured the app
has one implicit viewer that uses JELLYFIN_USER_ID, exactly as before M4.

Everything here is pure (plain dicts and strings in, plain values out) so it is tested without
a server or Jellyfin.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from services import nl_settings

COOKIE_NAME = "nl_viewer"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365
BOTH = "both"
DEFAULT_KEY = "default"            # the implicit viewer when no people are configured
BOTH_LABEL = "Both of us"
POSITION_GAP_SECONDS = 120.0       # positions closer than this are "the same place"

_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,23}$")
_LANG = re.compile(r"^[a-z]{3}$")
SIZES = ("S", "M", "L", "XL")
BACKGROUNDS = ("none", "shadow", "box")


@dataclass(frozen=True)
class Person:
    id: str
    name: str
    jellyfin_user_id: str
    profile_id: Optional[int] = None


@dataclass(frozen=True)
class Viewer:
    """Who is watching: one person, both of them, or the implicit single viewer."""

    key: str                                   # person id, "both" or "default"
    label: str
    people: Tuple[Person, ...] = ()            # empty only for the implicit viewer
    implicit: bool = False

    @property
    def is_both(self) -> bool:
        return self.key == BOTH

    @property
    def primary(self) -> Optional[Person]:
        return self.people[0] if self.people else None

    @property
    def jellyfin_users(self) -> List[Optional[str]]:
        """Jellyfin users to report to. [None] = the default (JELLYFIN_USER_ID) user."""
        return [p.jellyfin_user_id for p in self.people] or [None]

    @property
    def profile_ids(self) -> List[int]:
        return [p.profile_id for p in self.people if p.profile_id is not None]

    def public(self) -> Dict[str, Any]:
        return {"key": self.key, "label": self.label, "both": self.is_both, "implicit": self.implicit}


IMPLICIT_VIEWER = Viewer(key=DEFAULT_KEY, label="You", people=(), implicit=True)


class PeopleConfigError(ValueError):
    pass


def parse_people(raw: Any) -> List[Person]:
    """Validates the configured list. Raises PeopleConfigError with a message that names the problem."""
    if raw in (None, "", []):
        return []
    if not isinstance(raw, list):
        raise PeopleConfigError("northern_lights.people must be a list.")
    people: List[Person] = []
    seen = set()
    for i, entry in enumerate(raw, 1):
        if not isinstance(entry, dict):
            raise PeopleConfigError(f"northern_lights.people #{i} must be a mapping.")
        pid = str(entry.get("id") or "").strip().lower()
        name = str(entry.get("name") or "").strip()
        user = str(entry.get("jellyfin_user_id") or "").strip()
        if not _ID.match(pid) or pid in (BOTH, DEFAULT_KEY):
            raise PeopleConfigError(
                f"northern_lights.people #{i}: id {pid!r} must be 1-24 lowercase letters, digits, - or _ "
                f"(and not '{BOTH}' or '{DEFAULT_KEY}')."
            )
        if pid in seen:
            raise PeopleConfigError(f"northern_lights.people: id {pid!r} is used twice.")
        if not name:
            raise PeopleConfigError(f"northern_lights.people #{i} ({pid}): name is missing.")
        if not user:
            raise PeopleConfigError(f"northern_lights.people #{i} ({pid}): jellyfin_user_id is missing.")
        profile = entry.get("profile_id")
        if profile is not None:
            try:
                profile = int(profile)
            except (TypeError, ValueError):
                raise PeopleConfigError(f"northern_lights.people #{i} ({pid}): profile_id must be a number.")
        seen.add(pid)
        people.append(Person(id=pid, name=name[:40], jellyfin_user_id=user, profile_id=profile))
    return people


class People:
    """The configured people and the viewer cookie."""

    def __init__(self, people: Optional[Sequence[Person]] = None):
        self.people: List[Person] = list(people or [])

    @classmethod
    def from_config(cls, config: Optional[Dict[str, Any]], env_json: Optional[str] = None) -> "People":
        """`env_json` (NL_PEOPLE) wins over config.yaml. A bad list is an error at start-up, not a silent fallback."""
        if env_json and env_json.strip():
            try:
                raw = json.loads(env_json)
            except ValueError as e:
                raise PeopleConfigError(f"NL_PEOPLE is not valid JSON ({e}).") from e
        else:
            raw = ((config or {}).get("northern_lights") or {}).get("people")
        return cls(parse_people(raw))

    @property
    def configured(self) -> bool:
        return bool(self.people)

    def get(self, person_id: str) -> Optional[Person]:
        return next((p for p in self.people if p.id == person_id), None)

    def choices(self) -> List[Dict[str, Any]]:
        """What the who's-watching screen offers: each person, then Both (only with two or more people)."""
        out = [{"key": p.id, "label": p.name} for p in self.people]
        if len(self.people) >= 2:
            out.append({"key": BOTH, "label": BOTH_LABEL})
        return out

    def viewer_for(self, key: Optional[str]) -> Optional[Viewer]:
        """The viewer a cookie value names; None when not chosen / unknown. Always the implicit
        viewer when nobody is configured (the cookie is ignored then)."""
        if not self.configured:
            return IMPLICIT_VIEWER
        key = (key or "").strip().lower()
        if key == BOTH and len(self.people) >= 2:
            return Viewer(key=BOTH, label=BOTH_LABEL, people=tuple(self.people))
        person = self.get(key) if key else None
        if person:
            return Viewer(key=person.id, label=person.name, people=(person,))
        return None

    def parse_cookie(self, cookie_header: Optional[str]) -> Optional[Viewer]:
        """The viewer from a raw `Cookie:` header (or None when absent / unknown)."""
        return self.viewer_for(cookie_value(cookie_header))

    def default_viewer(self) -> Viewer:
        """Used by API calls that arrive with no valid cookie: the first person (pages ask first)."""
        return self.viewer_for(self.people[0].id) if self.people else IMPLICIT_VIEWER

    def others(self, viewer: Viewer) -> List[Person]:
        mine = {p.id for p in viewer.people}
        return [p for p in self.people if p.id not in mine]


def cookie_value(cookie_header: Optional[str], name: str = COOKIE_NAME) -> Optional[str]:
    """One cookie's value from a Cookie header. Never raises; the last duplicate wins like browsers do."""
    if not cookie_header:
        return None
    found = None
    for part in str(cookie_header).split(";"):
        key, sep, value = part.strip().partition("=")
        if sep and key.strip() == name:
            found = value.strip().strip('"')
    return found


def safe_next(target: Optional[str], default: str) -> str:
    """Where to go after choosing: only a path inside /nl/ or /player/ (no scheme, host, backslash or control characters)."""
    t = (target or "").strip()
    if (
        t.startswith(("/nl/", "/player/")) or t in ("/nl", "/player")
    ) and not t.startswith("//") and "\\" not in t and not re.search(r"[\x00-\x1f\x7f]", t) and "://" not in t:
        return t
    return default


# ---------------------------------------------------------------- resume rule (U2)


def fmt_clock(seconds: float) -> str:
    s = max(0, int(seconds or 0))
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def resume_summary(
    viewer: Viewer,
    states: Dict[str, Dict[str, Any]],
    people: Sequence[Person],
    gap: float = POSITION_GAP_SECONDS,
) -> Dict[str, Any]:
    """Where to resume, and a note when the two of them are in different places.

    `states` maps person id -> {"position": seconds, "played": bool} for the people we could ask.
    - one viewer: their own position; the note says where the other person is when it differs by
      more than `gap` ("You're at 42:10, Sam is at 38:00");
    - Both: the further position wins; the note names both when they differ by more than `gap`.
    Returns {resume_seconds, played, note, positions}."""
    mine = [p for p in viewer.people if p.id in states]
    if not mine:
        return {"resume_seconds": 0.0, "played": False, "note": None, "positions": []}
    pos = {p.id: float(states[p.id].get("position") or 0.0) for p in mine}
    played = {p.id: bool(states[p.id].get("played")) for p in mine}
    # A finished person has no position to resume; if everyone finished, the title counts as played.
    live = [pos[p.id] for p in mine if not played[p.id]]
    resume = max(live) if live else 0.0
    all_played = all(played.values())
    note = None
    positions = [{"person": p.id, "name": p.name, "seconds": pos[p.id], "played": played[p.id]} for p in mine]

    if viewer.is_both:
        if len(mine) >= 2 and not all_played:
            values = [(p, pos[p.id]) for p in mine]
            if max(v for _, v in values) - min(v for _, v in values) > gap:
                note = ", ".join(f"{p.name} is at {fmt_clock(v)}" for p, v in values)
    else:
        me = mine[0]
        ahead = []
        for other in people:
            if other.id == me.id or other.id not in states:
                continue
            their, done = float(states[other.id].get("position") or 0.0), bool(states[other.id].get("played"))
            if done or their <= 0:
                continue
            if abs(their - pos[me.id]) > gap:
                ahead.append((other, their))
                positions.append({"person": other.id, "name": other.name, "seconds": their, "played": False})
        if ahead and not played[me.id]:
            mine_text = "You're at the start" if pos[me.id] <= 0 else f"You're at {fmt_clock(pos[me.id])}"
            note = mine_text + ", " + ", ".join(f"{o.name} is at {fmt_clock(v)}" for o, v in ahead)
    return {"resume_seconds": resume, "played": all_played, "note": note, "positions": positions}


# ---------------------------------------------------------------- language prefs (U3)

# ISO 639-2 has a bibliographic and a terminologic code for some languages; Jellyfin files use either.
_LANG_SYNONYMS = {
    "fre": "fra", "ger": "deu", "chi": "zho", "dut": "nld", "cze": "ces", "gre": "ell",
    "rum": "ron", "per": "fas", "slo": "slk", "mac": "mkd", "may": "msa", "bur": "mya",
    "alb": "sqi", "arm": "hye", "baq": "eus", "geo": "kat", "ice": "isl", "mao": "mri", "tib": "bod", "wel": "cym",
}
PREF_FIELDS = ("audio_lang", "subtitle_lang", "subtitles_on", "sub_style", "search_lang")


def lang_key(code: Any) -> str:
    """A three-letter language code with the B/T twins folded together ("fre" == "fra"); "" if unusable."""
    c = str(code or "").strip().lower()
    return _LANG_SYNONYMS.get(c, c) if _LANG.match(c) else ""


def clean_sub_style(value: Any) -> Optional[Dict[str, Any]]:
    """The saved subtitle look, kept only if it is a valid one ({size, background, raise})."""
    if not isinstance(value, dict):
        return None
    size, background, raise_ = value.get("size"), value.get("background"), value.get("raise")
    if size not in SIZES or background not in BACKGROUNDS or raise_ not in (0, 1, 2) or isinstance(raise_, bool):
        return None
    return {"size": size, "background": background, "raise": raise_}


def clean_prefs(patch: Dict[str, Any]) -> Dict[str, Any]:
    """Validates a prefs update. Languages are three lowercase letters (or "" / null to clear), the rest
    as documented. Raises ValueError naming the first bad field. Unknown keys are ignored."""
    out: Dict[str, Any] = {}
    for name in ("audio_lang", "subtitle_lang", "search_lang"):
        if name in patch:
            v = patch[name]
            if v in (None, ""):
                out[name] = None
            elif isinstance(v, str) and _LANG.match(v):
                out[name] = v
            else:
                raise ValueError(f"{name} must be a three-letter language code like eng.")
    if "subtitles_on" in patch:
        v = patch["subtitles_on"]
        if v is not None and not isinstance(v, bool):
            raise ValueError("subtitles_on must be true or false.")
        out["subtitles_on"] = v
    if "hide_spoilers" in patch:
        v = patch["hide_spoilers"]
        if v is not None and not isinstance(v, bool):
            raise ValueError("hide_spoilers must be true or false.")
        out["hide_spoilers"] = v
    if "skip_seconds" in patch:
        v = patch["skip_seconds"]
        if v is not None and not nl_settings.valid_skip(v):
            raise ValueError("skip_seconds must be 10, 15, 30 or 60.")
        out["skip_seconds"] = v
    if "autoplay" in patch:
        v = patch["autoplay"]
        if v is not None and not isinstance(v, bool):
            raise ValueError("autoplay must be true or false.")
        out["autoplay"] = v
    if "quality_kbps" in patch:
        v = patch["quality_kbps"]
        if v is not None and not nl_settings.valid_quality(v):
            raise ValueError("quality_kbps must be null (Auto), 8000, 4000, 2000 or 1000.")
        out["quality_kbps"] = v
    if "picture_fit" in patch:
        v = patch["picture_fit"]
        if v is not None and not nl_settings.valid_fit(v):
            raise ValueError("picture_fit must be fit, fill or original.")
        out["picture_fit"] = v
    if "sub_style" in patch:
        v = patch["sub_style"]
        if v is None:
            out["sub_style"] = None
        else:
            style = clean_sub_style(v)
            if style is None:
                raise ValueError("sub_style must be {size: S|M|L|XL, background: none|shadow|box, raise: 0|1|2}.")
            out["sub_style"] = style
    return out


def prefs_owner(viewer: Viewer, has_row: Any) -> Optional[str]:
    """Whose prefs row a viewer reads: their own; for Both its own row if it exists, else the first person's."""
    if viewer.is_both:
        return BOTH if has_row(BOTH) else (viewer.primary.id if viewer.primary else None)
    return viewer.key


def _is_commentary(track: Dict[str, Any]) -> bool:
    return "commentary" in str(track.get("label") or track.get("display_title") or "").lower()


def _is_special_audio(track: Dict[str, Any]) -> bool:
    """A commentary or an audio-description (P24) track: never the default pick for a language."""
    return _is_commentary(track) or bool(track.get("is_audio_description"))


def pick_audio_index(tracks: Iterable[Dict[str, Any]], lang: Optional[str]) -> Optional[int]:
    """The audio track in the preferred language (not a commentary; the file's default one first), or None."""
    want = lang_key(lang)
    if not want:
        return None
    matches = [t for t in tracks if lang_key(t.get("language")) == want and not _is_special_audio(t)]
    if not matches:
        return None
    matches.sort(key=lambda t: (not t.get("is_default"), -(t.get("channels") or 0)))
    return matches[0]["index"]


def pick_subtitle_track(tracks: Iterable[Dict[str, Any]], lang: Optional[str]) -> Optional[Dict[str, Any]]:
    """The best subtitle track in the preferred language: full subtitles before forced-only, text before
    pictures (no burning-in needed), the file's default first, SDH last."""
    want = lang_key(lang)
    if not want:
        return None
    matches = [t for t in tracks if lang_key(t.get("language")) == want]
    if not matches:
        return None
    matches.sort(key=lambda t: (
        bool(t.get("is_forced")), bool(t.get("picture", t.get("kind") == "image")), bool(t.get("is_hearing_impaired")), not t.get("is_default"),
    ))
    return matches[0]


@dataclass
class Prefs:
    audio_lang: Optional[str] = None
    subtitle_lang: Optional[str] = None
    subtitles_on: bool = False
    sub_style: Optional[Dict[str, Any]] = None
    search_lang: Optional[str] = None
    hide_spoilers: bool = False
    skip_seconds: Optional[int] = None        # M10c: None = the device's own choice
    autoplay: bool = True                     # M10c: default for shows with no switch of their own
    quality_kbps: Optional[int] = None        # M10c: None = Auto
    picture_fit: str = "fit"                  # M10c
    extra: Dict[str, Any] = field(default_factory=dict)

    def public(self) -> Dict[str, Any]:
        return {
            "audio_lang": self.audio_lang,
            "subtitle_lang": self.subtitle_lang,
            "subtitles_on": bool(self.subtitles_on),
            "sub_style": self.sub_style,
            "search_lang": self.search_lang,
            "hide_spoilers": bool(self.hide_spoilers),
            "skip_seconds": self.skip_seconds,
            "autoplay": bool(self.autoplay),
            "quality_kbps": self.quality_kbps,
            "picture_fit": self.picture_fit,
        }

    @classmethod
    def from_row(cls, row: Optional[Dict[str, Any]]) -> "Prefs":
        row = row or {}
        return cls(
            audio_lang=row.get("audio_lang") or None,
            subtitle_lang=row.get("subtitle_lang") or None,
            subtitles_on=bool(row.get("subtitles_on")),
            sub_style=row.get("sub_style") or None,
            search_lang=row.get("search_lang") or None,
            hide_spoilers=bool(row.get("hide_spoilers")),
            skip_seconds=row.get("skip_seconds") if nl_settings.valid_skip(row.get("skip_seconds")) else None,
            autoplay=True if row.get("autoplay") is None else bool(row.get("autoplay")),
            quality_kbps=row.get("quality_kbps") if nl_settings.valid_quality(row.get("quality_kbps")) else None,
            picture_fit=row.get("picture_fit") if nl_settings.valid_fit(row.get("picture_fit")) else "fit",
        )
