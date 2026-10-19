#!/usr/bin/env bash
# The Train button. install.sh puts "Train CouchCinema" in ~/Applications, which runs this in
# Terminal: it downloads the server's database, trains, checks the scores and, if the new model
# is not worse, sends it to the app. Extra options are passed to nightly.sh.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CC_HOME="${CC_HOME:-$HOME/.couchcinema}"
env_value() { [ -f "$CC_HOME/env" ] && grep -m1 "^$1=" "$CC_HOME/env" | cut -d= -f2- | tr -d "\"'" || true; }
done_msg() {  # done_msg <message>: a Mac notification (if possible) and the line in Terminal
  printf '\n%s\n' "$1"
  command -v osascript >/dev/null 2>&1 && osascript -e "display notification \"$1\" with title \"CouchCinema\"" >/dev/null 2>&1
  if [ -t 0 ]; then read -r -p "Press Enter to close. " _; fi
}

server="${CC_SERVER:-$(env_value CC_SERVER)}"
[ -n "$server" ] || { done_msg "Not set up yet: run bash ops/mac/install.sh first."; exit 1; }
echo "Checking the server ($server) ..."
if ! curl -fsS --max-time 10 -o /dev/null "${server%/}/healthz"; then
  done_msg "Can't reach the server. Are you at home, or is Tailscale on? Nothing was changed."
  exit 1
fi

echo "Training. This takes about 10 to 20 minutes; you can leave this window open and carry on."
if bash "$REPO/ops/mac/nightly.sh" "$@"; then
  last="$(grep -E '7\. done: ' "${CC_LOG_DIR:-$HOME/Library/Logs/CouchCinema}/$(date +%F).log" 2>/dev/null | tail -1 | sed 's/.*7\. done: //')"
  done_msg "Done: ${last:-ok}"
else
  done_msg "Training stopped with an error; the app keeps its current model. The lines above say why."
  exit 1
fi
