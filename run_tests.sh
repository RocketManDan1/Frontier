#!/usr/bin/env bash
# ───────────────────────────────────────────────────────────────────────────
# run_tests.sh — Run the Frontier: Sol 2000 test suite
#
# Usage:
#   ./run_tests.sh                    # Run all tests
#   ./run_tests.sh -k smoke           # Run only smoke tests
#   ./run_tests.sh -k catalog         # Run only catalog tests
#   ./run_tests.sh -k migration       # Run only migration tests
#   ./run_tests.sh -k "game_logic"    # Run only game logic tests
#   ./run_tests.sh -x                 # Stop on first failure
#   ./run_tests.sh -v                 # Verbose output
#   ./run_tests.sh --tb=short         # Short tracebacks
#
# Run inside Docker:
#   docker compose exec frontier-sol-2000 bash -c "./run_tests.sh"
#
# Or directly when developing locally:
#   pip install pytest httpx && ./run_tests.sh
# ───────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Ensure DEV_SKIP_AUTH so tests bypass authentication
export DEV_SKIP_AUTH=1
# Use in-memory or temp DB so tests don't touch production data
export DB_DIR="${TEST_DB_DIR:-/tmp/frontier_test_data}"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${YELLOW}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${YELLOW}║  Frontier: Sol 2000 — Test Suite                ║${NC}"
echo -e "${YELLOW}╚══════════════════════════════════════════════════╝${NC}"
echo ""

# Check dependencies
if ! python -c "import pytest" 2>/dev/null; then
    echo -e "${RED}pytest not installed. Installing test dependencies...${NC}"
    pip install pytest httpx 2>/dev/null || pip install --user pytest httpx
fi

# Run pytest with sensible defaults; pass through any extra args
python -m pytest tests/ \
    --tb=short \
    -q \
    --no-header \
    "$@"

EXIT_CODE=$?

echo ""
if [ $EXIT_CODE -eq 0 ]; then
    echo -e "${GREEN}✓ All tests passed${NC}"
else
    echo -e "${RED}✗ Some tests failed (exit code: $EXIT_CODE)${NC}"
fi

exit $EXIT_CODE
