#!/usr/bin/env bash
# @widget-id: branch-status
# @name: Branch Status
# @description: Branch name with ahead/behind info
# @category: git
# @refresh: 30s
# @color: true

# Get current branch
branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)

if [ -z "$branch" ] || [ "$branch" = "HEAD" ]; then
    echo "?"
    exit 0
fi

# Try to get ahead/behind info
ahead=$(git rev-list --count "@{u}..HEAD" 2>/dev/null || echo 0)
behind=$(git rev-list --count "HEAD..@{u}" 2>/dev/null || echo 0)

if [ "$ahead" -eq 0 ] && [ "$behind" -eq 0 ]; then
    echo "$branch"
elif [ "$ahead" -gt 0 ] && [ "$behind" -gt 0 ]; then
    echo "$branch (+$ahead/-$behind)"
elif [ "$ahead" -gt 0 ]; then
    echo "$branch (+$ahead)"
elif [ "$behind" -gt 0 ]; then
    echo "$branch (-$behind)"
else
    echo "$branch"
fi
