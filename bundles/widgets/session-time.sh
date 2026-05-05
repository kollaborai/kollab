#!/usr/bin/env bash
# @widget-id: session-time
# @name: Session Time
# @description: Current session duration
# @category: time
# @refresh: 30s
# @color: true

# Try to get session start from Kollabor environment
if [ -n "$KOLLAB_SESSION_START" ]; then
    start=$KOLLAB_SESSION_START
else
    # Fallback to using a marker file
    marker="$HOME/.kollab/session-start"
    if [ ! -f "$marker" ]; then
        date +%s > "$marker"
    fi
    start=$(cat "$marker")
fi

now=$(date +%s)
elapsed=$((now - start))

hours=$((elapsed / 3600))
minutes=$(((elapsed % 3600) / 60))
seconds=$((elapsed % 60))

if [ $hours -gt 0 ]; then
    printf "session: %d:%02d:%02d\n" $hours $minutes $seconds
else
    printf "session: %d:%02d\n" $minutes $seconds
fi
