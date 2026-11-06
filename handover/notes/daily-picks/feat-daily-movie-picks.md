---
feature: daily-picks
status: in-progress
new: true
---
## Line
- **Today's picks** (`services/wishmatch_movies/daily.py`, core `packages/wishmatch/wishmatch/recent.py`, `wishmatch.daily_picks` / `wishmatch.recent` in `recommender_rules.yaml`, guide `docs/guides/wishmatch.md`): once a day, films for each of you and for both, from what you watched in the last 30 days (Jellyfin played dates plus seen marks and ratings in the app). Those titles become a wish (moods they agree on, feelings most share, "avoid" from what you disliked); Wishmatch ranks as usual (tastes, crowd, IMDb/RT, well known, nothing seen or played lately). Each card says "Because you watched X". `GET /api/nl/daily-picks`; kept in `daily_picks.json`. Free, no model.

## Open work
- **Today's picks, next:** the "Picked for you today" row in the player app (web, TV, laptop); Labiba to choose whether the top joint pick downloads by itself.

## What changed
2026-11-06: server part for Labiba's "recommend some movies we might like based on what we have seen these days".
