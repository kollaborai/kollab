"""Compaction's model-registry reader must survive an oversized JSON integer.

The plugin loads the registry at import time, so a ValueError here fails the
import itself, not just one lookup.
"""

import json

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
