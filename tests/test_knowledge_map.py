"""
The knowledge map and the guides name only files that exist (tests/test_knowledge_map.py)

    uv run python tests/test_knowledge_map.py

docs/KNOWLEDGE_MAP.md is how people find a feature without searching the code, so a
renamed or deleted file must be fixed there in the same PR. Every `path/with/a/slash` in
backticks must exist (a `*` matches anything; a path with <...> is a pattern, skipped).
"""

import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = ["docs/KNOWLEDGE_MAP.md", "web/README.md"] + sorted(
    os.path.relpath(p, ROOT) for p in glob.glob(os.path.join(ROOT, "docs/guides/*.md")))
NOT_FILES = ("/api/", "/nl/", "/player", "/admin", "/run/", "/data", "/media", "/models", "/mnt/", "http", "~",
             "ghcr.io", "rupaai/")
failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} {detail}")
        failures.append(label)


PREFIXES = ("", "static/", "templates/", "web/")   # short forms like `nl/tv/` or `routes/core.py`


def exists(path, base):
    path = path.rstrip("/")
    for prefix in (base + "/",) + PREFIXES:
        full = os.path.join(ROOT, prefix + path)
        if glob.glob(full) if "*" in path else os.path.exists(full):
            return True
    return False


for doc in DOCS:
    text = open(os.path.join(ROOT, doc), encoding="utf-8").read()
    print(doc)
    base = os.path.dirname(doc)
    paths = set()
    for raw in re.findall(r"`([^`\s]+)`", text):
        if "/" not in raw or "<" in raw or "{" in raw or raw.startswith(NOT_FILES + ("/", ".")) or "=" in raw:
            continue
        paths.add(raw)
    missing = sorted(p for p in paths if not exists(p, base))
    check(f"{len(paths)} named paths exist", not missing, missing)
    for link in re.findall(r"\]\(([^)#\s]+)\)", text):
        if link.startswith("http"):
            continue
        target = os.path.normpath(os.path.join(base, link))
        if not os.path.exists(os.path.join(ROOT, target)):
            check(f"link {link} works", False, target)

if failures:
    print(f"\n{len(failures)} check(s) failed")
    sys.exit(1)
print("\nall knowledge-map checks passed")
