---
feature: title-scores
status: in-progress
new: true
plan: docs/WISHMATCH_NEXT.md
---
## Line
- **IMDb and Rotten Tomatoes scores** (`services/title_scores.py`, `title_scores:` in `homelab/config.yaml`, schema v24): once a day on mars prod only, IMDb numbers from TMDB (free, 2,000 a day) and scores from OMDb by IMDb number (Labiba chose the free key on 2026-11-05: 1,000 a day). Fills `media_items.imdb_id`, `imdb_rating`, `imdb_votes`, `rt_score` (0-100), `metacritic`, `scores_checked_at`; films first, most votes first; lookups counted per day in `title_scores_days` so restarts never go over. Key: Homelab secret `OMDB_API_KEY`. By hand: `utils/fetch_title_scores.py [--status]`. Wishmatch quality uses IMDb (TMDB until known) plus Rotten Tomatoes at `rt_weight: 0.3`; the rating filter means IMDb; with no filter, under 6 is left out (`default_min_rating`, "Any rating" turns it off).

## Open work
- **Title scores, next:** Labiba adds `OMDB_API_KEY` in Homelab and restarts prod; cards show IMDb and RT first, our score as a side note (`docs/WISHMATCH_NEXT.md` step 5). Whole catalogue about 51 days; films with 1,000+ votes about 5.

## What changed
Wishmatch now judges quality by IMDb and Rotten Tomatoes.
