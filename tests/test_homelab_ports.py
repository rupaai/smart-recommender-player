"""
Offline check that Prowlarr's port is not published on the home network (tests/test_homelab_ports.py)

    uv run python tests/test_homelab_ports.py

Only the app reaches Prowlarr, at http://prowlarr:9696 (PROWLARR_URL). Publishing 9696 would
open its page to the whole home network.
"""

import os
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

with open(os.path.join(ROOT, "homelab.yaml"), encoding="utf-8") as f:
    config = yaml.safe_load(f)
services = {s["name"]: s for s in config["services"]}

prowlarr = services["prowlarr"]
ok = True


def check(cond, msg):
    global ok
    print(("ok   " if cond else "FAIL ") + msg)
    ok = ok and cond


check("ports" not in prowlarr, "prowlarr publishes no ports on the host")
check(
    config["env"].get("PROWLARR_URL") == "http://prowlarr:9696",
    "the app's PROWLARR_URL still points at http://prowlarr:9696",
)

if not ok:
    sys.exit(1)
print("homelab ports checks passed")
