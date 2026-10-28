---
version: 1
slug: "static-nl-tv-tv-css"
primary_target: "static/nl/tv/tv.css"
related_targets: ["templates/nl/tv/base.html","static/nl/player.css","static/nl/subtitles.css"]
---

# TV app (10-foot) surface

Scope: Northern Lights on the TV and the laptop /player pages: `templates/nl/tv/*`, `static/nl/tv/tv.css`, the `.tv` part of `static/nl/player.css`, `.nls-tv` in `static/nl/subtitles.css`. Mode: Operate (browse, pick, play with a D-pad remote). Server and Android app untouched.

Audience: Labiba and Gabriele on the sofa, evening, lights low, 2-3 m from a TCL Google TV (1.8 GB RAM, ~245 MB free while playing).

Brand note: Labiba (2026-10-28) asked for a newer, much more aurora-borealis look on the TV while staying responsive, fast and simple. This overrides PRODUCT.md's "aurora only as foil" for the TV surface only; the light ticket look stays on phone/web pages.

## Direction contract
THESIS: The TV is a window on a polar night; the aurora hangs in the sky above the shelves and the remote's focus is the light it casts. Refuses the stock streaming-app grey with a red/orange accent.
OWN-WORLD: Polar-night ground (#060a13 to #0a1222), oxygen green #46f0a6 as the action and focus light, teal #2fd3c9 and violet #8f72ff as the curtain's falloff, a faint rose fringe. Archivo (self-hosted variable): wordmark wide and tracked, headings sentence case, titles condensed. Pills and buttons rounded, translucent on the night.
STORY: You know where focus is from across the room, see what to watch next, press OK.
FIRST VIEWPORT: Aurora sky fixed behind the bar and first shelf (top ~60% of the screen, fading into night); wordmark left, Home/Downloads/Search pills, person's initial right; Continue watching first row in full light.
FORM: pinned by the user (aurora); seed: none (unavailable). Signature move: focus as aurora light (green ring plus a downward glow) on every card and button, and a green-to-teal progress/seek line. Still sky, painted once (no running animation) unless Labiba picks drift.
FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
