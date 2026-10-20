.PHONY: check lint shellcheck test test-mac

check: lint shellcheck test test-mac

lint:
	uv run ruff check .

# The suite is script-style (each file runs itself and exits non-zero on failure;
# see PROJECT_HANDOVER.md section 5), so run each file.
# Importing server.py creates the media folders named in the config, and config.yaml
# points at /Users/..., so tests get a copy of it with those paths moved under a temp dir.
test:
	@set -e; T=$$(mktemp -d); \
	sed "s|/Users/[^[:space:]\"]*|$$T/media|g" config.yaml > $$T/config.yaml; \
	for f in tests/test_*.py; do echo "== $$f"; CONFIG_PATH=$$T/config.yaml uv run python $$f > $$T/out.log 2>&1 || { cat $$T/out.log; echo "FAILED: $$f"; exit 1; }; done; \
	echo "all test files passed"

# The Mac scripts (ops/mac, tests/mac). CI's runner has shellcheck; locally it is optional.
shellcheck:
	@if command -v shellcheck >/dev/null 2>&1; then find ops/mac tests/mac -name '*.sh' | xargs shellcheck -x -P SCRIPTDIR -e SC2015; else echo "shellcheck not installed; skipped (CI runs it)"; fi

# The Mac scripts run against stand-ins for uv, launchctl and friends, in a temp dir; nothing touches the machine.
test-mac:
	@set -e; for f in tests/mac/test_*.sh; do echo "== $$f"; bash $$f; done
