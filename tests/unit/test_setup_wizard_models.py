"""Gate: the /setup wizard's model suggestions stay in sync with the registry.

Two drift bugs this catches:
  * a retired model (``retired: true`` in models.json) offered to a new user
  * a provider's ``default_model`` that no longer exists in the registry, so
    the wizard silently fails to preselect anything
"""

import json
from pathlib import Path

from plugins.altview.setup_altview import PROVIDERS, _load_model_suggestions

REGISTRY = Path(__file__).resolve().parents[2] / "bundles" / "data" / "models.json"


def test_retired_models_not_suggested():
    models = json.loads(REGISTRY.read_text())["models"]
    retired = {n for n, m in models.items() if m.get("retired")}
    assert retired, "registry has no retired entries — test would be vacuous"

    suggested = set()
    for choice in PROVIDERS:
        if choice.provider:
            suggested.update(_load_model_suggestions(choice.provider))
    assert not (retired & suggested)


def test_provider_defaults_are_preselectable():
    for choice in PROVIDERS:
        if not choice.default_model:
            continue
        options = _load_model_suggestions(choice.provider)
        assert choice.default_model in options, (
            f"{choice.key}: default_model {choice.default_model!r} is not in the "
            "registry, so the wizard cannot preselect it"
        )
