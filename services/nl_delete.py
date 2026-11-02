"""
Delete from the server (services/nl_delete.py): the "Delete from server" button on title and show pages.

Removes the files of one movie, a whole show, or one season of a show, for everyone. It can't be undone,
so the pages show `preview()` first (what goes and how much space it frees) and the server only deletes
when the request says `confirm: true`.

Only files under the Movies and Shows folders are touched (the same rule and code as the watched cleanup,
services/media_cleanup.py): the video, the files beside it that share its name (subtitles, .nfo), and
folders left empty. The catalogue forgets the files, so the title leaves Movies, Shows and the home rows.
Ratings and watch history stay, so the recommender still learns from them. Then Jellyfin rescans.
"""

from __future__ import annotations

import os
import threading
from typing import Any, Callable, Dict, List, Optional, Sequence

from services.media_cleanup import _inside, delete_files, related_files


def size_label(n: int) -> str:
    gb = n / 1024 ** 3
    if gb >= 1:
        return f"{gb:.1f} GB"
    return f"{max(1, round(n / 1024 ** 2))} MB" if n else "0 MB"


class LibraryDelete:
    """`db` is the DatabaseManager, `roots` the Movies and Shows folders, `refresh` asks Jellyfin to rescan."""

    def __init__(self, db: Any, roots: Sequence[str], refresh: Callable[[], None]):
        self.db = db
        self.roots = [os.path.abspath(r) for r in roots]
        self.refresh = refresh
        self._lock = threading.Lock()

    def _pick(self, item_id: int, season: Optional[int]) -> Optional[Dict[str, Any]]:
        item = self.db.get_nl_title(item_id)
        if not item:
            return None
        sources = item["sources"]
        is_show = item.get("media_type") == "tv"
        if is_show and season is not None:
            sources = [s for s in sources if s.get("season_number") == season]
            if not sources:
                return None
        name = item.get("title") or "This title"
        if is_show and season is not None:
            what = f"{name}, season {season}" if season else f"{name}, specials"
        elif is_show:
            seasons = {s.get("season_number") for s in sources}
            what = f"{name}, all {len(seasons)} seasons" if len(seasons) > 1 else f"{name}, every episode"
        else:
            what = f"{name} ({item['release_year']})" if item.get("release_year") else name
        return {"item": item, "sources": sources, "what": what, "is_show": is_show}

    def preview(self, item_id: int, season: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """What the button would delete: a label, the number of videos and the space freed. None = unknown."""
        p = self._pick(item_id, season)
        if p is None:
            return None
        size = 0
        for s in p["sources"]:
            for full, _known in related_files(s["file_path"], self.roots):
                try:
                    size += os.path.getsize(full)
                except OSError:
                    pass
        videos = len(p["sources"])
        return {
            "item_id": item_id, "season": season, "what": p["what"], "videos": videos,
            "episodes": videos if p["is_show"] else 0, "bytes": size, "size": size_label(size),
            "outside": sum(1 for s in p["sources"] if not _inside(s["file_path"], self.roots)),
        }

    def delete(self, item_id: int, season: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Deletes the files and forgets them in the catalogue. A file that is already gone from the disk is
        forgotten too. A file outside the Movies and Shows folders is never touched and is counted as kept."""
        with self._lock:
            p = self._pick(item_id, season)
            if p is None:
                return None
            gone: List[str] = []
            freed = 0
            kept = 0
            errors = 0
            for s in p["sources"]:
                path = s["file_path"]
                if not _inside(path, self.roots):
                    kept += 1
                    continue
                if not os.path.lexists(path):
                    gone.append(path)                      # deleted by hand earlier: just forget it
                    continue
                sizes = 0
                for full, _known in related_files(path, self.roots):
                    try:
                        sizes += os.path.getsize(full)
                    except OSError:
                        pass
                try:
                    removed = delete_files(path, self.roots)
                except OSError as e:
                    print(f"[Delete] could not delete a file of {p['what']}: {type(e).__name__}", flush=True)
                    errors += 1
                    continue
                if removed:
                    gone.extend(removed)
                    freed += sizes
                else:
                    kept += 1                              # a link or not a plain file: left alone
            if gone:
                self.db.remove_source_files(gone)
                try:
                    self.refresh()
                except Exception as e:                     # Jellyfin away: it notices on its next scan
                    print(f"[Delete] Jellyfin rescan failed: {e}", flush=True)
            left = self.db.get_nl_title(item_id) is not None
            print(f"[Delete] {p['what']}: freed {size_label(freed)}" + (f", kept {kept}" if kept else "")
                  + (f", {errors} failed" if errors else ""), flush=True)
            return {"what": p["what"], "freed_bytes": freed, "freed": size_label(freed), "kept": kept,
                    "errors": errors, "title_left": left}
