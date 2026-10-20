"""Browser-side tests for the Northern Lights watch-session lifecycle."""

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    node = shutil.which("node")
    if not node:
        print("node not installed; watch-session behavior test skipped")
        return
    result = subprocess.run(
        [node, os.path.join(ROOT, "tests", "js", "nl_watch_session_check.js")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    if result.returncode:
        sys.exit(result.returncode)
    print("All Northern Lights watch-session tests passed")


if __name__ == "__main__":
    main()
