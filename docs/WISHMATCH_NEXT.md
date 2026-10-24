---
type: plan
status: waiting
tags: [wishmatch, recommender, ratings, imdb, rotten-tomatoes]
relatedTo: [PROJECT_HANDOVER.md, docs/RECOMMENDER_ARCHITECTURE.md]
---

# Wishmatch: next changes (after Labiba's review, 2026-10-24)

Wishmatch (PROJECT_HANDOVER.md §17) is built (steps 1-7). Labiba looked at the side-by-side page
(https://claude.ai/artifact/CVjgz3bngxDR7MdEf3fxEa) and found it decent, with these changes wanted before
she turns it on (`recommender.engine: wishmatch` in `homelab/config.yaml`, her call).

## What she asked for

1. **Fewer old films she doesn't know.** Too many older titles. Favour newer and better-known titles; older
   classics only when they fit very well or when a decade is asked for ("80s").
2. **Films and shows separately by default.** Films unless the wish says "show" / "series" or the Shows toggle
   is on; mixed only when the wish says "mix", "both", "films and shows". Today `media_type: None` mixes.
3. **Quality from IMDb (and Rotten Tomatoes), not our own score.** Poorly rated titles stay out unless she sets
   the rating filter low; the rating filter means the IMDb score. Our ratings only steer taste.
4. **Cards always show the IMDb and Rotten Tomatoes scores**; our own score only as a small side note
   (today the cards show TMDB's score and our numbers, which she finds annoying).
5. More training data, especially Gabriele's dislikes (he has rated none, so his profile has no avoided words).

## Plan

Each step its own PR on its own branch (`<type>/<topic>`, AGENTS.md). Keep legacy and TIGER untouched; every
number in the `wishmatch:` section of `recommender_rules.yaml`; tests for each.

1. `feat/wishmatch-films-or-shows` - default kind from the wish and the toggle (item 2). Engine already takes
   `Request.kinds`; the movie service decides: "show/series/episode" -> tv, "mix/both/films and shows" -> both,
   else the toggle, else films. Update `utils/compare_engines.py` expectations (mix only when asked).
2. `feat/wishmatch-recency` - a recency pull (item 1): e.g. `weights.recency` with a half-life in years
   (`recency_half_life: 15`), skipped when a decade or "classic"/"old" is typed; and raise the "known" bar
   (`candidates.min_popularity`) or weight votes more. Check with the side-by-side test: median release year and
   share of "well"-known titles in each top 10.
3. `feat/imdb-ratings` - IMDb scores (item 3), free:
   - `media_items.imdb_id` (+ `imdb_rating`, `imdb_votes`), additive migration.
   - `utils/backfill_imdb_ids.py` on the Mac: TMDB `/movie/{id}/external_ids` and `/tv/{id}/external_ids`
     with the existing TMDB key (free; about 50,000 calls, rate-limited, resumable, most-voted first).
   - `utils/imdb_ratings.py`: download IMDb's free `title.ratings.tsv.gz` (datasets.imdbws.com, about 8 MB;
     ask Labiba before the first download) and fill `imdb_rating` / `imdb_votes`; run at Train; carried to mars
     by the catalogue patch (add the columns to `services/catalogue_patch.py`).
   - Wishmatch quality uses `imdb_rating` (fallback TMDB); the rating filter (`min_rating`) means IMDb.
4. `feat/rotten-tomatoes` - Rotten Tomatoes scores via OMDb (item 3/4). **Needs Labiba's decision first:**
   free OMDb key (1,000 lookups a day, best-known first: the titles she sees get scores within days, the whole
   catalogue in about 50 days) or paid (about $1 a month, all in a day). She puts `OMDB_API_KEY` in the Mac's
   `.env` herself; never handle the key. Store `rt_score` (+ `metacritic` if free in the same call).
5. `feat/cards-show-imdb-rt` - cards in the original app, Northern Lights and TV show IMDb and RT first, our
   score as a side note (item 4). Phone, laptop and TV (D-pad) layouts.
6. Rerun the side-by-side test, update the page, then ask Labiba to turn Wishmatch on.

## Prompt for a fresh session

> Read `PROJECT_HANDOVER.md` (§17 Wishmatch) and `docs/WISHMATCH_NEXT.md`, then build the plan's steps in
> order, one PR per step on its own `<type>/<topic>` branch. Start with step 1 (films or shows by default).
> Ask Labiba before step 4 (OMDb free or paid) and before the first IMDb file download. Never spend money or
> handle API keys yourself; keep the legacy engine untouched; update the wishmatch handover note in each PR;
> rerun `utils/compare_engines.py` after steps 1-3 and share the side-by-side page.
