"""Tests for scripts/update_model_directory.py — the external-source
regenerator for bundles/data/models.json.

Mapping, curated-safe merge semantics, and the --check drift gate are all
pure functions over fixture payloads shaped like the live sources
(models.dev api.json and the OpenRouter /api/v1/models catalog). No network
in tests.
"""

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "update_model_directory.py"

_spec = importlib.util.spec_from_file_location("kollab_update_model_directory", SCRIPT)
assert _spec and _spec.loader
upd = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = upd
_spec.loader.exec_module(upd)

REGISTRY = REPO / "bundles" / "data" / "models.json"


MODELS_DEV_PAYLOAD = {
    "openai": {
        "models": {
            "gpt-5.6-luna": {
                "id": "gpt-5.6-luna",
                "attachment": True,
                "reasoning": True,
                "tool_call": True,
                "temperature": False,
                "release_date": "2026-09-22",
                "modalities": {"input": ["text", "image"], "output": ["text"]},
                "limit": {"context": 1050000, "output": 128000},
                "pricing": {"input": 0.1, "output": 0.5, "cache_read": 0.01},
            },
            "brand-new-model": {
                "id": "brand-new-model",
                "attachment": True,
                "reasoning": True,
                "tool_call": True,
                "release_date": "2026-11-01",
                "modalities": {"input": ["text", "image"], "output": ["text"]},
                "limit": {"context": 400000, "output": 64000},
                "pricing": {"input": 1.0, "output": 2.0},
            },
        }
    },
    "some-unsupported-provider": {"models": {"x": {"limit": {"context": 1}}}},
}

OPENROUTER_PAYLOAD = {
    "data": [
        {
            "id": "openai/gpt-5.6-luna:free",
            "context_length": 1050000,
            "created": 1789123456,
            "architecture": {"input_modalities": ["text", "image"]},
            "pricing": {"prompt": "0.0000001", "completion": "0.0000005"},
            "top_provider": {"max_completion_tokens": 128000},
            "supported_parameters": ["tools", "reasoning", "temperature"],
        },
        {
            "id": "inclusionai/ling-3.2-flash",
            "context_length": 1048576,
            "created": 1794591958,
            "architecture": {"input_modalities": ["text", "image"]},
            "pricing": {"prompt": "0", "completion": "0"},
            "top_provider": {"max_completion_tokens": 262144},
            "supported_parameters": ["tools", "reasoning"],
        },
    ]
}


def test_map_models_dev_entry_to_kollab_schema():
    mapped = upd.map_models_dev_model("gpt-5.6-luna", MODELS_DEV_PAYLOAD["openai"]["models"]["gpt-5.6-luna"], "openai")
    assert mapped["provider"] == "openai"
    assert mapped["context_window"] == 1050000
    assert mapped["max_output"] == 128000
    assert mapped["supports_vision"] is True
    assert mapped["supports_tools"] is True
    assert mapped["supports_thinking"] is True
    assert mapped["supports_sampling"] is False  # temperature: false
    assert mapped["released"] == "2026-09-22"
    assert mapped["pricing_in"] == 0.1
    assert mapped["pricing_out"] == 0.5
    assert mapped["pricing_cached_in"] == 0.01


def test_map_openrouter_entry_to_kollab_schema():
    mapped = upd.map_openrouter_model(OPENROUTER_PAYLOAD["data"][0])
    assert mapped["provider"] == "openrouter"
    assert mapped["context_window"] == 1050000
    assert mapped["max_output"] == 128000
    assert mapped["supports_vision"] is True
    assert mapped["supports_tools"] is True
    assert mapped["supports_thinking"] is True
    # per-token strings scale to per-million dollars, rounded
    assert mapped["pricing_in"] == round(0.0000001 * 1_000_000, 4)
    assert mapped["pricing_out"] == round(0.0000005 * 1_000_000, 4)
    assert mapped["released"] == upd.unix_to_date(1789123456)


def test_openrouter_unknown_prices_clear_stale_values_and_keep_free_prices():
    dynamic_router = {
        "id": "openrouter/auto",
        "context_length": 100000,
        "pricing": {"prompt": "-1", "completion": "-1"},
    }
    data = {
        "models": {
            "openrouter/auto": {
                "provider": "openrouter",
                "context_window": 100000,
                "pricing_in": 25.0,
                "pricing_out": 50.0,
            }
        }
    }

    changed, _ = upd.apply_merge(
        data, {}, {"data": [dynamic_router]}, today="2026-11-24"
    )

    assert changed is True
    assert data["models"]["openrouter/auto"]["pricing_in"] is None
    assert data["models"]["openrouter/auto"]["pricing_out"] is None
    free = upd.map_openrouter_model({"pricing": {"prompt": "0", "completion": "0"}})
    assert free["pricing_in"] == 0.0
    assert free["pricing_out"] == 0.0


def test_models_dev_invalid_price_becomes_unknown():
    mapped = upd.map_models_dev_model(
        "model",
        {
            "limit": {"context": 100},
            "pricing": {"input": -1.0, "output": 0.0},
        },
        "openai",
    )

    assert mapped["pricing_in"] is None
    assert mapped["pricing_out"] == 0.0


def test_openrouter_model_output_caps_survive_catalog_refresh():
    expected = {
        "deepseek/deepseek-v4-flash-vision-exp": 262144,
        "gryphe/mythomax-l2-13b": 4096,
        "meta-llama/llama-3.1-8b-instruct": 16384,
        "z-ai/glm-5.3-flash": 131072,
    }

    for model_id, limit in expected.items():
        mapped = upd.map_openrouter_model(
            {
                "id": f"{model_id}:nitro",
                "context_length": 100000,
                "top_provider": {"max_completion_tokens": limit * 2},
            }
        )
        assert mapped["max_output"] == limit


def test_unknown_source_providers_are_skipped():
    additions, updates = upd.merge_sources(
        json.loads(REGISTRY.read_text()),
        MODELS_DEV_PAYLOAD,
        OPENROUTER_PAYLOAD,
    )
    # gpt-5.6-luna exists curated -> update; brand-new-model is added;
    # some-unsupported-provider is skipped entirely.
    assert "brand-new-model" in updates["added"]
    assert all(not str(k).startswith("some-unsupported") for k in updates["added"])


def test_non_chat_models_without_context_window_are_skipped():
    payload = {
        "openai": {
            "models": {
                "gpt-image-2": {
                    "id": "gpt-image-2",
                    "modalities": {"input": ["text", "image"], "output": ["image"]},
                    "limit": {},
                }
            }
        }
    }
    data = json.loads(REGISTRY.read_text())
    additions, planned = upd.merge_sources(data, payload, {"data": []})
    assert "gpt-image-2" not in additions
    assert "gpt-image-2" not in planned["added"]


def test_merge_updates_sourced_fields_and_preserves_curated_only_ones():
    data = json.loads(REGISTRY.read_text())
    curated = data["models"]["gpt-5.6-luna"]
    assert curated["notes"]  # curated-only text survives
    assert curated["supports_vision"] is True

    changed, report = upd.apply_merge(data, MODELS_DEV_PAYLOAD, OPENROUTER_PAYLOAD, today="2026-11-15")
    assert changed is True
    merged = data["models"]["gpt-5.6-luna"]
    assert merged["notes"] == curated["notes"]
    assert merged["default_output"] == curated.get("default_output")
    assert data["models"]["brand-new-model"]["provider"] == "openai"
    assert "Auto-generated" in data["models"]["brand-new-model"]["notes"]
    assert data["_meta"]["updated"] == "2026-11-15"
    # report lists what changed, reviewable in the PR
    assert any("gpt-5.6-luna" in line for line in report)


def test_openrouter_entries_use_tagless_vendor_prefixed_ids():
    data = json.loads(REGISTRY.read_text())
    upd.apply_merge(data, MODELS_DEV_PAYLOAD, OPENROUTER_PAYLOAD, today="2026-11-15")
    added = data["models"]["inclusionai/ling-3.2-flash"]
    assert added["provider"] == "openrouter"
    assert added["supports_vision"] is True
    # The :free tag is stripped off the catalog key; the canonical id entry
    # covers tagged variants at lookup time.
    assert "openai/gpt-5.6-luna" not in data["models"] or (
        data["models"]["openai/gpt-5.6-luna"]["provider"] == "openrouter"
    )


def test_no_changes_leaves_meta_and_reports_clean():
    payload = {
        "openai": {
            "models": {
                # exactly what the curated entry already says
                "gpt-5.6-luna": {
                    "attachment": True,
                    "reasoning": True,
                    "tool_call": True,
                    "temperature": True,
                    "release_date": "2026-07-09",
                    "modalities": {"input": ["text", "image"], "output": ["text"]},
                    "limit": {"context": 1050000, "output": 128000},
                    "pricing": {"input": 1.0, "output": 6.0, "cache_read": 0.1},
                }
            }
        }
    }
    data = json.loads(REGISTRY.read_text())
    changed, report = upd.apply_merge(data, payload, {"data": []}, today="2026-11-15")
    assert changed is False
    assert report == ["no changes; model directory already matches the sources"]
    assert data["_meta"]["updated"] != "2026-11-15"


def test_models_dev_true_temperature_refreshes_sampling_support():
    data = {"models": {"model": {"provider": "openai", "supports_sampling": False}}}
    source = {
        "openai": {
            "models": {
                "model": {
                    "temperature": True,
                    "limit": {"context": 10},
                }
            }
        }
    }

    upd.apply_merge(data, source, {"data": []}, today="2026-11-16")

    assert data["models"]["model"]["supports_sampling"] is True


def test_malformed_models_dev_nested_sections_do_not_abort_refresh():
    source = {
        "openai": {
            "models": {
                "bad-limit": {"limit": ["unexpected"]},
                "bad-modalities": {
                    "limit": {"context": 10},
                    "modalities": ["unexpected"],
                    "pricing": ["unexpected"],
                },
            }
        }
    }
    data = {"models": {}}

    upd.apply_merge(data, source, {"data": []}, today="2026-11-16")

    assert "bad-limit" not in data["models"]
    assert data["models"]["bad-modalities"]["supports_vision"] is False


def test_malformed_openrouter_nested_sections_do_not_abort_refresh():
    source = {
        "data": [
            {
                "id": "vendor/model",
                "context_length": 10,
                "top_provider": ["unexpected"],
                "architecture": ["unexpected"],
                "pricing": ["unexpected"],
            }
        ]
    }
    data = {"models": {}}

    upd.apply_merge(data, {}, source, today="2026-11-16")

    assert data["models"]["vendor/model"]["supports_vision"] is False


def _write_refresh_inputs(tmp_path, openrouter_text, registry_text):
    models_dev = tmp_path / "models_dev.json"
    models_dev.write_text("{}")
    openrouter = tmp_path / "openrouter.json"
    openrouter.write_text(openrouter_text)
    registry = tmp_path / "models.json"
    registry.write_text(registry_text)
    return [
        "--source-models-dev",
        str(models_dev),
        "--source-openrouter",
        str(openrouter),
        "--registry",
        str(registry),
    ], registry


def test_oversized_json_integer_source_price_becomes_unknown(tmp_path):
    # json.loads raises ValueError for a literal past Python's int-from-str
    # digit limit. That price must map to unknown while valid prices refresh.
    source = {
        "data": [
            {
                "id": "vendor/valid",
                "context_length": 1000,
                "pricing": {"prompt": "0.0000005", "completion": "0.000001"},
            },
            {
                "id": "vendor/overflow",
                "context_length": 1000,
                "pricing": {"prompt": "PLACEHOLDER", "completion": "0.000001"},
            },
        ]
    }
    argv, registry = _write_refresh_inputs(
        tmp_path,
        json.dumps(source).replace('"PLACEHOLDER"', "9" * 5000),
        json.dumps({"models": {}}),
    )

    assert upd.main(argv) == 0

    models = json.loads(registry.read_text())["models"]
    assert models["vendor/valid"]["pricing_in"] == 0.5
    assert models["vendor/overflow"]["pricing_in"] is None


def test_oversized_json_integer_in_registry_does_not_abort_refresh(tmp_path):
    # load_registry reads the existing registry with json.loads. A price past
    # the digit limit must not stop the refresh; the source price replaces it.
    source = {
        "data": [
            {
                "id": "vendor/valid",
                "context_length": 1000,
                "pricing": {"prompt": "0.0000005", "completion": "0.000001"},
            }
        ]
    }
    registry_text = json.dumps(
        {
            "models": {
                "vendor/valid": {
                    "provider": "openrouter",
                    "context_window": 1000,
                    "pricing_in": "PLACEHOLDER",
                }
            }
        }
    ).replace('"PLACEHOLDER"', "9" * 5000)
    argv, registry = _write_refresh_inputs(tmp_path, json.dumps(source), registry_text)

    assert upd.main(argv) == 0

    models = json.loads(registry.read_text())["models"]
    assert models["vendor/valid"]["pricing_in"] == 0.5


def test_refresh_clears_invalid_retained_prices_before_writing_strict_json(tmp_path):
    source = {"data": [{"id": "vendor/retained", "context_length": 2000}]}
    registry_text = json.dumps(
        {
            "models": {
                "vendor/retained": {
                    "provider": "openrouter",
                    "context_window": 1000,
                    "pricing_in": "OVERSIZED_POSITIVE",
                    "pricing_out": "OVERSIZED_NEGATIVE",
                    "pricing_cached_in": "OVERSIZED_CACHED_NEGATIVE",
                },
                "vendor/valid-sibling": {
                    "provider": "openrouter",
                    "context_window": 1000,
                    "pricing_in": 0.5,
                    "pricing_out": 1.25,
                    "pricing_cached_in": 0.25,
                },
            }
        }
    )
    registry_text = (
        registry_text.replace('"OVERSIZED_POSITIVE"', "9" * 5000)
        .replace('"OVERSIZED_NEGATIVE"', "-" + "9" * 5000)
        .replace('"OVERSIZED_CACHED_NEGATIVE"', "-" + "8" * 5000)
    )
    argv, registry = _write_refresh_inputs(tmp_path, json.dumps(source), registry_text)

    assert upd.main(argv) == 0

    def reject_non_json_number(value):
        raise AssertionError(f"non-standard JSON number written: {value}")

    models = json.loads(registry.read_text(), parse_constant=reject_non_json_number)[
        "models"
    ]
    retained = models["vendor/retained"]
    assert retained["context_window"] == 2000
    assert retained["pricing_in"] is None
    assert retained["pricing_out"] is None
    assert retained["pricing_cached_in"] is None
    assert models["vendor/valid-sibling"]["pricing_in"] == 0.5
    assert models["vendor/valid-sibling"]["pricing_out"] == 1.25
    assert models["vendor/valid-sibling"]["pricing_cached_in"] == 0.25


def test_refresh_clears_invalid_retained_output_limits_before_writing_strict_json(
    tmp_path,
):
    source = {"data": [{"id": "vendor/retained", "context_length": 2000}]}
    registry_text = json.dumps(
        {
            "models": {
                "vendor/retained": {
                    "provider": "openrouter",
                    "context_window": 1000,
                    "max_output": "OVERSIZED_OUTPUT",
                },
                "vendor/negative": {
                    "provider": "openrouter",
                    "context_window": 1000,
                    "max_output": -1,
                },
                "vendor/float": {
                    "provider": "openrouter",
                    "context_window": 1000,
                    "max_output": 1.5,
                },
                "vendor/valid-sibling": {
                    "provider": "openrouter",
                    "context_window": 1000,
                    "max_output": 4096,
                },
            }
        }
    ).replace('"OVERSIZED_OUTPUT"', "9" * 5000)
    argv, registry = _write_refresh_inputs(tmp_path, json.dumps(source), registry_text)

    assert upd.main(argv) == 0

    def reject_non_json_number(value):
        raise AssertionError(f"non-standard JSON number written: {value}")

    models = json.loads(registry.read_text(), parse_constant=reject_non_json_number)[
        "models"
    ]
    for name in ("vendor/retained", "vendor/negative", "vendor/float"):
        assert models[name]["max_output"] is None
    assert models["vendor/retained"]["context_window"] == 2000
    assert models["vendor/valid-sibling"]["max_output"] == 4096


def test_refresh_refuses_to_write_unrepresentable_retained_context_window(tmp_path):
    source = {"data": [{"id": "vendor/updated", "context_length": 2000}]}
    registry_text = json.dumps(
        {
            "models": {
                "vendor/unmapped": {
                    "provider": "openrouter",
                    "context_window": "OVERSIZED_CONTEXT",
                },
                "vendor/updated": {
                    "provider": "openrouter",
                    "context_window": 1000,
                },
            }
        }
    ).replace('"OVERSIZED_CONTEXT"', "9" * 5000)
    argv, registry = _write_refresh_inputs(tmp_path, json.dumps(source), registry_text)

    assert upd.main(argv) == 2
    assert registry.read_text() == registry_text


def test_refresh_repairs_oversized_context_and_output_from_source(tmp_path):
    source = {
        "data": [
            {
                "id": "vendor/repaired",
                "context_length": 2000,
                "top_provider": {"max_completion_tokens": 4096},
            }
        ]
    }
    registry_text = json.dumps(
        {
            "models": {
                "vendor/repaired": {
                    "provider": "openrouter",
                    "context_window": "OVERSIZED_CONTEXT",
                    "max_output": "OVERSIZED_OUTPUT",
                }
            }
        }
    )
    registry_text = registry_text.replace('"OVERSIZED_CONTEXT"', "9" * 5000)
    registry_text = registry_text.replace('"OVERSIZED_OUTPUT"', "8" * 5000)
    argv, registry = _write_refresh_inputs(tmp_path, json.dumps(source), registry_text)

    assert upd.main(argv) == 0

    def reject_non_json_number(value):
        raise AssertionError(f"non-standard JSON number written: {value}")

    models = json.loads(registry.read_text(), parse_constant=reject_non_json_number)[
        "models"
    ]
    assert models["vendor/repaired"]["context_window"] == 2000
    assert models["vendor/repaired"]["max_output"] == 4096
