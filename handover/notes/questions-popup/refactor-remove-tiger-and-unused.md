---
feature: questions-popup
status: ready
---
## Line
- **10.6 Questions pop-up:** done. It runs on the mood map (`MoodSession`, §10.10) and opens on every search while "Ask me a few questions first" is on; with `engine: wishmatch` it is Wishmatch's own questions (§17). If it never opens, the serving catalogue has no facets or mood tags (`GET /api/vibe/status` shows `mood_data`). The old dial-based `PopupSession` and its question bank were removed on 2026-10-31.
