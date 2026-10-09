#!/usr/bin/env bash
# One-off moves of your existing data onto the server (the cutover). Run with sudo on mars.
#
#   sudo import-data.sh db <catalogue_full.db>         your database -> /srv/couchcinema/data
#   sudo import-data.sh models <models dir>            the Mac's models/ folder -> a first model release
#   sudo import-data.sh media <folder> Movies|Shows    films/shows -> $MEDIA_ROOT (moved, not copied)
#
# Needs a copy of the file or folder already on this machine (scp / rsync it to your home
# folder first). Nothing here ever deletes data it did not just move.
set -euo pipefail

CC_ROOT="${CC_ROOT:-/srv/couchcinema}"
DEPLOY_USER="${DEPLOY_USER:-deploy}"
RUNTIME_ROOT="${RUNTIME_ROOT:-/run/user}"
ENV_FILE="$CC_ROOT/.env"

info() { printf '\033[1m==> %s\033[0m\n' "$*"; }
ok() { printf '    ok: %s\n' "$*"; }
die() { printf '\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

env_get() { [ -f "$ENV_FILE" ] && { grep -m1 "^$1=" "$ENV_FILE" | cut -d= -f2- || true; } || true; }

app_running() {
  local uid
  uid="$(id -u "$DEPLOY_USER")"
  [ -n "$(runuser -u "$DEPLOY_USER" -- env "XDG_RUNTIME_DIR=$RUNTIME_ROOT/$uid" "DOCKER_HOST=unix://$RUNTIME_ROOT/$uid/docker.sock" \
    docker compose -p couchcinema --project-directory "$CC_ROOT" -f "$CC_ROOT/compose.yaml" ps -q app 2>/dev/null)" ]
}

cmd_db() {
  local src="${1:-}" dest="$CC_ROOT/data/catalogue_full.db" tmp
  [ -f "$src" ] || die "usage: import-data.sh db <path to catalogue_full.db>"
  [ ! -e "$dest" ] || die "$dest already exists. The server owns your data now; importing over it would lose what you rated since. Move it aside by hand if you really mean it."
  ! app_running || die "the app is running; it must not be until the database is in place"
  mkdir -p "$CC_ROOT/data"
  tmp="$dest.importing"
  info "Copying and checking the database"
  # SQLite's own backup API gives a consistent copy even if the source has a -wal file.
  python3 - "$src" "$tmp" <<'EOF'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
d = sqlite3.connect(dst)
s.backup(d)
s.close()
checks = d.execute("PRAGMA integrity_check").fetchone()[0]
if checks != "ok":
    sys.exit(f"integrity_check failed: {checks}")
tables = {r[0] for r in d.execute("SELECT name FROM sqlite_master WHERE type='table'")}
for t in ("media_items", "watch_history"):
    if t not in tables:
        sys.exit(f"this does not look like catalogue_full.db: no {t} table")
n = d.execute("SELECT COUNT(*) FROM media_items").fetchone()[0]
w = d.execute("SELECT COUNT(*) FROM watch_history").fetchone()[0]
print(f"    media_items: {n}, watch_history: {w}, schema version: {d.execute('PRAGMA user_version').fetchone()[0]}")
d.close()
EOF
  chown "$DEPLOY_USER:$DEPLOY_USER" "$tmp"
  chmod 600 "$tmp"
  mv "$tmp" "$dest"
  ok "installed $dest"
}

cmd_models() {
  local src="${1:-}" id dest
  [ -d "$src/tiger" ] || [ -d "$src/vibe" ] || die "usage: import-data.sh models <your Mac's models/ folder> (expected tiger/ and vibe/ inside)"
  id="manual-$(date -u +%Y%m%dT%H%M%SZ)"
  dest="$CC_ROOT/models/releases/$id"
  mkdir -p "$dest"
  info "Copying models to $dest"
  rsync -a "$src"/ "$dest"/
  if [ ! -f "$dest/manifest.json" ]; then
    printf '{"bundle_id": "%s", "gate": "manual"}\n' "$id" >"$dest/manifest.json"
  fi
  chown -R "$DEPLOY_USER:$DEPLOY_USER" "$dest"
  # Atomic repoint: write a temp link beside it, then rename over the old one.
  ln -sfn "releases/$id" "$CC_ROOT/models/current.new"
  mv -T "$CC_ROOT/models/current.new" "$CC_ROOT/models/current"
  chown -h "$DEPLOY_USER:$DEPLOY_USER" "$CC_ROOT/models/current"
  ok "models/current -> releases/$id"
}

cmd_media() {
  local src="${1:-}" kind="${2:-}" root cap used incoming
  [ -d "$src" ] || die "usage: import-data.sh media <folder> Movies|Shows"
  [ "$kind" = Movies ] || [ "$kind" = Shows ] || die "usage: import-data.sh media <folder> Movies|Shows"
  root="$(env_get MEDIA_ROOT)"
  [ -n "$root" ] || die "MEDIA_ROOT not found in $ENV_FILE; run bootstrap.sh first"
  cap="$(sed -n 's/^ *max_media_gb: *\([0-9][0-9]*\).*/\1/p' "$CC_ROOT/config.server.yaml" | head -1)"
  used="$(du -sk "$root" 2>/dev/null | cut -f1)"
  incoming="$(du -sk "$src" | cut -f1)"
  if [ -n "$cap" ] && [ "$cap" -gt 0 ] && [ $(((used + incoming) / 1024 / 1024)) -ge "$cap" ]; then
    die "that would take $root past its ${cap}GB cap ($((used / 1024 / 1024))GB used, $((incoming / 1024 / 1024))GB incoming). Import less, or raise torrent.max_media_gb in deploy/config.server.yaml and deploy."
  fi
  info "Moving $src into $root/$kind"
  rsync -a --remove-source-files --chown="$DEPLOY_USER:$DEPLOY_USER" "$src"/ "$root/$kind"/
  find "$src" -depth -type d -empty -delete 2>/dev/null || true
  ok "done. Jellyfin picks the files up on its next library scan."
}

[ "$(id -u)" -eq 0 ] || die "run this with sudo"
case "${1:-}" in
  db) shift; cmd_db "$@" ;;
  models) shift; cmd_models "$@" ;;
  media) shift; cmd_media "$@" ;;
  *) sed -n '2,10p' "${BASH_SOURCE[0]}"; exit 1 ;;
esac
