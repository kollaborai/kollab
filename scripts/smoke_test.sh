#!/usr/bin/env bash
#
# Smoke test for kollab PyPI release.
# Runs in a clean Docker container to validate the published package.
#
# Usage:
#   ./scripts/smoke_test.sh              # test latest
#   ./scripts/smoke_test.sh 0.5.5        # test specific version
#
# Exits 0 on success, 1 on failure.

set -euo pipefail

VERSION="${1:-}"
INSTALL_CMD="pip install kollab"
if [ -n "$VERSION" ]; then
    INSTALL_CMD="pip install kollab==${VERSION}"
fi

echo "=== kollab PyPI smoke test ==="
echo "Installing: $INSTALL_CMD"
echo ""

# Run everything in a clean container
docker run --rm python:3.12-slim bash -c "
    set -e

    PASS=0
    FAIL=0

    test_check() {
        local name=\"\$1\"
        local result=\"\$2\"
        if [ \"\$result\" = \"0\" ]; then
            PASS=\$((PASS + 1))
            echo \"  [PASS] \$name\"
        else
            FAIL=\$((FAIL + 1))
            echo \"  [FAIL] \$name\"
        fi
    }

    # --- Install ---
    echo '--- install ---'
    ${INSTALL_CMD} 2>&1 | tail -3

    # --- Version ---
    echo ''
    echo '--- version ---'
    VERSION=\$(kollab --version 2>&1)
    echo \"  kollab version: \$VERSION\"
    test_check 'kollab CLI runs' 0

    # --- Core imports ---
    echo ''
    echo '--- imports ---'
    python3 -c 'from kollabor_ai.providers.openrouter_model_info import OpenRouterModelInfo; print(\"  openrouter_model_info: OK\")'
    test_check 'openrouter_model_info import' \$?

    python3 -c 'from kollabor_ai.providers.openrouter_provider import OpenRouterProvider; print(\"  openrouter_provider: OK\")'
    test_check 'openrouter_provider import' \$?

    python3 -c 'from kollabor_ai.providers.models import OpenRouterConfig, ProviderType; print(\"  provider models: OK\")'
    test_check 'provider models import' \$?

    python3 -c 'from kollabor_events import EventBus; print(\"  event bus: OK\")'
    test_check 'events import' \$?

    python3 -c 'from kollabor_plugins.base import BasePlugin; print(\"  plugin base: OK\")'
    test_check 'plugins import' \$?

    python3 -c 'from kollabor_tui.terminal_state import get_terminal_size; print(\"  tui: OK\")'
    test_check 'tui import' \$?

    # --- OpenRouter metadata logic ---
    echo ''
    echo '--- openrouter metadata ---'
    python3 -c \"
from kollabor_ai.providers.openrouter_model_info import OpenRouterModelInfo

info = OpenRouterModelInfo()

# Graceful fallback when no cache
effective = info.compute_effective_max_tokens(128000, 'nonexistent', 0)
assert effective == 128000, f'fallback failed: {effective}'
print('  fallback returns config default: OK')

# Minimum floor
info._cache['tiny'] = {'context_length': 100, 'max_completion_tokens': None}
effective = info.compute_effective_max_tokens(128000, 'tiny', 50)
assert effective == 256, f'floor failed: {effective}'
print('  minimum floor 256 tokens: OK')
\"
    test_check 'metadata fallback + floor' \$?

    # --- All sub-packages installed ---
    echo ''
    echo '--- sub-packages ---'
    # Only check packages listed in kollab's runtime dependencies
    for pkg in kollabor-events kollabor-ai kollabor-config kollabor-plugins kollabor-tui kollabor-agent; do
        VERSION_INFO=\$(pip show \$pkg 2>/dev/null | grep Version || echo 'NOT INSTALLED')
        echo \"  \$pkg: \$VERSION_INFO\"
        if echo \"\$VERSION_INFO\" | grep -q 'NOT INSTALLED'; then
            test_check \"\$pkg installed\" 1
        else
            test_check \"\$pkg installed\" 0
        fi
    done

    # --- First-run bundled agent seed ---
    echo ''
    echo '--- first-run seed files ---'
    rm -rf /root/.kollab
    python3 -c \"
from pathlib import Path
from kollabor_config.config_utils import initialize_config, initialize_system_prompt

initialize_config()
initialize_system_prompt()

prompt = Path.home() / '.kollab' / 'agents' / 'default' / 'system_prompt.md'
section = Path.home() / '.kollab' / 'agents' / 'default' / 'sections' / '00-header.md'
assert prompt.exists(), f'missing default agent prompt: {prompt}'
assert section.exists(), f'missing default agent sections: {section}'
text = prompt.read_text(encoding='utf-8')
assert 'SYSTEM PROMPT LOAD FAILURE' not in text
print(f'  default agent prompt: {prompt}')
print(f'  default agent section: {section}')
\"
    test_check 'first-run agent seed files' \$?

    # --- Summary ---
    echo ''
    echo '========================================'
    TOTAL=\$((PASS + FAIL))
    if [ \"\$FAIL\" -eq 0 ]; then
        echo \"  ALL \$TOTAL TESTS PASSED\"
        exit 0
    else
        echo \"  \$PASS/\$TOTAL PASSED, \$FAIL FAILED\"
        exit 1
    fi
"

EXIT_CODE=$?
echo ""
if [ $EXIT_CODE -eq 0 ]; then
    echo "=== smoke test passed ==="
else
    echo "=== smoke test FAILED ==="
fi
exit $EXIT_CODE
