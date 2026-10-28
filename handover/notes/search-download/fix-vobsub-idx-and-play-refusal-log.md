---
feature: search-download
status: ready
---
## Line
- **A. Search and download** (`torrent_service.py`): providers Prowlarr (optional, `PROWLARR_URL`/`PROWLARR_API_KEY`) then YTS then Apibay. Downloads run as asyncio tasks tracked in the in-memory `downloads_store`; finished files are organised into Jellyfin's layout (`Movie (Year)/…`, `Show (Year)/Season XX/Show - SxxEyy`) and registered in `media_sources`. Healthy Prowlarr indexers: YTS, ThePirateBay, NyaaSi, Knaben (EZTV unreachable; 1337x and friends need FlareSolverr, left out). Disk guard: `torrent.max_media_gb`, `torrent.min_free_gb`. A VobSub picture subtitle (`.sub`) moves with its `.idx` under the same new name (2026-10-28: "5 Centimeters per Second" got a `.sub` whose `.idx` stayed in downloads, so it could not be read).
