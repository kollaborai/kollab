#!/usr/bin/env zsh
set -e
set -u
set -o pipefail

SESSION="kollab_readme_demo"
KOLLAB_REPO="/Users/malmazan/dev/kollab"
DEMO_REPO="/Users/malmazan/dev/kdex"
CAST_PATH="$KOLLAB_REPO/docs/assets/demo.cast"
GIF_PATH="$KOLLAB_REPO/docs/assets/kollab-demo.gif"
DEMO_MODEL="deepseek/deepseek-v3.2"

tmux kill-session -t "$SESSION" 2>/dev/null || true

# Clear stale demo/runtime processes from prior attempts.
pkill -f "$KOLLAB_REPO/.venv/bin/kollab" 2>/dev/null || true

rm -f "$CAST_PATH" "$GIF_PATH"
mkdir -p "$(dirname "$GIF_PATH")"

cd "$DEMO_REPO"

tmux new-session -d -s "$SESSION" -x 110 -y 34
sleep 2

tmux send-keys -t "$SESSION" "cd '$DEMO_REPO'" C-m
sleep 0.4

tmux send-keys -t "$SESSION" "export PATH=\"$KOLLAB_REPO/.venv/bin:\$PATH\"" C-m
sleep 0.4

tmux send-keys -t "$SESSION" "unset ANTHROPIC_API_KEY OPENAI_API_KEY GEMINI_API_KEY AZURE_OPENAI_API_KEY XAI_API_KEY" C-m
sleep 0.4

tmux send-keys -t "$SESSION" "export KOLLAB_OPENROUTER_AUTO_MODEL=\"$DEMO_MODEL\"" C-m
sleep 0.4

tmux send-keys -t "$SESSION" "hash -r 2>/dev/null || rehash 2>/dev/null || true" C-m
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

tmux send-keys -t "$SESSION" "export PATH=\"$KOLLAB_REPO/.venv/bin:\$PATH\"" C-m
sleep 0.4

tmux send-keys -t "$SESSION" "unset ANTHROPIC_API_KEY OPENAI_API_KEY GEMINI_API_KEY AZURE_OPENAI_API_KEY XAI_API_KEY" C-m
sleep 0.4

tmux send-keys -t "$SESSION" "export KOLLAB_OPENROUTER_AUTO_MODEL=\"$DEMO_MODEL\"" C-m
sleep 0.4

tmux send-keys -t "$SESSION" "hash -r 2>/dev/null || rehash 2>/dev/null || true" C-m
sleep 0.4

tmux send-keys -t "$SESSION" "which kollab" C-m
sleep 0.4

tmux send-keys -t "$SESSION" "kollab " C-m
sleep 4

DEMO_TEXT="Use the terminal tool first. Run pwd, ls -la, and read README.md and pyproject.toml. Then summarize kdex in five concise bullets and list the first commands a new user should try."

for (( i = 1; i <= ${#DEMO_TEXT}; i++ )); do
  tmux send-keys -t "$SESSION" -l -- "${DEMO_TEXT[i]}"
  sleep 0.05
done

sleep 0.5
tmux send-keys -t "$SESSION" C-m

echo "Recording. Attach with:"
echo "tmux attach -t $SESSION"
echo
echo "Press Enter here when the answer looks good, then this will render the GIF."
read -r

tmux send-keys -t "$SESSION" C-c
sleep 1

tmux send-keys -t "$SESSION" "exit" C-m
sleep 1

tmux kill-session -t "$SESSION" 2>/dev/null || true

agg \
  --theme github-dark \
  --cols 110 \
  --rows 34 \
  --font-size 14 \
  --line-height 1.25 \
  --idle-time-limit 1 \
  --last-frame-duration 2 \
  "$CAST_PATH" \
  "$GIF_PATH"

echo "Rendered: $GIF_PATH"
