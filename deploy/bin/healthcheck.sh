#!/usr/bin/env bash
# Health check, run from cron every 5 minutes. Probes the app, Jellyfin and
# Prowlarr. Sends one alert when a service has failed 2 checks in a row and one
# "back" alert when it recovers. Pings HEALTHCHECKS_URL while the app is up.
# Does nothing while a deploy holds $CC_ROOT/state/deploy.lock. Silent, exit 0.
#
#   healthcheck.sh
set -uo pipefail

CC_ROOT="${CC_ROOT:-/srv/couchcinema}"
notify="$(dirname "${BASH_SOURCE[0]}")/notify.sh"

env_value() { [ -f "$CC_ROOT/.env" ] && grep -m1 "^$1=" "$CC_ROOT/.env" | cut -d= -f2-; }

state_dir="$CC_ROOT/state"
state_file="$state_dir/health.state"
mkdir -p "$state_dir" 2>/dev/null || exit 0

# A deploy holds this lock. If we cannot take it, a deploy is running: skip.
{ exec 9>"$state_dir/deploy.lock"; } 2>/dev/null || exit 0
flock -n 9 || exit 0

APP_HEALTH_URL="${APP_HEALTH_URL:-http://localhost:8000/healthz}"
JELLYFIN_HEALTH_URL="${JELLYFIN_HEALTH_URL:-http://localhost:8096/health}"
PROWLARR_HEALTH_URL="${PROWLARR_HEALTH_URL:-http://localhost:9696/ping}"

services=(app jellyfin prowlarr)
urls=("$APP_HEALTH_URL" "$JELLYFIN_HEALTH_URL" "$PROWLARR_HEALTH_URL")

# prev: failure counts from the last run. next: counts for this run.
declare -A prev=() next=()
if [ -f "$state_file" ]; then
  while read -r svc count; do
    if [ -n "$svc" ]; then
      prev[$svc]="${count:-0}"
    fi
  done < "$state_file"
fi

app_ok=0
for i in "${!services[@]}"; do
  svc="${services[$i]}"
  url="${urls[$i]}"
  if curl -fsS --max-time 10 -o /dev/null "$url" 2>/dev/null; then
    if [ "${prev[$svc]:-0}" -ge 2 ]; then
      "$notify" "couchcinema: $svc is back" "$svc is healthy again" default white_check_mark
    fi
    if [ "$svc" = app ]; then
      app_ok=1
    fi
  else
    count=$(( ${prev[$svc]:-0} + 1 ))
    next[$svc]=$count
    if [ "$count" -eq 2 ]; then
      "$notify" "couchcinema: $svc is down" \
        "$svc failed 2 health checks in a row (about 10 minutes). Look at: docker compose -p couchcinema logs $svc" \
        high rotating_light
    fi
  fi
done

# Write the new state atomically: temp file in the same directory, then mv.
tmp="$(mktemp "$state_dir/health.state.XXXXXX")" || exit 0
for svc in "${services[@]}"; do
  if [ -n "${next[$svc]+x}" ]; then
    printf '%s %s\n' "$svc" "${next[$svc]}"
  fi
done > "$tmp"
mv -f "$tmp" "$state_file"

# Heartbeat only while the app is up. If it is down, the missing ping makes
# healthchecks.io notice, even when the whole machine is down.
HEALTHCHECKS_URL="${HEALTHCHECKS_URL:-$(env_value HEALTHCHECKS_URL)}"
if [ "$app_ok" = 1 ] && [ -n "$HEALTHCHECKS_URL" ]; then
  curl -fsS --max-time 10 -o /dev/null "$HEALTHCHECKS_URL" 2>/dev/null || true
fi
exit 0
