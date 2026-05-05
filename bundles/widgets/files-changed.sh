#!/usr/bin/env bash
# @widget-id: files-changed
# @name: Files Changed
# @description: Modified/added/deleted file counts
# @category: git
# @refresh: 10s
# @hooks: post_command
# @color: true

# Get git status
status=$(git status --porcelain 2>/dev/null)

if [ -z "$status" ]; then
    echo "clean"
else
    # Count changes
    modified=$(echo "$status" | grep -c "^M" || echo 0)
    added=$(echo "$status" | grep -c "^A" || echo 0)
    deleted=$(echo "$status" | grep -c "^D" || echo 0)
    total=$(echo "$status" | wc -l | tr -d ' ')

    if [ "$total" -gt 0 ]; then
        parts=""
        [ "$modified" -gt 0 ] && parts="${parts}M:${modified} "
        [ "$added" -gt 0 ] && parts="${parts}A:${added} "
        [ "$deleted" -gt 0 ] && parts="${parts}D:${deleted} "
        echo "$parts" | sed 's/ *$//'
    else
        echo "clean"
    fi
fi
