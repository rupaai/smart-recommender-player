"""
Torrent Service (services/torrent_service.py)
Consolidates search, non-blocking download, cancelation, Jellyfin file organization
(Movies & TV Shows), and download progress tracking into a single service module.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from typing import Any, Dict, List, Optional, Set

import requests
import yaml

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".webm"}

# ---------------------------------------------------------------------------
# Global in-memory download progress store
# Keyed by movie_id → status dict polled via GET /api/torrent/status/<id>
# ---------------------------------------------------------------------------
downloads_store: Dict[int, Dict[str, Any]] = {}


# ===========================================================================
# Torrent Searcher
# ===========================================================================
class TorrentSearcher:
    """Searches YTS (for movies) and Apibay (for movies and TV shows).

    Returns results with name, quality badge, size_bytes, seeds, and magnet link.
    Supports two sort modes: smallest-first (quick) or quality/seeders (best).
    """

    YTS_DOMAINS = ["https://yts.mx"]

    TRACKERS = [
        "udp://tracker.opentrackr.org:1337/announce",
        "udp://open.demonii.com:1337/announce",
        "udp://open.stealth.si:80/announce",
        "udp://tracker.torrent.eu.org:451/announce",
        "udp://explodie.org:6969/announce",
    ]

    # Minimum size thresholds (bytes)
    MIN_MOVIE_SIZE_BYTES = 500 * 1024 * 1024  # 500 MB for feature films
    MIN_TV_SIZE_BYTES = 100 * 1024 * 1024     # 100 MB for individual TV episodes

    def __init__(self, config: Optional[Dict] = None):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def search(
        self,
        query: str,
        sort_by_size: bool = True,
        media_type: str = "movie",
    ) -> List[Dict[str, Any]]:
        """Searches torrent endpoints for movies or TV shows.

        Args:
            query: Movie or TV show title search string.
            sort_by_size: If True sort smallest-first (quick downloads).
                          If False sort by quality tier then seeders (best quality).
            media_type: 'movie' or 'tv'.
        """
        results: List[Dict[str, Any]] = []

        if media_type == "tv":
            # YTS only indexes movies, so search Apibay for TV shows
            results = self._search_apibay(query, min_size=self.MIN_TV_SIZE_BYTES)
        else:
            # For movies, search YTS then fallback to Apibay
            results = self._search_yts(query)
            if not results:
                print("[Searcher] YTS returned no valid results. Trying Apibay backup...", flush=True)
                results = self._search_apibay(query, min_size=self.MIN_MOVIE_SIZE_BYTES)

        # Filter dead torrents
        active = [r for r in results if r["seeds"] >= 1]

        if sort_by_size:
            active.sort(key=lambda x: x["size_bytes"])
        else:
            # Sort by quality tier (4K > 1080p > 720p) then by seeds descending
            quality_order = {"2160p": 0, "4K": 0, "1080p": 1, "720p": 2}
            active.sort(
                key=lambda x: (quality_order.get(x.get("quality", ""), 3), -x["seeds"])
            )

        return active

    # ------------------------------------------------------------------
    # Internal: Tracker URL string
    # ------------------------------------------------------------------
    def _build_tracker_string(self) -> str:
        return "".join(f"&tr={requests.utils.quote(t)}" for t in self.TRACKERS)

    def _normalize(self, text: str) -> str:
        return re.sub(r"[^a-z0-9]", "", text.lower())

    # ------------------------------------------------------------------
    # Internal: YTS Search (Movies Only)
    # ------------------------------------------------------------------
    def _search_yts(self, query: str) -> List[Dict[str, Any]]:
        encoded_query = requests.utils.quote(query)
        tracker_str = self._build_tracker_string()
        norm_query = self._normalize(query)

        for base_url in self.YTS_DOMAINS:
            try:
                url = f"{base_url}/api/v2/list_movies.json?query_term={encoded_query}"
                response = self.session.get(url, timeout=8)

                if response.status_code != 200:
                    continue

                data = response.json()
                if data.get("status") != "ok" or data["data"].get("movie_count", 0) == 0:
                    continue

                results: List[Dict[str, Any]] = []
                for movie in data["data"]["movies"]:
                    movie_title = movie.get("title", "")
                    norm_movie_title = self._normalize(movie_title)

                    if norm_query not in norm_movie_title and norm_movie_title not in norm_query:
                        continue

                    for torrent in movie.get("torrents", []):
                        size_bytes = torrent.get("size_bytes", 0)
                        seeds = torrent.get("seeds", 0)
                        quality = torrent.get("quality", "")

                        if size_bytes < self.MIN_MOVIE_SIZE_BYTES or seeds < 1:
                            continue

                        info_hash = torrent["hash"]
                        display_name = f"{movie['title']} ({movie['year']}) [{quality}]"
                        magnet_uri = (
                            f"magnet:?xt=urn:btih:{info_hash}"
                            f"&dn={requests.utils.quote(display_name)}"
                            f"{tracker_str}"
                        )
                        results.append({
                            "name": display_name,
                            "quality": quality,
                            "size_bytes": size_bytes,
                            "size_display": _format_size(size_bytes),
                            "seeds": seeds,
                            "peers": torrent.get("peers", 0),
                            "magnet": magnet_uri,
                            "media_type": "movie",
                        })

                if results:
                    return results

            except requests.exceptions.RequestException as e:
                print(f"[Searcher] YTS request error: {e}", flush=True)
                continue

        return []

    # ------------------------------------------------------------------
    # Internal: Apibay Fallback (Movies & TV Shows)
    # ------------------------------------------------------------------
    def _search_apibay(self, query: str, min_size: int = 100 * 1024 * 1024) -> List[Dict[str, Any]]:
        try:
            url = f"https://apibay.org/q.php?q={requests.utils.quote(query)}"
            response = self.session.get(url, timeout=8)
            items = response.json() if response.status_code == 200 else []

            tracker_str = self._build_tracker_string()
            results: List[Dict[str, Any]] = []
            query_words = [w.lower() for w in re.findall(r"\w+", query) if len(w) > 2]

            for item in items:
                if item.get("id") == "0":
                    break

                name = item.get("name", "")
                name_lower = name.lower()

                if query_words and not all(word in name_lower for word in query_words):
                    continue

                size_bytes = int(item.get("size", 0))
                seeds = int(item.get("seeders", 0))

                if size_bytes < min_size or seeds < 1:
                    continue

                quality = _infer_quality(name)
                info_hash = item["info_hash"]
                magnet = (
                    f"magnet:?xt=urn:btih:{info_hash}"
                    f"&dn={requests.utils.quote(name)}"
                    f"{tracker_str}"
                )

                results.append({
                    "name": name,
                    "quality": quality,
                    "size_bytes": size_bytes,
                    "size_display": _format_size(size_bytes),
                    "seeds": seeds,
                    "peers": int(item.get("leechers", 0)),
                    "magnet": magnet,
                    "media_type": "tv" if any(k in name_lower for k in ("s0", "s1", "season", "complete")) else "movie",
                })

            return results

        except Exception as e:
            print(f"[Searcher] Apibay search failed: {e}", flush=True)
            return []


# ===========================================================================
# Torrent Downloader & Jellyfin Organizer
# ===========================================================================
class TorrentDownloader:
    """Manages non-blocking torrent downloads via libtorrent.

    Features:
    - Launching background downloads.
    - Real-time download cancellation.
    - Automated file organizing for Movies and TV Shows.
    - Post-completion Jellyfin library refresh.
    """

    def __init__(self, config: Dict[str, Any], db_manager=None):
        import libtorrent as lt

        self.lt = lt
        self.session = lt.session()
        self.db = db_manager

        # Target directory: prefer media_folders[0], fallback to torrent.download_dir
        media_folders = config.get("media_folders", [])
        if media_folders:
            self.target_dir = os.path.abspath(media_folders[0])
        else:
            torrent_cfg = config.get("torrent", {})
            self.target_dir = os.path.abspath(torrent_cfg.get("download_dir", "./downloads"))

        # Staging area for in-progress downloads (separate from target)
        torrent_cfg = config.get("torrent", {})
        self.download_dir = os.path.abspath(torrent_cfg.get("download_dir", "./downloads"))
        os.makedirs(self.download_dir, exist_ok=True)

        print(f"[Downloader] Jellyfin target: {self.target_dir}", flush=True)
        print(f"[Downloader] Download staging: {self.download_dir}", flush=True)

        min_port = torrent_cfg.get("min_port", 6881)
        settings = {
            "listen_interfaces": f"0.0.0.0:{min_port}",
            "enable_dht": True,
            "enable_lsd": True,
            "enable_upnp": True,
            "enable_natpmp": True,
            "peer_fingerprint": "SM",
            "dht_bootstrap_nodes": (
                "router.bittorrent.com:6881,"
                "router.utorrent.com:6881,"
                "dht.transmissionbt.com:6881"
            ),
        }
        if "settings" in torrent_cfg:
            settings.update(torrent_cfg["settings"])

        self.session.apply_settings(settings)
        if hasattr(self.session, "start_dht"):
            self.session.start_dht()
        self.torrent_handles: Dict[str, Any] = {}
        self.cancelled_tasks: Set[int] = set()

    # ------------------------------------------------------------------
    # Cancellation & Cleanup
    # ------------------------------------------------------------------
    async def cleanup(self, info_hash: str) -> None:
        """Removes a completed or aborted torrent from the libtorrent session."""
        try:
            h = info_hash.lower()
            if h in self.torrent_handles:
                self.session.remove_torrent(self.torrent_handles[h])
                del self.torrent_handles[h]
        except Exception as e:
            print(f"[Downloader] Cleanup error: {e}", flush=True)

    async def _cancel_cleanup(self, info_hash: str, movie_id: int) -> None:
        """Removes a cancelled torrent (with partial files) and finalizes status."""
        # Remove the torrent from the session, deleting partial data
        hash_str = str(info_hash).lower()
        handle = self.torrent_handles.get(hash_str)
        if handle is not None:
            try:
                flags = int(getattr(getattr(self.lt, "options_t", None), "delete_files", 0) or 1)
                self.session.remove_torrent(handle, flags)
            except Exception:
                pass
        await self.cleanup(info_hash)
        self._purge_partial_files(movie_id)

        if movie_id in downloads_store:
            downloads_store[movie_id]["status"] = "cancelled"
            downloads_store[movie_id]["error"] = "Download cancelled by user"
            downloads_store[movie_id]["progress"] = 0.0
        self.cancelled_tasks.discard(movie_id)
        print(f"[Downloader] Cancelled & cleaned up: ID {movie_id}", flush=True)

    def _purge_partial_files(self, movie_id: int) -> None:
        """Best-effort removal of partial files left in the staging directory."""
        try:
            torrent_name = (downloads_store.get(movie_id) or {}).get("name") or ""
            if not torrent_name or not os.path.isdir(self.download_dir):
                return
            prefix = torrent_name[:40]
            for entry in os.listdir(self.download_dir):
                if not entry.startswith(prefix):
                    continue
                entry_path = os.path.join(self.download_dir, entry)
                if os.path.isdir(entry_path):
                    shutil.rmtree(entry_path, ignore_errors=True)
                else:
                    os.remove(entry_path)
        except Exception as e:
            print(f"[Downloader] Partial-file purge error: {e}", flush=True)

    async def cancel(self, movie_id: int) -> bool:
        """Cancels an active download task for a given movie/show ID."""
        print(f"[Downloader] Cancellation requested for ID: {movie_id}", flush=True)
        self.cancelled_tasks.add(movie_id)

        status_info = downloads_store.get(movie_id)
        if not status_info:
            return False

        # Flag cancellation immediately for instant UI feedback.
        # The download loop performs the torrent cleanup and partial-file
        # purge itself, so we never race handle.status() on a removed torrent.
        downloads_store[movie_id]["status"] = "cancelled"
        downloads_store[movie_id]["error"] = "Download cancelled by user"
        return True

        return False

    # ------------------------------------------------------------------
    # Automated File Organization (Jellyfin Specification)
    # ------------------------------------------------------------------
    def _organize_for_jellyfin(
        self,
        movie_id: int,
        title: str,
        media_type: str = "movie",
    ) -> str:
        """Extracts largest video file and places it into clean Jellyfin directory structure.

        - Movie:
          <target_dir>/<Title> (<Year>)/<Title> (<Year>).<ext>

        - TV Show:
          <target_dir>/<Show Title> (<Year>)/Season <XX>/<Show Title> - S<XX>E<YY>.<ext>
        """
        clean_title = re.sub(r'[\\/*?:"<>|]', "", title).strip()

        year = None
        if self.db:
            item = self.db.get_item_by_id(movie_id)
            if item:
                year = item.get("release_year")
                if item.get("media_type"):
                    media_type = item["media_type"]

        # 1. Locate largest video file in staging download directory
        largest_file = None
        max_size = 0

        for root, _, files in os.walk(self.download_dir):
            if os.path.abspath(root).startswith(os.path.abspath(self.target_dir)):
                continue
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext in VIDEO_EXTENSIONS:
                    full = os.path.join(root, f)
                    sz = os.path.getsize(full)
                    if sz > max_size:
                        max_size = sz
                        largest_file = full

        if not largest_file:
            print(f"[Downloader] No video file found to organize for '{title}'.", flush=True)
            return ""

        ext = os.path.splitext(largest_file)[1].lower()
        largest_file_name = os.path.basename(largest_file)

        # 2. Determine target path based on media type (Movie vs TV Show)
        if media_type == "tv":
            # Extract season and episode from filename or title
            tv_match = re.search(
                r"(?:s([0-9]{1,2})e([0-9]{1,2})|([0-9]{1,2})x([0-9]{1,2}))",
                largest_file_name,
                re.IGNORECASE,
            )
            if not tv_match:
                tv_match = re.search(
                    r"(?:s([0-9]{1,2})e([0-9]{1,2})|([0-9]{1,2})x([0-9]{1,2}))",
                    title,
                    re.IGNORECASE,
                )

            if tv_match:
                if tv_match.group(1) is not None:
                    season = int(tv_match.group(1))
                    episode = int(tv_match.group(2))
                else:
                    season = int(tv_match.group(3))
                    episode = int(tv_match.group(4))
            else:
                season = 1
                episode = 1

            show_folder_name = f"{clean_title} ({year})" if year else clean_title
            season_folder_name = f"Season {season:02d}"
            target_file_name = f"{clean_title} - S{season:02d}E{episode:02d}{ext}"

            target_show_dir = os.path.join(self.target_dir, show_folder_name, season_folder_name)
            os.makedirs(target_show_dir, exist_ok=True)
            target_file_path = os.path.join(target_show_dir, target_file_name)

            shutil.move(largest_file, target_file_path)
            print(f"[Downloader] Organized TV episode: {target_file_path}", flush=True)

            if self.db:
                self.db.register_source_file(
                    file_path=target_file_path,
                    parsed_title=clean_title,
                    parsed_year=year,
                    media_type="tv",
                    season_number=season,
                    episode_number=episode,
                    file_size=max_size,
                    source_type="local",
                )

        else:
            # Movie format
            folder_name = f"{clean_title} ({year})" if year else clean_title
            target_dir = os.path.join(self.target_dir, folder_name)
            os.makedirs(target_dir, exist_ok=True)
            target_file_path = os.path.join(target_dir, f"{folder_name}{ext}")

            shutil.move(largest_file, target_file_path)
            print(f"[Downloader] Organized Movie: {target_file_path}", flush=True)

            if self.db:
                self.db.register_source_file(
                    file_path=target_file_path,
                    parsed_title=clean_title,
                    parsed_year=year,
                    media_type="movie",
                    file_size=max_size,
                    source_type="local",
                )

        # 3. Trigger Jellyfin library refresh
        try:
            from jellyfin import JellyfinRefresher
            refresher = JellyfinRefresher()
            if refresher.refresh_library():
                print("[Downloader] Jellyfin library refresh triggered successfully.", flush=True)
            else:
                print("[Downloader] Jellyfin refresh skipped (check .env).", flush=True)
        except Exception as e:
            print(f"[Downloader] Jellyfin refresh error: {e}", flush=True)

        return target_file_path

    # ------------------------------------------------------------------
    # Non-blocking Download Task
    # ------------------------------------------------------------------
    async def download(
        self,
        magnet_link: str,
        movie_id: int,
        movie_title: str,
        media_type: str = "movie",
    ) -> None:
        """Non-blocking download task. Updates downloads_store every second."""
        lt = self.lt
        print(f"\n[Downloader] Starting: '{movie_title}' [ID: {movie_id}, {media_type.upper()}]", flush=True)

        # Reset cancellation flag
        if movie_id in self.cancelled_tasks:
            self.cancelled_tasks.remove(movie_id)

        try:
            params = lt.parse_magnet_uri(magnet_link)
            params.save_path = self.download_dir

            handle = self.session.add_torrent(params)
            info_hash = str(handle.info_hash()).lower()
            self.torrent_handles[info_hash] = handle

            downloads_store[movie_id] = {
                "hash": info_hash,
                "movie_id": movie_id,
                "name": movie_title,
                "media_type": media_type,
                "progress": 0.0,
                "status": "connecting",
                "download_speed": 0,
                "total_size": 0,
                "downloaded": 0,
                "error": None,
            }

            min_threshold = self.TorrentSearcher_min_size(media_type)

            while True:
                await asyncio.sleep(1)

                # Check if cancelled by user (loop owns cleanup to avoid races)
                if movie_id in self.cancelled_tasks:
                    print(f"[Downloader] Aborting cancelled task: '{movie_title}'", flush=True)
                    await self._cancel_cleanup(info_hash, movie_id)
                    return

                # Poll torrent status safely — a removed/invalid handle must
                # never clobber the 'cancelled' status set by cancel().
                try:
                    status = handle.status()
                    has_meta = handle.has_metadata()
                    state_str = str(status.state)
                except Exception:
                    if movie_id in self.cancelled_tasks:
                        await self._cancel_cleanup(info_hash, movie_id)
                        return
                    raise

                total_wanted = status.total_wanted if has_meta else 0
                total_done = status.total_done if has_meta else 0

                if has_meta and total_wanted > 0:
                    progress_pct = round((total_done / total_wanted) * 100, 2)
                    torrent_name = handle.name()
                else:
                    progress_pct = 0.0
                    torrent_name = movie_title

                # Abort fake / shortcut torrents
                if has_meta and total_wanted < min_threshold:
                    downloads_store[movie_id] = {
                        "movie_id": movie_id,
                        "status": "error",
                        "error": "File size too small (fake torrent link).",
                    }
                    await self.cleanup(info_hash)
                    return

                # Bail out before clobbering a just-issued cancel request
                if movie_id in self.cancelled_tasks:
                    await self._cancel_cleanup(info_hash, movie_id)
                    return

                downloads_store[movie_id] = {
                    "hash": info_hash,
                    "movie_id": movie_id,
                    "name": torrent_name,
                    "media_type": media_type,
                    "progress": progress_pct,
                    "status": state_str,
                    "download_speed": status.download_rate,
                    "total_size": total_wanted,
                    "downloaded": total_done,
                    "error": None,
                }

                is_complete = (
                    has_meta
                    and total_wanted > 0
                    and total_done >= total_wanted
                    and state_str in ("seeding", "finished")
                )

                if is_complete:
                    downloads_store[movie_id]["progress"] = 100.0
                    downloads_store[movie_id]["status"] = "organizing"

                    self._organize_for_jellyfin(movie_id, movie_title, media_type)

                    downloads_store[movie_id]["status"] = "completed"
                    await self.cleanup(info_hash)
                    return

        except Exception as e:
            if movie_id in self.cancelled_tasks:
                # Cancelled downloads must not surface as errors
                await self._cancel_cleanup(info_hash, movie_id)
                return
            print(f"[Downloader] Error: {e}", flush=True)
            downloads_store[movie_id] = {
                "movie_id": movie_id,
                "status": "error",
                "error": str(e),
            }

    @staticmethod
    def TorrentSearcher_min_size(media_type: str) -> int:
        if media_type == "tv":
            return 80 * 1024 * 1024  # 80 MB
        return 450 * 1024 * 1024      # 450 MB


# ===========================================================================
# Helpers
# ===========================================================================
def _format_size(size_bytes: int) -> str:
    """Returns a human-readable file size string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    if size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def _infer_quality(name: str) -> str:
    """Infers quality tag from a torrent filename."""
    lower = name.lower()
    if "2160p" in lower or "4k" in lower or "uhd" in lower:
        return "2160p"
    if "1080p" in lower:
        return "1080p"
    if "720p" in lower:
        return "720p"
    if "480p" in lower:
        return "480p"
    return "HD"
