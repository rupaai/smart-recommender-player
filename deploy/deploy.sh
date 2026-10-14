#!/usr/bin/env bash
# Deploy one image tag to this server. Run by the GitHub Actions runner (as the deploy
# user, so against its rootless Docker) from a checkout of the commit being deployed:
#
#   deploy/deploy.sh sha-<full commit sha>
#
# 1. refuses to run without a database (a fresh empty one would strand your real data)
# 2. copies compose.yaml, config.server.yaml, household.yaml and helper scripts into /srv/couchcinema
# 3. snapshots the database (newest 7 kept)
# 4. pulls the image, starts it, and waits for /healthz to report this commit
# 5. on failure brings the previous tag back up and exits non-zero
#
# It never restores the database snapshot by itself; see deploy/RUNBOOK.md.
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CC_ROOT="${CC_ROOT:-/srv/couchcinema}"
IMAGE_REPO="${IMAGE_REPO:-ghcr.io/rupaai/smart-recommender-player}"
HEALTH_URL="${HEALTH_URL:-http://localhost:8000/healthz}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-90}"
POLL_INTERVAL="${POLL_INTERVAL:-3}"
KEEP_SNAPSHOTS=7
KEEP_IMAGES=3

say() { printf '==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# Only ever our compose project, only ever the deploy user's own daemon.
dc() { docker compose -p couchcinema --project-directory "$CC_ROOT" -f "$CC_ROOT/compose.yaml" "$@"; }

# Succeeds when /healthz says ok (and, if given, reports the expected commit).
healthy() {
  local expect_sha="${1:-}"
  curl -fsS --max-time 5 "$HEALTH_URL" 2>/dev/null | EXPECT="$expect_sha" python3 -c '
import json, os, sys
try:
    d = json.load(sys.stdin)
except ValueError:
    sys.exit(1)
want = os.environ["EXPECT"]
sys.exit(0 if d.get("ok") is True and (not want or d.get("git_sha") == want) else 1)'
}

wait_healthy() {
  local expect_sha="${1:-}"
  SECONDS=0
  while [ "$SECONDS" -lt "$HEALTH_TIMEOUT" ]; do
    healthy "$expect_sha" && return 0
    sleep "$POLL_INTERVAL"
  done
  return 1
}

main() {
  local tag="${1:-}"
  [[ "$tag" =~ ^[A-Za-z0-9._-]{1,128}$ ]] || die "usage: deploy.sh <image tag>, e.g. sha-0123abc... (letters, digits, . _ - only)"

  # Never fall back to the machine's main Docker daemon: that is where Home Assistant lives.
  case "${DOCKER_HOST:-}" in
    "" | *"/var/run/docker.sock" | *"/run/docker.sock")
      die "DOCKER_HOST must point at the deploy user's rootless Docker, not the system one (got '${DOCKER_HOST:-}')" ;;
  esac

  [ -f "$CC_ROOT/.env" ] || die "$CC_ROOT/.env not found: run bootstrap.sh first"
  [ -s "$CC_ROOT/data/catalogue_full.db" ] || [ "${ALLOW_NEW_DB:-0}" = 1 ] \
    || die "no database at $CC_ROOT/data/catalogue_full.db. Copy yours across first (deploy/RUNBOOK.md, 'Copy your data'); starting the app now would create an empty one."

  mkdir -p "$CC_ROOT/state" "$CC_ROOT/backups/pre-deploy" "$CC_ROOT/bin"
  exec 9>"$CC_ROOT/state/deploy.lock"
  flock -n 9 || die "another deploy is already running"

  local old_tag=""
  [ ! -f "$CC_ROOT/deployed_tag" ] || old_tag="$(tr -d '[:space:]' <"$CC_ROOT/deployed_tag")"
  say "Deploying $tag (currently: ${old_tag:-nothing})"

  say "Copying compose.yaml, config.server.yaml, household.yaml and helper scripts"
  local f
  for f in compose.yaml config.server.yaml; do
    [ ! -f "$CC_ROOT/$f" ] || cp -p "$CC_ROOT/$f" "$CC_ROOT/$f.prev"
    cp "$SRC_DIR/$f" "$CC_ROOT/$f"
  done
  [ ! -f "$CC_ROOT/household.yaml" ] || cp -p "$CC_ROOT/household.yaml" "$CC_ROOT/household.yaml.prev"
  cp "$SRC_DIR/../household.yaml" "$CC_ROOT/household.yaml"
  if [ -d "$SRC_DIR/bin" ]; then
    cp "$SRC_DIR"/bin/*.sh "$CC_ROOT/bin/"
    chmod 755 "$CC_ROOT"/bin/*.sh
  fi

  local snap
  snap="$CC_ROOT/backups/pre-deploy/$(date -u +%Y%m%dT%H%M%SZ)-before-${tag}.db"
  say "Snapshotting the database to ${snap#"$CC_ROOT"/}"
  if [ -s "$CC_ROOT/data/catalogue_full.db" ]; then
    python3 - "$CC_ROOT/data/catalogue_full.db" "$snap" <<'PY'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
s = sqlite3.connect(src)
d = sqlite3.connect(dst)
s.backup(d)   # SQLite's online backup: consistent even while the app is writing
result = d.execute("PRAGMA quick_check").fetchone()[0]
d.close()
s.close()
if result != "ok":
    sys.exit(f"snapshot failed its integrity check: {result}")
PY
    chmod 600 "$snap"
  fi
  # shellcheck disable=SC2012  # file names here are ours: timestamp-tag.db
  ls -1t "$CC_ROOT"/backups/pre-deploy/*.db 2>/dev/null | tail -n +$((KEEP_SNAPSHOTS + 1)) | xargs -r rm -- || true

  say "Pulling $IMAGE_REPO:$tag"
  IMAGE_TAG="$tag" dc pull app || die "could not pull $IMAGE_REPO:$tag (does CI have an image for that commit, and is the registry login valid?). Nothing was changed."

  say "Starting"
  IMAGE_TAG="$tag" dc up -d

  local expect=""
  [[ "$tag" == sha-* ]] && expect="${tag#sha-}"
  if wait_healthy "$expect"; then
    printf '%s\n' "$tag" >"$CC_ROOT/deployed_tag.tmp"
    mv "$CC_ROOT/deployed_tag.tmp" "$CC_ROOT/deployed_tag"
    say "Healthy. $tag is live."
    prune_images "$tag" "$old_tag"
    return 0
  fi

  printf '\nERROR: %s did not become healthy within %ss. Last 100 lines from the app:\n' "$tag" "$HEALTH_TIMEOUT" >&2
  IMAGE_TAG="$tag" dc logs --tail 100 app >&2 || true
  rollback "$old_tag"
  exit 1
}

rollback() {
  local old_tag="$1" f
  if [ -z "$old_tag" ]; then
    say "No earlier version to go back to; stopping the app so it doesn't restart-loop."
    dc stop app || true
    return 0
  fi
  say "Rolling back to $old_tag"
  for f in compose.yaml config.server.yaml household.yaml; do
    [ ! -f "$CC_ROOT/$f.prev" ] || cp -p "$CC_ROOT/$f.prev" "$CC_ROOT/$f"
  done
  IMAGE_TAG="$old_tag" dc up -d
  local expect=""
  [[ "$old_tag" == sha-* ]] && expect="${old_tag#sha-}"
  if wait_healthy "$expect"; then
    say "Rolled back: $old_tag is live again."
  else
    printf 'ERROR: %s is NOT healthy after the rollback either. Look at the logs: docker compose -p couchcinema logs app\n' "$old_tag" >&2
  fi
}

# Keep the newest few app images (the disk is small); never touch anything else.
prune_images() {
  local current="$1" previous="$2" t n=0
  while read -r t; do
    n=$((n + 1))
    [[ "$t" == sha-* ]] || continue
    [ "$n" -le "$KEEP_IMAGES" ] && continue
    [ "$t" = "$current" ] || [ "$t" = "$previous" ] || docker image rm "$IMAGE_REPO:$t" >/dev/null 2>&1 || true
  done < <(docker image ls "$IMAGE_REPO" --format '{{.Tag}}')
}

main "$@"
