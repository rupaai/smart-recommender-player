---
feature: watched-cleanup
status: ready
---
## Line
- **Watched cleanup** (`services/media_cleanup.py`, `media_cleanup:` in `homelab/config.yaml`): once a day, deletes a movie or episode watched more than 7 days ago, by either person on mars (`watched_by: any`; `all` = everyone). Keeps favourites (title or show, anyone's), anything someone is part way through, and anything on someone's watchlist (shared or "Just me"). Only under Movies and Shows; subtitles and empty folders go too. Real deletes on mars since 2026-11-02 (`dry_run: false`); the report counts why each other watched item stayed. Media cap on `mars`: `torrent.max_media_gb: 500`.

## Open work
none

## What changed
Labiba chose "watched by either of us" and real deletes on 2026-11-02.
