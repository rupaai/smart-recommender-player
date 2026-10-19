#!/usr/bin/env bash
# Sets up training on your Mac. Safe to run again.
#
#   bash ops/mac/install.sh              save your settings and add the "Train CouchCinema" button
#                                        to ~/Applications (and remove the old 03:30 schedule)
#   bash ops/mac/install.sh --uninstall  remove the button (and the old schedule)
#
# It asks for three things (or reads CC_SERVER, ADMIN_TOKEN, NTFY_TOPIC from the environment):
# the server address, the admin token from the server setup, and your ntfy topic.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CC_HOME="${CC_HOME:-$HOME/.couchcinema}"
APPS="${CC_APPS_DIR:-$HOME/Applications}"
BUTTON="$APPS/Train CouchCinema.command"
# The old scheduled job (until 2026-10): a launchd agent that ran at 03:30, mostly while the Mac slept.
OLD_LABEL="com.couchcinema.nightly"
OLD_PLIST="${CC_AGENTS_DIR:-$HOME/Library/LaunchAgents}/$OLD_LABEL.plist"

remove_schedule() {
  if [ -f "$OLD_PLIST" ]; then
    launchctl bootout "gui/$(id -u)/$OLD_LABEL" 2>/dev/null || true
    rm -f "$OLD_PLIST"
    say "Removed the old 03:30 schedule. Training now runs only when you press the button."
  fi
}

say() { printf '%s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

if [ "${1:-}" = "--uninstall" ]; then
  remove_schedule
  rm -f "$BUTTON"
  say "Removed the Train button. Your settings in $CC_HOME and your backups were left alone."
  exit 0
fi

command -v uv >/dev/null 2>&1 || die "uv is not installed. Install it first: brew install uv"
[ "$(uname)" = Darwin ] || die "this installer is for macOS"

mkdir -p "$CC_HOME" "$HOME/Library/Logs/CouchCinema"
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
  { echo "CC_SERVER=$server"; echo "ADMIN_TOKEN=$token"; echo "NTFY_TOPIC=$topic"; echo "# Optional: a healthchecks.io ping URL, pinged after each training run"; echo "HEALTHCHECKS_NIGHTLY_URL="; } >"$ENV_FILE"
  say "Saved $ENV_FILE (only you can read it)."
fi

say "Checking the server can be reached ..."
(cd "$REPO" && uv run python ops/mac/cc.py model-status >/dev/null) \
  || die "could not reach the server with those settings. Is Tailscale on? Is the admin token right? Fix $ENV_FILE and run this again."
say "  ok"

remove_schedule
mkdir -p "$APPS"
# A .command file opens in Terminal when double-clicked, so you see the training as it goes.
printf '#!/bin/bash\nexec bash "%s/ops/mac/train-now.sh" "$@"\n' "$REPO" >"$BUTTON"
chmod +x "$BUTTON"
say ""
say "Installed. To train and send a new model to the app, open \"Train CouchCinema\""
say "(in Applications in your home folder, or search it in Spotlight; you can drag it to the Dock)."
say "Or from Terminal:   bash ops/mac/train-now.sh"
