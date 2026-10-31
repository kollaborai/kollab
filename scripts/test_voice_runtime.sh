#!/usr/bin/env bash
# Run the voice-package tests under a voice runtime python WITHOUT installing
# pytest into the runtime venv (issue #499).
#
# The runtime venvs that kollabor_voice.bootstrap builds ship only the pinned
# runtime dependencies; pytest is deliberately absent. This script materializes
# a throwaway pytest install in a temp dir and puts it on PYTHONPATH instead.
#
# Usage:
#   scripts/test_voice_runtime.sh [runtime-python] [pytest args...]
# With no argument the newest interpreter under
# ~/.kollab/voice/runtimes/*/bin/python is used.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
py="${1:-}"
if [ $# -gt 0 ]; then shift; fi

if [ -z "$py" ]; then
    py="$(ls -t "$HOME"/.kollab/voice/runtimes/*/bin/python 2>/dev/null | head -1 || true)"
fi
if [ -z "$py" ] || [ ! -x "$py" ]; then
    echo "error: no voice runtime python found; pass one as argument 1" >&2
    echo "       e.g. scripts/test_voice_runtime.sh \\" >&2
    echo "         ~/.kollab/voice/runtimes/<hash>/bin/python" >&2
    exit 2
fi

if ! command -v uv >/dev/null 2>&1; then
    echo "error: uv is required (https://docs.astral.sh/uv/)" >&2
    exit 2
fi

escape="$(mktemp -d "${TMPDIR:-/tmp}/voice-pytest-escape.XXXXXX")"
trap 'rm -rf "$escape"' EXIT

uv pip install --target "$escape" --quiet pytest pytest-asyncio

cd "$root"
PYTHONPATH="$escape:$root/packages/kollabor-voice/src" \
    "$py" -m pytest packages/kollabor-voice/tests "$@"
