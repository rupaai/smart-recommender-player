#!/usr/bin/env bash
# Shared sandbox for the deploy script tests. Source it; do not run it.
#
# The deploy scripts change users, Docker, systemd and cron, so these tests never run them
# for real. new_sandbox builds a temp directory with stand-ins ("stubs") for every system
# command on the script's PATH. The stubs log each call to $STUB_STATE/calls.log and keep
# just enough state (does the user exist, is the daemon up) to make a second run behave
# like a second run. What this proves: the order of steps, the arguments, idempotency and
# the safety rules. What it can't prove: that Ubuntu, rootless Docker or Jellyfin behave.

# shellcheck disable=SC2034  # used by the test files that source this
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FAILS=0

fail() { printf '  FAIL: %s\n' "$*"; FAILS=$((FAILS + 1)); }
pass() { [ -z "${VERBOSE:-}" ] || printf '  ok: %s\n' "$*"; }

assert_eq() { [ "$1" = "$2" ] && pass "$3" || fail "$3 (expected '$2', got '$1')"; }
assert_file() { [ -e "$1" ] && pass "${2:-$1 exists}" || fail "${2:-$1 missing}"; }
assert_no_file() { [ ! -e "$1" ] && pass "${2:-$1 absent}" || fail "${2:-$1 should not exist}"; }
assert_grep() { grep -qE -- "$1" "$2" && pass "${3:-$2 has $1}" || fail "${3:-$2 should match $1}"; }
assert_no_grep() { ! grep -qE -- "$1" "$2" && pass "${3:-$2 has no $1}" || fail "${3:-$2 should not match $1}"; }
assert_calls() { local n; n=$(grep -cE -- "$1" "$STUB_STATE/calls.log" || true); assert_eq "$n" "$2" "$3"; }

new_sandbox() {
  T="$(mktemp -d)"
  STUB_STATE="$T/state"; STUBS="$T/stubs"
  mkdir -p "$STUB_STATE" "$STUBS" "$T/home" "$T/run" "$T/bin-src"
  : >"$STUB_STATE/calls.log"
  export T STUB_STATE STUBS

  cat >"$STUBS/stub" <<'STUB'
#!/usr/bin/env bash
name="$(basename "$0")"
S="$STUB_STATE"
if [ -n "${DOCKER_HOST:-}" ] && [ "$name" = docker ]; then
  echo "$name [DOCKER_HOST=$DOCKER_HOST] $*" >>"$S/calls.log"
else
  echo "$name $*" >>"$S/calls.log"
fi
case "$name" in
  id)
    case "$*" in
      "-u") exec /usr/bin/id -u ;;
      "-u deploy") [ -f "$S/user" ] && echo 1001 || exit 1 ;;
      "deploy") [ -f "$S/user" ] || exit 1 ;;
      "-nG deploy") echo "${STUB_GROUPS:-deploy}" ;;
      *) exec /usr/bin/id "$@" ;;
    esac ;;
  getent)
    case "$*" in
      "passwd deploy") [ -f "$S/user" ] && echo "deploy:x:1001:1001::$T/home/deploy:/bin/bash" || exit 2 ;;
      "group render") echo "render:x:110:" ;;
    esac ;;
  useradd) touch "$S/user"; mkdir -p "$T/home/deploy" ;;
  usermod)
    case "$1" in
      --add-subuids) echo "deploy:${2%%-*}:65536" >>"$SUBUID_FILE" ;;
      --add-subgids) echo "deploy:${2%%-*}:65536" >>"$SUBGID_FILE" ;;
    esac ;;
  loginctl)
    mkdir -p "$RUNTIME_ROOT/1001"
    [ -S "$RUNTIME_ROOT/1001/bus" ] || python3 -c "import socket,sys; socket.socket(socket.AF_UNIX).bind(sys.argv[1])" "$RUNTIME_ROOT/1001/bus" ;;
  runuser) shift 3; exec "$@" ;;   # runuser -u deploy -- cmd...
  docker)
    case "$*" in
      "compose version") echo "Docker Compose version v2.test" ;;
      *"-H unix:///var/run/docker.sock"*) exit 1 ;;
      "info"*) [ -f "$S/daemon_up" ] || exit 1; case "$*" in *--format*) echo "name=seccomp name=rootless" ;; esac ;;
      "ps -a --format"*) echo "couchcinema-jellyfin-1" ;;
      "compose "*)
        case "$*" in
          *" ps -q"*) [ -f "$S/app_running" ] && echo abc123def || true ;;
          *" up "*) echo "${IMAGE_TAG:-}" >"$S/last_up_tag"; touch "$S/stack_up" ;;
        esac ;;
    esac ;;
  dockerd-rootless-setuptool.sh) touch "$S/daemon_up" ;;
  systemctl)
    case "$*" in
      "cat "*) [ -f "$S/unit_installed" ] || exit 1 ;;
      "is-active "*) [ -f "$S/unit_started" ] || exit 3 ;;
    esac ;;
  curl)
    out=""; url=""
    while [ $# -gt 0 ]; do
      case "$1" in -o) out="$2"; shift ;; -*) ;; *) url="$1" ;; esac
      shift
    done
    case "$url" in
      */actions/runner/releases/latest)
        printf '{"tag_name":"v9.9.9","body":"<!-- BEGIN SHA linux-x64 -->%s<!-- END SHA linux-x64 -->"}' "$(cat "$S/runner.sha")" ;;
      */releases/download/*) cp "$S/runner.tgz" "$out" ;;
      *) [ -z "$out" ] || : >"$out" ;;
    esac ;;
  rsync)
    remove=0; args=()
    for a in "$@"; do
      case "$a" in --remove-source-files) remove=1 ;; -a | --chown=*) ;; *) args+=("$a") ;; esac
    done
    src="${args[0]}"; dst="${args[1]}"; mkdir -p "$dst"; cp -a "$src." "$dst"
    [ "$remove" = 0 ] || find "$src" -type f -delete ;;
  ss) exit 1 ;;
  *) : ;;   # setfacl, udevadm, apparmor_parser, apt-get, passwd, chown, tailscale, newuidmap, ...
esac
STUB
  chmod +x "$STUBS/stub"
  local c
  for c in id getent useradd usermod loginctl runuser docker dockerd-rootless-setuptool.sh systemctl curl \
    rsync ss setfacl udevadm apparmor_parser apt-get passwd chown tailscale newuidmap slirp4netns cron dpkg; do
    ln -sf stub "$STUBS/$c"
  done

  # A fake GitHub runner download with a matching checksum.
  mkdir -p "$T/runner-src"
  cat >"$T/runner-src/config.sh" <<'EOF'
#!/usr/bin/env bash
echo "config.sh $*" >>"$STUB_STATE/calls.log"
touch .runner
EOF
  cat >"$T/runner-src/svc.sh" <<'EOF'
#!/usr/bin/env bash
echo "svc.sh $*" >>"$STUB_STATE/calls.log"
case "$1" in install) touch "$STUB_STATE/unit_installed" ;; start) touch "$STUB_STATE/unit_started" ;; esac
EOF
  chmod +x "$T/runner-src/"*.sh
  tar -czf "$STUB_STATE/runner.tgz" -C "$T/runner-src" config.sh svc.sh
  sha256sum "$STUB_STATE/runner.tgz" | cut -d' ' -f1 >"$STUB_STATE/runner.sha"

  export PATH="$STUBS:$PATH"
  export CC_ROOT="$T/srv" RUNTIME_ROOT="$T/run" SUBUID_FILE="$T/subuid" SUBGID_FILE="$T/subgid"
  export CRON_FILE="$T/cron.d-couchcinema" UDEV_RULE_FILE="$T/udev.rules" APPARMOR_FILE="$T/apparmor.rootlesskit"
  export USERNS_RESTRICT_FILE="$T/userns_restrict" RENDER_NODE="$T/renderD128" WAIT_SECONDS=2
  echo 1 >"$USERNS_RESTRICT_FILE"
  : >"$RENDER_NODE"
  unset DOCKER_HOST MEDIA_ROOT TMDB_API_KEY PROWLARR_API_KEY HEALTHCHECKS_URL RUNNER_TOKEN STUB_GROUPS
}

finish() {
  rm -rf "$T"
  if [ "$FAILS" -gt 0 ]; then
    echo "$FAILS check(s) failed"
    exit 1
  fi
}
