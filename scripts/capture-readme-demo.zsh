#!/usr/bin/env zsh
set -e
set -u
set -o pipefail

SESSION="kollab_readme_demo"
KOLLAB_REPO="/Users/malmazan/dev/kollab"
DEMO_REPO="/Users/malmazan/dev/kdex"
CAST_PATH="$KOLLAB_REPO/docs/assets/demo.cast"
GIF_PATH="$KOLLAB_REPO/docs/assets/kollab-demo.gif"
RENDER_CAST_PATH="/tmp/kollab-demo-render.cast"
DEMO_MODEL="deepseek/deepseek-v3.2"

tmux kill-session -t "$SESSION" 2>/dev/null || true

# Clear stale demo/runtime processes from prior attempts.
pkill -f "$KOLLAB_REPO/.venv/bin/kollab" 2>/dev/null || true

rm -f "$CAST_PATH" "$GIF_PATH" "$RENDER_CAST_PATH"
mkdir -p "$(dirname "$GIF_PATH")"

cd "$DEMO_REPO"

tmux new-session -d -s "$SESSION" -x 110 -y 34
sleep 2

tmux send-keys -t "$SESSION" "cd '$DEMO_REPO'" C-m
sleep 0.4

tmux send-keys -t "$SESSION" "unset ANTHROPIC_API_KEY OPENAI_API_KEY GEMINI_API_KEY AZURE_OPENAI_API_KEY XAI_API_KEY" C-m
sleep 0.4

tmux send-keys -t "$SESSION" "export KOLLAB_OPENROUTER_AUTO_MODEL=\"$DEMO_MODEL\"" C-m
sleep 0.4

tmux send-keys -t "$SESSION" "which kollab" C-m
sleep 0.4

tmux send-keys -t "$SESSION" "command -v asciinema" C-m
sleep 0.4

tmux send-keys -t "$SESSION" "python - <<'PY'" C-m
tmux send-keys -t "$SESSION" "from kollabor_ai.profile_manager import ProfileManager" C-m
tmux send-keys -t "$SESSION" "pm = ProfileManager()" C-m
tmux send-keys -t "$SESSION" "p = pm.get_active_profile()" C-m
tmux send-keys -t "$SESSION" "print(f'profile: {pm.active_profile_name} provider: {p.get_provider()} model: {p.get_model()}')" C-m
tmux send-keys -t "$SESSION" "PY" C-m
sleep 0.8

tmux send-keys -t "$SESSION" "asciinema rec --overwrite --cols 110 --rows 34 -i 1 -q '$CAST_PATH'" C-m
sleep 2

tmux send-keys -t "$SESSION" "unset ANTHROPIC_API_KEY OPENAI_API_KEY GEMINI_API_KEY AZURE_OPENAI_API_KEY XAI_API_KEY" C-m
sleep 0.4

tmux send-keys -t "$SESSION" "export KOLLAB_OPENROUTER_AUTO_MODEL=\"$DEMO_MODEL\"" C-m
sleep 0.4

tmux send-keys -t "$SESSION" "which kollab" C-m
sleep 0.4

tmux send-keys -t "$SESSION" "kollab --hub stop all" C-m
sleep 1

tmux send-keys -t "$SESSION" "kollab --agent coder --as lapis --detached" C-m
sleep 2

tmux send-keys -t "$SESSION" "kollab --agent technical-writer --as sapphire --detached" C-m
sleep 2

tmux send-keys -t "$SESSION" "kollab --hub status" C-m
sleep 1

tmux send-keys -t "$SESSION" "kollab --permissions trust --stay --as koordinator" C-m
sleep 4

PROMPT="Coordinate with lapis and sapphire to understand this kdex project and prepare a short README-ready summary. Ask lapis to inspect the project structure, ask sapphire to shape the explanation for new users, then combine their input into a concise plan."

for (( i = 1; i <= ${#PROMPT}; i++ )); do
  tmux send-keys -t "$SESSION" -l -- "${PROMPT[i]}"
  sleep 0.05
done

sleep 0.5
tmux send-keys -t "$SESSION" C-m

echo "Recording. Attach with:"
echo "tmux attach -t $SESSION"
echo
echo "Press Enter here when the answer looks good, then this will render the GIF."
read -r

CUTOFF_EPOCH="$(python - <<'PY'
import time
print(f"{time.time():.6f}")
PY
)"

tmux send-keys -t "$SESSION" C-c
sleep 1

tmux send-keys -t "$SESSION" "exit" C-m
sleep 1

tmux kill-session -t "$SESSION" 2>/dev/null || true

(cd "$DEMO_REPO" && kollab --hub stop all >/dev/null 2>&1) || true

python - "$CAST_PATH" "$RENDER_CAST_PATH" "$CUTOFF_EPOCH" <<'PY'
import json
import sys

source, target, cutoff_epoch = sys.argv[1], sys.argv[2], float(sys.argv[3])

with open(source, "r", encoding="utf-8") as src:
    header = json.loads(src.readline())
    cutoff = max(0.0, cutoff_epoch - float(header.get("timestamp", 0)) - 0.75)
    with open(target, "w", encoding="utf-8") as dst:
        dst.write(json.dumps(header, separators=(",", ":")) + "\n")
        for line in src:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if float(event[0]) > cutoff:
                break
            dst.write(json.dumps(event, separators=(",", ":")) + "\n")
PY

agg \
  -q \
  --theme github-dark \
  --cols 110 \
  --rows 34 \
  --font-size 14 \
  --line-height 1.25 \
  --idle-time-limit 0.4 \
  --last-frame-duration 2 \
  --speed 12 \
  --fps-cap 8 \
  "$RENDER_CAST_PATH" \
  "$GIF_PATH"

rm -f "$RENDER_CAST_PATH"

echo "Rendered: $GIF_PATH"
