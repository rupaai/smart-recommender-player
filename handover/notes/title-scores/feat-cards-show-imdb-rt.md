---
feature: title-scores
status: ready
new: true
plan: docs/WISHMATCH_NEXT.md
---
## Line
- **IMDb and Rotten Tomatoes scores** (`services/title_scores.py`, `title_scores:` in `homelab/config.yaml`, schema v24): once a day on mars prod only, IMDb numbers from TMDB (free, 2,000 a day) and scores from OMDb by IMDb number (Labiba chose the free key on 2026-11-05: 1,000 a day). Fills `media_items.imdb_id`, `imdb_rating`, `imdb_votes`, `rt_score` (0-100), `metacritic`, `scores_checked_at`; films first, most votes first; lookups counted per day in `title_scores_days` so restarts never go over. Key: Homelab secret `OMDB_API_KEY`. By hand: `utils/fetch_title_scores.py [--status]`. Wishmatch quality uses IMDb (TMDB until known) plus Rotten Tomatoes at `rt_weight: 0.3`; the rating filter means IMDb; with no filter, under 6 is left out (`default_min_rating`, "Any rating" turns it off). Cards (original app) and the web and TV title pages show IMDb and RT first, TMDB only until IMDb is known, our own score as a small "ours" note (`card_scores`, `scoreMeta` in `static/app.js`, `scoreFacts` in `static/nl/nl.js`).

## Open work
- **Title scores, next:** running on mars prod since 2026-11-05 (key set, first runs seen in the logs). Whole catalogue about 51 days; films with 1,000+ votes about 5. Rerun `utils/compare_engines.py` once most well-known films have scores.

## What changed
Cards and title pages show IMDb and Rotten Tomatoes.
