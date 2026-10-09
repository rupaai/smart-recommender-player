#!/usr/bin/env bash
# bootstrap.sh and import-data.sh against stubbed system commands (see harness.sh).
set -uo pipefail
# shellcheck source=harness.sh
. "$(dirname "${BASH_SOURCE[0]}")/harness.sh"
BOOT="$REPO_ROOT/deploy/bootstrap.sh"
IMPORT="$REPO_ROOT/deploy/import-data.sh"

echo "== bootstrap: first run"
new_sandbox
export TMDB_API_KEY=tmdbkey123 RUNNER_TOKEN=tok123 MEDIA_ROOT="$T/media" HEALTHCHECKS_URL=https://hc-ping.com/abc
"$BOOT" </dev/null >"$T/run1.log" 2>&1 || { cat "$T/run1.log"; fail "first run exited non-zero"; }
ENV="$CC_ROOT/.env"
assert_grep '^TMDB_API_KEY=tmdbkey123$' "$ENV" "TMDB key written"
assert_grep "^MEDIA_ROOT=$T/media$" "$ENV" "media root written"
assert_grep '^APP_USER=0:0$' "$ENV" "app runs as 0:0 under rootless Docker"
assert_grep '^SVC_PUID=0$' "$ENV" "Prowlarr runs as container root"
assert_grep '^ADMIN_TOKEN=[0-9a-f]{64}$' "$ENV" "admin token generated"
assert_grep '^NTFY_TOPIC=couchcinema-[a-z0-9]{16}$' "$ENV" "private ntfy topic generated"
assert_grep '^HEALTHCHECKS_URL=https://hc-ping.com/abc$' "$ENV" "healthchecks URL written"
assert_eq "$(stat -c %a "$ENV")" 600 ".env is mode 600"
for d in data models/releases backups/pre-deploy jellyfin/config jellyfin/cache prowlarr/config; do assert_file "$CC_ROOT/$d" "$d created"; done
assert_file "$CC_ROOT/compose.yaml" "compose.yaml copied"
assert_file "$CC_ROOT/config.server.yaml" "config.server.yaml copied"
for d in Movies Shows downloads; do assert_file "$T/media/$d" "media/$d created"; done
KEY="$(sed -n 's/^PROWLARR_API_KEY=//p' "$ENV")"
assert_grep "<ApiKey>$KEY</ApiKey>" "$CC_ROOT/prowlarr/config/config.xml" "Prowlarr key seeded and matching .env"
assert_calls '^useradd ' 1 "deploy user created once"
assert_no_grep 'usermod.*-aG|gpasswd|adduser .*docker' "$STUB_STATE/calls.log" "deploy user is never added to a group"
assert_calls '^dockerd-rootless-setuptool.sh install' 1 "rootless Docker installed once"
assert_calls '^loginctl enable-linger deploy' 1 "linger enabled"
assert_calls '^config.sh .*--labels mars' 1 "runner registered with label mars"
assert_calls '^svc.sh install deploy' 1 "runner service installed as deploy"
assert_file "$APPARMOR_FILE" "AppArmor profile for rootlesskit installed"
assert_file "$UDEV_RULE_FILE" "render node rule installed"
assert_calls '^setfacl -m u:deploy:rw' 1 "deploy granted the render node"
assert_grep 'DOCKER_HOST=unix://.*/docker.sock' "$T/home/deploy/actions-runner/.env" "runner jobs use the deploy user's daemon"
assert_calls '^docker .*compose -p couchcinema .*up -d jellyfin prowlarr$' 1 "only jellyfin and prowlarr started"
assert_eq "$(grep -E '^docker .* up ' "$STUB_STATE/calls.log" | grep -vc 'up -d jellyfin prowlarr$')" 0 "app is not started by bootstrap"
# Every docker call after the daemon exists is addressed to the deploy user's own socket.
assert_eq "$(grep -E '^docker (info|compose -p|ps)' "$STUB_STATE/calls.log" | grep -vc 'DOCKER_HOST=unix://.*/1001/docker.sock')" 0 "every docker call uses the rootless socket"
assert_no_grep '^docker .*(prune|rm |stop|kill)' "$STUB_STATE/calls.log" "never prunes, removes or stops anything"
assert_grep '^\*/5 \* \* \* \* deploy .*/bin/healthcheck.sh$' "$CRON_FILE" "health check every 5 minutes"
assert_grep '^17 6 \* \* \* deploy .*/bin/diskcheck.sh$' "$CRON_FILE" "disk check daily"
assert_file "$CC_ROOT/bin/healthcheck.sh" "alert scripts copied to the server folder"
assert_calls '^curl .*Title: couchcinema is set up.*ntfy.sh/couchcinema-' 1 "test notification sent to the generated topic"
grep -q 'ADMIN_TOKEN' "$T/run1.log" && pass "summary prints the admin token" || fail "summary should print the admin token"

echo "== bootstrap: second run changes nothing"
cp "$ENV" "$T/env.before"; cp "$CC_ROOT/prowlarr/config/config.xml" "$T/xml.before"
: >"$STUB_STATE/calls.log"
unset RUNNER_TOKEN
"$BOOT" </dev/null >"$T/run2.log" 2>&1 || { cat "$T/run2.log"; fail "second run exited non-zero"; }
cmp -s "$ENV" "$T/env.before" && pass ".env unchanged" || fail ".env changed on re-run"
cmp -s "$CC_ROOT/prowlarr/config/config.xml" "$T/xml.before" && pass "Prowlarr config unchanged" || fail "Prowlarr config changed"
assert_calls '^useradd ' 0 "no second user"
assert_calls '^dockerd-rootless-setuptool' 0 "no second Docker install"
assert_calls '^config.sh' 0 "runner not registered again"
assert_calls '^svc.sh install' 0 "runner service not installed again"
assert_calls '^curl .*releases' 0 "runner not downloaded again"

echo "== bootstrap: refuses a deploy user in the docker group"
STUB_GROUPS="deploy docker" "$BOOT" </dev/null >"$T/run3.log" 2>&1 && fail "should refuse" || pass "refused"
assert_grep "group 'docker'" "$T/run3.log" "says why"
rm -rf "$T"

echo "== bootstrap: missing prerequisite stops before changing anything"
new_sandbox
# The real machine running this test may have newuidmap, so hide it: a PATH of the stubs
# plus a copy of every real command except that one.
mkdir "$T/realbin"
for f in /usr/bin/* /bin/* /usr/local/bin/*; do
  [ "$(basename "$f")" = newuidmap ] || ln -sf "$f" "$T/realbin/$(basename "$f")" 2>/dev/null
done
rm "$STUBS/newuidmap"
SAVED_PATH="$PATH"
PATH="$STUBS:$T/realbin"
export TMDB_API_KEY=k MEDIA_ROOT="$T/media"
"$BOOT" </dev/null >"$T/run4.log" 2>&1 && fail "should stop" || pass "stopped"
assert_grep 'apt-get install -y uidmap' "$T/run4.log" "prints the exact apt command"
assert_calls '^useradd ' 0 "nothing was changed"
PATH="$SAVED_PATH"
rm -rf "$T"

echo "== bootstrap: Docker without rootless tools (Ubuntu's docker.io)"
new_sandbox
rm "$STUBS/dockerd-rootless-setuptool.sh"
export TMDB_API_KEY=k MEDIA_ROOT="$T/media" RUNNER_TOKEN=t
"$BOOT" </dev/null >"$T/run5.log" 2>&1 && pass "setup succeeds" || { cat "$T/run5.log"; fail "failed without rootless tools on the machine"; }
assert_calls '^curl .*docker-rootless-extras-29.1.3.tgz' 1 "downloads the extras matching the installed Docker version"
assert_file "$T/home/deploy/bin/dockerd-rootless-setuptool.sh" "installed into the deploy user's ~/bin"
assert_file "$T/home/deploy/bin/docker" "docker linked next to the setup script (it looks there)"
assert_calls '^dockerd-rootless-setuptool.sh install' 1 "uses it to set up the deploy user's daemon"
assert_grep "$T/home/deploy/bin/rootlesskit" "$APPARMOR_FILE" "AppArmor profile points at that rootlesskit"
: >"$STUB_STATE/calls.log"
"$BOOT" </dev/null >/dev/null 2>&1
assert_calls '^curl .*docker-rootless-extras' 0 "second run doesn't download again"
rm -rf "$T"

echo "== bootstrap: --set"
new_sandbox
export TMDB_API_KEY=k MEDIA_ROOT="$T/media" RUNNER_TOKEN=t
"$BOOT" </dev/null >/dev/null 2>&1
unset RUNNER_TOKEN
"$BOOT" --set JELLYFIN_API_KEY=abc123 >"$T/set.log" 2>&1 && pass "accepted" || { cat "$T/set.log"; fail "--set failed"; }
assert_grep '^JELLYFIN_API_KEY=abc123$' "$CC_ROOT/.env" "value stored"
assert_no_grep '^docker .*up -d --no-deps app' "$STUB_STATE/calls.log" "app not touched before the first deploy"
echo sha-abc >"$CC_ROOT/deployed_tag"
"$BOOT" --set JELLYFIN_USER_ID=u1 >/dev/null 2>&1
assert_grep '^docker .*up -d --no-deps app$' "$STUB_STATE/calls.log" "only the app container is restarted"
assert_eq "$(cat "$STUB_STATE/last_up_tag")" sha-abc "restart keeps the deployed tag"
"$BOOT" --set 'BAD_KEY=1' >/dev/null 2>&1 && fail "unknown key accepted" || pass "unknown key rejected"
"$BOOT" --set 'TMDB_API_KEY=a b' >/dev/null 2>&1 && fail "value with a space accepted" || pass "unsafe value rejected"
rm -rf "$T"

echo "== import-data.sh"
new_sandbox
export TMDB_API_KEY=k MEDIA_ROOT="$T/media" RUNNER_TOKEN=t
"$BOOT" </dev/null >/dev/null 2>&1
python3 - "$T/mac.db" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
c.execute("CREATE TABLE media_items (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT)")
c.execute("CREATE TABLE watch_history (id INTEGER PRIMARY KEY, media_item_id INTEGER)")
c.executemany("INSERT INTO media_items (title) VALUES (?)", [("A",), ("B",)])
c.execute("INSERT INTO watch_history (media_item_id) VALUES (1)")
c.commit()
PY
echo "not a database" >"$T/junk.db"
"$IMPORT" db "$T/junk.db" >/dev/null 2>&1 && fail "junk accepted" || pass "non-database rejected"
assert_no_file "$CC_ROOT/data/catalogue_full.db" "junk not installed"
"$IMPORT" db "$T/mac.db" >"$T/imp.log" 2>&1 && pass "database imported" || { cat "$T/imp.log"; fail "import failed"; }
assert_grep 'media_items: 2, watch_history: 1' "$T/imp.log" "row counts shown"
assert_eq "$(stat -c %a "$CC_ROOT/data/catalogue_full.db")" 600 "database is mode 600"
"$IMPORT" db "$T/mac.db" >/dev/null 2>&1 && fail "overwrote existing database" || pass "refuses to overwrite the server's database"
rm "$CC_ROOT/data/catalogue_full.db"; touch "$STUB_STATE/app_running"
"$IMPORT" db "$T/mac.db" >/dev/null 2>&1 && fail "imported while app running" || pass "refuses while the app runs"
rm "$STUB_STATE/app_running"

mkdir -p "$T/mac-models/tiger" "$T/mac-models/vibe"; echo '{}' >"$T/mac-models/tiger/semantic_ids.json"
"$IMPORT" models "$T/mac-models" >/dev/null 2>&1 && pass "models imported" || fail "models import failed"
assert_file "$CC_ROOT/models/current/tiger/semantic_ids.json" "models/current points at a release"
assert_grep '"bundle_id": "manual-' "$CC_ROOT/models/current/manifest.json" "manifest created for /healthz"

mkdir -p "$T/films/Heat"; echo x >"$T/films/Heat/heat.mkv"
"$IMPORT" media "$T/films" Movies >/dev/null 2>&1 && pass "media imported" || fail "media import failed"
assert_file "$T/media/Movies/Heat/heat.mkv" "film moved into place"
assert_no_file "$T/films/Heat/heat.mkv" "and removed from the source"
"$IMPORT" media "$T/films" Music >/dev/null 2>&1 && fail "bad kind accepted" || pass "only Movies or Shows"
if fallocate -l 1100M "$T/big" 2>/dev/null; then
  sed -i 's/max_media_gb: 100/max_media_gb: 1/' "$CC_ROOT/config.server.yaml"
  mkdir "$T/bigdir"; mv "$T/big" "$T/bigdir/"
  "$IMPORT" media "$T/bigdir" Shows >"$T/cap.log" 2>&1 && fail "went past the cap" || pass "refuses to go past the media cap"
fi
finish
