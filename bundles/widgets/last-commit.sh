#!/usr/bin/env bash
# @widget-id: last-commit
# @name: Last Commit
# @description: Most recent commit message
# @category: git
# @refresh: 30s
# @color: true

# Get last commit message
commit=$(git log -1 --format="%s" 2>/dev/null)

if [ -z "$commit" ]; then
    echo "commit: ?"
else
    # Truncate if too long
    if [ ${#commit} -gt 35 ]; then
        commit="${commit:0:32}..."
    fi
    echo "commit: $commit"
fi
