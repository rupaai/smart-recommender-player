---
feature: new-episodes
status: ready
new: true
---
## Line
- **New & popular** (`services/nl_new_episodes.py`, `web/routes/nl_new.py`, `new_episodes:` in `config.yaml`, guide `docs/guides/new-episodes.md`): mars lists new episodes of your shows (files, seen, or Watch later) and TMDB-trending shows still airing (well rated, taste-checked with Wishmatch), and starts the best matching copy on one tap (a finished season as one pack). Pages `/nl/new`, `/nl/tv/new`, `/player/new` and a "New episodes" home row, all drawn by `static/nl/new-episodes.js`. TMDB only, free. Copies stay on the server; pages get a `copy_id`.

## Open work
- **New & popular, next:** not yet tried on mars or the real TV. Later idea: download new episodes of followed shows by itself (Labiba's day-of-week idea).

## What changed
2026-11-05: pages added (web, TV, laptop) and the home row.
