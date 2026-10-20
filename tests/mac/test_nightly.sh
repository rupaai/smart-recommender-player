#!/usr/bin/env bash
# Tests for ops/mac/nightly.sh against stand-ins for uv and curl, in a temp dir.
# Proves the order of steps, the flags, the gate, the lock and the alerts; it does not train
# anything or touch a server. Run: bash tests/mac/test_nightly.sh
set -uo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FAILS=0
fail() { printf '  FAIL: %s\n' "$*"; FAILS=$((FAILS + 1)); }
ok() { [ -z "${VERBOSE:-}" ] || printf '  ok: %s\n' "$*"; }
has() { grep -qE -- "$1" "$CALLS" && ok "$2" || fail "$2 (no call matching $1)"; }
hasnt() { ! grep -qE -- "$1" "$CALLS" && ok "$2" || fail "$2 (unexpected call matching $1)"; }
logged() { grep -qE -- "$1" "$LOGFILE" && ok "$2" || fail "$2 (log lacks $1)"; }

new_sandbox() {
  T="$(mktemp -d)"
  mkdir -p "$T/repo/ops/mac" "$T/repo/training/tiger" "$T/repo/training/data/ml-latest-small" "$T/bin" "$T/home/.couchcinema"
  cp "$REPO_ROOT/ops/mac/nightly.sh" "$REPO_ROOT/ops/mac/train-now.sh" "$REPO_ROOT/ops/mac/install.sh" "$T/repo/ops/mac/"
  cp "$REPO_ROOT/training/retrain_tiger.sh" "$T/repo/training/"
  echo '{"items":{}}' >"$T/repo/training/tiger/semantic_ids.json"
  : >"$T/repo/training/data/ml-latest-small/ratings.csv"
  printf 'CC_SERVER=http://srv.example:8000\nADMIN_TOKEN=tok\nNTFY_TOPIC=testtopic\nHEALTHCHECKS_NIGHTLY_URL=https://hc.example/ping\n' >"$T/home/.couchcinema/env"
  CALLS="$T/calls.log"; : >"$CALLS"
  cat >"$T/bin/uv" <<'STUB'
#!/usr/bin/env bash
echo "uv $*" >>"$CALLS"
args="$*"
case "$args" in
  *"cc.py export "*) python3 -c "import sqlite3,sys; sqlite3.connect(sys.argv[1]).execute('create table t(x)')" "${args##*cc.py export }" ;;
  *"cc.py model-status"*) echo '{"current":"b0","manifest":{"metrics":{"tiger_hit_rate":0.5}}}' ;;
  *"cc.py build-bundle "*) rest="${args##*build-bundle }"; : >"${rest%% *}" ;;
  *"cc.py diff-catalogue "*) rest="${args##*diff-catalogue }"; set -- $rest; : >"$3" ;;
  *"pipeline.py setting "*) k="${args##*setting }"; echo "${k##* }" ;;
  *"pipeline.py evaluate"*) o="${args##*--out }"; echo '{"tiger_hit_rate":0.5}' >"${o%% *}" ;;
  *"pipeline.py gate"*) echo "stub gate message"; exit "${STUB_GATE_EXIT:-0}" ;;
esac
if [ -n "${STUB_FAIL:-}" ] && echo "$args" | grep -qE -- "$STUB_FAIL"; then exit 1; fi
exit 0
STUB
  # shellcheck disable=SC2016
  printf '#!/usr/bin/env bash\necho "curl $*" >>"$CALLS"\ncase "$*" in *healthz*) [ -z "${STUB_NO_SERVER:-}" ] || exit 7 ;; esac\nexit 0\n' >"$T/bin/curl"
  # shellcheck disable=SC2016
  printf '#!/usr/bin/env bash\necho "launchctl $*" >>"$CALLS"\n' >"$T/bin/launchctl"
  printf '#!/usr/bin/env bash\necho Darwin\n' >"$T/bin/uname"
  # shellcheck disable=SC2016
  printf '#!/usr/bin/env bash\necho "osascript $*" >>"$CALLS"\n' >"$T/bin/osascript"
  chmod +x "$T/bin/uv" "$T/bin/curl" "$T/bin/launchctl" "$T/bin/uname" "$T/bin/osascript"
  export CALLS T
  export PATH="$T/bin:$PATH" HOME="$T/home" CC_HOME="$T/home/.couchcinema" \
         CC_LOG_DIR="$T/logs" CC_BACKUP_DIR="$T/backups" CC_BUNDLE_DIR="$T/bundles"
  unset STUB_GATE_EXIT STUB_FAIL STUB_NO_SERVER
  LOGFILE="$T/logs/$(date +%F).log"
}
run() { bash "$T/repo/ops/mac/nightly.sh" "$@"; }

echo "== nightly: a normal night publishes"
new_sandbox
run --skip-catalogue; rc=$?
[ $rc -eq 0 ] && ok "exit 0" || fail "exit code $rc"
has "cc.py export .*/backups/.*\.db" "backs up"
has "cc.py export .*snapshot\.db" "takes a fresh snapshot"
hasnt "build_full_catalogue" "no catalogue refresh when skipped"
has "bootstrap_sequences" "TIGER sequences step ran"
has "train_tiger_predictor" "TIGER training step ran"
has "build_vibe_index.py --out .*/work/vibe" "builds the vibe index into the work folder"
has "uv run --with torch --with sentence-transformers --with onnx --with onnxscript python training/build_vibe_index.py" \
  "installs the vibe-index training dependencies"
has "build-bundle .*--gate passed" "builds a passing bundle"
has "push-bundle" "publishes it"
has "curl .*hc.example/ping" "pings healthchecks"
hasnt "curl .*testtopic" "no alert on success"
[ ! -d "$T/home/.couchcinema/nightly.lock" ] && ok "lock released" || fail "lock left behind"
[ ! -e "$T/repo/models" ] && ok "local models/ untouched" || fail "retrain wrote into models/"

echo "== nightly: gate says no"
new_sandbox
STUB_GATE_EXIT=3 run --skip-catalogue; rc=$?
[ $rc -eq 0 ] && ok "still a clean exit" || fail "exit code $rc"
has "build-bundle .*--gate failed" "bundle is built and marked failed"
hasnt "push-bundle" "nothing published"
logged "NOT published, gate failed: stub gate message" "log says why"
has "curl .*hc.example/ping" "heartbeat still sent"

echo "== nightly: weekly catalogue refresh"
new_sandbox
run --catalogue --skip-train; rc=$?
[ $rc -eq 0 ] && ok "exit 0" || fail "exit code $rc"
has "build_full_catalogue.py --db .*catalogue\.work\.db" "refreshes a work copy, not the backup"
has "backfill_tmdb_vibe" "enrichment ran"
hasnt "mood_tag\.py" "mood tagging stays off by default"
has "diff-catalogue .*--min-votes 200 --min-rating 6.0" "quality floor applied (defaults)"
has "push-patch" "patch pushed"
hasnt "train_tiger_predictor" "no training with --skip-train"

echo "== nightly: catalogue failure does not stop the night"
new_sandbox
STUB_FAIL="build_full_catalogue" run --catalogue; rc=$?
[ $rc -eq 0 ] && ok "exit 0" || fail "exit code $rc"
hasnt "push-patch" "no patch after a failed refresh"
has "build-bundle" "training still ran"
has "curl .*testtopic" "a warning was sent"

echo "== nightly: training failure alerts and stops"
new_sandbox
STUB_FAIL="train_tiger_predictor" run --skip-catalogue; rc=$?
[ $rc -ne 0 ] && ok "non-zero exit" || fail "should have failed"
hasnt "push-bundle" "nothing published"
has "curl .*testtopic" "ntfy alert sent"
hasnt "curl .*hc.example/ping" "no success ping"
[ ! -d "$T/home/.couchcinema/nightly.lock" ] && ok "lock released after failure" || fail "lock left behind"

echo "== nightly: server unreachable"
new_sandbox
STUB_FAIL="cc.py export" run; rc=$?
[ $rc -ne 0 ] && ok "non-zero exit" || fail "should have failed"
has "curl .*testtopic" "ntfy alert sent"
hasnt "retrain" "did not start training"

echo "== nightly: missing MovieLens file is explained"
new_sandbox
rm "$T/repo/training/data/ml-latest-small/ratings.csv"
run --skip-catalogue; rc=$?
[ $rc -ne 0 ] && ok "non-zero exit" || fail "should have failed"
logged "ratings.csv is missing" "log names the missing file"

echo "== nightly: only one run at a time"
new_sandbox
mkdir "$T/home/.couchcinema/nightly.lock"; sleep 30 & other=$!; echo $other >"$T/home/.couchcinema/nightly.lock/pid"
run --skip-catalogue; rc=$?
kill $other 2>/dev/null
[ $rc -eq 0 ] && ok "second run exits quietly" || fail "exit code $rc"
hasnt "cc.py export" "second run did nothing"
logged "still going" "log says why"
echo 99999999 >"$T/home/.couchcinema/nightly.lock/pid"
run --skip-catalogue
has "cc.py export" "a stale lock is taken over"

echo "== catalogue is weekly, not tied to Sundays"
new_sandbox
run --skip-train
has "build_full_catalogue" "first run refreshes (never refreshed)"
[ -f "$CC_HOME/last-catalogue" ] && ok "refresh date saved" || fail "no last-catalogue"
: >"$CALLS"; run --skip-train
hasnt "build_full_catalogue" "not again within the week"
logged "not due \\(last one" "log says when the last one was"
touch -t "$(date -d '-8 days' +%Y%m%d%H%M 2>/dev/null || date -v-8d +%Y%m%d%H%M)" "$CC_HOME/last-catalogue"
: >"$CALLS"; run --skip-train
has "build_full_catalogue" "due again after a week"

echo "== Train button: server not reachable"
new_sandbox
out="$(STUB_NO_SERVER=1 bash "$T/repo/ops/mac/train-now.sh" </dev/null 2>&1)"; rc=$?
[ $rc -ne 0 ] && ok "non-zero exit" || fail "should have failed"
has "curl .*srv.example:8000/healthz" "checks the server first"
hasnt "cc.py" "did not start"
hasnt "curl .*testtopic" "no phone alert: you are watching"
echo "$out" | grep -q "Can't reach the server" && ok "says why" || fail "no clear message: $out"

echo "== Train button: trains and publishes"
new_sandbox
out="$(bash "$T/repo/ops/mac/train-now.sh" --skip-catalogue </dev/null 2>&1)"; rc=$?
[ $rc -eq 0 ] && ok "exit 0" || fail "exit code $rc: $out"
has "push-bundle" "publishes the new model"
has "osascript .*Done: published a new model" "Mac notification with the result"

echo "== install: adds the button, removes the old schedule"
new_sandbox
mkdir -p "$T/agents"; : >"$T/agents/com.couchcinema.nightly.plist"
CC_AGENTS_DIR="$T/agents" CC_APPS_DIR="$T/apps" bash "$T/repo/ops/mac/install.sh" >/dev/null 2>&1; rc=$?
[ $rc -eq 0 ] && ok "exit 0" || fail "exit code $rc"
[ ! -f "$T/agents/com.couchcinema.nightly.plist" ] && ok "old schedule file removed" || fail "old plist left"
has "launchctl bootout .*com.couchcinema.nightly" "old job unloaded"
hasnt "launchctl bootstrap" "nothing scheduled"
[ -x "$T/apps/Train CouchCinema.command" ] && grep -q "$T/repo/ops/mac/train-now.sh" "$T/apps/Train CouchCinema.command" \
  && ok "button points at train-now.sh" || fail "no button"

[ $FAILS -eq 0 ] && echo "all nightly tests passed" || { echo "$FAILS failed"; exit 1; }
