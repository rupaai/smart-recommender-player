---
feature: deployment
status: ready
---
## Line
Live on `mars` under Homelab (`homelab.yaml`; Jellyfin and Prowlarr are Homelab services). CI (`.github/workflows/ci.yml`) runs `make check` and pushes a linux/amd64 image `ghcr.io/rupaai/smart-recommender-player:sha-<sha>` for every push to `main`; Homelab puts it on dev, and prod waits for Promote. Prod answers on port 7101 (2026-10-23; older notes say 8000). Secrets (`TMDB_API_KEY`, `JELLYFIN_API_KEY`, ...) are set in the Homelab companion and arrive as files in `/run/secrets` (`secrets_as: files`; `services/homelab_secrets.py` copies them into the environment at start-up, older Homelab still uses env vars; values are trimmed of spaces and line breaks); `ADMIN_TOKEN` is made by Homelab (`generate_secrets`; copy it from Apps -> Northern Lights -> Secrets), and the cast secret is kept by the app in `/data`. Running downloads are stored in SQLite and start again after a restart. The old couchcinema deploy scripts are gone; `docs/guides/deployment.md` has the steps to remove what is left on `mars`. Admin API under `/admin` (Bearer `ADMIN_TOKEN`) for exports, catalogue patches and model bundles. Mac training run, no schedule (Labiba's choice, 2026-10-19: the Mac is off at night): the **Train CouchCinema** button (`ops/mac/train-now.sh`, put in `~/Applications` by `ops/mac/install.sh`, which also removes the old launchd job) checks `/healthz`, then runs `ops/mac/nightly.sh`: backup, catalogue refresh, TIGER retrain, vibe index build, eval gate, publish bundle; catalogue refresh when `~/.couchcinema/last-catalogue` is a week old. Full steps: `ops/mac/README.md`.

## What changed
The steps to remove the old setup moved from README.md to docs/guides/deployment.md (README was rewritten).
