"""Only explicitly granted process secrets enter a service environment."""

import json
import sys

import pytest


def setup_service(tmp_path, monkeypatch, granted):
    from kollabor_plugins.packages import files_sha256

    monkeypatch.setenv("HOME", str(tmp_path))
    home = tmp_path / ".kollab"
    root = home / "plugins" / "hello"
    root.mkdir(parents=True)
    script = "import os; print(os.environ['TEST_TOKEN']); print('UNRELATED' in os.environ)"
    manifest = {
        "services": {
            "worker": {"command": [sys.executable, "-c", script], "restart": "never", "secrets": ["TEST_TOKEN"]}
        }
    }
    (root / "kollab-plugin.json").write_text(json.dumps(manifest))
    entry = {"enabled": True, "enabled_path": "/usr/bin:/bin", "granted": granted, "files_sha256": files_sha256(root)}
    (home / "plugins.json").write_text(json.dumps({"plugins": {"hello": entry}, "dev": {}}))
    return home


def test_granted_secret_is_given_only_to_child(tmp_path, monkeypatch):
    import kollabor_ai.profile_manager as profile_manager
    from kollabor_plugins.services import run_service

    home = setup_service(tmp_path, monkeypatch, ["secret:TEST_TOKEN"])
    monkeypatch.setenv("UNRELATED", "not-for-plugin")

    def get_key(name):
        assert name == "TEST_TOKEN"
        return "secret-test-value"

    monkeypatch.setattr(profile_manager, "_keyring_get", get_key)
    assert run_service("hello", "worker", "abcdef") == 0
    text = (home / "plugin-data" / "hello" / "run" / "worker.log").read_text()
    assert "secret-test-value" in text and "False" in text


def test_undeclared_grant_refused_before_spawn(tmp_path, monkeypatch):
    from kollabor_plugins.services import run_service

    setup_service(tmp_path, monkeypatch, [])
    with pytest.raises(ValueError, match="secret"):
        run_service("hello", "worker", "abcdef")
