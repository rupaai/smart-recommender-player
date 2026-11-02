---
feature: delete-from-server
status: ready
new: true
---
## Line
- **Delete from server** (`services/nl_delete.py`, `static/nl/delete.js`, `/api/nl/delete/{id}`): a button on the web and TV title and show pages deletes a movie, one season or a whole show for everyone. A panel first shows what goes and the space it frees; "Keep it" is focused, and the server refuses without `confirm: true`. Same file rules as the watched cleanup (only under Movies and Shows; subtitles and empty folders go too). The catalogue forgets the files; ratings and history stay. Jellyfin rescans.

## What changed
New. Labiba asked to clean up the server by hand (2026-11-02); the automatic cleanup is still in dry run.
