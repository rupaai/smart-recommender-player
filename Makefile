.PHONY: check lint test

check: lint test

lint:
	uv run ruff check .

# The suite is script-style (each file runs itself and exits non-zero on failure;
# see the "uv run python tests/..." lines in PROJECT_HANDOVER.md), so run each file.
# Importing server.py creates the media folders named in the config, and config.yaml
# points at /Users/..., so tests get a copy of it with those paths moved under a temp dir.
test:
	@set -e; T=$$(mktemp -d); \
	sed "s|/Users/[^[:space:]\"]*|$$T/media|g" config.yaml > $$T/config.yaml; \
	for f in tests/test_*.py; do echo "== $$f"; CONFIG_PATH=$$T/config.yaml uv run python $$f > $$T/out.log 2>&1 || { cat $$T/out.log; echo "FAILED: $$f"; exit 1; }; done; \
	echo "all test files passed"
