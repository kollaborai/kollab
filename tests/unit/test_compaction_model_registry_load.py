"""Compaction's model-registry reader must survive an oversized JSON integer.

The plugin loads the registry at import time, so a ValueError here fails the
import itself, not just one lookup.
"""

import json
from types import SimpleNamespace

from plugins import context_compaction_plugin as compaction


def test_oversized_json_integer_in_user_override_does_not_abort_load(
    tmp_path, monkeypatch
):
    # json.load raises ValueError past Python's int-from-str digit limit. The
    # override must read as an unrepresentable value while the rest still loads.
    text = json.dumps(
        {
            "models": {
                "valid-override": {"provider": "openrouter", "context_window": 1234},
                "overflow-override": {
                    "provider": "openrouter",
                    "context_window": "PLACEHOLDER",
                },
            }
        }
    )
    (tmp_path / "models.json").write_text(text.replace('"PLACEHOLDER"', "9" * 5000))
    monkeypatch.setattr(
        compaction,
        "resolve_global_path",
        lambda *parts: tmp_path.joinpath(*parts),
    )

    models = compaction._load_model_registry()["models"]

    assert models["valid-override"]["context_window"] == 1234
    assert not isinstance(models["overflow-override"]["context_window"], int)


def test_unrepresentable_overrides_are_not_emitted_in_budget_hud(tmp_path, monkeypatch):
    text = json.dumps(
        {
            "models": {
                "overflow-override": {
                    "provider": "openrouter",
                    "context_window": "MODEL_WINDOW",
                }
            },
            "provider_defaults": {
                "overflow-provider": {"context_window": "DEFAULT_WINDOW"}
            },
        }
    )
    text = text.replace('"MODEL_WINDOW"', "9" * 5000).replace(
        '"DEFAULT_WINDOW"', "8" * 5000
    )
    (tmp_path / "models.json").write_text(text)
    monkeypatch.setattr(
        compaction,
        "resolve_global_path",
        lambda *parts: tmp_path.joinpath(*parts),
    )
    registry = compaction._load_model_registry()
    monkeypatch.setattr(compaction, "_MODEL_REGISTRY", registry)

    active_values = {"model": "overflow-override", "provider": "openrouter"}
    active_profile = SimpleNamespace(
        get_model=lambda: active_values["model"],
        get_provider=lambda: active_values["provider"],
    )
    profile_manager = SimpleNamespace(get_active_profile=lambda: active_profile)
    queued = []
    service = SimpleNamespace(queue_agent_hud=lambda **event: queued.append(event))
    plugin = SimpleNamespace(
        _llm_service=service,
        _profile_manager=profile_manager,
        config={"plugins.context_compaction.budget_hud_every_n_turns": 1},
        _turns_since_budget_hud=0,
        _get_prompt_tokens=lambda: 0,
        _estimate_history_tokens=lambda history: 0,
    )
    plugin._resolve_context_window = lambda: (
        compaction.ContextCompactionPlugin._resolve_context_window(plugin)
    )

    for model, provider in (
        ("overflow-override", "openrouter"),
        ("model-not-in-registry", "overflow-provider"),
    ):
        active_values.update(model=model, provider=provider)
        assert plugin._resolve_context_window() is None
        assert (
            compaction.ContextCompactionPlugin._get_context_window_tokens(plugin) == 0
        )
        compaction.ContextCompactionPlugin._maybe_emit_budget_hud(plugin, [])

    assert queued == []
