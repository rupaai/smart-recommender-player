"""
Offline tests: downloads survive a restart (stored requests, resumed at start-up, orphan staging folders removed),
staging folders carry the environment name, finishing one download keeps the others' .ltresume files, and the
app starts without a media folder.

    uv run python tests/test_download_resume.py
"""

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import nl_fake  # noqa: E402

failures = []
MAGNET = "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567&dn=X"


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} — got: {detail}")
        failures.append(label)


def run(tmp):
    os.environ.pop("HOMELAB_ENV", None)
    ctx = nl_fake.boot(tmp)
    server = ctx["server"]
    state = ctx["state"]
    from database import DatabaseManager
    from services.nl_downloads import DownloadRequests
    dl = state.torrent_downloader
    downloads = dl.download_dir

    print("requests are stored in the database")
    reqs = state.download_requests
    reqs.remember("aaa1", MAGNET, "Seven", "movie", movie_id=7)
    reqs.remember("bbb2", MAGNET, "Eight", "tv", movie_id=8)
    reqs.finished("bbb2")
    again = DownloadRequests(DatabaseManager(state.db.db_path))      # a new process reads the same file
    check("a new instance still has the link", again.get("aaa1") == {"magnet": MAGNET, "title": "Seven", "media_type": "movie", "movie_id": 7})
    check("only unfinished downloads are listed for resume", [r["download_id"] for r in again.unfinished()] == ["aaa1"], again.unfinished())
    check("a finished one is still known (Retry)", again.get("bbb2") is not None and "bbb2" in again.download_ids())

    print("start-up resumes unfinished downloads and removes orphan staging folders")
    started = []

    async def fake_download(magnet, download_id, movie_id, title, media_type="movie"):
        started.append((magnet, download_id, movie_id, title, media_type))

    dl.download = fake_download
    state.downloads_store.clear()
    for name in ("download_aaa1", "download_bbb2", "download_zzz9", "id_99", "prod-download_ccc3", "other"):
        os.makedirs(os.path.join(downloads, name), exist_ok=True)
    open(os.path.join(downloads, "abc.ltresume"), "wb").close()

    async def start():
        await server.resume_downloads()
        await asyncio.sleep(0.05)
    asyncio.run(start())
    check("the unfinished download was started again", started == [(MAGNET, "aaa1", 7, "Seven", "movie")], started)
    check("it shows in the list as connecting", state.downloads_store.get("aaa1", {}).get("status") == "connecting")
    check("it is marked finished once it returns", DownloadRequests(state.db).unfinished() == [])
    left = sorted(os.listdir(downloads))
    check("the orphan folders are gone", "download_zzz9" not in left and "id_99" not in left, left)
    check("folders of stored downloads, other environments, other names and .ltresume files stay",
          {"download_aaa1", "download_bbb2", "prod-download_ccc3", "other", "abc.ltresume"} <= set(left), left)

    print("staging folders carry the environment name")
    os.environ["HOMELAB_ENV"] = "dev"
    from services.torrent_service import TorrentDownloader
    dev = TorrentDownloader(state.config, db_manager=state.db)
    check("dev folder name", os.path.basename(dev._staging_path("x1")) == "dev-download_x1", dev._staging_path("x1"))
    os.makedirs(os.path.join(downloads, "dev-download_orph"))
    removed = dev.remove_orphan_staging(set())
    check("dev removes only its own orphans", removed == ["dev-download_orph"] and os.path.isdir(os.path.join(downloads, "download_aaa1"))
          and os.path.isdir(os.path.join(downloads, "prod-download_ccc3")), (removed, sorted(os.listdir(downloads))))
    os.environ["HOMELAB_ENV"] = "prod"
    prod = TorrentDownloader(state.config, db_manager=state.db)
    os.makedirs(os.path.join(downloads, "id_55"))
    removed = sorted(prod.remove_orphan_staging({"aaa1", "bbb2"}))
    check("prod also removes its old unprefixed orphans (id_<n> and download_<id>)",
          removed == ["id_55", "prod-download_ccc3"] and os.path.isdir(os.path.join(downloads, "download_aaa1")), removed)
    os.environ.pop("HOMELAB_ENV")

    print("finishing one download keeps the others' resume files")
    for h in ("aaaa", "bbbb"):
        open(dl._resume_file_for(h), "wb").close()
    dl._clear_resume_file("aaaa")
    check("only that torrent's .ltresume is deleted", not os.path.exists(dl._resume_file_for("aaaa"))
          and os.path.exists(dl._resume_file_for("bbbb")))


def no_media(tmp):
    print("the app starts and works when the media folder is missing")
    code = f'''
import json, os, sys, asyncio
sys.path.insert(0, {ROOT!r})
cfg = {{"media_folders": ["/proc/nomedia/Movies"], "jellyfin_library": {{"movie_dir": "/proc/nomedia/Movies", "tv_dir": "/proc/nomedia/Shows"}},
        "torrent": {{"download_dir": "/proc/nomedia/downloads"}}}}
json.dump(cfg, open({os.path.join(tmp, "nm.yaml")!r}, "w"))
os.environ["CONFIG_PATH"] = {os.path.join(tmp, "nm.yaml")!r}
os.environ.pop("LOCAL_CONFIG_PATH", None)   # only this file's folders
os.environ["DATABASE_PATH"] = {os.path.join(tmp, "nm.db")!r}
import server
from web import state  # noqa: E402
from fastapi.testclient import TestClient
c = TestClient(server.app)
print("HEALTH", c.get("/healthz").status_code)
print("HOME", c.get("/").status_code)
asyncio.run(server.resume_downloads())
asyncio.run(state.torrent_downloader.download("magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567", "d5", 5, "T"))
print("DL", state.downloads_store["d5"]["status"])
'''
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=tmp)
    check("healthz is 200", "HEALTH 200" in out.stdout, out.stdout + out.stderr[-800:])
    check("the home page loads", "HOME 200" in out.stdout)
    check("a download says why it cannot run", "DL error" in out.stdout, out.stdout)


def main():
    tmp = tempfile.mkdtemp(prefix="dl_resume_test_")
    try:
        run(tmp)
        no_media(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if failures:
        print(f"\n{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("\nAll download resume tests passed")


if __name__ == "__main__":
    main()
