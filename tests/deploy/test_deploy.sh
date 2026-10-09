#!/usr/bin/env bash
# deploy.sh against a stubbed Docker and a tiny fake /healthz server (see harness.sh).
set -uo pipefail
# shellcheck source=harness.sh
. "$(dirname "${BASH_SOURCE[0]}")/harness.sh"
DEPLOY="$REPO_ROOT/deploy/deploy.sh"

start_health_server() {
  cat >"$T/health.py" <<'PY'
import http.server, json, os, sys
S = os.environ["STUB_STATE"]
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        down = os.path.exists(S + "/health_down")
        sha = open(S + "/health_sha").read().strip() if os.path.exists(S + "/health_sha") else ""
        body = json.dumps({"ok": not down, "git_sha": sha}).encode()
        self.send_response(503 if down else 200)
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a): pass
srv = http.server.HTTPServer(("127.0.0.1", 0), H)
open(sys.argv[1], "w").write(str(srv.server_address[1]))
srv.serve_forever()
PY
  python3 "$T/health.py" "$T/port" >/dev/null 2>&1 &
  HEALTH_PID=$!
  local i=0
  while [ ! -s "$T/port" ] && [ "$i" -lt 50 ]; do sleep 0.1; i=$((i + 1)); done
  HEALTH_URL="http://127.0.0.1:$(cat "$T/port")/healthz"
  export HEALTH_URL
}

# A server that is already running sha-old, with a real database and an old compose file.
setup_server() {
  new_sandbox
  start_health_server
  export HEALTH_TIMEOUT=3 POLL_INTERVAL=0.2 DOCKER_HOST="unix://$T/run/1001/docker.sock"
  mkdir -p "$CC_ROOT/data" "$CC_ROOT/state"
  echo "TMDB_API_KEY=x" >"$CC_ROOT/.env"
  echo "old-compose" >"$CC_ROOT/compose.yaml"
  echo "old-config" >"$CC_ROOT/config.server.yaml"
  echo sha-old >"$CC_ROOT/deployed_tag"
  echo old >"$STUB_STATE/health_sha"
  python3 -c "
import sqlite3,sys
c=sqlite3.connect(sys.argv[1]); c.execute('create table media_items (id integer primary key, title text)'); c.execute(\"insert into media_items (title) values ('A')\"); c.commit()" "$CC_ROOT/data/catalogue_full.db"
  : >"$STUB_STATE/calls.log"
}
teardown() { kill "$HEALTH_PID" 2>/dev/null; wait "$HEALTH_PID" 2>/dev/null; rm -rf "$T"; }

echo "== deploy: good tag"
setup_server
printf 'sha-new\nsha-old\nsha-a\nsha-b\nsha-c\n' >"$STUB_STATE/image_tags"
"$DEPLOY" sha-new >"$T/out.log" 2>&1 && pass "exits 0" || { cat "$T/out.log"; fail "good deploy failed"; }
assert_eq "$(cat "$CC_ROOT/deployed_tag")" sha-new "deployed_tag updated"
assert_eq "$(cat "$STUB_STATE/last_up_tag")" sha-new "stack started with the new tag"
assert_calls '^docker .*pull app$' 1 "app image pulled"
assert_eq "$(find "$CC_ROOT/backups/pre-deploy" -name "*.db" | wc -l)" 1 "database snapshot taken"
assert_eq "$(python3 -c "import sqlite3,sys; print(sqlite3.connect(sys.argv[1]).execute('select title from media_items').fetchone()[0])" "$CC_ROOT"/backups/pre-deploy/*.db)" A "snapshot is a readable copy"
cmp -s "$CC_ROOT/compose.yaml" "$REPO_ROOT/deploy/compose.yaml" && pass "compose.yaml copied from the repo" || fail "compose.yaml not copied"
assert_eq "$(cat "$CC_ROOT/compose.yaml.prev")" old-compose "previous compose.yaml kept"
assert_file "$CC_ROOT/bin/notify.sh" "helper scripts copied"
assert_calls '^docker .*image rm .*:sha-b$' 1 "old image beyond the newest 3 removed"
assert_calls '^docker .*image rm .*:sha-c$' 1 "(second old image too)"
assert_calls '^docker .*image rm .*:sha-old' 0 "previous image kept for rollback"
assert_no_grep '^docker .*(prune|system)' "$STUB_STATE/calls.log" "no prune commands"
assert_no_grep 'down|rm -f' "$STUB_STATE/calls.log" "never takes anything down"
teardown

echo "== deploy: broken tag rolls back"
setup_server
"$DEPLOY" sha-broken1 >"$T/out.log" 2>&1 && fail "broken deploy exited 0" || pass "exits non-zero"
assert_eq "$(cat "$CC_ROOT/deployed_tag")" sha-old "deployed_tag untouched"
assert_eq "$(cat "$STUB_STATE/last_up_tag")" sha-old "old tag brought back up"
assert_eq "$(cat "$CC_ROOT/compose.yaml")" old-compose "compose.yaml restored"
assert_grep 'app log line: boot failed' "$T/out.log" "app logs printed"
assert_grep 'Rolled back: sha-old is live again' "$T/out.log" "rollback confirmed healthy"
teardown

echo "== deploy: healthy but wrong commit counts as a failure"
setup_server
# The stub app reports whatever tag it was started with; make it report a different sha.
# shellcheck disable=SC2016
sed -i 's|echo "${IMAGE_TAG#sha-}" >"$S/health_sha"|echo "stale" >"$S/health_sha"|' "$STUBS/stub"
"$DEPLOY" sha-new >"$T/out.log" 2>&1 && fail "accepted the wrong git_sha" || pass "rejected"
teardown

echo "== deploy: first deploy that fails stops the app"
setup_server
rm "$CC_ROOT/deployed_tag"
"$DEPLOY" sha-broken2 >"$T/out.log" 2>&1 && fail "should fail" || pass "exits non-zero"
assert_file "$STUB_STATE/app_stopped" "app stopped instead of restart-looping"
teardown

echo "== deploy: image that can't be pulled changes nothing"
setup_server
"$DEPLOY" sha-missing1 >"$T/out.log" 2>&1 && fail "should fail" || pass "exits non-zero"
assert_calls '^docker .* up ' 0 "nothing was started"
assert_eq "$(cat "$CC_ROOT/deployed_tag")" sha-old "deployed_tag untouched"
teardown

echo "== deploy: refuses unsafe situations"
setup_server
rm "$CC_ROOT/data/catalogue_full.db"
"$DEPLOY" sha-new >"$T/out.log" 2>&1 && fail "deployed without a database" || pass "no database: refused"
assert_grep 'no database' "$T/out.log" "says why"
assert_calls '^docker ' 0 "docker untouched"
ALLOW_NEW_DB=1 "$DEPLOY" sha-new >/dev/null 2>&1 && pass "ALLOW_NEW_DB=1 permits a fresh start" || fail "ALLOW_NEW_DB=1 should work"
teardown
setup_server
DOCKER_HOST='' "$DEPLOY" sha-new >/dev/null 2>&1 && fail "ran without DOCKER_HOST" || pass "refuses without DOCKER_HOST"
DOCKER_HOST=unix:///var/run/docker.sock "$DEPLOY" sha-new >/dev/null 2>&1 && fail "used the system daemon" || pass "refuses the system Docker socket"
"$DEPLOY" 'sha-x; rm -rf /' >/dev/null 2>&1 && fail "accepted a bad tag" || pass "refuses a malformed tag"
"$DEPLOY" >/dev/null 2>&1 && fail "accepted no tag" || pass "refuses no tag"
assert_calls '^docker ' 0 "docker never called"
( exec 9>"$CC_ROOT/state/deploy.lock"; flock 9; sleep 3 ) 2>/dev/null &
LOCK_PID=$!
sleep 0.5
"$DEPLOY" sha-new >"$T/out.log" 2>&1 && fail "ran two deploys at once" || pass "second deploy refused while one runs"
assert_grep 'another deploy' "$T/out.log" "says why"
wait "$LOCK_PID"
teardown

echo "== deploy: keeps only the newest 7 snapshots"
setup_server
for i in 1 2 3 4 5 6 7 8 9; do : >"$CC_ROOT/backups/pre-deploy/2020010${i}T000000Z-before-x.db"; touch -d "2020-01-0$i" "$CC_ROOT/backups/pre-deploy/2020010${i}T000000Z-before-x.db"; done 2>/dev/null || { mkdir -p "$CC_ROOT/backups/pre-deploy"; for i in 1 2 3 4 5 6 7 8 9; do : >"$CC_ROOT/backups/pre-deploy/2020010${i}T000000Z-before-x.db"; touch -d "2020-01-0$i" "$CC_ROOT/backups/pre-deploy/2020010${i}T000000Z-before-x.db"; done; }
"$DEPLOY" sha-new >/dev/null 2>&1
assert_eq "$(find "$CC_ROOT/backups/pre-deploy" -name "*.db" | wc -l)" 7 "7 snapshots remain"
assert_no_file "$CC_ROOT/backups/pre-deploy/20200101T000000Z-before-x.db" "oldest pruned"
assert_eq "$(find "$CC_ROOT/backups/pre-deploy" -name "*-before-sha-new.db" | wc -l)" 1 "newest kept"
teardown

echo "== notify.sh"
new_sandbox
mkdir -p "$CC_ROOT"
echo "NTFY_TOPIC=topic-abc" >"$CC_ROOT/.env"
"$REPO_ROOT/deploy/bin/notify.sh" "Title" "Body" high >/dev/null 2>&1; rc=$?
assert_eq "$rc" 0 "exits 0"
assert_grep '^curl .*-H Title: Title.*-H Priority: high.*https://ntfy.sh/topic-abc$' "$STUB_STATE/calls.log" "posts to the topic from .env"
rm "$CC_ROOT/.env"
"$REPO_ROOT/deploy/bin/notify.sh" "Title" "Body" >/dev/null 2>&1; assert_eq "$?" 0 "no topic: still exits 0"
finish
