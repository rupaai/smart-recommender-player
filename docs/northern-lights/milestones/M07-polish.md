# M7 Polish

**Features:** P21, P22, P23, P14, P17, L6, L8–L10.

## Design
- **Seek-bar thumbnails (P21):** turn on Jellyfin's Trickplay (Dashboard → Libraries → Trickplay). It builds tile sheets; read them via `/Videos/{id}/Trickplay/{width}/tiles.m3u8` through the proxy and show the right tile on seek-bar hover or scrub. On the TV, show the thumbnail while holding left/right.
- **Chapters (P22):** from `Chapters` in the item metadata. Show ticks on the seek bar and a chapter list in a menu.
- **Skip intro / credits (P23):** install the Intro Skipper plugin. Jellyfin 10.10+ exposes `GET /MediaSegments/{id}`. Show "Skip intro" while inside an Intro segment and "Next episode" at Outro. Add a per-show "always skip intro" toggle (stored in `nl_show_prefs`).
- **Phone gestures (P14):** tap toggles controls, double-tap left/right half skips, swipe up/down on the right half changes volume where the browser allows it.
- **Speed (P17):** 0.75× / 1× / 1.25× / 1.5× / 2× in a menu.
- **Rows and filters (L6, L8):** Recently Added and Recently Watched rows; an "Unwatched only" filter; "Hide from home" on a card's menu.
- **Extras (L9, L10):** collapsible Cast & crew, Trailer (TMDB YouTube key, opened in a new tab or YouTube on the TV) and Tech details (resolution, codecs, file size); a "More like this" row from TMDB similar plus the recommender.

## Build prompt
```
<generic header from README.md>
Milestone: docs/northern-lights/milestones/M07-polish.md

Polish pass; split into small PRs into northern-lights-player-app (e.g. thumbnails+chapters,
segments/skip intro, gestures+speed, rows/filters/extras). Each PR is independently testable.
Proxy allowlist additions must stay GET-only and limited to the exact Jellyfin paths needed.
```
