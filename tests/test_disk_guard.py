"""
Offline tests for the download disk guard (services/disk_guard.py)

    uv run python tests/test_disk_guard.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.disk_guard import GB, check_download, media_bytes  # noqa: E402

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} {detail}")
        failures.append(label)


root = tempfile.mkdtemp()
movies = os.path.join(root, "Movies")
dl = os.path.join(root, "downloads")
os.makedirs(movies)
os.makedirs(dl)
with open(os.path.join(movies, "a.mkv"), "wb") as f:
    f.truncate(2 * GB)  # sparse: counts as 2 GB without writing it
cfg = {"media_folders": [movies, dl], "torrent": {"download_dir": dl, "max_media_gb": 10, "min_free_gb": 0}}

print("media size")
check("nested/duplicate folders are counted once", media_bytes([movies, dl, movies]) == 2 * GB)

print("cap")
check("fits under the cap", check_download(cfg, 5 * GB) is None)
msg = check_download(cfg, 9 * GB)
check("over the cap is refused with a reason", bool(msg) and "10 GB limit" in msg, msg)
busy = {7: {"status": "downloading", "total_size": 6 * GB, "downloaded": 1 * GB}}
check("in-progress downloads count in full", check_download(cfg, 4 * GB, busy) is not None)
check("a download is not counted against itself", check_download(cfg, 4 * GB, busy, exclude_id=7) is None)
check("errored downloads are ignored", check_download(cfg, 4 * GB, {7: {"status": "error", "total_size": 6 * GB}}) is None)
cfg["torrent"]["max_media_gb"] = 0
check("max_media_gb 0 means no cap", check_download(cfg, 500 * GB) is None)

print("free space")
cfg["torrent"]["min_free_gb"] = 10 ** 9  # more free space than any disk has
msg = check_download(cfg, 1)
check("too little free space is refused", bool(msg) and "free" in msg, msg)

if failures:
    print(f"\n{len(failures)} failed")
    sys.exit(1)
print("\nall passed")
