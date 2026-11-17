"""A failed removal rolls back the folder and registry together."""

import json

import pytest


def test_remove_registry_failure_restores_folder(tmp_path, monkeypatch):
    from kollabor_plugins import packages

    monkeypatch.setenv("HOME", str(tmp_path))
    home = tmp_path / ".kollab"
    root = home / "plugins" / "hello"
    root.mkdir(parents=True)
    (root / "kollab-plugin.json").write_text('{"id":"hello"}')
    registry = {"plugins": {"hello": {"enabled": False}}, "dev": {}}
    (home / "plugins.json").write_text(json.dumps(registry))
    writer = packages._write_json

    def fail(path, value):
        if path.name == "plugins.json" and "hello" not in value["plugins"]:
            raise OSError("simulated disk failure")
        return writer(path, value)

    monkeypatch.setattr(packages, "_write_json", fail)
    with pytest.raises(OSError, match="simulated"):
        packages.remove_package("hello", expected_record=registry["plugins"]["hello"])
    assert root.is_dir()
    assert json.loads((home / "plugins.json").read_text()) == registry
    assert not (home / "plugins" / "hello.old").exists()
