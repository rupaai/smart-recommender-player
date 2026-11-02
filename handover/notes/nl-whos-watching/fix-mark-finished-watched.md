---
feature: nl-whos-watching
status: ready
---
## Line
- **M4 Who's watching** (merged, switched on in `homelab/config.yaml`: Labiba = Jellyfin user `rupaai`, Gabriele = `gabstevens`, by name since prod's users were re-created on 2026-10-21): people in `northern_lights.people` config, cookie `nl_viewer` (a person or "Both of us"), per-person Jellyfin user, progress written for each viewer (`/UserItems/{id}/UserData?userId=`), and **Played** written too when a viewer reaches the end or 90 percent (Jellyfin never marks it for the API-key session itself), per-person prefs (`/api/nl/prefs`), finishes rated to the right profiles.

## What changed
2026-11-02: nothing had ever been marked watched in Jellyfin (Recently Watched empty for both, Hoppers watched by both showed 2 percent and not played), so the watched cleanup never had anything to delete. Titles watched before this fix stay unmarked.
