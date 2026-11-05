---
feature: title-scores
status: in-progress
new: true
plan: docs/WISHMATCH_NEXT.md
---
## Line
- **IMDb and Rotten Tomatoes scores** (`services/title_scores.py`, `title_scores:` in `homelab/config.yaml`, schema v24): once a day on mars prod only, IMDb numbers from TMDB (free, 2,000 a day) and scores from OMDb by IMDb number (Labiba chose the free key on 2026-11-05: 1,000 a day). Fills `media_items.imdb_id`, `imdb_rating`, `imdb_votes`, `rt_score` (0-100), `metacritic`, `scores_checked_at`; films first, most votes first; lookups counted per day in `title_scores_days` so restarts never go over. Key: Homelab secret `OMDB_API_KEY`. By hand: `utils/fetch_title_scores.py [--status]`.

## Open work
- **Title scores, next:** Labiba adds `OMDB_API_KEY` in Homelab and restarts prod; then Wishmatch quality and the rating filter use `imdb_rating`, cards show IMDb and RT (`docs/WISHMATCH_NEXT.md` steps 3 and 5). Whole catalogue about 51 days; films with 1,000+ votes about 5.

## What changed
New daily job and score columns. Nothing reads the scores yet.
