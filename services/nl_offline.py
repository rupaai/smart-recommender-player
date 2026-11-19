"""
Save for offline (services/nl_offline.py): the "Save for offline" button on title and show pages.

For trips where streaming from the server would be slow. The phone or laptop downloads the original file
(no conversion, the same quality as at home) and its subtitle files, and plays them later without the
server, in a video app such as VLC or IINA (both free). Saved copies get plain names, "Title (Year).mkv"
or "Show - S01E02.mkv", and each subtitle file gets the same name plus its language part
("Title (Year).en.srt"), so video apps load the subtitles by themselves. Subtitles inside the video file
come along anyway.

Only files the catalogue knows, inside the Movies and Shows folders, are ever sent (the same folder rule
and file matching as "Delete from server", services/nl_delete.py). Downloads use ranges, so a browser can
pick up a broken download where it stopped.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from services.media_cleanup import _inside, related_files
from services.nl_delete import size_label

SUBTITLE_EXTS = (".srt", ".vtt", ".ass", ".ssa", ".sub", ".idx", ".sup", ".smi")
_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


def safe_name(text: str) -> str:
    """A file name every phone and laptop accepts: no slashes, colons or other reserved signs."""
    name = _BAD.sub(" ", text or "").strip(" .")
    return re.sub(r"\s+", " ", name)[:150] or "Video"


def episode_label(season: Optional[int], episode: Optional[int]) -> str:
    if season is None or episode is None:
        return ""
    return f"S{int(season):02d}E{int(episode):02d}"


class OfflineCopies:
    """`db` is the DatabaseManager, `roots` the Movies and Shows folders."""

    def __init__(self, db: Any, roots: Sequence[str]):
        self.db = db
        self.roots = [os.path.abspath(r) for r in roots]

    # --- what can be saved -------------------------------------------------------------------------

    def _video_name(self, item: Dict[str, Any], source: Dict[str, Any]) -> str:
        ext = os.path.splitext(source["file_path"])[1].lower() or ".mkv"
        title = item.get("title") or "Video"
        if item.get("media_type") == "tv":
            label = episode_label(source.get("season_number"), source.get("episode_number"))
            if not label:                                   # no episode number: keep the file's own name
                return safe_name(os.path.splitext(os.path.basename(source["file_path"]))[0]) + ext
            return safe_name(f"{title} - {label}") + ext
        year = item.get("release_year")
        return safe_name(f"{title} ({year})" if year else title) + ext

    def _parts(self, source: Dict[str, Any]) -> Optional[Tuple[str, List[str]]]:
        """(video's real path, its subtitle files' real paths) or None when it can't be sent."""
        files = related_files(source["file_path"], self.roots)
        if not files:
            return None
        video = os.path.realpath(source["file_path"])
        subs = [full for full, _ in files if full != video and full.lower().endswith(SUBTITLE_EXTS)]
        return video, subs

    def _row(self, item: Dict[str, Any], source: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        parts = self._parts(source)
        if parts is None:
            return None
        video, subs = parts
        name = self._video_name(item, source)
        stem = os.path.splitext(name)[0]
        old_stem = os.path.splitext(os.path.basename(video))[0]
        sid = source["id"]
        try:
            size = os.path.getsize(video)
        except OSError:
            return None
        subtitles = []
        for n, full in enumerate(subs):
            try:
                sub_size = os.path.getsize(full)
            except OSError:
                continue
            tail = os.path.basename(full)[len(old_stem):]   # ".en.srt", ".forced.en.srt", ...
            subtitles.append({"name": stem + tail, "bytes": sub_size, "href": f"/api/nl/offline/subtitle/{sid}/{n}"})
        return {
            "source_id": sid,
            "label": episode_label(source.get("season_number"), source.get("episode_number")),
            "season": source.get("season_number"), "episode": source.get("episode_number"),
            "name": name, "bytes": size, "size": size_label(size),
            "href": f"/api/nl/offline/file/{sid}", "subtitles": subtitles,
        }

    def list(self, item_id: int, season: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """The files "Save for offline" offers for a movie, a whole show or one season. None = unknown."""
        item = self.db.get_nl_title(item_id)
        if not item:
            return None
        is_show = item.get("media_type") == "tv"
        sources = item["sources"]
        if is_show and season is not None:
            sources = [s for s in sources if s.get("season_number") == season]
            if not sources:
                return None
        rows = [r for r in (self._row(item, s) for s in sources) if r]
        total = sum(r["bytes"] + sum(x["bytes"] for x in r["subtitles"]) for r in rows)
        name = item.get("title") or "This title"
        if is_show and season is not None:
            what = f"{name}, season {season}" if season else f"{name}, specials"
        elif is_show:
            what = name
        else:
            what = f"{name} ({item['release_year']})" if item.get("release_year") else name
        return {"item_id": item_id, "season": season, "what": what, "is_show": is_show,
                "files": rows, "bytes": total, "size": size_label(total)}

    # --- sending one file ---------------------------------------------------------------------------

    def _source(self, source_id: int) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
        source = self.db.get_source(source_id)
        if not source:
            return None
        item = self.db.get_nl_title(source["media_item_id"])
        if not item or not _inside(source["file_path"], self.roots):
            return None
        return item, source

    def video(self, source_id: int) -> Optional[Tuple[str, str]]:
        """(real path, download name) of a catalogue video, or None (unknown, gone, or outside the folders)."""
        found = self._source(source_id)
        if not found:
            return None
        item, source = found
        parts = self._parts(source)
        if not parts:
            return None
        return parts[0], self._video_name(item, source)

    def subtitle(self, source_id: int, n: int) -> Optional[Tuple[str, str]]:
        """(real path, download name) of the video's n-th subtitle file, in the order list() gives."""
        found = self._source(source_id)
        if not found:
            return None
        item, source = found
        row = self._row(item, source)
        parts = self._parts(source)
        if not row or not parts or not 0 <= n < len(parts[1]):
            return None
        names = [s for s in row["subtitles"] if s["href"].endswith(f"/{n}")]
        if not names:
            return None
        return parts[1][n], names[0]["name"]
