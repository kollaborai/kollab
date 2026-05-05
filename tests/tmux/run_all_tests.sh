#!/bin/bash
# ============================================================================
# Kollab CLI Test Suite Runner
# ============================================================================
# Runs all JSON test specs in tests/tmux/specs/
#
# Usage:
#   ./run_all_tests.sh
#   SHOW_CAPTURES=true ./run_all_tests.sh
# ============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SPECS_DIR="$SCRIPT_DIR/specs"
TEST_RUNNER="$SCRIPT_DIR/lib/test_runner.sh"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Counters
TOTAL_SPECS=0
PASSED_SPECS=0
FAILED_SPECS=0

echo "=============================================="
echo "Kollab CLI Test Suite"
echo "=============================================="
echo "Specs directory: $SPECS_DIR"
echo "Test runner: $TEST_RUNNER"
echo ""

# Find all JSON specs
SPECS=$(find "$SPECS_DIR" -name "*.json" -type f | sort)

if [ -z "$SPECS" ]; then
    echo "No test specs found in $SPECS_DIR"
    exit 1
fi

# Count specs
TOTAL_SPECS=$(echo "$SPECS" | wc -l | tr -d ' ')

echo "Found $TOTAL_SPECS test specs"
echo ""

# Run each spec
for SPEC in $SPECS; do
    SPEC_NAME=$(basename "$SPEC")
    echo "=============================================="
    echo "Running: $SPEC_NAME"
    echo "=============================================="

    if "$TEST_RUNNER" "$SPEC"; then
        echo -e "${GREEN}✓ PASS${NC}: $SPEC_NAME"
        PASSED_SPECS=$((PASSED_SPECS + 1))
    else
        echo -e "${RED}✗ FAIL${NC}: $SPEC_NAME"
        FAILED_SPECS=$((FAILED_SPECS + 1))
    fi

    echo ""
done

# Summary
echo "=============================================="
echo "Test Suite Summary"
echo "=============================================="
echo "Total specs:  $TOTAL_SPECS"
echo -e "Passed:       ${GREEN}$PASSED_SPECS${NC}"
echo -e "Failed:       ${RED}$FAILED_SPECS${NC}"
echo ""

if [ "$FAILED_SPECS" -eq 0 ]; then
    echo -e "${GREEN}All tests passed!${NC}"
    exit 0
else
    echo -e "${RED}Some tests failed${NC}"
    exit 1
fi
