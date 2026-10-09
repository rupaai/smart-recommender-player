#!/usr/bin/env bash
# Installs the nightly job on your Mac. Safe to run again.
#
#   bash ops/mac/install.sh              set up and start the 03:30 job
#   bash ops/mac/install.sh --uninstall  stop and remove it
#
# It asks for three things (or reads CC_SERVER, ADMIN_TOKEN, NTFY_TOPIC from the environment):
# the server address, the admin token from the server setup, and your ntfy topic.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CC_HOME="${CC_HOME:-$HOME/.couchcinema}"
AGENTS="${CC_AGENTS_DIR:-$HOME/Library/LaunchAgents}"
LABEL="com.couchcinema.nightly"
PLIST="$AGENTS/$LABEL.plist"
DOMAIN="gui/$(id -u)"

say() { printf '%s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

if [ "${1:-}" = "--uninstall" ]; then
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  say "Removed the nightly job. Your settings in $CC_HOME and your backups were left alone."
  exit 0
fi

command -v uv >/dev/null 2>&1 || die "uv is not installed. Install it first: brew install uv"
command -v launchctl >/dev/null 2>&1 || die "this installer is for macOS"

mkdir -p "$CC_HOME" "$HOME/Library/Logs/CouchCinema" "$AGENTS"
chmod 700 "$CC_HOME"

ENV_FILE="$CC_HOME/env"
get() { [ -f "$ENV_FILE" ] && grep -m1 "^$1=" "$ENV_FILE" | cut -d= -f2- || true; }

if [ -f "$ENV_FILE" ] && [ -n "$(get ADMIN_TOKEN)" ]; then
  say "Keeping your existing settings in $ENV_FILE"
else
  server="${CC_SERVER:-}"; token="${ADMIN_TOKEN:-}"; topic="${NTFY_TOPIC:-}"
  if [ -z "$server" ]; then read -r -p "Server address [http://192.168.1.183:8000]: " server; server="${server:-http://192.168.1.183:8000}"; fi
  if [ -z "$token" ]; then read -r -s -p "Admin token (printed at the end of the server setup): " token; echo; fi
  if [ -z "$topic" ]; then read -r -p "ntfy topic (printed at the end of the server setup): " topic; fi
  [ -n "$token" ] || die "the admin token is needed"
  umask 077
  { echo "CC_SERVER=$server"; echo "ADMIN_TOKEN=$token"; echo "NTFY_TOPIC=$topic"; echo "# Optional: a healthchecks.io ping URL for the nightly job"; echo "HEALTHCHECKS_NIGHTLY_URL="; } >"$ENV_FILE"
  say "Saved $ENV_FILE (only you can read it)."
fi

say "Checking the server can be reached ..."
(cd "$REPO" && uv run python ops/mac/cc.py model-status >/dev/null) \
  || die "could not reach the server with those settings. Is Tailscale on? Is the admin token right? Fix $ENV_FILE and run this again."
say "  ok"

sed -e "s|__REPO__|$REPO|g" -e "s|__HOME__|$HOME|g" "$REPO/ops/mac/$LABEL.plist" >"$PLIST"
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$PLIST"
launchctl print "$DOMAIN/$LABEL" >/dev/null || die "launchd did not accept the job"
say ""
say "Installed. It runs every night at 03:30 (or when the Mac wakes, if it was asleep)."
say "Try it now:   launchctl kickstart -k $DOMAIN/$LABEL"
say "Watch it:     tail -f ~/Library/Logs/CouchCinema/\$(date +%F).log"
