"""
Offline test for the Download tab (tests/test_direct_download_slots.py): a result's download slot belongs to its file
(the torrent's info hash), not to its place in the list, so re-sorting or searching again never moves a running
download's progress, Cancel or Play onto a different copy. Runs tests/js/direct_download_slots_check.js under node.

    uv run python tests/test_direct_download_slots.py
"""

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    node = shutil.which("node")
    if not node:
        print("  (node not installed: skipped)")
        return
    out = subprocess.run(
        [node, os.path.join(ROOT, "tests", "js", "direct_download_slots_check.js")],
        capture_output=True, text=True, timeout=60,
    )
    print(out.stdout + out.stderr)
    if out.returncode != 0:
        print("❌ download slots follow the position, not the file")
        sys.exit(1)
    print("✅ download slots follow the file")


if __name__ == "__main__":
    main()
