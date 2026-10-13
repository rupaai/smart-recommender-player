# M4 Who's watching

**Features:** U1–U3.
**Done when:** the app opens on "Who's watching? Labiba · [partner] · Both of us", each of you has your own progress and languages, and "Both of us" updates both.

## Design
- **Two Jellyfin users**, one per person, created once by hand in Jellyfin's dashboard (document this in the runbook). Their ids go in config (e.g. `northern_lights.people: [{name, jellyfin_user_id, profile_id}]`). `profile_id` links to the recommender's existing profiles (`/api/profiles`).
- **Who's watching screen (U1):** shown on launch and from the header. The choice is stored in a cookie (`nl_viewer=labiba|partner|both`). No passwords; the app is only reachable at home and on your tailnet.
- **Progress (U2):** each person's progress goes to their own Jellyfin user. **Both** reports progress to both users (two sessions); Resume uses the further position and shows "you're at 42:10, he's at 38:00" if they differ by more than 2 min.
- **Language preferences (U3):** per person, preferred audio and subtitle languages plus "subtitles on/off by default", stored in a new `nl_person_prefs` table. `/api/nl/play` picks default tracks from them. "Both" uses Labiba's preferences unless set separately.
- **Recommender link:** watch events and ratings go to the matching profile(s), so the existing co-view logic gets the right signal.

## Checks
Tests: viewer cookie parsing, Both reporting to two users, the further-position resume rule, default track choice from prefs. `make check`.

## Build prompt
```
<generic header from README.md>
Milestone: docs/northern-lights/milestones/M04-whos-watching.md

Add the who's-watching flow. Read how /api/profiles and co-view ratings work in server.py and
database.py first and reuse those profiles. Config-driven people list; no passwords. Every
Jellyfin call that was using JELLYFIN_USER_ID now uses the viewer's user id(s). Tests + make
check, handover + README, PR into northern-lights-player-app.
```
