"""Reads the secrets Homelab gives the app as files (one file per secret in /run/secrets).

With `secrets_as: files` in homelab.yaml, Homelab keeps secrets out of the container's
environment, so they don't show in `docker inspect`. This copies them into os.environ at
start-up, so the rest of the app keeps reading os.getenv as before. A value already in the
environment wins.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

SECRETS_DIR = "/run/secrets"
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def load(directory: str = SECRETS_DIR) -> list[str]:
    """Sets each secret file in `directory` as an env var; returns the names it set."""
    root = Path(directory)
    if not root.is_dir():
        return []
    loaded = []
    for path in sorted(root.iterdir()):
        if not _NAME.match(path.name) or not path.is_file():
            continue
        if path.name in os.environ:
            continue
        # A secret file often ends with a line break; kept, it made every correct ADMIN_TOKEN fail.
        os.environ[path.name] = path.read_text(encoding="utf-8").strip()
        loaded.append(path.name)
    return loaded
