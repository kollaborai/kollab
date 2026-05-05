#!/usr/bin/env bash
# Move .bak, .deleted, and other temporary files to .trash directory
#
# Usage:
#   scripts/trash.sh              move temp files to .trash/
#   scripts/trash.sh --dry-run   show what would be moved
#   scripts/trash.sh -h          show this help

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TRASH_DIR="$PROJECT_ROOT/.trash"

dry_run=0

for arg in "$@"; do
    case "$arg" in
        --dry-run|-n)
            dry_run=1
            ;;
        --help|-h)
            echo "usage: $(basename "$0") [options]"
            echo ""
            echo "moves temp/backup files to .trash/ directory"
            echo ""
            echo "options:"
            echo "  --dry-run, -n   show what would be moved without moving"
            echo "  --help, -h      show this help"
            echo ""
            echo "patterns: *.bak *.deleted *.old *.backup *.orig *.temp *.tmp"
            exit 0
            ;;
        *)
            echo "unknown option: $arg" >&2
            echo "run with --help for usage" >&2
            exit 1
            ;;
    esac
done

# Create trash dir if needed (unless dry run)
[[ $dry_run -eq 0 ]] && mkdir -p "$TRASH_DIR"

# Counters
moved=0

# Patterns to clean
patterns=("*.bak" "*.deleted" "*.old" "*.backup" "*.orig" "*.temp" "*.tmp")

if [[ $dry_run -eq 1 ]]; then
    echo "dry run - nothing will be moved"
fi
echo "scanning: $PROJECT_ROOT"

for pattern in "${patterns[@]}"; do
    while IFS= read -r -d '' file; do
        # Skip if in .trash already
        [[ "$file" == "$TRASH_DIR"/* ]] && continue

        filename=$(basename "$file")
        if [[ $dry_run -eq 1 ]]; then
            echo "  would move: $filename"
        else
            echo "  moving: $filename"
            mv "$file" "$TRASH_DIR/"
        fi
        moved=$((moved + 1))
    done < <(find "$PROJECT_ROOT" -name "$pattern" -type f -print0 2>/dev/null || true)
done

echo ""
echo "summary:"
if [[ $dry_run -eq 1 ]]; then
    echo "  would move: $moved"
else
    echo "  moved:    $moved"
    echo "  trash:    $TRASH_DIR"
fi
