---
feature: logs
status: ready
new: true
plan: /mnt/project-files/homehub/logs_build_plan.md
---
## Line
- **JSON logs** (`server.py`, `tests/test_homelab_logs.py`): start-up calls `homelab.logs.setup("INFO")` from the Homelab Python plugin `homelab-sdk` (a git dependency pinned to a commit in `pyproject.toml`). Python logging and uvicorn print one JSON line per record, with the level, so Homelab Logs can read them. No token needed. Plain `print()` lines are not changed and show as info. The Dockerfile installs git only so `uv sync` can fetch the plugin.

## What changed
Logs now print JSON lines with levels for Homelab Logs, through the `homelab-sdk` plugin.
