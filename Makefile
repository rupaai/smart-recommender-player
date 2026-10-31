.PHONY: check lint shellcheck test test-mac

check: lint shellcheck test test-mac

lint:
	uv run ruff check .

# The suite is script-style (each file runs itself and exits non-zero on failure;
# see PROJECT_HANDOVER.md section 5), so run each file.
# Importing server.py creates the media folders named in the settings, so tests get their own
# settings file (LOCAL_CONFIG_PATH, laid over config.yaml) with every folder under a temp dir.
# A config.local.yaml of yours is not used by the tests.
test:
	@set -e; T=$$(mktemp -d); \
	printf 'media_folders: [%s, %s]\njellyfin_library: {movie_dir: %s, tv_dir: %s}\ntorrent: {download_dir: %s}\n' \
	  "$$T/media/Movies" "$$T/media/Shows" "$$T/media/Movies" "$$T/media/Shows" "$$T/media/downloads" > $$T/config.local.yaml; \
	for f in tests/test_*.py; do echo "== $$f"; LOCAL_CONFIG_PATH=$$T/config.local.yaml uv run python $$f > $$T/out.log 2>&1 || { cat $$T/out.log; echo "FAILED: $$f"; exit 1; }; done; \
	echo "all test files passed"

# The Mac scripts (ops/mac, tests/mac). CI's runner has shellcheck; locally it is optional.
shellcheck:
	@if command -v shellcheck >/dev/null 2>&1; then find ops/mac tests/mac -name '*.sh' | xargs shellcheck -x -P SCRIPTDIR -e SC2015; else echo "shellcheck not installed; skipped (CI runs it)"; fi

# The Mac scripts run against stand-ins for uv, launchctl and friends, in a temp dir; nothing touches the machine.
test-mac:
	@set -e; for f in tests/mac/test_*.sh; do echo "== $$f"; bash $$f; done
