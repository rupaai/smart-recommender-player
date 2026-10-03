#!/usr/bin/env python3
"""
One-off maintenance: point Jellyfin libraries at the CURRENT media roots.

Why this exists: services/jellyfin_service.py is additive-only by design
(it never deletes libraries or paths), so when the app's media roots moved
(~/Media/Movies, ~/Media/Shows) any *old* folder Jellyfin still watches keeps
serving stale data. This script:

  1. Tests which auth style the running Jellyfin accepts (X-Emby-Token header,
     modern `Authorization: MediaBrowser Token="..."` header, or `api_key`
     query param) — newer Jellyfin builds may drop legacy styles.
  2. Snapshots every virtual folder (library) with its Locations.
  3. Runs JellyfinService.ensure_libraries() to add the current movie/tv roots
     (creating the typed libraries if missing).
  4. Reports and (with --apply) REMOVES stale paths from the configured
     "Movies" / "TV Shows" libraries. Never touches other libraries —
     it only prints them for manual review.
  5. Triggers a library refresh.

Usage:
  uv run python utils/fix_jellyfin_libraries.py            # dry-run (no deletes)
  uv run python utils/fix_jellyfin_libraries.py --apply    # actually delete stale paths
  uv run python utils/fix_jellyfin_libraries.py --set-key NEWKEY --apply
                                                           # update .env first, then fix
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from services.jellyfin_service import JellyfinService  # noqa: E402


def load_app_config() -> Dict[str, Any]:
    cfg_path = PROJECT_ROOT / "config.yaml"
    if cfg_path.exists():
        with open(cfg_path) as fh:
            return yaml.safe_load(fh) or {}
    return {}


def set_env_key(new_key: str) -> None:
    """Idempotently rewrite JELLYFIN_API_KEY in .env (preserves other lines)."""
    env_path = PROJECT_ROOT / ".env"
    lines: List[str] = []
    if env_path.exists():
        lines = env_path.read_text().splitlines()
    replaced = False
    out: List[str] = []
    for line in lines:
        if line.strip().startswith("JELLYFIN_API_KEY="):
            out.append(f"JELLYFIN_API_KEY={new_key}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.insert(0, f"JELLYFIN_API_KEY={new_key}")
    env_path.write_text("\n".join(out) + "\n")
    print("[.env] JELLYFIN_API_KEY updated.")


def probe_auth(session: requests.Session, base_url: str, api_key: str) -> Optional[str]:
    """Returns the auth style that works, or None if every style gets rejected."""
    probes = [
        ("X-Emby-Token header", {"headers": {"X-Emby-Token": api_key}}),
        ("Authorization MediaBrowser header", {"headers": {"Authorization": f'MediaBrowser Token="{api_key}"'}}),
        ("api_key query param", {"params": {"api_key": api_key}}),
    ]
    for label, kwargs in probes:
        try:
            resp = session.get(f"{base_url}/Library/VirtualFolders", timeout=8, **kwargs)
        except Exception as e:
            print(f"[auth] {label}: connection error ({e})")
            continue
        if resp.status_code in (200, 204):
            print(f"[auth] {label}: OK")
            return label
        print(f"[auth] {label}: HTTP {resp.status_code}")


def apply_auth_style(service: JellyfinService, style: str, api_key: str) -> None:
    """Align the service session with whichever auth style the server accepts."""
    if style == "X-Emby-Token header":
        return
    service.session.headers.pop("X-Emby-Token", None)
    if style == "Authorization MediaBrowser header":
        service.session.headers["Authorization"] = f'MediaBrowser Token="{api_key}"'
    else:  # api_key query param
        service.session.params = {"api_key": api_key}  # type: ignore[attr-defined]


def main() -> int:
    parser = argparse.ArgumentParser(description="Point Jellyfin libraries at the current media roots.")
    parser.add_argument("--apply", action="store_true", help="actually delete stale library paths")
    parser.add_argument("--set-key", metavar="NEWKEY", help="write this new API key into .env first")
    args = parser.parse_args()

    if args.set_key:
        set_env_key(args.set_key)

    load_dotenv(PROJECT_ROOT / ".env")
    base_url = (os.getenv("JELLYFIN_URL") or "").rstrip("/")
    api_key = os.getenv("JELLYFIN_API_KEY")
    if not base_url or not api_key:
        print("ERROR: JELLYFIN_URL / JELLYFIN_API_KEY not set in .env")
        return 2

    config = load_app_config()
    service = JellyfinService(config)

    print(f"[info] Jellyfin:  {base_url}")
    print(f"[info] movie_dir: {service.movie_dir}")
    print(f"[info] tv_dir:    {service.tv_dir}")

    # ---- 1. auth probe --------------------------------------------------
    style = probe_auth(service.session, base_url, api_key)
    if style is None:
        print(
            "\n[AUTH] Every auth style was rejected (401).\n"
            "  → The API key in .env is invalid/expired for this server.\n"
            "  → Fix: Jellyfin Dashboard → API Keys → '+' → copy the new key, then:\n"
            "       uv run python utils/fix_jellyfin_libraries.py --set-key <NEWKEY> --apply\n"
        )
        return 2
    apply_auth_style(service, style, api_key)

    # ---- 2. snapshot ----------------------------------------------------
    libs = service.get_virtual_folders()
    if libs is None:
        print("[error] Could not list virtual folders even though auth probed OK.")
        return 2
    print("\n[snapshot] Current libraries:")
    for lib in libs:
        print(f"  - {lib.get('Name')} ({lib.get('CollectionType')}) → {lib.get('Locations')}")

    # ---- 3. additive setup of the current roots -------------------------
    print("\n[ensure] Adding current media roots (idempotent, additive)...")
    result = service.ensure_libraries()
    print(f"  created: {result['created']}")
    print(f"  updated: {result['updated']}")
    print(f"  skipped: {result['skipped']}")

    # ---- 4. stale path cleanup (configured libraries only) --------------
    wanted_dirs = {service.movie_dir, service.tv_dir}
    configured = {service.movies_library_name.lower(), service.tv_library_name.lower()}
    libs = service.get_virtual_folders() or []
    for lib in libs:
        name = lib.get("Name") or ""
        if name.lower() not in configured:
            continue
        stale = [p for p in (lib.get("Locations") or []) if os.path.abspath(p) not in wanted_dirs]
        for path in stale:
            print(f"\n[stale] {name}: '{path}' is not a current media root")
            if not args.apply:
                print("        (dry-run — rerun with --apply to remove)")
                continue
            try:
                resp = service.session.delete(
                    f"{base_url}/Library/VirtualFolders/Paths",
                    params={"name": name, "path": path, "refreshLibrary": "true"},
                    timeout=10,
                )
                if resp.status_code in (200, 204):
                    print(f"        removed ✔ ({path})")
                else:
                    resp2 = service.session.delete(
                        f"{base_url}/Library/VirtualFolders/Paths",
                        params={"name": name},
                        json={"Path": path},
                        timeout=10,
                    )
                    if resp2.status_code in (200, 204):
                        print(f"        removed ✔ ({path})")
                    else:
                        print(
                            f"        delete returned HTTP {resp.status_code}/{resp2.status_code}"
                            " — remove it manually in the dashboard"
                        )
            except Exception as e:
                print(f"        delete failed: {e} — remove it manually in the dashboard")

    # ---- 5. refresh -----------------------------------------------------
    try:
        resp = service.session.post(f"{base_url}/Library/Refresh", timeout=10)
        print(f"\n[refresh] POST /Library/Refresh → HTTP {resp.status_code}")
    except Exception as e:
        print(f"[refresh] failed: {e}")

    print("\n[done] Verify with:")
    print("  curl -s http://localhost:8000/api/jellyfin/status | python3 -m json.tool")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

