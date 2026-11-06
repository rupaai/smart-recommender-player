---
feature: daily-picks
status: in-progress
new: true
---
## Line
- **Today's picks** (`services/wishmatch_movies/daily.py`, core `packages/wishmatch/wishmatch/recent.py`, `wishmatch.daily_picks` / `wishmatch.recent` in `recommender_rules.yaml`, guide `docs/guides/wishmatch.md`): once a day, films for each of you and for both, from what you watched in the last 30 days (Jellyfin played dates plus seen marks and ratings in the app). Those titles become a wish (moods they agree on, feelings most share, "avoid" from what you disliked); Wishmatch ranks as usual (tastes, crowd, IMDb/RT, well known, nothing seen or played lately). Each card says "Because you watched X". `GET /api/nl/daily-picks`; kept in `daily_picks.json`. Home row "Picked for you today" (`static/nl/daily-picks.js`, web, TV, laptop): a film on the server opens its page, any other card is "Get it" (`POST /api/nl/daily-picks/download`, best copy via `NewEpisodes.pick` (kind "movie"), as on New & popular). Free, no model.

## Open work
- **Today's picks, next:** not yet seen on mars or the real TV. Labiba to choose whether the top joint pick downloads by itself.

## What changed
2026-11-06: server part for Labiba's "recommend some movies we might like based on what we have seen these days".
