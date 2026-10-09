# Deployment plan: Mac trains, OptiPlex serves

Written 2026-10-09. Status: **plan only, nothing built yet.** Build one task at a time, only when Labiba says go.

This plan is written to be executed by Sonnet and Haiku sessions. Each task says which model fits, which files it touches, and how to tell it is done. Read the **Rules for whoever builds this** section before starting any task.

---

## 1. The shape in one picture

```mermaid
flowchart LR
  subgraph GH[GitHub]
    CI[CI: lint, tests, image build] --> GHCR[(Image registry GHCR)]
    DEPLOY[Deploy workflow, manual button]
  end
  subgraph SRV[OptiPlex 3060, Ubuntu, Docker]
    RUNNER[Actions runner] --> APP[couchcinema app + downloads]
    APP --- JF[Jellyfin]
    APP --- PR[Prowlarr]
    APP --- DB[(catalogue_full.db, user data)]
    APP --- MOD[(models/current)]
  end
  subgraph MAC[Mac, launchd jobs]
    NIGHTLY[nightly: backup, catalogue, enrich, train, eval]
  end
  DEPLOY -. job pulled outbound .-> RUNNER
  GHCR --> RUNNER
  NIGHTLY -- Tailscale: export, patch, model bundle --> APP
```

- **Server (Dell OptiPlex 3060, 16GB, Intel 8th gen, x86_64):** runs only what the two of you use: the web app (which also does the torrent downloads), Jellyfin and Prowlarr, each in its own Docker container, always restarting. No torch, no training.
- **Mac:** does the heavy and occasional work as scheduled jobs: backup pull, catalogue refresh, enrichment, training, evaluation, publishing models.
- **GitHub:** checks every change. Deploys happen only when Labiba presses "Run workflow" on the Deploy workflow, and only for a commit whose checks passed.
- **Network:** everything between Mac and server goes over Tailscale. The server needs no open ports and no Tailscale rule changes, because the Actions runner connects *out* to GitHub.

## 2. Decisions already made (don't reopen)

| Topic | Decision |
|---|---|
| Server hardware | Dell OptiPlex 3060, 16GB RAM, x86_64. Images are `linux/amd64`. Jellyfin uses Intel Quick Sync via `/dev/dri`. |
| Server OS | Ubuntu Server 24.04 LTS. |
| Isolation | Docker Compose, one container per service, `restart: unless-stopped`. |
| Media storage | Internal drive now (`/srv/media`), external HDD later (`/mnt/media`). Only the `MEDIA_ROOT` value changes. |
| User data | Lives on the server, on the internal drive, always. Never on the external drive. |
| Backups | User data only (the SQLite DB). Never media. |
| Downloads | Run on the server, files land on the server. |
| Training | On the Mac only. |
| Deploy access | Self-hosted GitHub Actions runner on the server (outbound only). No Tailscale ACL changes for CI. |
| Deploy trigger | Manual (`workflow_dispatch`), only for a commit with green CI. Automatic rollback if the health check fails. |
| Code host | GitHub (already there; least friction). |

## 3. The one hard problem: ids

Read this before touching any data task. It is the reason the data flow looks the way it does.

- `catalogue_full.db` holds **both** the catalogue (`media_items`, `vibe_clusters`, …) **and** the user data (`watch_history`, `rating_details`, `watch_later`, `profiles`, …) in one file.
- User tables point at `media_items.id`, an autoincrement integer.
- The model files point at the same ids: `models/tiger/semantic_ids.json`, `item_map.json` and `models/vibe/item_ids.json` are keyed by `media_items.id`.
- The app adds catalogue rows on its own too (Rate search imports from TMDB via `tmdb_import`).

So:

1. **The server is the only place ids are assigned.** After cutover the Mac never ships a whole database to the server.
2. **The server never deletes or renumbers `media_items`.** "Garbage" titles are simply never added, or are hidden with a flag, so old model files never point at a missing id.
3. **Everything the Mac sends that changes the database is a patch keyed by `(media_type, tmdb_id)`**, never by `id`. The server maps it to its own ids when applying.
4. **The Mac always trains on a fresh copy of the server database**, pulled after its catalogue patch was applied, so the ids in the model match the server's ids.

## 4. Layout on the server

```
/srv/couchcinema/
  compose.yaml          # copied from the repo by each deploy
  .env                  # secrets, written once by hand (TMDB, Jellyfin, Prowlarr, ADMIN_TOKEN, NTFY_TOPIC)
  config.server.yaml    # server version of config.yaml (paths under /media)
  data/catalogue_full.db
  models/releases/<bundle_id>/   # last 3 model bundles
  models/current -> releases/<bundle_id>
  backups/pre-deploy/   # DB snapshot before each deploy, keep 7
  deployed_tag          # image tag currently running
  jellyfin/config, jellyfin/cache, prowlarr/config
/srv/media/Movies, /srv/media/Shows, /srv/media/downloads   # MEDIA_ROOT, moves to /mnt/media later
```

**Path rule:** every container mounts `MEDIA_ROOT` at the same path, `/media`. The app tells Jellyfin about files by path, so the path must be identical inside both containers.

---

## 5. Rules for whoever builds this

1. Read `PROJECT_HANDOVER.md` and this file first. Don't read the whole repo.
2. One task = one branch from `main` = one PR. Name branches `deploy/<task-id>`, e.g. `deploy/p1-2`.
3. Run `uv run pytest -q` before every push. Never skip or delete a test to get green.
4. Push after each finished task, then update `PROJECT_HANDOVER.md` (branch list and section 13) in the same PR.
5. Tick the task's checkbox in this file in the same PR.
6. Never put secrets in the repo. Secrets go in the server's `.env` or the Mac's `~/.couchcinema/env`.
7. Don't change recommender behaviour in these tasks. This is plumbing.
8. Tasks marked **[Labiba]** are manual steps on the machines; write them as a runbook, don't try to run them.
9. Haiku tasks are small and mechanical. If a Haiku task turns out to need a design choice, stop and hand it to Sonnet.

---

## Phase 1: Make the app portable (no behaviour change)

### [ ] P1.1 Dev tooling (Haiku)
- Add a `dev` dependency group in `pyproject.toml` with `pytest` and `ruff`; refresh `uv.lock`.
- Add `[tool.ruff]` with a deliberately small rule set so nothing gets reformatted: `select = ["E9", "F63", "F7", "F82"]` (syntax errors and undefined names only).
- Add a `Makefile` with `check: ruff check . && pytest -q`.
- **Done when:** `uv sync --group dev && make check` passes locally.

### [ ] P1.2 Config and paths from environment (Sonnet)
- `server.py` has `CONFIG_PATH = "config.yaml"`. Read `os.getenv("CONFIG_PATH", "config.yaml")` instead.
- Add `MODELS_DIR` (default `models`) and use it everywhere `models/` is hard-coded: `services/recommender_service.py`, `services/generative_retriever.py` (respect existing `TIGER_MODEL_DIR` if set), `services/vibe_index.py`, `server.py`.
- Add `deploy/config.server.yaml`: a copy of `config.yaml` with `media_folders: [/media/Movies, /media/Shows]`, `jellyfin_library.movie_dir: /media/Movies`, `tv_dir: /media/Shows`, `torrent.download_dir: /media/downloads`, `database_path: /data/catalogue_full.db`, and `enable_upnp: false`, `enable_natpmp: false`.
- **Done when:** tests pass, and `CONFIG_PATH=deploy/config.server.yaml MODELS_DIR=/tmp/x uv run python -c "import server"` starts without touching `/Users/...` paths.

### [ ] P1.3 Health and version endpoint (Haiku)
- Add `GET /healthz` returning `{"ok": true, "git_sha": $GIT_SHA, "model_bundle": <id from models/current/manifest.json or null>, "db_schema": <schema version>}`. It must also run `SELECT 1` on the DB and return 503 if that fails.
- Add a test in `tests/test_healthz.py` using FastAPI's `TestClient`.
- **Done when:** the test passes.

### [ ] P1.4 Split serve and train dependencies (Haiku)
- Confirm nothing in `server.py`, `services/`, `database.py`, `scanner.py`, `enricher.py` imports torch, sentence-transformers or anthropic at module level. Training already uses `uv run --with torch`; keep it that way.
- Move `anthropic` usage behind the existing `llm` extra (already done; just verify).
- **Done when:** `uv sync --no-dev` (no extras) is enough to import `server`.

## Phase 2: Containers

### [ ] P2.1 Dockerfile (Sonnet)
- `Dockerfile` at repo root: `python:3.12-slim`, install uv, `uv sync --frozen --no-dev`, copy app code (not `training/`, `sample_library/`, `*.db`, `models/`), run as a non-root user, `EXPOSE 8000`, `HEALTHCHECK` hitting `/healthz`, `CMD ["uv","run","--no-sync","python","server.py"]`.
- Bake `ARG GIT_SHA` into `ENV GIT_SHA`.
- `.dockerignore` mirroring the above plus `.git`, `.venv`, `downloads`, `backups`, `reports`.
- Check that `libtorrent` installs from wheels on `linux/amd64` for the pinned version. If not, pin to the newest version that has a wheel and say so in the PR.
- **Done when:** `docker build --platform linux/amd64 .` succeeds and the image is under 600MB.

### [ ] P2.2 Compose file (Sonnet)
- `deploy/compose.yaml` with three services, all `restart: unless-stopped`, all on one network:
  - `app`: image `ghcr.io/rupaai/smart-recommender-player:${IMAGE_TAG}`, env from `.env` plus `CONFIG_PATH=/config/config.server.yaml`, `MODELS_DIR=/models/current`, `DATABASE_PATH=/data/catalogue_full.db`, `JELLYFIN_URL=http://jellyfin:8096`, `PROWLARR_URL=http://prowlarr:9696`. Mounts `./data:/data`, `./models:/models`, `./config.server.yaml:/config/config.server.yaml:ro`, `${MEDIA_ROOT}:/media`. Ports `8000:8000` and `6881-6891:6881-6891` (tcp+udp).
  - `jellyfin`: `jellyfin/jellyfin:<pinned version>`, mounts `./jellyfin/config`, `./jellyfin/cache`, `${MEDIA_ROOT}:/media`, `devices: [/dev/dri:/dev/dri]`, port `8096:8096`.
  - `prowlarr`: `lscr.io/linuxserver/prowlarr:<pinned version>`, mounts `./prowlarr/config`, port `9696:9696`.
- Pin every third-party image to an exact version (no `latest`).
- `deploy/.env.example` listing every variable with a comment and no values: `IMAGE_TAG`, `MEDIA_ROOT`, `TMDB_API_KEY`, `JELLYFIN_API_KEY`, `JELLYFIN_USER_ID`, `PROWLARR_API_KEY`, `ADMIN_TOKEN`, `NTFY_TOPIC`.
- **Done when:** on the Mac, with a local image tag and `MEDIA_ROOT=./sample_library`, `docker compose -f deploy/compose.yaml up` serves the app on :8000 and Jellyfin on :8096, and `/healthz` is ok.

## Phase 3: CI (checks on every change)

### [ ] P3.1 CI workflow (Sonnet)
- `.github/workflows/ci.yml`, on `pull_request` and `push` to `main`:
  - job `check` (`ubuntu-latest`): checkout, `astral-sh/setup-uv`, `uv sync --frozen --group dev`, `make check`.
  - job `image` (needs `check`): `docker/setup-buildx-action`, `docker/build-push-action` for `linux/amd64` with `GIT_SHA` build arg and GHA layer cache. On PRs build only; on `main` push to `ghcr.io/rupaai/smart-recommender-player:sha-<full sha>` and `:main`, using `GITHUB_TOKEN` with `packages: write`.
- **Done when:** a PR shows both jobs green, and after merge the image appears under the repo's Packages.

### [ ] P3.2 Dependency updates (Haiku)
- `.github/dependabot.yml` for `pip`/uv (`/`), `docker` (`/` and `/deploy`), and `github-actions`, weekly, grouped into one PR per ecosystem.
- **Done when:** the file validates (Dependabot shows it under Insights → Dependency graph).

### [ ] P3.3 Branch protection [Labiba]
- On GitHub: Settings → Branches → protect `main`, require the `check` and `image` jobs to pass before merging.

## Phase 4: Set up the server once [Labiba]

Write this as `deploy/RUNBOOK.md` (Haiku writes the runbook; Labiba runs it). Steps:

1. Install Ubuntu Server 24.04 LTS. Enable `unattended-upgrades` for security patches.
2. Install Docker Engine and the compose plugin from Docker's apt repo. Add your user to `docker`.
3. Install Tailscale and join it to your boyfriend's tailnet (he approves the device once). Note its MagicDNS name, e.g. `optiplex`.
4. Create `/srv/couchcinema` and `/srv/media/{Movies,Shows,downloads}` as in section 4. Copy `deploy/config.server.yaml` there.
5. Write `/srv/couchcinema/.env` from `.env.example`. Generate `ADMIN_TOKEN` with `openssl rand -hex 32`; put the same token in the Mac's `~/.couchcinema/env`.
6. Log Docker in to GHCR with a read-only token (`read:packages`) so it can pull private images.
7. Install the GitHub Actions runner from repo Settings → Actions → Runners → New self-hosted runner (Linux x64). Give it the label `optiplex`, install it as a service (`./svc.sh install && ./svc.sh start`) under a dedicated `deploy` user in the `docker` group.
8. **Cutover of your data:** stop the app on the Mac, copy the Mac's `catalogue_full.db` to `/srv/couchcinema/data/`, and copy existing media into `/srv/media`. From now on, rate and watch only through the server. The Mac's copy becomes a dev copy.
9. Point Jellyfin at `/media/Movies` and `/media/Shows` on first start, and enable Intel Quick Sync (VAAPI/QSV) in its playback settings.

Security notes: keep the repo **private** (a self-hosted runner on a public repo can run code from forks). Only the deploy workflow uses the `optiplex` label.

## Phase 5: Deploy with one button

### [ ] P5.1 Deploy script (Sonnet)
- `deploy/deploy.sh <image_tag>`, run on the server by the runner from a checkout of the deployed commit:
  1. Copy `deploy/compose.yaml` to `/srv/couchcinema/`.
  2. Snapshot the DB with `sqlite3 data/catalogue_full.db ".backup backups/pre-deploy/<timestamp>.db"`; keep the newest 7.
  3. Remember the old tag from `deployed_tag`.
  4. `IMAGE_TAG=<new> docker compose pull app && docker compose up -d`.
  5. Poll `http://localhost:8000/healthz` for up to 90s and check its `git_sha` matches.
  6. On success write the new tag to `deployed_tag`. On failure bring the old tag back up, print the app's last 100 log lines, and exit non-zero.
- The schema migration runs when the app starts (`DatabaseManager`), so the pre-deploy snapshot is the way back if a migration goes wrong. The script does **not** restore it automatically; restoring is a runbook step.
- **Done when:** `shellcheck` passes and a dry run with a deliberately broken tag rolls back.

### [ ] P5.2 Deploy workflow (Sonnet)
- `.github/workflows/deploy.yml`, `on: workflow_dispatch` with input `ref` (default `main`).
  - job `verify` (`ubuntu-latest`): resolve `ref` to a SHA; fail unless the CI workflow run for that SHA concluded `success` and image `sha-<sha>` exists.
  - job `deploy` (needs `verify`, `runs-on: [self-hosted, optiplex]`, `environment: production`, `concurrency: deploy`): checkout that SHA, run `deploy/deploy.sh sha-<sha>`, send an ntfy message with the result.
- **Done when:** pressing Run workflow on `main` deploys and the app's `/healthz` shows the new SHA; running it on a commit with red CI stops at `verify`.

### [ ] P5.3 Runbook additions (Haiku)
- In `deploy/RUNBOOK.md`: how to roll back (re-run Deploy on the older commit), how to restore a pre-deploy DB snapshot, how to read logs (`docker compose logs app`).

## Phase 6: Mac ↔ server data flow

### [ ] P6.1 Admin API (Sonnet)
All under `/admin`, require header `Authorization: Bearer $ADMIN_TOKEN`, return 404 when `ADMIN_TOKEN` is unset.
- `GET /admin/export`: streams a consistent copy of the DB made with SQLite's online backup API (`sqlite3.Connection.backup`) into a temp file. Include `X-Media-Items-Max-Id`.
- `POST /admin/catalogue-patch`: body is JSONL, one row per item `{media_type, tmdb_id, fields:{...}}` plus optional `{table:"vibe_clusters", rows:[...]}` replace blocks. Inserts unknown `(media_type, tmdb_id)` rows (the server assigns ids), updates listed columns on known ones, never deletes. Runs in one transaction. Returns counts.
- Allow-list the columns a patch may set (catalogue and enrichment columns of `media_items` only). Reject anything touching user tables.
- Tests: patch inserts, updates, rejects user-table columns, and is idempotent when applied twice.
- **Done when:** tests pass.

### [ ] P6.2 Model bundles (Sonnet)
- A bundle is `bundle-<UTC timestamp>.tar.gz` containing `tiger/…`, `vibe/…`, an optional `db_patch.jsonl` (columns training writes, e.g. `facets`, `vibe_cluster_id`, plus `vibe_clusters`), and `manifest.json`: `{bundle_id, created_at, git_sha, source_db_max_id, files:{path: sha256}, metrics:{...}, gate:"passed"}`.
- `POST /admin/model-bundle` (multipart upload):
  1. Verify every sha256 and that `gate == "passed"`.
  2. Check every item id in `semantic_ids.json` and `vibe/item_ids.json` exists in `media_items`.
  3. Unpack to `models/releases/<bundle_id>/`, load it in a throwaway retriever and run one recommendation as a smoke test.
  4. Apply `db_patch.jsonl` (same code as P6.1) and atomically repoint `models/current` (write a temp symlink, then `os.replace`).
  5. Reload models in-process under a lock (re-create the generative retriever and vibe index). If the reload fails, point `current` back and reload again.
  6. Keep the newest 3 releases.
- `POST /admin/model-rollback`: repoint to the previous release and reload.
- **Done when:** tests cover a good bundle, a bad checksum, a missing id, and rollback.

### [ ] P6.3 Mac client (Sonnet)
- `ops/mac/cc.py` with subcommands that talk to `http://optiplex:8000` (the MagicDNS name; traffic is encrypted by Tailscale) (from `~/.couchcinema/env`): `export <out.db>`, `diff-catalogue <before.db> <after.db> <out.jsonl>` (rows keyed by `(media_type, tmdb_id)`, only allow-listed columns), `push-patch <file>`, `build-bundle <out.tar.gz>`, `push-bundle <file>`.
- **Done when:** a round trip against a local `docker compose` server works: export, run a catalogue script on the copy, diff, push, and the new rows appear on the server.

## Phase 7: Mac scheduled jobs

### [ ] P7.1 Nightly pipeline script (Sonnet)
`ops/mac/nightly.sh`, one lock file so runs never overlap, logs to `~/Library/Logs/CouchCinema/<date>.log`, each step can be skipped by flag:

1. **Backup:** `cc.py export` to `~/CouchCinemaBackups/<date>.db`, check `PRAGMA integrity_check`, keep 30 days. This is the user-data backup.
2. **Catalogue refresh (weekly, Sundays):** copy the backup to a work file, run `utils/build_full_catalogue.py` and the backfills on it with a quality floor so garbage stays out (start with `vote_count >= 200 and rating >= 6.0`, as settings in `config.yaml`), then enrichment (`utils/mood_tag.py`, `utils/backfill_tmdb_vibe.py`). `cc.py diff-catalogue` and `push-patch`.
3. **Fresh snapshot:** `cc.py export` again so training sees the server's ids.
4. **Train:** `training/retrain_tiger.sh` and `training/build_vibe_index.py` against the snapshot (`DATABASE_PATH` pointing at it). Stop the script from copying into the local `models/`; it writes to a staging folder instead.
5. **Evaluate:** run the existing gates (`export_tiger_onnx.py` deployment gates, `utils/eval_taste_model.py`) and compare with the metrics in the server's current manifest. Set `gate:"passed"` only if no metric drops by more than 1% (setting in `config.yaml`).
6. **Publish:** `cc.py build-bundle` and `push-bundle` if the gate passed; otherwise keep the bundle locally and say why in the log.
7. **Report:** ping healthchecks.io on success, send an ntfy message on any failure.

**Done when:** a full run against the real server either publishes a bundle or explains in the log why it didn't.

### [ ] P7.2 launchd job (Haiku)
- `ops/mac/com.couchcinema.nightly.plist` with `StartCalendarInterval` at 03:30. launchd runs a missed job when the Mac wakes. Install instructions in `ops/mac/README.md` (`launchctl bootstrap gui/$(id -u) …`).
- **Done when:** `launchctl print` shows the job and a manual `launchctl kickstart` runs it.

## Phase 8: Know when something breaks

### [ ] P8.1 Alerts (Haiku)
- Server cron (in the RUNBOOK, installed once): every 5 minutes, `curl -fsS localhost:8000/healthz && curl -fsS <healthchecks.io ping url>`. If the pings stop, healthchecks.io emails/pushes you, even when the whole box is down.
- Daily disk check: if `MEDIA_ROOT` or `/` is above 85% full, send an ntfy message.
- Install the ntfy app on both phones and subscribe to the private topic name from `.env`.
- **Done when:** stopping the app container triggers an alert within 15 minutes.

### [ ] P8.2 Disk housekeeping (Sonnet, optional)
- Setting `cleanup.delete_watched_after_days` (default off). A daily server job lists downloads both of you have marked watched more than N days ago and deletes them, logging what it removed. Ships off; Labiba turns it on.

## Phase 9: Moving to the external drive [Labiba, runbook by Haiku]

1. Format the drive ext4, label it `media`.
2. Add to `/etc/fstab`: `LABEL=media /mnt/media ext4 defaults,nofail,x-systemd.device-timeout=10s 0 2`. `nofail` means a missing drive never blocks boot.
3. `rsync -aHP /srv/media/ /mnt/media/`, then set `MEDIA_ROOT=/mnt/media` in `.env` and re-run Deploy (or `docker compose up -d`).
4. Rescan the libraries in Jellyfin. Delete `/srv/media` once everything plays.

## Phase 10: Make it presentable (Haiku)

- README section "Architecture & operations" with the diagram from section 1, the CI/CD flow, and the model release flow (models ship separately from code and only when they beat the current one).
- A short ADR folder `docs/adr/`: 001 single server owns ids, 002 outbound runner instead of Tailscale ACLs, 003 model bundles with eval gate.

---

## Later (not in this plan)

- Weekday download scheduling ("download by day of the week"). It belongs on the server inside the app, using the current model's recommendations. Plan it separately once Phase 6 works.
- Subtitle-based enrichment on the Mac. It slots into P7.1 step 2 as one more script writing allow-listed columns.
- A staging copy of the stack on the Mac for trying a release before deploying.

## Suggested order

P1.1 → P1.2 → P1.3 → P1.4 → P2.1 → P2.2 → P3.1 → P3.2 → P3.3 → Phase 4 → P5.1 → P5.2 → P5.3 → P8.1 → P6.1 → P6.2 → P6.3 → P7.1 → P7.2 → Phase 9 when the drive arrives → Phase 10.

The app is usable on the server after P5.2. Phases 6–7 bring learning back; until then, models can be copied to `/srv/couchcinema/models/releases/` by hand once.
