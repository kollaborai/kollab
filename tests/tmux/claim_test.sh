#!/bin/bash
# Helper script for agents to claim/complete tests

CHECKLIST="$(dirname "$0")/CONVERSION_CHECKLIST.md"

show_usage() {
    echo "Usage:"
    echo "  $0 claim <test-name> <agent-name>   - Claim a test"
    echo "  $0 done <test-name> <agent-name>    - Mark test as done"
    echo "  $0 list                              - Show available tests"
    echo ""
    echo "Examples:"
    echo "  $0 claim test_agent_create.sh agent1"
    echo "  $0 done test_agent_create.sh agent1"
    echo "  $0 list"
}

list_tests() {
    echo "Available tests (TODO):"
    grep "^| TODO" "$CHECKLIST" | awk -F'|' '{gsub(/^[ \t]+|[ \t]+$/, "", $4); gsub(/^[ \t]+|[ \t]+$/, "", $6); print "  [" $6 "] " $4}'
    echo ""
    echo "In progress:"
    grep "^| IN_PROGRESS" "$CHECKLIST" | awk -F'|' '{gsub(/^[ \t]+|[ \t]+$/, "", $3); gsub(/^[ \t]+|[ \t]+$/, "", $4); print "  " $3 " -> " $4}'
    echo ""
    todo_count=$(grep "^| TODO" "$CHECKLIST" | wc -l | tr -d ' ')
    prog_count=$(grep "^| IN_PROGRESS" "$CHECKLIST" | wc -l | tr -d ' ')
    done_count=$(grep "^| DONE" "$CHECKLIST" | wc -l | tr -d ' ')
    echo "Summary:"
    echo "  TODO: $todo_count"
    echo "  IN_PROGRESS: $prog_count"
    echo "  DONE: $done_count"
}

claim_test() {
    local test_name="$1"
    local agent="$2"

    if [ -z "$test_name" ] || [ -z "$agent" ]; then
        echo "Error: Missing test name or agent name"
        show_usage
        exit 1
    fi

    # Check if test exists and is TODO
    if ! grep -q "^| TODO.*$test_name" "$CHECKLIST"; then
        echo "Error: Test '$test_name' not found or not available (must be TODO)"
        exit 1
    fi

    # Update status to IN_PROGRESS
    sed -i.bak "s/^| TODO | - | $test_name/| IN_PROGRESS | $agent | $test_name/" "$CHECKLIST"
    rm "${CHECKLIST}.bak"

    echo "✓ Claimed: $test_name by $agent"
    echo "  Next: Convert to JSON in tests/tmux/specs/"
}

complete_test() {
    local test_name="$1"
    local agent="$2"

    if [ -z "$test_name" ] || [ -z "$agent" ]; then
        echo "Error: Missing test name or agent name"
        show_usage
        exit 1
    fi

    # Check if test is IN_PROGRESS by this agent
    if ! grep -q "^| IN_PROGRESS | $agent | $test_name" "$CHECKLIST"; then
        echo "Error: Test '$test_name' not in progress by $agent"
        exit 1
    fi

    # Update status to DONE
    sed -i.bak "s/^| IN_PROGRESS | $agent | $test_name/| DONE | $agent | $test_name/" "$CHECKLIST"
    rm "${CHECKLIST}.bak"

    echo "✓ Completed: $test_name by $agent"
}

case "${1:-}" in
    claim)
        claim_test "$2" "$3"
        ;;
    done)
        complete_test "$2" "$3"
        ;;
    list)
        list_tests
        ;;
    *)
        show_usage
        exit 1
        ;;
esac
