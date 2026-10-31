"""
Film features and the questions-session store (services/vibe_popup.py)

`features()` reads, once per film, everything the questions pop-ups test: the mood
dials (facets, with the LLM mood tags' dials as a fallback), the tag words, genres,
runtime, year, language, rating and vibe cluster. The mood-map pop-up
(services/mood_popup.py, vibe_moods.py) uses it.

`SessionStore` keeps the open questions sessions (mood map or Wishmatch) in memory for
SESSION_TTL seconds, so "Show more" can page through the answered pool.

(The older dial-by-dial "guess the vibe" pop-up that lived here was removed on
2026-10-31; nothing had used it since the mood map replaced it.)
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Dict, Optional

SESSION_TTL = 3 * 3600


def _json(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        out = json.loads(value)
        return out if isinstance(out, dict) else {}
    except ValueError:
        return {}


def features(item: Dict[str, Any]) -> Dict[str, Any]:
    """Everything the belief can test on one film, computed once per item."""
    fx = item.get("_popup_fx")
    if fx is not None:
        return fx
    tags = _json(item.get("vibe_tags"))
    facets = {k: v for k, v in _json(item.get("facets")).items() if isinstance(v, (int, float))}
    for k, v in (tags.get("dials") or {}).items():
        if isinstance(v, (int, float)):
            facets.setdefault(k, float(v))
    kws = {k.strip().lower() for k in (item.get("tier2_keywords") or "").split(",") if k.strip()}
    flags = {f.replace("_", " ") for f in (tags.get("flags") or [])}
    fx = {
        "facets": facets,
        "feels": set(tags.get("feels") or []),
        "good": set(tags.get("good_for") or []),
        "palette": set(tags.get("palette") or []),
        "kw": kws,
        "flags": flags,
        "tagged": bool(tags),
        "genres": {g.strip().lower() for g in (item.get("genres") or "").split(",") if g.strip()},
        "runtime": int(item.get("runtime") or 0),
        "year": int(item.get("release_year") or 0),
        "lang": (item.get("original_language") or "").lower(),
        "rating": float(item.get("rating") or 0.0),
        "votes": int(item.get("vote_count") or 0),
        "cluster": item.get("vibe_cluster_id"),
        "tv": item.get("media_type") == "tv",
    }
    item["_popup_fx"] = fx
    return fx


class SessionStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._s: Dict[int, Any] = {}

    def put(self, s: Any) -> None:
        with self._lock:
            now = time.time()
            for k in [k for k, v in self._s.items() if now - v.updated > SESSION_TTL]:
                del self._s[k]
            self._s[s.id] = s

    def get(self, sid: Optional[int]) -> Optional[Any]:
        with self._lock:
            return self._s.get(sid) if sid is not None else None
