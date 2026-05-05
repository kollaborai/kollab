#!/usr/bin/env bash
# @widget-id: pomodoro
# @name: Pomodoro
# @description: Pomodoro timer (click to start/pause)
# @category: productivity
# @refresh: 1s
# @interactive: true
# @interaction-type: toggle
# @on-activate: ~/.kollab/status-widgets/.pomodoro-toggle.sh
# @color: true

STATE_DIR="$HOME/.kollab/state"
STATE_FILE="$STATE_DIR/pomodoro"

mkdir -p "$STATE_DIR"

# Read state
if [ -f "$STATE_FILE" ]; then
    source "$STATE_FILE"
else
    # Initialize
    status="idle"
    start_time=0
    duration=1500  # 25 minutes in seconds
    completed=0
fi

if [ "$status" = "idle" ]; then
    echo "pomodoro ($completed done)"
else
    now=$(date +%s)
    elapsed=$((now - start_time))
    remaining=$((duration - elapsed))

    if [ $remaining -le 0 ]; then
        # Timer done!
        if [ "$status" = "work" ]; then
            ((completed++))
        fi
        # Reset to idle
        {
            echo "status=idle"
            echo "start_time=0"
            echo "duration=1500"
            echo "completed=$completed"
        } > "$STATE_FILE"
        echo "DONE! ($completed)"
    else
        mins=$((remaining / 60))
        secs=$((remaining % 60))
        case "$status" in
            work) echo "[W] $(printf "%02d:%02d" $mins $secs) ($completed)" ;;
            break) echo "[B] $(printf "%02d:%02d" $mins $secs)" ;;
            pause) echo "[P] $(printf "%02d:%02d" $mins $secs)" ;;
        esac
    fi
fi
