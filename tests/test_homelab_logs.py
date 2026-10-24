"""
Offline tests for the JSON logging set up at start-up (tests/test_homelab_logs.py)

Importing server calls homelab.logs.setup("INFO"), so Python's logging prints one JSON line
per record with a level Homelab's Logs can read. No HOMELAB_TOKEN is needed.

    uv run python tests/test_homelab_logs.py
"""

import contextlib
import io
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.pop("HOMELAB_TOKEN", None)

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} — got: {detail}")
        failures.append(label)


import server  # noqa: E402,F401 - start-up is what we are testing

print("handler")
root = logging.getLogger()
json_handlers = [h for h in root.handlers if getattr(h, "_homelab_json", False)]
check("exactly one JSON handler on the root logger", len(json_handlers) == 1, len(json_handlers))
check("root level is INFO", root.level == logging.INFO, root.level)

print("lines")
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    log = logging.getLogger("nl.test")
    log.debug("hidden debug")
    log.info("hello %s", "world")
    log.warning("careful", extra={"show_id": 7})
    log.error("broke")
lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
check("debug is below INFO and prints nothing", len(lines) == 3, lines)
records = []
for ln in lines:
    try:
        records.append(json.loads(ln))
    except ValueError:
        records.append({})
check("every line is one JSON object", all(records), lines)
check("info line has level info and the message", records[0].get("level") == "info" and records[0].get("msg") == "hello world", records[0])
check("warning maps to warn and keeps extra fields", records[1].get("level") == "warn" and records[1].get("show_id") == 7, records[1])
check("error line has level error", records[2].get("level") == "error", records[2])
check("lines name the logger", records[0].get("logger") == "nl.test", records[0])

print("no duplicates")
import homelab.logs  # noqa: E402

homelab.logs.setup("INFO")
check("a second setup adds no second handler", len([h for h in root.handlers if getattr(h, "_homelab_json", False)]) == 1)

if failures:
    print(f"\n{len(failures)} FAILED")
    sys.exit(1)
print("\nall passed")
