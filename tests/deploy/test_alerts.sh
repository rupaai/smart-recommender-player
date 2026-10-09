#!/usr/bin/env bash
# healthcheck.sh and diskcheck.sh against fake health endpoints and a stubbed curl for ntfy.
set -uo pipefail
# shellcheck source=harness.sh
. "$(dirname "${BASH_SOURCE[0]}")/harness.sh"
HC="$REPO_ROOT/deploy/bin/healthcheck.sh"
DC="$REPO_ROOT/deploy/bin/diskcheck.sh"

setup() {
  new_sandbox
  mkdir -p "$CC_ROOT/state"
  printf 'NTFY_TOPIC=topic-abc\nHEALTHCHECKS_URL=https://hc-ping.com/uuid\nMEDIA_ROOT=%s\n' "$T/media" >"$CC_ROOT/.env"
  mkdir -p "$T/media"
  cat >"$T/svc.py" <<'PY'
import http.server, os, sys
S = os.environ["STUB_STATE"]
NAMES = {"/healthz": "app", "/health": "jellyfin", "/ping": "prowlarr"}
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        down = os.path.exists(f"{S}/down_{NAMES.get(self.path, '')}")
        self.send_response(503 if down else 200)
        self.end_headers()
        self.wfile.write(b"{}")
    def log_message(self, *a): pass
srv = http.server.HTTPServer(("127.0.0.1", 0), H)
open(sys.argv[1], "w").write(str(srv.server_address[1]))
srv.serve_forever()
PY
  python3 "$T/svc.py" "$T/port" >/dev/null 2>&1 &
  SVC_PID=$!
  local i=0
  while [ ! -s "$T/port" ] && [ "$i" -lt 50 ]; do sleep 0.1; i=$((i + 1)); done
  local base
  base="http://127.0.0.1:$(cat "$T/port")"
  export APP_HEALTH_URL="$base/healthz" JELLYFIN_HEALTH_URL="$base/health" PROWLARR_HEALTH_URL="$base/ping"
}
teardown() { kill "$SVC_PID" 2>/dev/null; wait "$SVC_PID" 2>/dev/null; rm -rf "$T"; }
alerts() { grep -c 'Title: couchcinema' "$STUB_STATE/calls.log" || true; }
pings() { grep -c 'hc-ping.com' "$STUB_STATE/calls.log" || true; }

echo "== healthcheck: all healthy"
setup
"$HC" >"$T/out" 2>&1; assert_eq "$?" 0 "exits 0"
assert_eq "$(pings)" 1 "pings healthchecks.io"
assert_eq "$(alerts)" 0 "no alerts"
assert_eq "$(wc -c <"$T/out")" 0 "prints nothing"

echo "== healthcheck: app goes down and comes back"
touch "$STUB_STATE/down_app"; : >"$STUB_STATE/calls.log"
"$HC"; assert_eq "$(alerts)" 0 "first failure: no alert yet (could be a blip)"
"$HC"; assert_eq "$(alerts)" 1 "second failure in a row: one alert"
assert_grep 'Title: couchcinema: app is down.*Priority: high' "$STUB_STATE/calls.log" "alert is high priority and names the app"
"$HC"; "$HC"; assert_eq "$(alerts)" 1 "no repeat alerts while it stays down"
assert_eq "$(pings)" 0 "no heartbeat while the app is down"
rm "$STUB_STATE/down_app"
"$HC"
assert_eq "$(alerts)" 2 "recovery message sent"
assert_grep 'Title: couchcinema: app is back' "$STUB_STATE/calls.log" "says it is back"
assert_eq "$(pings)" 1 "heartbeat resumes"
assert_eq "$(wc -c <"$CC_ROOT/state/health.state")" 0 "state cleared"

echo "== healthcheck: a one-off blip stays quiet"
touch "$STUB_STATE/down_app"; "$HC"; rm "$STUB_STATE/down_app"; "$HC"
assert_eq "$(alerts)" 2 "no new alert or recovery message"

echo "== healthcheck: jellyfin down alerts, heartbeat continues"
: >"$STUB_STATE/calls.log"; touch "$STUB_STATE/down_jellyfin"
"$HC"; "$HC"
assert_grep 'couchcinema: jellyfin is down' "$STUB_STATE/calls.log" "jellyfin alert"
assert_eq "$(pings)" 2 "app is fine, so the heartbeat continues"

echo "== healthcheck: silent during a deploy"
: >"$STUB_STATE/calls.log"; touch "$STUB_STATE/down_app"
( exec 9>"$CC_ROOT/state/deploy.lock"; flock 9; sleep 2 ) &
LOCK_PID=$!
sleep 0.5
"$HC"; "$HC"
assert_eq "$(alerts)" 0 "no alerts while deploy.lock is held"
assert_eq "$(pings)" 0 "no ping either"
wait "$LOCK_PID"
teardown

echo "== diskcheck"
setup
printf 'torrent:\n  max_media_gb: 1\n' >"$CC_ROOT/config.server.yaml"
"$DC"; assert_eq "$(alerts)" 0 "empty media folder: quiet"
if fallocate -l 900M "$T/media/film.mkv" 2>/dev/null; then
  "$DC"
  assert_eq "$(alerts)" 1 "media at 85% of its cap: alert"
  assert_grep 'media folder is nearly full' "$STUB_STATE/calls.log" "says what is full"
fi
: >"$STUB_STATE/calls.log"
ALERT_PERCENT=0 "$DC"
assert_grep 'server disk is nearly full' "$STUB_STATE/calls.log" "root filesystem alert at threshold"
: >"$STUB_STATE/calls.log"
ALERT_PERCENT=101 "$DC"; assert_eq "$(alerts)" 0 "nothing above an impossible threshold"
teardown
finish
