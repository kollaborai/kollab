#!/usr/bin/env bash
# teardown.sh: stop ONLY what story5/proof.sh started: the s5-mac, s5-mac2 and s5-srv
# tmux sessions and the processes run from the m1 venvs (~/kollab-m1/venv on each
# host). Delegates to ../m1/teardown.sh with the s5 session names; leaves every other
# session, the uv tool install, workspaces, evidence and network state alone.
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
tmux kill-session -t "${S5_MAC2_SESSION:-s5-mac2}" 2>/dev/null && echo "killed tmux session ${S5_MAC2_SESSION:-s5-mac2}" || echo "no tmux session ${S5_MAC2_SESSION:-s5-mac2}"
export M1_MAC_SESSION=${M1_MAC_SESSION:-s5-mac} M1_SRV_SESSION=${M1_SRV_SESSION:-s5-srv}
exec bash "$here/../m1/teardown.sh"
