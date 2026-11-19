---
feature: offline
status: ready
new: true
---
## Line
- **Save for offline** (`services/nl_offline.py`, `static/nl/offline.js`, routes `/api/nl/offline/...` in `web/routes/nl_library.py`; guide `docs/guides/playback.md`): Labiba, 2026-11-19, before a trip to Mexico. A button on title and show pages (phone, laptop, laptop player `/player`; not the TV) downloads the original file, untouched, and the subtitle files beside it, named "Title (Year).mkv" / "Show - S01E02.mkv" with matching subtitle names, to play in VLC or IINA without the server. A show saves the season on screen; "Save all" starts the files one after another. Only catalogue files inside Movies and Shows are sent; range requests let a broken download go on. Free. No smaller copy yet (would be a re-encode on mars's processor). Watching offline isn't reported back.

## What changed
New "Save for offline" button and its three routes.
