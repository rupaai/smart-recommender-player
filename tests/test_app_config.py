"""
Offline tests for the settings loader (services/app_config.py)

    uv run python tests/test_app_config.py
"""

import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from services import app_config  # noqa: E402

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} {detail}")
        failures.append(label)


def write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


print("Merging")
base = {"torrent": {"download_dir": "downloads", "min_free_gb": 20}, "media_folders": ["a", "b"], "region": "GB"}
out = app_config.merge(base, {"torrent": {"download_dir": "/x"}, "media_folders": ["c"]})
check("a section changes only the keys written", out["torrent"] == {"download_dir": "/x", "min_free_gb": 20}, out)
check("a list is replaced as a whole", out["media_folders"] == ["c"], out)
check("other keys are kept", out["region"] == "GB", out)
check("the base is not changed", base["torrent"]["download_dir"] == "downloads", base)

print("Loading")
saved = {k: os.environ.pop(k, None) for k in ("CONFIG_PATH", "LOCAL_CONFIG_PATH")}
tmp = tempfile.mkdtemp()
main = os.path.join(tmp, "config.yaml")
write(main, "recommender: {engine: legacy, final_pool: 200}\n")
check("no local file: the main file alone", app_config.load_config(main)["recommender"]["engine"] == "legacy")
write(os.path.join(tmp, "config.local.yaml"), "recommender: {engine: wishmatch}\nnorthern_lights: {people: []}\n")
cfg = app_config.load_config(main)
check("config.local.yaml next to the main file is laid on top",
      cfg["recommender"] == {"engine": "wishmatch", "final_pool": 200}, cfg)
other = os.path.join(tmp, "other.yaml")
write(other, "recommender: {engine: tiger}\n")
os.environ["LOCAL_CONFIG_PATH"] = other
check("LOCAL_CONFIG_PATH picks another local file", app_config.load_config(main)["recommender"]["engine"] == "tiger")
os.environ["LOCAL_CONFIG_PATH"] = os.path.join(tmp, "missing.yaml")
check("a missing local file counts as empty", app_config.load_config(main)["recommender"]["engine"] == "legacy")
os.environ["CONFIG_PATH"] = main
check("CONFIG_PATH picks the main file", app_config.config_path() == main)
check("setting() reads a dotted name", app_config.setting(cfg, "recommender.final_pool") == 200)
check("setting() default when missing", app_config.setting(cfg, "nope.here", 7) == 7)
for k, v in saved.items():
    os.environ.pop(k, None)
    if v is not None:
        os.environ[k] = v

print("The shared settings carry no one's personal setup")
with open(os.path.join(ROOT, "config.yaml"), encoding="utf-8") as f:
    shared = f.read()
check("no home folders in config.yaml", not re.search(r"/(Users|home)/", shared))
check("no people in config.yaml", "jellyfin_user_id" not in shared.split("northern_lights:")[-1].split("\n\n")[0])
with open(os.path.join(ROOT, ".gitignore"), encoding="utf-8") as f:
    check("config.local.yaml is git-ignored", "config.local.yaml" in f.read().split())

if failures:
    print(f"\n{len(failures)} check(s) failed")
    sys.exit(1)
print("\nall settings checks passed")
