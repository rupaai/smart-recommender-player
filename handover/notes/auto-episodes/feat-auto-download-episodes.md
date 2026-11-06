---
feature: auto-episodes
status: ready
new: true
---
## Line
- **New episodes by themselves** (`services/nl_auto_episodes.py`, `auto_episodes:` in config, on in prod; guide `docs/guides/new-episodes.md`): every 3 hours mars keeps the next 3 unwatched episodes ready for each show you're watching (files on the server and watched or added in the last 60 days), one single episode at a time in order, never a season pack. "Ready" counts files after the last episode the person furthest along watched (Jellyfin). Disk limits are the downloader's. Last run: `GET /api/nl/auto-episodes`. Free (TMDB).

## Open work
- **New episodes by themselves, next:** not yet run on mars. Show the last run on `/nl/admin`.

## What changed
2026-11-06: Labiba's "download by the episode order, so I can start watching already".
