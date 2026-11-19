import json
from types import SimpleNamespace

import kollabor_ai.profile_manager as profile_manager_module
from kollabor_ai.profile_manager import ProfileManager


def test_config_write_failure_keeps_profile_registered(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "kollabor": {
                    "llm": {"profiles": {"custom": {"provider": "openai"}}}
                }
            }
        ),
        encoding="utf-8",
    )

    manager = ProfileManager.__new__(ProfileManager)
    profile = SimpleNamespace(is_provisioned=False)
    manager._active_profile_name = "default"
    manager._profiles = {"custom": profile}

    monkeypatch.setattr(
        profile_manager_module,
        "get_global_config_path_candidates",
        lambda: [config_path],
    )
    monkeypatch.setattr(
        profile_manager_module, "get_global_config_path", lambda: config_path
    )

    def fail_config_write(_path, _contents):
        raise OSError("disk full")

    monkeypatch.setattr(profile_manager_module, "write_text_atomic", fail_config_write)

    assert manager.delete_profile("custom") is False
    assert manager.get_profile("custom") is profile
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert "custom" in saved["kollabor"]["llm"]["profiles"]
