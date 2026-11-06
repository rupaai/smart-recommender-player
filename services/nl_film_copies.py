"""
The best copy of one film to download (services/nl_film_copies.py), for buttons that fetch a film on mars
without showing the torrent list: "Get it" on today's picks (and later, a pick that downloads by itself).

The search and its "Best match" order are the Download tab's (TorrentSearcher, sort="best"). Here only
releases of this film are kept: every word of the title before the year, and the year itself (one year
off is allowed: release dates differ by country). Episodes and season packs are dropped. The magnet link
stays on the server.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

_WORD_RE = re.compile(r"[a-z0-9]+")
_YEAR_RE = re.compile(r"(?<!\d)(19\d\d|20\d\d)(?!\d)")
_EPISODE_RE = re.compile(r"(?i)\bs\d{1,2}e\d{1,3}\b|\bseason\b|\bs\d{1,2}\b")
_NOISE = {"the", "a", "an", "and", "of"}


def words(text: str) -> set:
    return {w for w in _WORD_RE.findall((text or "").lower().replace("'", "")) if w not in _NOISE}


def film_matches(name: str, title: str, year: Optional[int]) -> bool:
    """Is this release this film? Title words before the year, and the year (±1) when we know it."""
    head = (name or "").lower().replace("'", "")
    if _EPISODE_RE.search(head):
        return False
    m = None
    for found in _YEAR_RE.finditer(head):
        if found.start() > 0:
            m = found
            break
    cut = head[: m.start()] if m else head
    want = words(title)
    if want and not want <= words(cut):
        return False
    if year:
        return bool(m) and abs(int(m.group(1)) - int(year)) <= 1
    return True


def film_copies(searcher: Any, title: str, year: Optional[int]) -> List[Dict[str, Any]]:
    """Matching releases, best first (the searcher's order)."""
    if searcher is None:
        raise RuntimeError("Downloading isn't set up on this server.")
    query = f"{title} {year}" if year else title
    results = searcher.search(query, media_type="movie", sort="best") or []
    return [r for r in results if r.get("magnet") and film_matches(r.get("name") or "", title, year)]


def best_film_copy(searcher: Any, title: str, year: Optional[int]) -> Dict[str, Any]:
    found = film_copies(searcher, title, year)
    if not found:
        raise LookupError(f"No copy of {title} found right now. Try again later or use the Download tab.")
    return found[0]
