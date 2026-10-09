#!/bin/sh
# Boot the app with a throwaway HOME and workspace that hold one made-up saved
# chat, so /resume has a row to list (specs/resume_escape_redraws_input.json).
# The chat is written by the app's own logger, so its format always matches.
set -e
repo=$(cd "$(dirname "$0")/../../.." && pwd)
# The repo's venv when there is one; otherwise resolve `python` before HOME
# moves, since a pyenv shim looks under $HOME.
py=$repo/.venv/bin/python
[ -x "$py" ] || py=$(python -c 'import sys; print(sys.executable)')
home=$(mktemp -d)
mkdir -p "$home/work"
cd "$home/work"
HOME=$home "$py" - <<'EOF'
import asyncio

from kollabor_ai.conversation_logger import KollaborConversationLogger
from kollabor_config.config_utils import get_conversations_dir


async def seed():
    log = KollaborConversationLogger(get_conversations_dir())
    log.reset_session("2601010000-resume-fixture")
    parent = await log.log_user_message("what is two plus two?")
    parent = await log.log_assistant_message("four", parent_uuid=parent)
    parent = await log.log_user_message("and three plus three?", parent_uuid=parent)
    await log.log_assistant_message("six", parent_uuid=parent)


asyncio.run(seed())
EOF
HOME=$home exec "$py" "$repo/main.py" --no-daemon
