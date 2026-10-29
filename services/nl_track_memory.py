"""Remembered audio and subtitle choice per title (Northern Lights).

When a viewer picks an audio track or a subtitle (or switches subtitles off) in the player, the page saves it for that
movie or show. The next time the title plays, the same track comes back instead of the language prefs' default.

A choice is saved as the track itself (Jellyfin stream index and the file it belongs to) plus what describes it
(language, label, forced, SDH, picture or text). On the same file the index is used; on another file (the next episode,
or a new copy of the film) the best track with the same language and the same kind is picked, so Italian stays Italian
and "English (forced)" stays forced. No match means the language prefs decide, as before.

Pure functions only; the rows live in `nl_track_memory` (database.py) and the picking happens in
`JellyfinPlayback.stream_for` (prefer["memory"]).
"""

import re
from typing import Any, Dict, Iterable, Optional

from services.nl_people import lang_key

_FLAGS = ("is_forced", "is_hearing_impaired", "picture", "is_commentary")
_CODEC = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")


def clean_choice(value: Any, source_id: Optional[int]) -> Optional[Dict[str, Any]]:
    """What to save for one axis from the page's {index, language, label, ...} (or {"off": true} for subtitles).
    Raises ValueError for anything malformed; None means "nothing to save"."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("a track must be an object")
    if value.get("off") is True:
        return {"off": True}
    index = value.get("index")
    if not isinstance(index, int) or isinstance(index, bool) or index < 0:
        raise ValueError("a track needs its stream index (a whole number, 0 or more)")
    label = value.get("label")
    codec = value.get("codec")
    out: Dict[str, Any] = {
        "index": index,
        "source_id": source_id,
        "language": lang_key(value.get("language")),
        "label": str(label)[:120] if isinstance(label, str) else "",
        "codec": codec.lower() if isinstance(codec, str) and _CODEC.match(codec) else "",
    }
    for flag in _FLAGS:
        out[flag] = bool(value.get(flag))
    if not out["is_commentary"]:
        out["is_commentary"] = "commentary" in out["label"].lower()
    return out


def _flags_of(track: Dict[str, Any]) -> Dict[str, bool]:
    label = str(track.get("label") or track.get("display_title") or "")
    return {
        "is_forced": bool(track.get("is_forced")),
        "is_hearing_impaired": bool(track.get("is_hearing_impaired")),
        "picture": bool(track.get("picture", track.get("kind") == "image")),
        "is_commentary": "commentary" in label.lower(),
    }


def recall(tracks: Iterable[Dict[str, Any]], saved: Optional[Dict[str, Any]], source_id: Optional[int]) -> Optional[Dict[str, Any]]:
    """The track of `tracks` that matches a saved choice, or None (also for {"off": true}; see `is_off`).

    Same file: the saved index, as long as its language still matches. Otherwise the tracks in the saved language are
    ranked by how many of forced / SDH / picture / commentary agree, then the same label, then the same codec, then the
    file's default. A saved track without a language matches only by its exact label."""
    if not saved or saved.get("off") or not isinstance(saved.get("index"), int):
        return None
    tracks = list(tracks)
    want = saved.get("language") or ""
    if source_id is not None and saved.get("source_id") == source_id:
        same = next((t for t in tracks if t.get("index") == saved["index"]), None)
        if same is not None and lang_key(same.get("language")) == want:
            return same
    label = saved.get("label") or ""
    if want:
        pool = [t for t in tracks if lang_key(t.get("language")) == want]
    else:
        pool = [t for t in tracks if not lang_key(t.get("language")) and label and (t.get("label") or "") == label]
    if not pool:
        return None

    def score(t: Dict[str, Any]):
        flags = _flags_of(t)
        agree = sum(flags[f] == bool(saved.get(f)) for f in _FLAGS)
        return (agree, (t.get("label") or "") == label, (t.get("codec") or "").lower() == (saved.get("codec") or ""), bool(t.get("is_default")))

    return max(pool, key=score)


def is_off(saved: Optional[Dict[str, Any]]) -> bool:
    """True when the saved subtitle choice is "Off"."""
    return bool(saved and saved.get("off"))
