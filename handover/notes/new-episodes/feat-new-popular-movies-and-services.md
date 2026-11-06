---
feature: new-episodes
status: ready
new: true
---
## Line
- **New & popular** (`services/nl_new_episodes.py`, `web/routes/nl_new.py`, `new_episodes:` in `config.yaml`, guide `docs/guides/new-episodes.md`): mars lists new episodes of your shows (files, seen, or Watch later), TMDB-trending shows still airing and trending films (well rated, taste-checked with Wishmatch, seen films left out), plus a tab per streaming service (`providers`, country `watch_region`) with its popular shows and films. A show's Download button always gets one episode, the next in watching order: S1E1 for a show you never had, on from your files for one you have, the latest season for one seen elsewhere (Labiba, 2026-11-05: no later seasons of unwatched shows). "Seasons" lists every season and episode to get any one, or a whole finished season. Pages `/nl/new`, `/nl/tv/new`, `/player/new` and a "New episodes" home row, drawn by `static/nl/new-episodes.js`. TMDB only, free; copies stay on the server.

## Open work
- **New & popular:** `watch_region` is US until Labiba sets her country. Not yet tried on mars or the real TV.

## What changed
2026-11-06: Popular movies and the streaming-service tabs.
