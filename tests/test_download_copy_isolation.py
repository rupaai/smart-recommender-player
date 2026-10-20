"""Per-copy staging and resume cleanup never remove another download's data."""

import asyncio
import os
import shutil
import tempfile

from services.torrent_service import TorrentDownloader, downloads_store


def main():
    root = tempfile.mkdtemp(prefix="download_copy_isolation_")
    try:
        downloader = object.__new__(TorrentDownloader)
        downloader.download_dir = os.path.join(root, "downloads")
        downloader.resume_dir = os.path.join(root, "resume")
        os.makedirs(downloader.download_dir)
        os.makedirs(downloader.resume_dir)

        first = downloader._torrent_staging_dir("copy-a")
        second = downloader._torrent_staging_dir("copy-b")
        assert first != second
        with open(os.path.join(first, "partial.mkv"), "wb") as f:
            f.write(b"first")
        with open(os.path.join(second, "partial.mkv"), "wb") as f:
            f.write(b"second")

        downloader._purge_partial_files("copy-a")
        assert not os.path.exists(first)
        assert os.path.isfile(os.path.join(second, "partial.mkv"))

        resume_a = downloader._resume_file_for("a" * 40)
        resume_b = downloader._resume_file_for("b" * 40)
        for path in (resume_a, resume_b):
            with open(path, "wb") as f:
                f.write(b"resume")
        downloader._clear_resume_file("a" * 40)
        assert not os.path.exists(resume_a)
        assert os.path.isfile(resume_b)

        # Cancellation may arrive after the route queues the task but before
        # the coroutine gets its first turn; it still must clear that copy.
        downloader.lt = object()
        downloader.cancelled_tasks = {"early-cancel"}
        early_stage = downloader._torrent_staging_dir("early-cancel")
        with open(os.path.join(early_stage, "partial.mkv"), "wb") as f:
            f.write(b"partial")
        early_hash = "c" * 40
        early_resume = downloader._resume_file_for(early_hash)
        with open(early_resume, "wb") as f:
            f.write(b"resume")
        downloads_store["early-cancel"] = {"movie_id": 12, "status": "cancelled"}
        asyncio.run(downloader.download(
            f"magnet:?xt=urn:btih:{early_hash}", "early-cancel", 12, "Example",
        ))
        assert not os.path.exists(early_stage)
        assert not os.path.exists(early_resume)
        downloads_store.pop("early-cancel", None)
        print("✅ per-copy staging and resume cleanup are isolated")
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
