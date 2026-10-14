# Server routers

`server.py` is the application composition root. It owns FastAPI setup,
middleware, startup, and construction of shared services. `routers/` contains
the URL registration by domain and, as extraction proceeds, the corresponding
endpoint implementations.

## Current layout

- `platform.py`: health, status, and small platform APIs.
- `recommendations.py`: recommendation and feedback APIs (the handlers remain
  in `server.py` so this domain stays unchanged during the split).
- `profiles.py`: profile, rating, and personal preference APIs.
- `playback.py`: Northern Lights playback and watch-session APIs.
- `jellyfin.py`: Jellyfin diagnostics and repair, legacy stream URL, current
  watch lookup, and the allowlisted Jellyfin media proxy. Both routes and
  handlers are implemented here.
- `catalogue.py`, `downloads.py`, and `pages.py`: routes and handlers for those
  domains.
- `_build.py`: common builder for the recommendations route table, whose
  handlers remain in `server.py`.

## Dependency wiring

`server.py` passes its live globals mapping to `build_routers()`. Extracted
routers receive that mapping and read integrations from it when a request runs.
This avoids importing `server.py` from a router and preserves tests that replace
services at runtime. The router builders also publish handler aliases into that
mapping so existing diagnostics and tests can still find handlers by name.
FastAPI resolves forward references in endpoint annotations during registration;
the builders publish only the annotation names used by that router before they
register its routes. The `F821` Ruff suppression on extracted modules is for
those runtime-resolved annotation strings; endpoint logic dependencies are
looked up through the injected mapping instead.
Keep each public HTTP contract intact. The recommendation handlers stay in
`server.py` until separately requested; do not change recommender behavior as
part of this work.

## Checks

Run `./.venv/bin/ruff check .` and the script-style test suite (`make test`, or
run each `tests/test_*.py` with the project's temporary config when uv's cache
is unavailable). `make check` also runs shell and deployment checks.
