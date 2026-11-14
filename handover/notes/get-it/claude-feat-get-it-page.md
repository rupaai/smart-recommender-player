---
feature: get-it
status: in-progress
new: true
---
## Line
- **Get it** (`services/nl_get_it.py`, `web/routes/nl_get.py`, `static/nl/get-it.js`; pages `/nl/get`, `/nl/tv/get`, `/player/get`): the player app's place to start a download (Labiba, 2026-11-14: the old Download tab was "too scattered"; plan https://claude.ai/artifact/3chNPyx3zB8Z4m86WgNA1N). Search TMDB titles (year shown, same names flagged), pick one, so files are filed under that title. A show is one page with every season: out, on the server, missing, "Get season" (best pick), "Choose a copy", single episodes. Reuses New & popular's copies and download. "Find a copy" links and TV search results not on the server open it. The old Download tab still works.

## Open work
- **Get it, next:** the "Are these the same show?" question to put look-alike library shows together, and the question when a copy's name points at another show. Then retire the old Download tab. Not yet tried on mars or the TV.

## What changed
First Get it PR: search, title page, all seasons on one page, copy chooser on web, TV and laptop.
