"""Enable state has one authority: the locked package registry."""

import json

import pytest


def test_enable_captures_path_and_preserves_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    monkeypatch.setenv("PATH", "/example/bin:/bin")
    from kollabor_plugins.settings import set_enabled

    home = tmp_path / ".kollab"
    home.mkdir()
    registry = home / "plugins.json"
    registry.write_text(json.dumps({"plugins": {"hello": {"enabled": False, "commit": "abc"}}, "dev": {}}))
    set_enabled("hello", True)
    data = json.loads(registry.read_text())
    assert data["plugins"]["hello"] == {"enabled": True, "commit": "abc", "enabled_path": "/example/bin:/bin"}
    set_enabled("hello", False)
    assert json.loads(registry.read_text())["plugins"]["hello"]["enabled"] is False
    with pytest.raises(ValueError):
        set_enabled("missing", True)
