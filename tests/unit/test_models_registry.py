"""Gate: the model registry must stay well-formed and current.

Runs scripts/validate_models.py. Fails if models.json is malformed or has gone
stale (see MAX_AGE_DAYS there), forcing the registry to be kept up to date.
"""

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
REGISTRY = REPO / "bundles" / "data" / "models.json"


def test_registry_passes_validator():
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "validate_models.py")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_actively_used_models_present():
    # Models the app actually runs must exist so the budget guard can resolve
    # their real context window instead of falling back to the default.
    models = json.loads(REGISTRY.read_text())["models"]
    for expected in ("glm-4.7", "glm-5.2"):
        assert expected in models, f"{expected} missing from registry"
