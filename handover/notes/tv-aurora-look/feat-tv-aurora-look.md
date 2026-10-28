---
feature: tv-aurora-look
status: ready
new: true
---
## Line
- **TV aurora look** (2026-10-28, CSS only, server and TV app unchanged): TV pages, laptop `/player` pages and the TV player use a polar-night ground (#070b15) with a still aurora sky behind the top of every page (`static/nl/tv/aurora-sky.webp`, 24 KB, made by `scripts/render_aurora_sky.js`, on a fixed layer, never animated, so scrolling never repaints it). Focus is aurora green (#46f0a6) with a soft glow below; primary buttons, progress and the seek line are green to teal. Archivo is now loaded from `/static/fonts` on the TV. Direction notes: `.impeccable/surfaces/static-nl-tv-tv-css.md`. Not yet seen on the real TV.

## What changed
Labiba asked for a newer, more aurora look on the TV that stays fast and simple.
