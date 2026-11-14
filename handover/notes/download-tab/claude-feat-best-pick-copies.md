---
feature: download-tab
status: ready
---
## Line
Done. Downloads start only from the Download tab ("Choose a copy" on Discover jumps there with the catalogue link). `services/torrent_ranking.py` scores quality × seeder health × size fit × scope match; tuning numbers in `config.yaml` `torrent_ranking:`. Numbers were tuned on sample lists only; tune from real results.
Each result's download slot is keyed by the torrent's info hash (`downloadSlotId` in `static/app.js`), never by its position in the list, so re-sorting or searching again keeps progress, Cancel and Play on the copy that was picked; the same torrent listed twice is drawn once (`tests/js/direct_download_slots_check.js`).
**Best pick** (2026-11-14, Labiba: "I usually go for the best one if it has decent seed"): one-tap downloads (New & popular, Today's picks, new episodes by themselves) take `best_pick_order()`'s first copy: best quality with decent seeders (`torrent_ranking.best_pick`: 10 + 0.25 per GB), the bigger file first within a quality, far-too-small copies last, Best match order when none is decent. "Other copies" marks Best pick and "few for this size".

## Open work
- **Get it page, next:** a player-app page to search a title, see every season of a show on one page and pick a copy (proposal https://claude.ai/artifact/3chNPyx3zB8Z4m86WgNA1N, waiting for Labiba's OK).

## What changed
New `best_pick_order()` and `torrent_ranking.best_pick` config; `NewEpisodes.copies()` uses it.
