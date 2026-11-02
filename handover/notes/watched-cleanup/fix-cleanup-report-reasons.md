---
feature: watched-cleanup
status: ready
---
## Line
- **Watched cleanup** (`services/media_cleanup.py`, `media_cleanup:` in `homelab/config.yaml`): once a day, deletes a movie or episode that every configured person watched, the last of them over 7 days ago, unless it or its show is a Jellyfin favorite. Only under Movies and Shows; subtitles and empty folders go too. Ships in **dry run** (admin page and log list what would go); set `dry_run: false` to delete. The report also counts why each other watched item stayed ("12 not watched by everyone yet, 3 watched too recently"). Media cap on `mars`: `torrent.max_media_gb: 500`.

## Open work
- **Watched cleanup, next:** still in dry run on mars (2026-11-02, nothing deleted ever). Labiba to choose the rule (both watched / either watched) after reading the reasons on `/nl/admin`, then set `dry_run: false`.

## What changed
2026-11-02: prod's last run listed nothing and gave no reason. Media was at 486 of 500 GB.
