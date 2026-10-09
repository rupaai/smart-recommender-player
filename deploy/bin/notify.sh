#!/usr/bin/env bash
# Send a push notification through ntfy. Never fails the caller.
#
#   notify.sh "<title>" "<message>" [priority: min|low|default|high|urgent] [tags]
#
# The topic comes from NTFY_TOPIC (and NTFY_SERVER, default https://ntfy.sh) in the
# environment, else from /srv/couchcinema/.env.
set -uo pipefail

CC_ROOT="${CC_ROOT:-/srv/couchcinema}"
title="${1:-couchcinema}"
message="${2:-}"
priority="${3:-default}"
tags="${4:-}"

env_value() { [ -f "$CC_ROOT/.env" ] && grep -m1 "^$1=" "$CC_ROOT/.env" | cut -d= -f2-; }

topic="${NTFY_TOPIC:-$(env_value NTFY_TOPIC)}"
server="${NTFY_SERVER:-$(env_value NTFY_SERVER)}"
server="${server:-https://ntfy.sh}"

if [ -z "$topic" ]; then
  echo "notify.sh: no NTFY_TOPIC set; would have sent: $title - $message" >&2
  exit 0
fi

curl -fsS --max-time 10 -o /dev/null \
  -H "Title: $title" -H "Priority: $priority" ${tags:+-H "Tags: $tags"} \
  -d "$message" "$server/$topic" \
  || echo "notify.sh: could not reach $server (message not sent)" >&2
exit 0
