"""
Offline tests for the Homelab version helper (tests/test_app_version.py).

    uv run python tests/test_app_version.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import app_version  # noqa: E402

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} — got: {detail}")
        failures.append(label)


def with_env(value):
    if value is None:
        os.environ.pop("HOMELAB_VERSION", None)
    else:
        os.environ["HOMELAB_VERSION"] = value


def test_helper():
    print("homelab_version / app_version")
    with_env(None)
    check("unset gives None", app_version.homelab_version() is None, app_version.homelab_version())
    check("unset gives dev", app_version.app_version() == "dev", app_version.app_version())

    with_env("")
    check("empty gives None", app_version.homelab_version() is None, app_version.homelab_version())
    check("empty gives dev", app_version.app_version() == "dev", app_version.app_version())

    with_env("   ")
    check("spaces give None", app_version.homelab_version() is None, app_version.homelab_version())

    with_env("v0.4.0")
    check("prod value is returned", app_version.homelab_version() == "v0.4.0", app_version.homelab_version())
    check("prod value is the app version", app_version.app_version() == "v0.4.0", app_version.app_version())

    with_env(" v0.4.0-dev.2\n")
    check("value is stripped", app_version.homelab_version() == "v0.4.0-dev.2", app_version.homelab_version())

    with_env(None)


def main():
    test_helper()
    if failures:
        print(f"\n{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("\nAll app version tests passed")


if __name__ == "__main__":
    main()
