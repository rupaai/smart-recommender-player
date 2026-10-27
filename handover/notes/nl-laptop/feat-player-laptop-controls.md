---
feature: nl-laptop
status: in-progress
new: true
---
## Line
- **Laptop app at /player** (in progress): TV pages and player for a laptop browser at `/player/...` (same templates and tv.js as `/nl/tv/`), with mouse, web keyboard shortcuts, volume, fullscreen and cast in the player.

## Open work
- **Laptop app, next:** original quality (no 20 Mbps cap).

## What changed
`player.js` splits the big-screen look (`bigScreen`, TV and laptop) from remote input (`remote`, Android app and `?tv=1`); `NLPlayerExtras.inputMode` decides. `player.css` `.tv.laptop` brings back the centre button, mute, volume and fullscreen.
