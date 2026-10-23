"""The app's version as Homelab reports it.

Homelab gives the container HOMELAB_VERSION, e.g. "v0.4.0" in prod or "v0.4.0-dev.2" in dev.
It is missing on local runs and before the first promote, so callers must cope with None.
"""

from __future__ import annotations

import os


def homelab_version() -> str | None:
    """HOMELAB_VERSION, stripped; None when it is unset or empty."""
    return os.getenv("HOMELAB_VERSION", "").strip() or None


def app_version() -> str:
    """The version to show in the API docs: HOMELAB_VERSION, else "dev"."""
    return homelab_version() or "dev"
