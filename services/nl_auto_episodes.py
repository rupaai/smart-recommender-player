"""
New episodes that download by themselves (services/nl_auto_episodes.py), on mars only.

Every `check_hours`, for each show you are watching, mars makes sure the next `episodes_ahead` episodes
are ready on the server, fetching them **one at a time, in order** (S01E01, then S01E02...), so the next
one is always there to watch. A season pack is never used: one episode file lands in the library as soon
as it is done, while a pack only arrives when every episode is in.

"A show you are watching": it has files on the server and one of you watched it, or it got a new file,
in the last `active_days` days (the same list as "Your shows" on the New & popular page). A show someone
rated below `new_episodes.skip_rated_below` is left alone.

"Ready": episode files on the server that come after the last episode the person furthest along has
watched (Jellyfin's played marks). When Jellyfin can't be asked, the show is skipped this time.

Room on the disk is checked by the downloader itself (`torrent.max_media_gb`, `torrent.min_free_gb`):
an episode that doesn't fit is refused with the usual message and tried again next time.

Copies are found the same way as the New & popular Download button (services/nl_new_episodes.py).
TMDB is free; nothing here spends money. Settings: `auto_episodes:` in config.yaml.
"""

from __future__ import annotations

import os
import time
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from services.nl_new_episodes import episode_code, parse_day

FIRST_RUN_DELAY = 15 * 60

DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "run_in": ["prod"],         # Homelab environments that run it (dev and prod share no disk, but dev has no room)
    "check_hours": 3,           # how often mars looks
    "episodes_ahead": 3,        # unwatched episodes kept ready per show
    "active_days": 60,          # a show counts as "being watched" this long after the last watch or new file
    "shows_per_run": 10,        # at most this many downloads are started per look
}


def settings_from(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    out = dict(DEFAULTS)
    out.update(((config or {}).get("auto_episodes") or {}))
    return out


def runs_here(cfg: Dict[str, Any], env: Optional[str] = None) -> bool:
    """On when enabled and, under Homelab, in one of `run_in`. Outside Homelab `enabled` decides."""
    env = (os.getenv("HOMELAB_ENV", "") if env is None else env).strip()
    return bool(cfg["enabled"]) and (not env or env in (cfg.get("run_in") or []))


def aired_episodes(details: Dict[str, Any], today: date) -> List[Tuple[int, int]]:
    """Every (season, episode) TMDB says has aired, in order. Specials (season 0) are left out."""
    last = details.get("last_episode_to_air") or {}
    aired = parse_day(last.get("air_date"))
    last_season = int(last.get("season_number") or 0)
    last_ep = int(last.get("episode_number") or 0)
    if not aired or aired > today or last_season < 1:
        return []
    out: List[Tuple[int, int]] = []
    for s in sorted(details.get("seasons") or [], key=lambda x: int(x.get("season_number") or 0)):
        n = int(s.get("season_number") or 0)
        if n < 1 or n > last_season:
            continue
        count = last_ep if n == last_season else int(s.get("episode_count") or 0)
        out.extend((n, e) for e in range(1, count + 1))
    return out


def next_to_get(aired: List[Tuple[int, int]], have: Set[Tuple[int, int]]) -> Optional[Tuple[int, int]]:
    """The first aired episode after the furthest one the server has (or had). Gaps further back are left:
    you skipped them on purpose, or the cleanup removed them after you watched."""
    if not aired:
        return None
    furthest = max(have) if have else None
    for code in aired:
        if furthest is None or code > furthest:
            return code if code not in have else None
    return None


def ready_after(episodes: List[Dict[str, Any]]) -> int:
    """`episodes` is one person's view of a show's files ({season, episode, played}). The number of files
    after the last one they watched."""
    eps = sorted((int(e["season"] or 0), int(e["episode"] or 0), bool(e.get("played")))
                 for e in episodes if e.get("episode") is not None)
    last_played = max((i for i, e in enumerate(eps) if e[2]), default=-1)
    return len(eps) - last_played - 1


class AutoEpisodes:
    """`new_episodes` is the NewEpisodes service (shows, TMDB details, copies). `ready(item_id)` returns
    how many unwatched episodes are ready for the person furthest along, or None when it can't tell.
    `downloading()` returns our ids of shows with a download running. `profiles()` lists everyone's
    rating profile ids."""

    def __init__(self, new_episodes: Any, ready: Callable[[int], Optional[int]],
                 downloading: Callable[[], Set[int]], profiles: Callable[[], List[int]],
                 config: Optional[Dict[str, Any]] = None, today: Optional[Callable[[], date]] = None,
                 clock: Callable[[], float] = time.time):
        self.ne = new_episodes
        self.ready = ready
        self.downloading = downloading
        self.profiles = profiles
        self.cfg = settings_from(config)
        self.today = today or date.today
        self.clock = clock
        self.last_run: Dict[str, Any] = {}

    def _active(self, row: Dict[str, Any], today: date) -> bool:
        when = str(row.get("active_at") or "")[:19]
        try:
            at = datetime.fromisoformat(when.replace("T", " ")).date()
        except ValueError:
            return False
        return (today - at) <= timedelta(days=int(self.cfg["active_days"]))

    def plan(self) -> List[Dict[str, Any]]:
        """What to fetch now: at most one episode per show (so they come in order), and a line for each
        show that was looked at and why nothing was fetched."""
        today = self.today()
        busy = self.downloading()
        out: List[Dict[str, Any]] = []
        for row in self.ne.your_shows(self.profiles()):
            title = row.get("title") or ""
            if not row.get("in_library") or not self._active(row, today):
                continue
            base = {"media_item_id": row["id"], "tmdb_id": int(row["tmdb_id"]), "title": title}
            if row["id"] in busy:
                out.append({**base, "action": "wait", "why": "An episode is still downloading."})
                continue
            ready = self.ready(row["id"])
            if ready is None:
                out.append({**base, "action": "skip", "why": "Jellyfin couldn't say what you've watched."})
                continue
            if ready >= int(self.cfg["episodes_ahead"]):
                out.append({**base, "action": "enough", "why": f"{ready} unwatched episodes are ready."})
                continue
            details = self.ne.details(int(row["tmdb_id"]))
            if not details:
                out.append({**base, "action": "skip", "why": "TMDB couldn't be reached."})
                continue
            code = next_to_get(aired_episodes(details, today), self.ne.had_episodes(row["id"]))
            if code is None:
                out.append({**base, "action": "enough", "why": "You have every episode that's out."})
                continue
            out.append({**base, "action": "get", "season": code[0], "episode": code[1],
                        "code": episode_code(*code), "why": f"{ready} ready, getting the next one."})
        return out

    def find_copies(self, plan: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """For each "get" line, the best copy of that one episode (never a season pack)."""
        out = []
        for line in [p for p in plan if p["action"] == "get"][: int(self.cfg["shows_per_run"])]:
            try:
                picked = self.ne.pick(line["tmdb_id"], line["season"], line["episode"])
            except (LookupError, RuntimeError) as e:
                line.update(action="no-copy", why=str(e))
                continue
            line.update(magnet=picked["magnet"], release=picked["release"])
            out.append(line)
        return out

    def finish(self, plan: List[Dict[str, Any]]) -> Dict[str, Any]:
        """The last run, for GET /api/nl/auto-episodes (no magnet links)."""
        lines = [{k: v for k, v in p.items() if k != "magnet"} for p in plan]
        self.last_run = {"at": int(self.clock()), "started": [p for p in lines if p["action"] == "started"],
                         "shows": lines}
        names = ", ".join(f"{p['title']} {p['code']}" for p in self.last_run["started"]) or "nothing new"
        print(f"[AutoEpisodes] Looked at {len(lines)} shows; started {names}.", flush=True)
        return self.last_run

    def public(self) -> Dict[str, Any]:
        return {"enabled": bool(self.cfg["enabled"]), "episodes_ahead": int(self.cfg["episodes_ahead"]),
                "check_hours": float(self.cfg["check_hours"]), "last_run": self.last_run or None}
