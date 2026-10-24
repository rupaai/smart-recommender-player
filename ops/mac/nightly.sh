#!/usr/bin/env bash
# The training run on the Mac (P7.1 of the old deployment plan). Nothing schedules it: the Train
# button (ops/mac/train-now.sh, "Train CouchCinema" in ~/Applications) runs it when you choose.
#
#   nightly.sh [--skip-backup] [--skip-catalogue] [--catalogue] [--skip-train] [--skip-publish]
#
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
LOG="$LOG_DIR/$TODAY.log"
mkdir -p "$LOG_DIR" "$BACKUP_DIR" "$BUNDLE_DIR" "$WORK"

DO_BACKUP=1; DO_CATALOGUE=""; DO_TRAIN=1; DO_PUBLISH=1
# Weekly: due when the last successful refresh is a week old, or never happened.
last_catalogue="$(cat "$CC_HOME/last-catalogue" 2>/dev/null || echo never)"
[ -n "$(find "$CC_HOME/last-catalogue" -mtime -7 2>/dev/null)" ] || DO_CATALOGUE=1
for arg in "$@"; do
  case "$arg" in
    --skip-backup) DO_BACKUP="" ;;
    --skip-catalogue) DO_CATALOGUE="skip" ;;
    --catalogue) DO_CATALOGUE=1 ;;
    --skip-train) DO_TRAIN="" ;;
    --skip-publish) DO_PUBLISH="" ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done
[ "$DO_CATALOGUE" = skip ] && DO_CATALOGUE=""

# From the Train button (a terminal) you watch it as it goes; either way it is all in the log.
if [ -t 1 ]; then exec > >(tee -a "$LOG") 2>&1; else exec >>"$LOG" 2>&1; fi
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
  log "2. catalogue refresh: not due (last one $last_catalogue)"
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
  DATABASE_PATH="$WORK/snapshot.db" uv run --with torch --with sentence-transformers --with onnx --with onnxscript \
    python training/build_vibe_index.py --out "$WORK/vibe" ${skip_export[@]+"${skip_export[@]}"} \
    || fail "train" "building the vibe index failed"
  log "4. train: Wishmatch crowd (MovieLens)"
  DATABASE_PATH="$WORK/snapshot.db" uv run python training/build_wishmatch_crowd.py \
    --movielens training/data/ml-latest-small --db "$WORK/snapshot.db" --out "$WORK/wishmatch" \
    || log "warning: the Wishmatch crowd build failed; Wishmatch works without crowd data"
  log "4. train: mood tags for new titles (Ollama, free; wishmatch.tagging_model)"
  DATABASE_PATH="$WORK/snapshot.db" uv run python utils/mood_tag_ollama.py --db "$WORK/snapshot.db" tag \
    --max-minutes "$("${PL[@]}" setting nightly.mood_tag_minutes 60)" \
    || log "warning: tagging new titles failed or no tagging model is set; they are tagged next time"
  # Written taste profiles cost a little Anthropic money (under 1 cent), so they are off until
  # config.yaml says nightly.wishmatch_profiles: true.
  if [ "$("${PL[@]}" setting nightly.wishmatch_profiles false)" = "true" ]; then
    log "4. train: Wishmatch taste profiles (Haiku, under 1 cent)"
    uv run --extra llm python utils/wishmatch_profiles.py --db "$WORK/snapshot.db" --yes \
      --push "${CC_SERVER:-$(env_value CC_SERVER)}" \
      || log "warning: the taste profiles could not be made; the old ones stay"
  fi

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
  wishmatch_arg=(); [ -f "$WORK/wishmatch/crowd.json" ] && wishmatch_arg=(--wishmatch "$WORK/wishmatch")
  BUNDLE="$BUNDLE_DIR/bundle-$TODAY-$(date +%H%M%S).tar.gz"
  "${CC[@]}" build-bundle "$BUNDLE" --metrics "$WORK/metrics.json" --tiger training/tiger --vibe "$WORK/vibe" \
    ${wishmatch_arg[@]+"${wishmatch_arg[@]}"} --db-before "$WORK/snapshot.before.db" --db-after "$WORK/snapshot.db" --source-db "$WORK/snapshot.db" --gate "$GATE" \
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
