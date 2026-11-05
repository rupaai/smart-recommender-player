---
feature: title-scores
status: in-progress
new: true
plan: docs/WISHMATCH_NEXT.md
---
## Line
- **IMDb and Rotten Tomatoes scores** (`services/title_scores.py`, `title_scores:` in `homelab/config.yaml`, schema v24): once a day on mars prod only, IMDb numbers from TMDB (free, 2,000 a day) and scores from OMDb by IMDb number (Labiba chose the free key on 2026-11-05: 1,000 a day). Fills `media_items.imdb_id`, `imdb_rating`, `imdb_votes`, `rt_score` (0-100), `metacritic`, `scores_checked_at`; films first, most votes first; lookups counted per day in `title_scores_days` so restarts never go over. Key: Homelab secret `OMDB_API_KEY`. By hand: `utils/fetch_title_scores.py [--status]`. Cards (original app) and the web and TV title pages show IMDb and RT first, TMDB only until IMDb is known, our own score as a small "ours" note (`card_scores`, `scoreMeta` in `static/app.js`, `scoreFacts` in `static/nl/nl.js`).

## Open work
- **Title scores, next:** Labiba adds `OMDB_API_KEY` in Homelab and restarts prod. Whole catalogue about 51 days; films with 1,000+ votes about 5. Wishmatch quality from IMDb is in its own PR (`feat/wishmatch-imdb-quality`).

## What changed
Cards and title pages show IMDb and Rotten Tomatoes.
