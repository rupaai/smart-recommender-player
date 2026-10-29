---
version: 1
slug: "static-nl-tv-tv-css"
primary_target: "static/nl/tv/tv.css"
related_targets: ["templates/nl/tv/base.html","static/nl/player.css","static/nl/subtitles.css"]
---

# TV app (10-foot) surface

Scope: Northern Lights on the TV and the laptop /player pages: `templates/nl/tv/*`, `static/nl/tv/tv.css`, the `.tv` part of `static/nl/player.css`, `.nls-tv` in `static/nl/subtitles.css`. Mode: Operate (browse, pick, play with a D-pad remote). Server and Android app untouched.

Audience: Labiba and Gabriele on the sofa, evening, lights low, 2-3 m from a TCL Google TV (1.8 GB RAM, ~245 MB free while playing).

Brand note: Labiba (2026-10-29) replaced the bright-sky polar-night look with a calmer soft aurora, as approved from mockups (Main, DarkSoft, FilmStrip, LightsDown) and the style prompt. It overrides PRODUCT.md's "aurora only as foil" for the TV surface only; the light ticket look stays on phone/web pages.

## Direction contract
THESIS: A calm, premium night after the lights are off: deep navy with faint, wide aurora glows. Never a bright sky, never a loop.
OWN-WORLD: Ground #0c1220 with three faint radial glows (blue top left, magenta right, teal bottom) on one fixed layer. Text #eef0f6, secondary #9aa3b8, hairlines rgba(238,240,246,.14). The violet-pink-blue aurora gradient (#9b5de5, #e2479a, #7fb2ec) only on the main button, progress lines and the seek line. DM Sans (self-hosted variable): headings 600-700, tight, sentence case; wordmark small, uppercase, tracked 0.2em. Radius 10-12px at 960 wide, secondary buttons outlined.
STORY: You know where focus is from across the room, you can tell a movie from a series, you know OK opens a page, and Play dims the room.
FIRST VIEWPORT: Wordmark, quiet Home/Downloads/Search (current page has a soft tint), the person's initial; Continue watching with Series / Movie tags; poster shelves with the type in the meta line.
FORM: Focus = a 2 px soft-cyan ring (#8fd3ff), a dark shadow below and a scale of 1.04-1.08; no glow halos. Cyan is only for focus. Chosen = soft tint and brighter text, no ring. Movie touches (only two): Lights down on Play (opacity only, once, under 0.8 s) and the film-strip seek bar (sprocket holes, chapters as frame gaps, played part tinted, thin white playhead).
RULES: No ongoing animation, no backdrop-filter, no blur or saturate filters, no blend modes, no big images. Text at least 1.5rem at 1920 and contrast 4.5:1 (24px text counts as large). Inline SVG icons in one stroke weight.
FINISH: Not yet seen on the real TV.
