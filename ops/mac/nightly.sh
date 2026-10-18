#!/usr/bin/env bash
# The nightly job on the Mac (docs/DEPLOYMENT_PLAN.md, P7.1).
#
#   nightly.sh [--scheduled] [--skip-backup] [--skip-catalogue] [--catalogue] [--skip-train] [--skip-publish]
#
#   --scheduled       how launchd runs it: every hour at :30 from 03:30. Runs the night once a
#                     day, the first time the server can be reached; until then it exits quietly
#                     (a laptop that is asleep, off or away from home is not a failure)
#   --catalogue       refresh the catalogue now even if the last refresh is less than a week old
#   --skip-*          leave a step out (handy for trying one step on its own)
#
# Steps: 1 backup, 2 catalogue refresh (weekly), 3 fresh snapshot, 4 train, 5 evaluate,
# 6 publish, 7 report. Settings live in config.yaml (nightly:) and ~/.couchcinema/env.
# One run at a time (lock), everything is logged to ~/Library/Logs/CouchCinema/<date>.log.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CC_HOME="${CC_HOME:-$HOME/.couchcinema}"
LOG_DIR="${CC_LOG_DIR:-$HOME/Library/Logs/CouchCinema}"
BACKUP_DIR="${CC_BACKUP_DIR:-$HOME/CouchCinemaBackups}"
BUNDLE_DIR="${CC_BUNDLE_DIR:-$HOME/CouchCinemaBundles}"
WORK="$CC_HOME/work"
TODAY="$(date +%F)"
HOUR="${CC_HOUR:-$(date +%H)}"   # CC_HOUR: tests only
LOG="$LOG_DIR/$TODAY.log"
mkdir -p "$LOG_DIR" "$BACKUP_DIR" "$BUNDLE_DIR" "$WORK"

SCHEDULED=""; DO_BACKUP=1; DO_CATALOGUE=""; DO_TRAIN=1; DO_PUBLISH=1
# Weekly: due when the last successful refresh is a week old (or never happened), whichever
# day the Mac is first on, so a missed Sunday is not a missed week.
last_catalogue="$(cat "$CC_HOME/last-catalogue" 2>/dev/null || echo never)"
if [ -z "$(find "$CC_HOME/last-catalogue" -mtime -7 2>/dev/null)" ]; then DO_CATALOGUE=1; fi
for arg in "$@"; do
  case "$arg" in
    --scheduled) SCHEDULED=1 ;;
    --skip-backup) DO_BACKUP="" ;;
    --skip-catalogue) DO_CATALOGUE="skip" ;;
    --catalogue) DO_CATALOGUE=1 ;;
    --skip-train) DO_TRAIN="" ;;
    --skip-publish) DO_PUBLISH="" ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done
[ "$DO_CATALOGUE" = skip ] && DO_CATALOGUE=""

# Scheduled runs: once a day, not before 03:00. Silent exits, so the day's log stays readable.
if [ -n "$SCHEDULED" ]; then
  [ "$HOUR" -ge 3 ] || exit 0
  [ "$(cat "$CC_HOME/last-run" 2>/dev/null)" = "$TODAY" ] && exit 0
fi

exec >>"$LOG" 2>&1
log() { printf '%s  %s\n' "$(date '+%H:%M:%S')" "$*"; }

env_value() { [ -f "$CC_HOME/env" ] && grep -m1 "^$1=" "$CC_HOME/env" | cut -d= -f2- | tr -d "\"'" || true; }

notify() {  # notify <title> <message> [priority]
  local topic server; topic="${NTFY_TOPIC:-$(env_value NTFY_TOPIC)}"; server="${NTFY_SERVER:-$(env_value NTFY_SERVER)}"
  [ -n "$topic" ] || { log "(no NTFY_TOPIC set; would have sent: $1 - $2)"; return 0; }
  curl -fsS --max-time 10 -o /dev/null -H "Title: $1" -H "Priority: ${3:-high}" -d "$2" "${server:-https://ntfy.sh}/$topic" \
    || log "could not send the notification"
}

ping_ok() {
  local url; url="${HEALTHCHECKS_NIGHTLY_URL:-$(env_value HEALTHCHECKS_NIGHTLY_URL)}"
  [ -n "$url" ] && { curl -fsS --max-time 10 -o /dev/null --data-raw "$1" "$url" || log "could not ping healthchecks.io"; }
  return 0
}

# ---- one run at a time ----------------------------------------------------
LOCK="$CC_HOME/nightly.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  old="$(cat "$LOCK/pid" 2>/dev/null || true)"
  if [ -n "$old" ] && kill -0 "$old" 2>/dev/null; then
    log "another nightly run is still going (pid $old); stopping"; exit 0
  fi
  log "found a stale lock from a run that died; taking over"
  rm -rf "$LOCK"; mkdir "$LOCK"
fi
echo $$ >"$LOCK/pid"
trap 'rm -rf "$LOCK"' EXIT

# A scheduled run waits a minute for the network (a Mac waking up joins wifi and Tailscale a
# little after launchd starts the job). Still nothing: try again next hour, no alert. Only
# when a whole day has gone by without reaching the server (from 21:00), one alert a day.
if [ -n "$SCHEDULED" ]; then
  server="${CC_SERVER:-$(env_value CC_SERVER)}"; tries="${CC_REACH_TRIES:-6}"
  until curl -fsS --max-time 5 -o /dev/null "${server%/}/healthz" 2>/dev/null; do
    tries=$((tries - 1))
    if [ "$tries" -le 0 ]; then
      log "server $server not reachable (asleep, off the home network or Tailscale off); trying again next hour"
      last_ok="$(cat "$CC_HOME/last-run" 2>/dev/null || echo never)"
      if [ "$HOUR" -ge 21 ] && [ "$(cat "$CC_HOME/last-unreachable-alert" 2>/dev/null)" != "$TODAY" ]; then
        echo "$TODAY" >"$CC_HOME/last-unreachable-alert"
        notify "CouchCinema nightly has not run today" "The Mac could not reach $server all day (last run: $last_ok). It keeps trying every hour while the Mac is on." default
      fi
      exit 0
    fi
    sleep "${CC_REACH_WAIT:-10}"
  done
  echo "$TODAY" >"$CC_HOME/last-run"   # reached the server: today's attempt counts, pass or fail
fi

cd "$REPO" || exit 1
CC=(uv run python ops/mac/cc.py)
PL=(uv run python ops/mac/pipeline.py)
RESULT="ok"

fail() {  # fail <step> <message>   (stops the run)
  log "FAILED: $1: $2"
  notify "CouchCinema nightly failed" "$1: $2. Log: $LOG"
  exit 1
}

run() { "$@" || { log "command failed: $*"; return 1; }; }

# Weekly: fill the catalogue from TMDB on a COPY of the server's database, then send only
# the difference (new titles above the quality floor, changed columns) back as a patch.
refresh_catalogue() {
  local base limit
  base="$BACKUP_DIR/$TODAY.db"
  if [ ! -f "$base" ]; then for f in "$BACKUP_DIR"/*.db; do base="$f"; done; fi   # names are dates, so the last one is newest
  [ -f "$base" ] || { log "no backup to work from"; return 1; }
  cp "$base" "$WORK/catalogue.before.db" && cp "$base" "$WORK/catalogue.work.db" || return 1
  run env DATABASE_PATH="$WORK/catalogue.work.db" uv run python utils/build_full_catalogue.py \
    --db "$WORK/catalogue.work.db" --history-source "$WORK/none.db" || return 1
  for script in backfill_vote_counts backfill_collections backfill_season_counts backfill_tmdb_vibe; do
    run env DATABASE_PATH="$WORK/catalogue.work.db" uv run python "utils/$script.py" || return 1
  done
  limit="$("${PL[@]}" setting nightly.mood_tag_limit 0)"
  if [ "$limit" -gt 0 ]; then
    run env DATABASE_PATH="$WORK/catalogue.work.db" uv run --extra llm python utils/mood_tag.py run --limit "$limit" --yes || return 1
  else
    log "mood tagging is off (nightly.mood_tag_limit: 0)"
  fi
  run "${CC[@]}" diff-catalogue "$WORK/catalogue.before.db" "$WORK/catalogue.work.db" "$WORK/catalogue.patch.jsonl" \
    --min-votes "$("${PL[@]}" setting nightly.min_vote_count 200)" --min-rating "$("${PL[@]}" setting nightly.min_rating 6.0)" || return 1
  run "${CC[@]}" push-patch "$WORK/catalogue.patch.jsonl"
}

log "=== nightly run $TODAY (backup=${DO_BACKUP:+yes} catalogue=${DO_CATALOGUE:+yes} train=${DO_TRAIN:+yes} publish=${DO_PUBLISH:+yes}) ==="

# ---- 1. backup --------------------------------------------------------------
if [ -n "$DO_BACKUP" ]; then
  log "1. backup: downloading the server's database"
  # cc.py export checks the copy with PRAGMA integrity_check before it keeps it.
  "${CC[@]}" export "$BACKUP_DIR/$TODAY.db" || fail "backup" "could not download the database (is Tailscale up?)"
  days="$("${PL[@]}" setting nightly.keep_backup_days 30)"
  find "$BACKUP_DIR" -name '*.db' -mtime "+$days" -delete
  log "1. backup saved to $BACKUP_DIR/$TODAY.db (keeping $days days)"
else
  log "1. backup: skipped"
fi

# ---- 2. catalogue refresh (Sundays) ------------------------------------------
if [ -n "$DO_CATALOGUE" ]; then
  log "2. catalogue refresh"
  if refresh_catalogue; then
    date +%F >"$CC_HOME/last-catalogue"
    log "2. catalogue refresh done"
  else
    log "2. catalogue refresh FAILED; carrying on with training on what the server has"
    notify "CouchCinema: catalogue refresh failed" "Training continues on the current catalogue. See $LOG" default
    RESULT="catalogue refresh failed"
  fi
else
  log "2. catalogue refresh: not tonight (last one $last_catalogue)"
fi

# ---- 3. fresh snapshot -------------------------------------------------------
if [ -n "$DO_TRAIN" ]; then
  log "3. snapshot: downloading the database again so training sees the server's ids"
  "${CC[@]}" export "$WORK/snapshot.db" || fail "snapshot" "could not download the database"
  cp "$WORK/snapshot.db" "$WORK/snapshot.before.db"

  # ---- 4. train ----------------------------------------------------------------
  [ -f training/tiger/semantic_ids.json ] || fail "train" "training/tiger/semantic_ids.json is missing; build the TIGER item codes once first (training/README.md)"
  [ -f training/data/ml-latest-small/ratings.csv ] || fail "train" "training/data/ml-latest-small/ratings.csv is missing (MovieLens, see training/README.md)"
  log "4. train: TIGER (about 5 to 15 minutes)"
  DATABASE_PATH="$WORK/snapshot.db" SKIP_DEPLOY=1 bash training/retrain_tiger.sh || fail "train" "TIGER training or its deployment gates failed"
  log "4. train: vibe index"
  skip_export=(); [ -f "$WORK/vibe/query_encoder.onnx" ] && skip_export=(--skip-export)
  DATABASE_PATH="$WORK/snapshot.db" uv run python training/build_vibe_index.py --out "$WORK/vibe" ${skip_export[@]+"${skip_export[@]}"} \
    || fail "train" "building the vibe index failed"

  # ---- 5. evaluate ---------------------------------------------------------------
  log "5. evaluate"
  uv run python utils/eval_taste_model.py --db "$WORK/snapshot.db" --vibe-dir "$WORK/vibe" --skip-discover --json "$WORK/taste.json" \
    || log "taste model evaluation failed; gating on the other numbers"
  "${PL[@]}" evaluate --db "$WORK/snapshot.db" --tiger training/tiger --taste-json "$WORK/taste.json" --out "$WORK/metrics.json" \
    || fail "evaluate" "could not evaluate the new model"
  "${CC[@]}" model-status >"$WORK/status.json" || fail "evaluate" "could not ask the server which model it runs"
  if gate_msg="$("${PL[@]}" gate --new "$WORK/metrics.json" --status "$WORK/status.json" --max-drop-pct "$("${PL[@]}" setting nightly.gate_max_drop_pct 1.0)")"; then
    GATE=passed
  else
    GATE=failed
  fi
  log "5. evaluate: gate $GATE ($gate_msg)"

  # ---- 6. publish ------------------------------------------------------------------
  BUNDLE="$BUNDLE_DIR/bundle-$TODAY-$(date +%H%M%S).tar.gz"
  "${CC[@]}" build-bundle "$BUNDLE" --metrics "$WORK/metrics.json" --tiger training/tiger --vibe "$WORK/vibe" \
    --db-before "$WORK/snapshot.before.db" --db-after "$WORK/snapshot.db" --source-db "$WORK/snapshot.db" --gate "$GATE" \
    || fail "publish" "could not build the model bundle"
  bundles=("$BUNDLE_DIR"/bundle-*.tar.gz)   # names carry the date and time: keep the last 3
  if [ "${#bundles[@]}" -gt 3 ]; then rm -f "${bundles[@]:0:${#bundles[@]}-3}"; fi
  if [ "$GATE" = passed ] && [ -n "$DO_PUBLISH" ]; then
    "${CC[@]}" push-bundle "$BUNDLE" || fail "publish" "the server refused the new model (see the log); the old one is still running"
    log "6. publish: the server now runs the new model"
    RESULT="published a new model"
  elif [ "$GATE" = passed ]; then
    log "6. publish: skipped (--skip-publish); bundle kept at $BUNDLE"
  else
    log "6. publish: NOT published, gate failed: $gate_msg. Bundle kept at $BUNDLE"
    RESULT="model held back: $gate_msg"
  fi
else
  log "3-6. training: skipped"
fi

# ---- 7. report ---------------------------------------------------------------------
log "7. done: $RESULT"
ping_ok "$RESULT"
exit 0
