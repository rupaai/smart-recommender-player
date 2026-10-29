---
feature: tv-aurora-look
status: ready
---
## Line
- **TV soft aurora look** (2026-10-29, CSS and TV JS only, server and TV app unchanged): TV pages, laptop `/player` pages and the TV player use deep navy (#0c1220) with three faint glows on one fixed CSS layer (no image), DM Sans from `/static/fonts` (the Archivo face stays only for the phone pages), a 2 px soft-cyan focus ring (#8fd3ff) with a dark shadow and a small scale, a violet-pink-blue gradient only on the main button and the progress and seek lines, and nothing animated. Continue watching tags each still "Series" or "Movie"; posters start the meta line with the type and show a "Details ›" chip on focus; the home page says "Press OK to open a title". The TV player's seek bar is a film strip (sprocket holes, chapters as frame gaps, played part tinted). Play on a title or show page fades the lights down once (opacity only, 0.7 s, skipped with reduced motion). Direction notes: `.impeccable/surfaces/static-nl-tv-tv-css.md`. Not yet seen on the real TV.

## Open work
none

## What changed
Replaces the polar-night look with sky image and green focus (PR #150). Removes `aurora-sky.webp` and `scripts/render_aurora_sky.js`.
