---
feature: nl-laptop
status: in-progress
new: true
---
## Line
- **Laptop app at /player** (in progress): the TV pages and player served for a laptop browser at `/player/...` (surface `laptop`, root `/player/`; same templates and tv.js as `/nl/tv/`), mouse hover/click/wheel plus arrow keys.

## Open work
- **Laptop app, next:** laptop controls in the player (volume, fullscreen, keyboard shortcuts) and original quality (no 20 Mbps cap).

## What changed
`/player/...` routes in server.py reuse the TV templates with `surface=laptop`; `base.html` links follow `root`; `tvHref` and `player.js` follow `data-root`.
