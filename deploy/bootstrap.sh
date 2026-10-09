#!/usr/bin/env bash
# One-command setup for the couchcinema stack on the shared server `mars`.
#
#   sudo ./smart-recommender-player/deploy/bootstrap.sh
#
# Safe to run again: every step checks first and skips what is already done. It never
# touches containers, users or files that are not ours (docs/DEPLOYMENT_PLAN.md 4a).
#
#   --set KEY=value     change one value in /srv/couchcinema/.env and restart only our app
#   --skip-runner       don't install the GitHub Actions runner (re-run later with a token)
#   --install-prereqs   apt-install the missing packages instead of just listing them
#
# Non-interactive: export TMDB_API_KEY, PROWLARR_API_KEY, HEALTHCHECKS_URL, MEDIA_ROOT,
# RUNNER_TOKEN before running, or pipe the answers in on stdin.
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Everything below can be overridden so tests/deploy can run this script in a sandbox.
CC_ROOT="${CC_ROOT:-/srv/couchcinema}"
DEPLOY_USER="${DEPLOY_USER:-deploy}"
GITHUB_REPO="${GITHUB_REPO:-rupaai/smart-recommender-player}"
RUNNER_NAME="${RUNNER_NAME:-mars}"
RUNTIME_ROOT="${RUNTIME_ROOT:-/run/user}"
SUBUID_FILE="${SUBUID_FILE:-/etc/subuid}"
SUBGID_FILE="${SUBGID_FILE:-/etc/subgid}"
CRON_FILE="${CRON_FILE:-/etc/cron.d/couchcinema}"
UDEV_RULE_FILE="${UDEV_RULE_FILE:-/etc/udev/rules.d/70-couchcinema-render.rules}"
APPARMOR_FILE="${APPARMOR_FILE:-/etc/apparmor.d/usr.bin.rootlesskit}"
USERNS_RESTRICT_FILE="${USERNS_RESTRICT_FILE:-/proc/sys/kernel/apparmor_restrict_unprivileged_userns}"
RENDER_NODE="${RENDER_NODE:-/dev/dri/renderD128}"
WAIT_SECONDS="${WAIT_SECONDS:-120}"

ALLOWED_KEYS="IMAGE_TAG MEDIA_ROOT RENDER_GID APP_USER SVC_PUID SVC_PGID TMDB_API_KEY JELLYFIN_API_KEY JELLYFIN_USER_ID PROWLARR_API_KEY ADMIN_TOKEN NTFY_TOPIC NTFY_SERVER HEALTHCHECKS_URL"

info() { printf '\033[1m==> %s\033[0m\n' "$*"; }
ok() { printf '    ok: %s\n' "$*"; }
warn() { printf '\033[33m    warning: %s\033[0m\n' "$*" >&2; }
die() { printf '\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

ENV_FILE="$CC_ROOT/.env"

# ---------------------------------------------------------------- running as deploy

deploy_uid() { id -u "$DEPLOY_USER"; }
deploy_home() { getent passwd "$DEPLOY_USER" | cut -d: -f6; }
xdg_dir() { echo "$RUNTIME_ROOT/$(deploy_uid)"; }
docker_host() { echo "unix://$(xdg_dir)/docker.sock"; }

# Run a command as the deploy user with its own rootless Docker daemon selected.
as_deploy() {
  runuser -u "$DEPLOY_USER" -- env \
    HOME="$(deploy_home)" \
    XDG_RUNTIME_DIR="$(xdg_dir)" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=$(xdg_dir)/bus" \
    DOCKER_HOST="$(docker_host)" \
    "$@"
}

# Only ever our compose project, only ever the deploy user's daemon.
dc() {
  as_deploy docker compose -p couchcinema --project-directory "$CC_ROOT" -f "$CC_ROOT/compose.yaml" "$@"
}

# ---------------------------------------------------------------- .env helpers

valid_value() { [[ "$1" =~ ^[A-Za-z0-9._:/@%+=,-]*$ ]]; }

env_get() {
  [ -f "$ENV_FILE" ] || return 0
  grep -m1 "^$1=" "$ENV_FILE" | cut -d= -f2- || true
}

env_set() {
  local key="$1" value="$2" tmp
  valid_value "$value" || die "value for $key has characters that are not allowed in .env (spaces, quotes, \$ or #)"
  tmp="$(mktemp "$ENV_FILE.XXXXXX")"
  K="$key" V="$value" awk '
    BEGIN { k = ENVIRON["K"]; v = ENVIRON["V"] }
    index($0, k "=") == 1 { if (!done) print k "=" v; done = 1; next }
    { print }
    END { if (!done) print k "=" v }
  ' "$ENV_FILE" >"$tmp"
  chmod 600 "$tmp"
  chown "$DEPLOY_USER:$DEPLOY_USER" "$tmp"
  mv "$tmp" "$ENV_FILE"
}

# env_default KEY VALUE: set only if the key is missing or empty.
env_default() { [ -n "$(env_get "$1")" ] || env_set "$1" "$2"; }

random_token() { python3 -c 'import secrets,sys; print(secrets.token_hex(int(sys.argv[1])))' "$1"; }
random_topic() { python3 -c 'import secrets,string; a=string.ascii_lowercase+string.digits; print("couchcinema-"+"".join(secrets.choice(a) for _ in range(16)))'; }

# ask VAR "label" secret(0|1) required(0|1): the environment wins, then .env, then a prompt.
ask() {
  local var="$1" label="$2" secret="$3" required="$4" value="${!1:-}"
  [ -n "$value" ] || value="$(env_get "$var")"
  if [ -z "$value" ]; then
    if [ "$secret" = 1 ]; then
      read -rsp "$label: " value || true
      echo
    else
      read -rp "$label: " value || true
    fi
    [ -z "$required" ] || [ "$required" = 0 ] || [ -n "$value" ] || die "$var is required"
  fi
  printf -v "$var" '%s' "$value"
}

# ---------------------------------------------------------------- steps

preflight() {
  info "Checking this machine"
  [ "$(id -u)" -eq 0 ] || die "run this with sudo"
  [ -f "$SRC_DIR/compose.yaml" ] || die "compose.yaml not found next to bootstrap.sh; run it from a full clone of the repo"

  local missing_cmds=() cmd
  for cmd in docker tailscale curl python3 tar runuser loginctl systemctl getent; do
    command -v "$cmd" >/dev/null 2>&1 || missing_cmds+=("$cmd")
  done
  [ "${#missing_cmds[@]}" -eq 0 ] || die "missing on this server: ${missing_cmds[*]}. This script checks for Docker and Tailscale but does not install them."
  docker compose version >/dev/null 2>&1 || die "the Docker Compose v2 plugin is missing (apt install docker-compose-plugin)"
  dpkg -s unattended-upgrades >/dev/null 2>&1 || die "unattended-upgrades is not installed (apt install unattended-upgrades)"

  # Rootless Docker needs these; they change nothing about the running system.
  local pkgs=()
  command -v newuidmap >/dev/null 2>&1 || pkgs+=(uidmap)
  dpkg -s dbus-user-session >/dev/null 2>&1 || pkgs+=(dbus-user-session)
  command -v dockerd-rootless-setuptool.sh >/dev/null 2>&1 || pkgs+=(docker-ce-rootless-extras)
  command -v slirp4netns >/dev/null 2>&1 || command -v pasta >/dev/null 2>&1 || pkgs+=(slirp4netns)
  command -v rsync >/dev/null 2>&1 || pkgs+=(rsync)
  command -v setfacl >/dev/null 2>&1 || pkgs+=(acl)
  if [ -d /etc/cron.d ] && ! command -v cron >/dev/null 2>&1; then pkgs+=(cron); fi
  if [ "${#pkgs[@]}" -gt 0 ]; then
    if [ "${INSTALL_PREREQS:-0}" = 1 ]; then
      info "Installing missing packages: ${pkgs[*]}"
      apt-get install -y --no-install-recommends "${pkgs[@]}"
    else
      die "missing packages: ${pkgs[*]}
    Install them, then run this script again:
        sudo apt-get install -y ${pkgs[*]}
    (or re-run with --install-prereqs and I will do it)"
    fi
  fi
  ok "Docker, Compose, Tailscale and unattended-upgrades are present"
}

ensure_deploy_user() {
  info "Deploy user '$DEPLOY_USER'"
  if id "$DEPLOY_USER" >/dev/null 2>&1; then
    ok "already exists"
  else
    useradd --create-home --shell /bin/bash --comment "couchcinema deploy (rootless Docker)" "$DEPLOY_USER"
    passwd -l "$DEPLOY_USER" >/dev/null
    ok "created (password locked: no login except through the runner or sudo -iu)"
  fi

  # Rootless Docker maps container users into a private range of subordinate ids.
  local file name start
  for file in "$SUBUID_FILE" "$SUBGID_FILE"; do
    touch "$file"
    if ! grep -q "^$DEPLOY_USER:" "$file"; then
      start="$(awk -F: 'BEGIN{m=99999} {e=$2+$3; if (e>m) m=e} END{print m+1}' "$file")"
      name=uids; [ "$file" = "$SUBGID_FILE" ] && name=gids
      usermod "--add-sub$name" "$start-$((start + 65535))" "$DEPLOY_USER"
      ok "allocated subordinate $name $start-$((start + 65535))"
    fi
  done

  # The docker group is root-equivalent and sudo is root: neither may ever hold this user.
  local g
  for g in $(id -nG "$DEPLOY_USER"); do
    case "$g" in
      docker | sudo | admin | wheel | lxd | root)
        die "$DEPLOY_USER is in group '$g', which would let CI reach other containers or root. Remove it: sudo gpasswd -d $DEPLOY_USER $g"
        ;;
    esac
  done
  ok "not in the docker or sudo groups"

  # Keep the user's service manager (and so its Docker daemon) running without a login.
  loginctl enable-linger "$DEPLOY_USER"
  local i=0
  while [ ! -S "$(xdg_dir)/bus" ] && [ "$i" -lt 30 ]; do sleep 1; i=$((i + 1)); done
  [ -S "$(xdg_dir)/bus" ] || die "the systemd user session for $DEPLOY_USER did not start (no $(xdg_dir)/bus)"
  ok "user session is running (linger on)"
}

# Ubuntu 24.04 blocks unprivileged user namespaces unless a binary has a profile that
# allows them. This profile covers rootlesskit only and changes nothing else.
ensure_apparmor() {
  [ -r "$USERNS_RESTRICT_FILE" ] && [ "$(cat "$USERNS_RESTRICT_FILE")" = 1 ] || return 0
  info "AppArmor profile for rootlesskit"
  if [ -f "$APPARMOR_FILE" ]; then
    ok "already installed"
    return 0
  fi
  local rk
  rk="$(readlink -f "$(command -v rootlesskit 2>/dev/null || echo /usr/bin/rootlesskit)")"
  cat >"$APPARMOR_FILE" <<EOF
# Installed by couchcinema bootstrap.sh: lets rootlesskit create user namespaces on Ubuntu 24.04.
abi <abi/4.0>,
include <tunables/global>
"$rk" flags=(unconfined) {
  userns,
  include if exists <local/usr.bin.rootlesskit>
}
EOF
  apparmor_parser -r "$APPARMOR_FILE"
  ok "loaded for $rk"
}

ensure_rootless_docker() {
  info "Rootless Docker for '$DEPLOY_USER'"
  if as_deploy docker info >/dev/null 2>&1; then
    ok "daemon already running"
  else
    as_deploy dockerd-rootless-setuptool.sh install
    as_deploy systemctl --user enable --now docker
    local i=0
    while ! as_deploy docker info >/dev/null 2>&1 && [ "$i" -lt 30 ]; do sleep 1; i=$((i + 1)); done
    as_deploy docker info >/dev/null 2>&1 || die "the rootless Docker daemon did not come up. Look at: sudo -iu $DEPLOY_USER journalctl --user -u docker -n 50"
    ok "installed and started"
  fi
  as_deploy docker info --format '{{.SecurityOptions}}' | grep -q rootless || die "the deploy user's Docker is not rootless"
  # Handy when logging in as deploy by hand.
  local profile
  profile="$(deploy_home)/.profile"
  grep -q 'couchcinema: rootless docker' "$profile" 2>/dev/null || cat >>"$profile" <<EOF
# couchcinema: rootless docker
export XDG_RUNTIME_DIR=$(xdg_dir)
export DOCKER_HOST=$(docker_host)
EOF
  chown "$DEPLOY_USER:$DEPLOY_USER" "$profile"
}

# Jellyfin's Quick Sync needs the render node. In rootless Docker a host group id can't be
# handed to the container, so give the deploy user itself access (container root IS deploy).
ensure_gpu_access() {
  info "Intel Quick Sync (render node)"
  if [ ! -e "$RENDER_NODE" ]; then
    warn "$RENDER_NODE not found; Jellyfin will fail to start with /dev/dri mounted. Check the GPU is enabled in the BIOS."
    return 0
  fi
  if [ ! -f "$UDEV_RULE_FILE" ]; then
    cat >"$UDEV_RULE_FILE" <<EOF
# Installed by couchcinema bootstrap.sh: lets the rootless deploy user use the GPU render node.
SUBSYSTEM=="drm", KERNEL=="renderD*", ACTION=="add|change", RUN+="/usr/bin/setfacl -m u:$DEPLOY_USER:rw \$env{DEVNAME}"
EOF
    udevadm control --reload
  fi
  setfacl -m "u:$DEPLOY_USER:rw" "$RENDER_NODE"
  ok "$DEPLOY_USER can read and write $RENDER_NODE"
}

ensure_dirs() {
  info "Folders"
  mkdir -p "$CC_ROOT"
  local d
  for d in data models/releases backups/pre-deploy jellyfin/config jellyfin/cache prowlarr/config state bin; do
    mkdir -p "$CC_ROOT/$d"
  done
  # Top level of each folder only: what the containers write inside is never re-owned.
  chown "$DEPLOY_USER:$DEPLOY_USER" "$CC_ROOT" "$CC_ROOT"/{data,models,models/releases,backups,backups/pre-deploy,jellyfin,jellyfin/config,jellyfin/cache,prowlarr,prowlarr/config,state,bin}
  chmod 700 "$CC_ROOT/data" "$CC_ROOT/backups"
  ok "$CC_ROOT"
}

sync_repo_files() {
  info "Copying compose.yaml, config.server.yaml and helper scripts into $CC_ROOT"
  cp "$SRC_DIR/compose.yaml" "$CC_ROOT/compose.yaml"
  cp "$SRC_DIR/config.server.yaml" "$CC_ROOT/config.server.yaml"
  if [ -d "$SRC_DIR/bin" ]; then
    cp "$SRC_DIR"/bin/*.sh "$CC_ROOT/bin/"
    chmod 755 "$CC_ROOT"/bin/*.sh
  fi
  chown "$DEPLOY_USER:$DEPLOY_USER" "$CC_ROOT/compose.yaml" "$CC_ROOT/config.server.yaml" "$CC_ROOT"/bin/*.sh 2>/dev/null || true
}

collect_settings() {
  info "Settings"
  if [ ! -f "$ENV_FILE" ]; then
    cp "$SRC_DIR/.env.example" "$ENV_FILE"
    chown "$DEPLOY_USER:$DEPLOY_USER" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
  fi
  chmod 600 "$ENV_FILE"

  MEDIA_ROOT="${MEDIA_ROOT:-$(env_get MEDIA_ROOT)}"
  if [ -z "$MEDIA_ROOT" ]; then
    read -rp "Where should films live? [/srv/media]: " MEDIA_ROOT || true
    MEDIA_ROOT="${MEDIA_ROOT:-/srv/media}"
  fi
  case "$MEDIA_ROOT" in /*) ;; *) die "MEDIA_ROOT must be an absolute path" ;; esac
  TMDB_API_KEY="${TMDB_API_KEY:-}"
  ask TMDB_API_KEY "TMDB API key (from your Mac's .env)" 1 1
  ask PROWLARR_API_KEY "Prowlarr API key (Enter to generate one)" 1 0
  ask HEALTHCHECKS_URL "healthchecks.io ping URL (Enter to skip for now)" 0 0
  if [ "${SKIP_RUNNER:-0}" != 1 ] && [ ! -f "$(deploy_home)/actions-runner/.runner" ]; then
    ask RUNNER_TOKEN "GitHub runner registration token (Enter to skip; GitHub > Settings > Actions > Runners > New self-hosted runner)" 1 0
  fi

  env_set MEDIA_ROOT "$MEDIA_ROOT"
  env_set TMDB_API_KEY "$TMDB_API_KEY"
  [ -n "${PROWLARR_API_KEY:-}" ] || PROWLARR_API_KEY="$(random_token 16)"
  env_set PROWLARR_API_KEY "$PROWLARR_API_KEY"
  [ -z "${HEALTHCHECKS_URL:-}" ] || env_set HEALTHCHECKS_URL "$HEALTHCHECKS_URL"

  # Rootless Docker: container root is the deploy user, so the containers run as root.
  env_set APP_USER "0:0"
  env_set SVC_PUID "0"
  env_set SVC_PGID "0"
  env_set RENDER_GID "0"
  env_default IMAGE_TAG "unset"   # a valid placeholder; deploy.sh sets the real tag
  env_default ADMIN_TOKEN "$(random_token 32)"
  env_default NTFY_TOPIC "$(random_topic)"
}

ensure_media_dirs() {
  info "Media folders in $MEDIA_ROOT (cap 100GB, set in config.server.yaml)"
  mkdir -p "$MEDIA_ROOT/Movies" "$MEDIA_ROOT/Shows" "$MEDIA_ROOT/downloads"
  # chown only the top level here: existing films are never re-owned or touched.
  chown "$DEPLOY_USER:$DEPLOY_USER" "$MEDIA_ROOT" "$MEDIA_ROOT/Movies" "$MEDIA_ROOT/Shows" "$MEDIA_ROOT/downloads"
  local free_gb
  free_gb="$(df -BG --output=avail "$MEDIA_ROOT" | tail -1 | tr -dc '0-9')"
  [ -z "$free_gb" ] || [ "$free_gb" -ge 20 ] || warn "only ${free_gb}GB free on the drive holding $MEDIA_ROOT; the app refuses downloads below 20GB free"
}

# Prowlarr only reads its API key from config.xml, so seed it there; the key in .env and
# the key Prowlarr uses can then never disagree. An existing config.xml always wins.
ensure_prowlarr_config() {
  local cfg="$CC_ROOT/prowlarr/config/config.xml" existing
  if [ -f "$cfg" ]; then
    existing="$(sed -n 's:.*<ApiKey>\(.*\)</ApiKey>.*:\1:p' "$cfg" | head -1)"
    if [ -n "$existing" ] && [ "$existing" != "$(env_get PROWLARR_API_KEY)" ]; then
      warn "Prowlarr already has its own API key; using that one in .env"
      env_set PROWLARR_API_KEY "$existing"
    fi
    return 0
  fi
  info "Seeding Prowlarr's API key"
  printf '<Config>\n  <ApiKey>%s</ApiKey>\n</Config>\n' "$(env_get PROWLARR_API_KEY)" >"$cfg"
  chown "$DEPLOY_USER:$DEPLOY_USER" "$cfg"
  chmod 600 "$cfg"
}

runner_unit() { echo "actions.runner.${GITHUB_REPO//\//-}.${RUNNER_NAME}.service"; }

ensure_runner() {
  info "GitHub Actions runner (label '$RUNNER_NAME')"
  if [ "${SKIP_RUNNER:-0}" = 1 ]; then ok "skipped (--skip-runner)"; return 0; fi
  local dir
  dir="$(deploy_home)/actions-runner"

  if [ ! -f "$dir/.runner" ]; then
    if [ -z "${RUNNER_TOKEN:-}" ]; then
      warn "no runner token given, so no runner installed. Get one on GitHub (Settings > Actions > Runners > New self-hosted runner) and run this script again."
      return 0
    fi
    local meta tag hash url tarball
    meta="$(curl -fsSL https://api.github.com/repos/actions/runner/releases/latest)" || die "could not look up the latest runner release"
    tag="$(printf '%s' "$meta" | python3 -c 'import json,sys; print(json.load(sys.stdin)["tag_name"].lstrip("v"))')"
    hash="$(printf '%s' "$meta" | python3 -c '
import json,re,sys
body=json.load(sys.stdin).get("body","")
m=re.search(r"<!-- BEGIN SHA linux-x64 -->([0-9a-f]{64})<!-- END SHA linux-x64 -->", body)
print(m.group(1) if m else "")')"
    url="https://github.com/actions/runner/releases/download/v$tag/actions-runner-linux-x64-$tag.tar.gz"
    tarball="$(mktemp)"
    curl -fsSL "$url" -o "$tarball" || die "could not download $url"
    if [ -n "$hash" ]; then
      echo "$hash  $tarball" | sha256sum -c --quiet - || die "runner download failed its checksum"
    else
      warn "no checksum published for runner $tag; relying on HTTPS only"
    fi
    mkdir -p "$dir"
    tar -xzf "$tarball" -C "$dir"
    rm -f "$tarball"
    chown -R "$DEPLOY_USER:$DEPLOY_USER" "$dir"
    as_deploy bash -c "cd '$dir' && ./config.sh --unattended --url 'https://github.com/$GITHUB_REPO' --token '$RUNNER_TOKEN' --name '$RUNNER_NAME' --labels '$RUNNER_NAME' --replace"
    ok "registered runner $tag"
  else
    ok "already registered"
  fi

  # Jobs run docker as the deploy user, against its own daemon.
  printf 'DOCKER_HOST=%s\nXDG_RUNTIME_DIR=%s\n' "$(docker_host)" "$(xdg_dir)" >"$dir/.env"
  chown "$DEPLOY_USER:$DEPLOY_USER" "$dir/.env"

  local unit
  unit="$(runner_unit)"
  if ! systemctl cat "$unit" >/dev/null 2>&1; then
    (cd "$dir" && ./svc.sh install "$DEPLOY_USER")
    mkdir -p "/etc/systemd/system/$unit.d"
    printf '[Unit]\nAfter=user@%s.service\nWants=user@%s.service\n' "$(deploy_uid)" "$(deploy_uid)" >"/etc/systemd/system/$unit.d/couchcinema.conf"
    systemctl daemon-reload
  fi
  systemctl is-active --quiet "$unit" || (cd "$dir" && ./svc.sh start)
  ok "service $unit is running"
}

ensure_cron() {
  info "Alerts (cron)"
  if [ ! -f "$SRC_DIR/bin/healthcheck.sh" ] || [ ! -f "$SRC_DIR/bin/diskcheck.sh" ]; then
    ok "alert scripts are not in this checkout yet; skipped"
    return 0
  fi
  local want
  want="# Installed by couchcinema bootstrap.sh. Runs as $DEPLOY_USER.
SHELL=/bin/bash
PATH=/usr/local/bin:/usr/bin:/bin
*/5 * * * * $DEPLOY_USER $CC_ROOT/bin/healthcheck.sh
17 6 * * * $DEPLOY_USER $CC_ROOT/bin/diskcheck.sh"
  if [ "$(cat "$CRON_FILE" 2>/dev/null || true)" != "$want" ]; then
    printf '%s\n' "$want" >"$CRON_FILE"
    chmod 644 "$CRON_FILE"
  fi
  ok "$CRON_FILE: health check every 5 minutes, disk check daily"
}

wait_for() {
  local name="$1" url="$2" service="$3" i=0
  while ! curl -fsS -o /dev/null "$url" 2>/dev/null && [ "$i" -lt "$WAIT_SECONDS" ]; do sleep 2; i=$((i + 2)); done
  curl -fsS -o /dev/null "$url" 2>/dev/null || {
    dc logs --tail 40 "$service" 2>/dev/null || true
    die "$name did not become healthy at $url within ${WAIT_SECONDS}s"
  }
  ok "$name is up"
}

start_stack() {
  info "Starting Jellyfin and Prowlarr (the app starts with the first deploy)"
  # Refuse to fight another service for a port, but only before our own stack exists.
  if [ -z "$(dc ps -q 2>/dev/null)" ]; then
    local p
    for p in 8096 9696; do
      if ss -H -ltn "sport = :$p" 2>/dev/null | grep -q .; then
        die "port $p is already in use by something else on this machine"
      fi
    done
  fi
  dc up -d jellyfin prowlarr
  wait_for Jellyfin "http://localhost:8096/health" jellyfin
  wait_for Prowlarr "http://localhost:9696/ping" prowlarr
}

verify_isolation() {
  info "Checking we only see our own containers"
  local names
  names="$(as_deploy docker ps -a --format '{{.Names}}' | sort | tr '\n' ' ')"
  ok "deploy's Docker lists: ${names:-nothing}"
  if as_deploy bash -c 'test -r /var/run/docker.sock && docker -H unix:///var/run/docker.sock ps' >/dev/null 2>&1; then
    die "$DEPLOY_USER can talk to the system Docker daemon; that must not be possible"
  fi
  ok "$DEPLOY_USER cannot reach the system Docker daemon"
}

cmd_set() {
  [ "$(id -u)" -eq 0 ] || die "run this with sudo"
  [ -f "$ENV_FILE" ] || die "$ENV_FILE does not exist yet; run bootstrap.sh without --set first"
  local pair key value
  for pair in "$@"; do
    key="${pair%%=*}"; value="${pair#*=}"
    [[ "$pair" == *=* ]] || die "use KEY=value"
    [[ " $ALLOWED_KEYS " == *" $key "* ]] || die "unknown setting $key (allowed: $ALLOWED_KEYS)"
    env_set "$key" "$value"
    ok "$key updated"
  done
  if [ -f "$CC_ROOT/deployed_tag" ]; then
    info "Restarting only the app container"
    as_deploy env "IMAGE_TAG=$(cat "$CC_ROOT/deployed_tag")" docker compose -p couchcinema --project-directory "$CC_ROOT" -f "$CC_ROOT/compose.yaml" up -d --no-deps app
  else
    ok "the app isn't deployed yet; the new value is used by the first deploy"
  fi
}

summary() {
  cat <<EOF

$(printf '\033[1m')Done.$(printf '\033[0m') Copy these somewhere safe:

  ADMIN_TOKEN  $(env_get ADMIN_TOKEN)
               (the Mac's nightly job needs it; it stays in $ENV_FILE)
  ntfy topic   $(env_get NTFY_TOPIC)
               (subscribe to it in the ntfy app on both phones)

Next: copy your database and films across (deploy/RUNBOOK.md), then run
GitHub > Actions > Deploy > Run workflow.
Jellyfin is at http://$(hostname):8096 and Prowlarr at http://$(hostname):9696.
EOF
}

main() {
  local sets=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --set) shift; [ $# -gt 0 ] || die "--set needs KEY=value"; sets+=("$1") ;;
      --skip-runner) SKIP_RUNNER=1 ;;
      --install-prereqs) INSTALL_PREREQS=1 ;;
      -h | --help) sed -n '2,15p' "${BASH_SOURCE[0]}"; exit 0 ;;
      *) die "unknown option $1" ;;
    esac
    shift
  done
  if [ "${#sets[@]}" -gt 0 ]; then cmd_set "${sets[@]}"; exit 0; fi

  preflight
  ensure_deploy_user
  ensure_apparmor
  ensure_rootless_docker
  ensure_gpu_access
  ensure_dirs
  sync_repo_files
  collect_settings
  ensure_media_dirs
  ensure_prowlarr_config
  ensure_runner
  ensure_cron
  start_stack
  verify_isolation
  summary
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then main "$@"; fi
