#!/usr/bin/env bash
# Daily disk check, run from cron. Alerts when the media folder uses ALERT_PERCENT
# of its cap (max_media_gb in config.server.yaml, default 100 GB), or when the
# filesystem holding ROOT_FS_PATH (default /) is ALERT_PERCENT full. Silent otherwise.
#
#   diskcheck.sh
set -uo pipefail

CC_ROOT="${CC_ROOT:-/srv/couchcinema}"
notify="$(dirname "${BASH_SOURCE[0]}")/notify.sh"

env_value() { [ -f "$CC_ROOT/.env" ] && grep -m1 "^$1=" "$CC_ROOT/.env" | cut -d= -f2-; }

MEDIA_ROOT="${MEDIA_ROOT:-$(env_value MEDIA_ROOT)}"
if [ -z "$MEDIA_ROOT" ]; then
  exit 0
fi

ALERT_PERCENT="${ALERT_PERCENT:-85}"

# Media cap in GB, from config.server.yaml. Missing or 0 means the 100 GB default.
cap_gb="$(sed -n 's/.*max_media_gb:[[:space:]"'\'']*\([0-9][0-9]*\).*/\1/p' "$CC_ROOT/config.server.yaml" 2>/dev/null | head -n1)"
cap_gb=$(( 10#${cap_gb:-0} ))
if [ "$cap_gb" -le 0 ]; then
  cap_gb=100
fi

# Media check, integer arithmetic only. used_kb * 100 >= cap_gb * 1024^2 * ALERT_PERCENT
used_kb="$(du -sk "$MEDIA_ROOT" 2>/dev/null | cut -f1)"
used_kb=$(( 10#${used_kb:-0} ))
if [ $(( used_kb * 100 )) -ge $(( cap_gb * 1024 * 1024 * ALERT_PERCENT )) ]; then
  used_gb=$(( used_kb / 1024 / 1024 ))
  "$notify" "couchcinema: media folder is nearly full" \
    "Media is using ${used_gb} GB of its ${cap_gb} GB cap. Delete watched films or raise torrent.max_media_gb." \
    high warning
fi

# Root filesystem check.
ROOT_FS_PATH="${ROOT_FS_PATH:-/}"
pct="$(df --output=pcent "$ROOT_FS_PATH" 2>/dev/null | tail -n1 | tr -dc '0-9')"
if [ -n "$pct" ] && [ $(( 10#$pct )) -ge "$ALERT_PERCENT" ]; then
  "$notify" "couchcinema: server disk is nearly full" \
    "The disk holding $ROOT_FS_PATH is $(( 10#$pct ))% full." \
    high warning
fi
exit 0
