"""
Jellyfin Service (services/jellyfin_service.py)
Dedicated typed library management for Movies & TV Shows:
- Idempotent creation of "Movies" (movies) and "TV Shows" (tvshows) libraries
  with realtime monitoring so new downloads appear within seconds
- Refresh + verified visibility polling (no more fire-and-forget refreshes)
- Diagnostics for the GET /api/jellyfin/status endpoint

Additive only: this service never deletes or renames existing libraries.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv


class JellyfinService:
    """Manages the connection and libraries of the local Jellyfin server."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        load_dotenv()
        self.config = config or {}
        self.base_url = (os.getenv("JELLYFIN_URL") or "").rstrip("/")
        self.api_key = os.getenv("JELLYFIN_API_KEY")
        self.user_id = os.getenv("JELLYFIN_USER_ID")
        self.session = requests.Session()
        if self.api_key:
            self.session.headers.update(
                {"X-Emby-Token": self.api_key, "Accept": "application/json"}
            )

        # Library roots: prefer explicit jellyfin_library config, fall back to
        # legacy single media_folders[0] so existing setups keep working.
        lib_cfg = self.config.get("jellyfin_library", {}) or {}
        media_folders = self.config.get("media_folders", []) or []
        self.movie_dir = os.path.abspath(
            lib_cfg.get("movie_dir")
            or (media_folders[0] if media_folders else "./media/Movies")
        )
        self.tv_dir = os.path.abspath(lib_cfg.get("tv_dir") or self.movie_dir)
        self.movies_library_name = lib_cfg.get("movies_library_name", "Movies")
        self.tv_library_name = lib_cfg.get("tv_library_name", "TV Shows")
        self.realtime_monitor = bool(lib_cfg.get("realtime_monitor", True))

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    def get_virtual_folders(self) -> Optional[List[Dict[str, Any]]]:
        """Returns the raw Jellyfin virtual-folder (library) list, or None."""
        if not self.configured:
            return None
        try:
            resp = self.session.get(
                f"{self.base_url}/Library/VirtualFolders", timeout=5
            )
            if resp.status_code == 401:
                print(
                    "[Jellyfin] Unauthorized (401): JELLYFIN_API_KEY is invalid or expired — "
                    "update it in .env. No library changes were made.",
                    flush=True,
                )
                return None
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            print(f"[Jellyfin] VirtualFolders error: {e}", flush=True)
            return None

    @staticmethod
    def _library_has_path(lib: Dict[str, Any], path: str) -> bool:
        locations = [os.path.abspath(p) for p in (lib.get("Locations") or [])]
        return path in locations

    # ------------------------------------------------------------------
    # Library setup (idempotent, additive-only)
    # ------------------------------------------------------------------
    def ensure_libraries(self) -> Dict[str, List[str]]:
        """Ensures dedicated typed 'Movies' and 'TV Shows' libraries exist.

        Never deletes or renames existing libraries or paths:
        - Typed library exists and already contains the path → skipped
        - Named library exists but the path is missing       → path added
          (if the add-path endpoint shape fails on this Jellyfin version,
          a clear warning is logged and setup continues — nothing is touched)
        - No library with that name                          → created

        Returns {"created": [...], "updated": [...], "skipped": [...]}.
        """
        os.makedirs(self.movie_dir, exist_ok=True)
        os.makedirs(self.tv_dir, exist_ok=True)

        result: Dict[str, List[str]] = {"created": [], "updated": [], "skipped": []}
        if not self.configured:
            print(
                "[Jellyfin] Library setup skipped: JELLYFIN_URL / JELLYFIN_API_KEY not set in .env.",
                flush=True,
            )
            return result

        libs = self.get_virtual_folders()
        if libs is None:
            print("[Jellyfin] Library setup skipped: server unreachable. Nothing was changed.", flush=True)
            return result

        wanted = [
            (self.movies_library_name, "movies", self.movie_dir),
            (self.tv_library_name, "tvshows", self.tv_dir),
        ]

        for name, collection_type, directory in wanted:
            existing = next(
                (lib for lib in libs if (lib.get("Name") or "").lower() == name.lower()),
                None,
            )

            if existing and self._library_has_path(existing, directory):
                result["skipped"].append(f"{name} → {directory}")
                continue

            if existing:
                # Library exists but is missing this root: add the path.
                resp = None
                try:
                    resp = self.session.post(
                        f"{self.base_url}/Library/VirtualFolders/Paths",
                        params={"name": name},
                        json={"Path": directory},
                        timeout=10,
                    )
                except Exception as e:
                    print(
                        f"[Jellyfin] WARNING: could not add '{directory}' to library '{name}': {e}. "
                        "Please add it manually in the dashboard — nothing was deleted.",
                        flush=True,
                    )
                if resp is not None and resp.status_code in (200, 204):
                    result["updated"].append(f"{name} + {directory}")
                    print(f"[Jellyfin] Added '{directory}' to existing library '{name}'.", flush=True)
                elif resp is not None:
                    print(
                        f"[Jellyfin] WARNING: add-path endpoint returned HTTP {resp.status_code} for "
                        f"library '{name}'. This Jellyfin version may use a different endpoint shape — "
                        f"please add '{directory}' to '{name}' manually in the dashboard. "
                        "Nothing was deleted or renamed.",
                        flush=True,
                    )
                continue

            # No such library yet: create a fresh typed one.
            try:
                resp = self.session.post(
                    f"{self.base_url}/Library/VirtualFolders",
                    params={
                        "name": name,
                        "collectionType": collection_type,
                        "refreshLibrary": "false",
                    },
                    json={
                        "EnableRealtimeMonitor": self.realtime_monitor,
                        "LibraryOptions": {"EnableRealtimeMonitor": self.realtime_monitor},
                        "PathInfos": [{"Path": directory}],
                    },
                    timeout=10,
                )
                if resp.status_code in (200, 204):
                    result["created"].append(f"{name} → {directory}")
                    print(
                        f"[Jellyfin] Created library '{name}' ({collection_type}) at '{directory}'.",
                        flush=True,
                    )
                else:
                    print(
                        f"[Jellyfin] WARNING: create-library call returned HTTP {resp.status_code} for "
                        f"'{name}'. Please create a {collection_type} library at '{directory}' manually "
                        "in the dashboard. Nothing was deleted.",
                        flush=True,
                    )
            except Exception as e:
                print(
                    f"[Jellyfin] WARNING: could not create library '{name}': {e}. "
                    "Please create it manually in the dashboard — nothing was deleted.",
                    flush=True,
                )

        return result

    # ------------------------------------------------------------------
    # Refresh + verified visibility
    # ------------------------------------------------------------------
    def refresh_and_verify(
        self,
        title: str,
        media_type: str = "movie",
        timeout_s: int = 120,
    ) -> Dict[str, Any]:
        """Triggers a library scan, then polls until the title is visible.

        Returns {"visible": bool, "hint": Optional[str]}.
        """
        if not self.configured:
            return {
                "visible": False,
                "hint": "Jellyfin not configured (set JELLYFIN_URL and JELLYFIN_API_KEY in .env)",
            }

        try:
            resp = self.session.post(f"{self.base_url}/Library/Refresh", timeout=10)
            if resp.status_code in (200, 204):
                print("[Jellyfin] Library refresh triggered.", flush=True)
            else:
                print(f"[Jellyfin] Refresh endpoint returned HTTP {resp.status_code}.", flush=True)
        except Exception as e:
            print(f"[Jellyfin] Refresh trigger failed: {e}", flush=True)

        include_types = "Series" if media_type == "tv" else "Movie"
        deadline = time.time() + max(5, timeout_s)
        while time.time() < deadline:
            time.sleep(5)
            try:
                resp = self.session.get(
                    f"{self.base_url}/Items",
                    params={
                        "searchTerm": title,
                        "Recursive": "true",
                        "IncludeItemTypes": include_types,
                        "Limit": 1,
                    },
                    timeout=10,
                )
                if resp.status_code == 200:
                    items = (resp.json() or {}).get("Items") or []
                    if items:
                        return {"visible": True, "hint": None}
            except Exception:
                continue

        return {"visible": False, "hint": self._visibility_hint(media_type)}

    def _visibility_hint(self, media_type: str) -> str:
        """Builds a human-readable hint for why the title is not visible yet."""
        hints: List[str] = []
        libs = self.get_virtual_folders()
        target = self.tv_dir if media_type == "tv" else self.movie_dir
        if libs is not None:
            locations: List[List[str]] = [
                [os.path.abspath(p) for p in (lib.get("Locations") or [])] for lib in libs
            ]
            if not any(target in locs for locs in locations):
                hints.append(
                    f"no Jellyfin library contains the target folder '{target}' — start the app once "
                    "so ensure_libraries() can add it, or add it in the dashboard"
                )
            typed = "tvshows" if media_type == "tv" else "movies"
            if not any(lib.get("CollectionType") == typed for lib in libs):
                hints.append(
                    f"no dedicated '{typed}' typed library found — Jellyfin works best with separate "
                    "Movies / TV Shows libraries"
                )
            if not any(
                (lib.get("LibraryOptions") or {}).get("EnableRealtimeMonitor") for lib in libs
            ):
                hints.append(
                    "realtime monitoring appears disabled — enable it per library in the dashboard "
                    "or rely on periodic scans"
                )
        hints.append(
            "4K/HEVC releases can take longer to appear and may need transcoding enabled "
            "for your TV client"
        )
        return "; ".join(hints)

    # ------------------------------------------------------------------
    # Diagnostics (GET /api/jellyfin/status)
    # ------------------------------------------------------------------
    def diagnostics(self) -> Dict[str, Any]:
        """Snapshot for the /api/jellyfin/status endpoint. Never raises."""
        diag: Dict[str, Any] = {
            "configured": self.configured,
            "base_url": self.base_url or None,
            "server_online": False,
            "movie_dir": self.movie_dir,
            "tv_dir": self.tv_dir,
            "movies_library_name": self.movies_library_name,
            "tv_library_name": self.tv_library_name,
            "realtime_monitor": self.realtime_monitor,
            "libraries": [],
            "env": {
                "JELLYFIN_URL": bool(os.getenv("JELLYFIN_URL")),
                "JELLYFIN_API_KEY": bool(os.getenv("JELLYFIN_API_KEY")),
                "JELLYFIN_USER_ID": bool(os.getenv("JELLYFIN_USER_ID")),
            },
        }
        libs = self.get_virtual_folders()
        if libs is not None:
            diag["server_online"] = True
            diag["libraries"] = [
                {
                    "name": lib.get("Name"),
                    "collection_type": lib.get("CollectionType"),
                    "locations": lib.get("Locations") or [],
                }
                for lib in libs
            ]
        return diag


# Legacy alias: older modules imported JellyfinRefresher from jellyfin.py.
JellyfinRefresher = JellyfinService