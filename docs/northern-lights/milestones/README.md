# Northern Lights milestones

One file per milestone. Each one has the goal, the features (IDs from `../ROADMAP.md`), design notes, a step list, the checks, and a **build prompt** at the bottom to hand to a cheaper model (Sonnet or Haiku) in a fresh session.

| File | Milestone | Status |
|---|---|---|
| [M00-foundation.md](M00-foundation.md) | M0 Foundation: a movie plays through our code | done |
| [M01-player-mvp.md](M01-player-mvp.md) | M1 Player MVP | done |
| [M02-browse.md](M02-browse.md) | M2 Browse like a real app | done |
| [M03-tv-app.md](M03-tv-app.md) | M3 On the TV | in progress (web part done) |
| [M04-whos-watching.md](M04-whos-watching.md) | M4 Who's watching | built (needs the second Jellyfin user and the `northern_lights.people` config; progress for "Both" not yet checked on a real Jellyfin) |
| [M05-subtitles.md](M05-subtitles.md) | M5 Subtitles that fix themselves (switch-over point) | built (needs the OpenSubtitles plugin in Jellyfin; TV Kotlin not compiled yet) |
| [M06-picks-for-two.md](M06-picks-for-two.md) | M6 Picks for two | built (nothing to configure; check `engine` in `GET /api/nl/picks` on the server: `recommender`, or `fallback` if the ML packages are missing) |
| [M07-polish.md](M07-polish.md) | M7 Polish | built in two commits: **M7a player polish** (thumbnails, chapters, skip intro, gestures, speed; needs Trickplay and the Intro Skipper plugin switched on in Jellyfin, and the new TV APK for speed on the TV; TV Kotlin not compiled yet) and **M7b browse polish** (Recently added / watched, Unwatched only, Hide from home, Cast & crew, Trailer, Tech details, More like this; nothing to switch on, uses `TMDB_API_KEY` when set; no Trailer on the TV) |
| [M08-casting.md](M08-casting.md) | M8 Casting | built, two commits: **M8a casting** built (Cast sender in the web player, receiver page, cast profile, signed stream tokens, phone as remote, hand-back; needs HTTPS for the phone, `northern_lights.cast_base_url`, `NL_CAST_SECRET`, optionally a $5 receiver registration, see RUNBOOK 6c; not tested with a real Chromecast) and **M8b phone keyboard for TV search** built (TV code plus locally drawn QR, `/nl/type/{code}` on the phone, nothing to configure) |
| [M09-couple-extras.md](M09-couple-extras.md) | M9 Couple extras | planned |
| [M10-health-settings.md](M10-health-settings.md) | M10 Health and settings | planned |

## Rules every milestone follows

- **Branches:** start from `northern-lights-player-app`, work on your own branch, and open the PR **into `northern-lights-player-app`**, never into `main`. One milestone per PR (a big one can be split into two PRs).
- **Jellyfin stays hidden.** The browser and the TV only ever talk to our FastAPI app. Our app talks to Jellyfin with the server-side API key (`services/jellyfin_service.py` already loads `JELLYFIN_URL`, `JELLYFIN_API_KEY`, `JELLYFIN_USER_ID`). The API key must never reach the browser, in JSON or inside a playlist.
- **Separate from the current UI.** New pages live under `/nl/...`, new API routes under `/api/nl/...`, new front-end files under `static/nl/` and `templates/nl/`. Don't change the existing ticket-look app (`templates/index.html`, `static/app.js`) unless the milestone says so.
- **Look:** follow `PRODUCT.md` (the Northern Lights ticket look, aurora only as foil). The player itself is dark and minimal.
- **Tests:** script-style like `tests/test_healthz.py` (each file runs itself and exits non-zero on failure). No real Jellyfin in tests: fake the HTTP calls. `make check` must pass (CI runs it on every PR).
- **Project rules:** update `PROJECT_HANDOVER.md` (branch list at the top plus a short section for the milestone), tick the milestone's status in this table, commit, and push.

## Generic prompt header (paste above each milestone's prompt)

```
You're working in the repo rupaai/smart-recommender-player (FastAPI + vanilla JS, SQLite).
Read PROJECT_HANDOVER.md, docs/northern-lights/ROADMAP.md, docs/northern-lights/milestones/README.md
and the milestone file named below before writing code. Follow the "Rules every milestone follows"
section exactly. Branch from `northern-lights-player-app` and open your PR into it.
```
