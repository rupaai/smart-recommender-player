# Northern Lights milestones

One file per milestone. Each one has the goal, the features (IDs from `../ROADMAP.md`), design notes, a step list, the checks, and a **build prompt** at the bottom to hand to a cheaper model (Sonnet or Haiku) in a fresh session.

| File | Milestone | Status |
|---|---|---|
| [M00-foundation.md](M00-foundation.md) | M0 Foundation: a movie plays through our code | done |
| [M01-player-mvp.md](M01-player-mvp.md) | M1 Player MVP | planned |
| [M02-browse.md](M02-browse.md) | M2 Browse like a real app | planned |
| [M03-tv-app.md](M03-tv-app.md) | M3 On the TV | planned |
| [M04-whos-watching.md](M04-whos-watching.md) | M4 Who's watching | planned |
| [M05-subtitles.md](M05-subtitles.md) | M5 Subtitles that fix themselves (switch-over point) | planned |
| [M06-picks-for-two.md](M06-picks-for-two.md) | M6 Picks for two | planned |
| [M07-polish.md](M07-polish.md) | M7 Polish | planned |
| [M08-casting.md](M08-casting.md) | M8 Casting | planned |
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
