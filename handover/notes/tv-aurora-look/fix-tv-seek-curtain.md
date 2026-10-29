---
feature: tv-aurora-look
status: ready
---
## Line
- **TV soft aurora look** (2026-10-29, CSS and TV JS only, server and TV app unchanged): TV pages, laptop `/player` pages and the TV player use deep navy (#0c1220) with three faint glows on one fixed CSS layer (no image), DM Sans from `/static/fonts` (the Archivo face stays only for the phone pages), a 2 px soft-cyan focus ring (#8fd3ff) with a dark shadow and a small scale, and a violet-pink-blue gradient on the main button and the progress and seek lines. Continue watching tags each still "Series" or "Movie"; posters start the meta line with the type and show a "Details ›" chip on focus; the home page says "Press OK to open a title". The TV player's seek bar is a slim film strip (sprocket dashes, chapters as frame gaps, played part tinted) that stays steady when seeking. Play on a title or show page parts two dark halves from an aurora line in the middle and shows the title big in aurora colours, then opens the player (1.8 s, once, skipped with reduced motion). Direction notes: `.impeccable/surfaces/static-nl-tv-tv-css.md`. Not yet seen on the real TV.

## Open work
- **TV soft aurora look, next:** check the seek bar and the Play curtain on the real TV.

## What changed
- Seek bar is much slimmer and no longer wobbles when seeking.
- Play opens from a middle line, with the title in aurora colours.
