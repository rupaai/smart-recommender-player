.PHONY: check lint test

check: lint test

lint:
	uv run ruff check .

# The suite is script-style (each file runs itself and exits non-zero on failure;
# see the "uv run python tests/..." lines in PROJECT_HANDOVER.md), so run each file.
test:
	@set -e; for f in tests/test_*.py; do echo "== $$f"; uv run python $$f > /tmp/cc_test.log 2>&1 || { cat /tmp/cc_test.log; echo "FAILED: $$f"; exit 1; }; done; echo "all test files passed"
