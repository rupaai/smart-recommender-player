"""
Northern Lights watched cleanup (services/media_cleanup.py)

Deletes a movie or an episode when:
  * every person (northern_lights.people, or the one JELLYFIN_USER_ID viewer) has it marked watched in Jellyfin,
  * the last of them watched it more than `days_after_watched` days ago,
  * nobody marked it, or its show, a favorite.

Only files under the Movies and Shows folders are ever touched. The video goes together with the files next
to it that share its name (subtitles, .nfo), and folders left empty are removed. Then Jellyfin rescans.

    media_cleanup:
      enabled: true
      days_after_watched: 7
      dry_run: true       # only list what would go (admin page and log); false = delete

The choosing (`pick`) and the deleting (`delete_files`) are plain functions, tested without Jellyfin.
"""

from __future__ import annotations

import os
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set

DEFAULT_DAYS = 7
FIRST_RUN_DELAY = 10 * 60        # seconds after start, so a restart loop never deletes anything in a hurry
RUN_EVERY = 24 * 60 * 60
PLAYED_LIMIT = 5000


def settings(config: Dict[str, Any]) -> Dict[str, Any]:
    raw = config.get("media_cleanup") or {}
    try:
        days = float(raw.get("days_after_watched", DEFAULT_DAYS))
    except (TypeError, ValueError):
        days = DEFAULT_DAYS
    return {
        "enabled": bool(raw.get("enabled", False)),
        "days": max(1.0, days),             # never "delete the moment it ends"
        "dry_run": bool(raw.get("dry_run", True)),
    }


def parse_date(value: Any) -> Optional[datetime]:
    """Jellyfin's dates look like 2026-10-13T20:00:00.1234567Z (7 fraction digits, which Python refuses)."""
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    text = re.sub(r"(\.\d{6})\d+", r"\1", text)
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def label(item: Dict[str, Any]) -> str:
    if item.get("Type") == "Episode":
        s, e = item.get("ParentIndexNumber"), item.get("IndexNumber")
        tag = f" S{int(s):02d}E{int(e):02d}" if isinstance(s, int) and isinstance(e, int) else ""
        return f"{item.get('SeriesName') or 'Show'}{tag}"
    year = item.get("ProductionYear")
    return f"{item.get('Name') or 'Movie'}" + (f" ({year})" if year else "")


def pick(
    played_per_person: Sequence[Sequence[Dict[str, Any]]],
    favorite_shows: Set[str],
    now: datetime,
    days: float,
) -> List[Dict[str, Any]]:
    """The items to delete. `played_per_person` holds, for each person, the Jellyfin items they have watched
    (with UserData). An item counts only when it is in every person's list."""
    if not played_per_person:
        return []
    per_person = [{str(it.get("Id")): it for it in items if it.get("Id")} for items in played_per_person]
    out: List[Dict[str, Any]] = []
    for item_id, item in per_person[0].items():
        copies = [p.get(item_id) for p in per_person]
        if any(c is None for c in copies):
            continue                                       # someone hasn't watched it
        data = [c.get("UserData") or {} for c in copies]
        if not all(d.get("Played") for d in data):
            continue
        if any(d.get("IsFavorite") for d in data) or str(item.get("SeriesId") or "") in favorite_shows:
            continue
        dates = [parse_date(d.get("LastPlayedDate")) for d in data]
        if any(d is None for d in dates):
            continue                                       # no date, so 7 days can't be counted: keep it
        last = max(dates)
        if now - last < timedelta(days=days) or not item.get("Path"):
            continue
        out.append({"id": item_id, "name": label(item), "path": item["Path"], "last_watched": last.isoformat()})
    out.sort(key=lambda c: c["last_watched"])
    return out


def _inside(path: str, roots: Iterable[str]) -> Optional[str]:
    """The root that `path` is inside (never the root itself), or None."""
    real = os.path.realpath(path)
    for root in roots:
        r = os.path.realpath(root)
        if real.startswith(r + os.sep):
            return r
    return None


def delete_files(video: str, roots: Sequence[str]) -> List[str]:
    """Deletes the video and the files beside it that share its name, then the folders left empty, up to
    (not including) the Movies or Shows folder. Refuses anything outside those folders. Returns what went."""
    root = _inside(video, roots)
    if not root or not os.path.isfile(video) or os.path.islink(video):
        return []
    folder = os.path.dirname(os.path.realpath(video))
    # Report each file under the path Jellyfin and the catalogue know it by, not its resolved one: a
    # symlinked media folder (or macOS's /var -> /private/var) would otherwise leave catalogue rows behind.
    known_folder = os.path.dirname(video)
    stem = os.path.splitext(os.path.basename(video))[0]
    removed: List[str] = []
    for name in sorted(os.listdir(folder)):
        full = os.path.join(folder, name)
        if (name == os.path.basename(video) or name.startswith(stem + ".")) and os.path.isfile(full) \
                and not os.path.islink(full):
            os.remove(full)
            removed.append(os.path.join(known_folder, name))
    while folder != root and folder.startswith(root + os.sep):
        try:
            os.rmdir(folder)                               # only succeeds when empty
        except OSError:
            break
        folder = os.path.dirname(folder)
    return removed


class MediaCleanup:
    """Runs the cleanup against Jellyfin. `playback` is JellyfinPlayback (its _request and resolve_user);
    `users` returns the configured Jellyfin users (an empty list = the default JELLYFIN_USER_ID user)."""

    def __init__(self, config: Dict[str, Any], playback: Any, users: Callable[[], List[str]], db: Any,
                 roots: Sequence[str], refresh: Callable[[], None]):
        self.config = config
        self.playback = playback
        self.users = users
        self.db = db
        self.roots = [os.path.abspath(r) for r in roots]
        self.refresh = refresh
        self.last: Optional[Dict[str, Any]] = None
        self._lock = threading.Lock()

    def _user_ids(self) -> List[str]:
        raw = self.users()
        if not raw:
            return [self.playback._user_id()]
        return [self.playback.resolve_user(u, "northern_lights.people jellyfin_user_id") for u in raw]

    def _items(self, uid: str, filters: str, types: str) -> List[Dict[str, Any]]:
        data = self.playback._request("GET", f"/Users/{uid}/Items", params={
            "Filters": filters, "IncludeItemTypes": types, "Recursive": "true", "Fields": "Path",
            "EnableUserData": "true", "Limit": PLAYED_LIMIT,
        }) or {}
        return list(data.get("Items") or [])

    def public(self) -> Dict[str, Any]:
        s = settings(self.config)
        return {"enabled": s["enabled"], "dry_run": s["dry_run"], "days": s["days"], "last": self.last}

    def run_once(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        s = settings(self.config)
        now = now or datetime.now(timezone.utc)
        report: Dict[str, Any] = {"ran_at": now.isoformat(), "dry_run": s["dry_run"], "items": [], "error": None}
        with self._lock:
            try:
                if not all(os.path.isdir(r) for r in self.roots):
                    raise RuntimeError("the Movies or Shows folder is missing")
                uids = self._user_ids()
                played = [self._items(u, "IsPlayed", "Movie,Episode") for u in uids]
                favorite_shows = {str(it.get("Id")) for u in uids for it in self._items(u, "IsFavorite", "Series")}
                chosen = pick(played, favorite_shows, now, s["days"])
                gone: List[str] = []
                for c in chosen:
                    if not _inside(c["path"], self.roots):
                        continue                           # Jellyfin points somewhere else: never touch it
                    entry = {"name": c["name"], "last_watched": c["last_watched"], "deleted": False}
                    if not s["dry_run"]:
                        try:
                            removed = delete_files(c["path"], self.roots)
                            entry["deleted"] = bool(removed)
                            gone.extend(removed)
                        except OSError as e:
                            entry["error"] = type(e).__name__
                    report["items"].append(entry)
                if gone:
                    if self.db is not None:
                        self.db.remove_source_files(gone)
                    self.refresh()
            except Exception as e:                         # Jellyfin down, bad config: try again tomorrow
                report["error"] = str(e)
        self.last = report
        verb = "would delete" if s["dry_run"] else "deleted"
        names = ", ".join(i["name"] for i in report["items"]) or "nothing"
        print(f"[Cleanup] {verb}: {names}" + (f" (error: {report['error']})" if report["error"] else ""), flush=True)
        return report
