---
feature: nl-player
status: ready
---
## Line
- **M0-M1 Player** (merged): `jellyfin_playback.py`, `device_profiles.py` (browser profile from probed codecs), `static/nl/player-core.js` (the only file touching hls.js, the `<video>` or the native bridge), `player.js`. Audio and subtitle menus, resume, skip-length chip, progress reports. **Tracks per title** (2026-10-29, Labiba): every audio or subtitle pick (also "Off") is saved per viewer and movie/show (`POST /api/nl/tracks/{id}`, table `nl_track_memory`, schema v23, `services/nl_track_memory.py`) and wins over the language prefs on the next play; same file = same stream index, another episode = the best track with the same language and kind (forced, SDH, picture, commentary). A viewer without their own choice gets the latest anyone made for that title.

## What changed
Picking Italian audio or English subtitles once is enough: the film, or the next episode of the show, starts with them on the web, laptop and TV. No TV app update needed.
