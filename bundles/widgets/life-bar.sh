#!/usr/bin/env bash
# @widget-id: life-bar
# @name: Life Bar
# @description: Energy bar (click to heal)
# @category: fun
# @refresh: 10s
# @interactive: true
# @interaction-type: action
# @on-activate: ~/.kollab/status-widgets/.life-bar-action.sh
# @color: true

# Use ASCII chars for consistent single-width rendering
# Using block chars that work reliably in all terminals
FILLED="="
EMPTY="-"

STATE_DIR="$HOME/.kollab/state"
STATE_FILE="$STATE_DIR/life"

mkdir -p "$STATE_DIR"

# Read state
if [ -f "$STATE_FILE" ]; then
    hearts=$(cat "$STATE_FILE")
else
    hearts=3
fi

max_hearts=5

# Clamp value
if [ "$hearts" -gt "$max_hearts" ]; then
    hearts=$max_hearts
fi
if [ "$hearts" -lt 0 ]; then
    hearts=0
fi

# Render energy bar
filled=$((hearts))
empty=$((max_hearts - hearts))

output="["
for ((i=0; i<filled; i++)); do output="$output$FILLED"; done
for ((i=0; i<empty; i++)); do output="$output$EMPTY"; done
output="$output]"

printf "%s E\n" "$output"
