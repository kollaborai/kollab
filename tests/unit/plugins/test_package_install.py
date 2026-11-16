"""Package swap and registry writes form one recoverable transaction."""

import json

import pytest


def test_install_and_rollback_on_registry_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    import kollabor_plugins.packages as packages

    state = tmp_path / ".kollab"

    def stage(name, payload):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "kollab-plugin.json").write_text(json.dumps({"id": "hello"}))
        (folder / "content.txt").write_text(payload)
        info = {
            "source": "https://example.invalid/repo",
            "ref": "main",
            "commit": "a" * 40,
            "files_sha256": packages.files_sha256(folder),
            "manifest": {"id": "hello", "uses": []},
        }
        return folder, info

    first, first_info = stage("first", "old")
    packages.install_stage(first, first_info, first_info["commit"], first_info["files_sha256"])
    registry = state / "plugins.json"
    original = json.loads(registry.read_text())
    assert original["plugins"]["hello"]["enabled"] is True
    assert original["enable_order"]["hello"] == 1
    assert (state / "plugins" / "hello" / "content.txt").read_text() == "old"

    second, second_info = stage("second", "new")
    real_write = packages.write_text_atomic

    def fail_registry(path, text):
        if str(path) == str(registry):
            raise OSError("simulated write failure")
        return real_write(path, text)

    monkeypatch.setattr(packages, "write_text_atomic", fail_registry)
    with pytest.raises(OSError, match="simulated"):
        packages.install_stage(second, second_info, second_info["commit"], second_info["files_sha256"])
    monkeypatch.setattr(packages, "write_text_atomic", real_write)
    packages.recover_installs()
    assert (state / "plugins" / "hello" / "content.txt").read_text() == "old"
    assert json.loads(registry.read_text()) == original
    assert not (state / "plugins" / "hello.old").exists()


def test_update_disabled_and_remove_preserve_command_order_invariants(tmp_path, monkeypatch):
    from kollabor_plugins import packages, services
    from kollabor_plugins.settings import set_enabled

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    monkeypatch.setattr(services, "stop_services", lambda _plugin_id: [])

    def install(name):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "kollab-plugin.json").write_text(json.dumps({"id": "hello"}))
        info = {"source": "https://example.invalid/repo", "ref": "main", "commit": "a" * 40,
                "files_sha256": packages.files_sha256(folder), "manifest": {"id": "hello", "uses": []}}
        packages.install_stage(folder, info, info["commit"], info["files_sha256"])

    install("first")
    registry_path = tmp_path / ".kollab" / "plugins.json"
    set_enabled("hello", False)
    install("update")
    registry = json.loads(registry_path.read_text())
    assert registry["plugins"]["hello"]["enabled"] is False
    assert "hello" not in registry["enable_order"]
    set_enabled("hello", True)
    registry = json.loads(registry_path.read_text())
    assert registry["enable_order"]["hello"] == 2
    packages.remove_package("hello", expected_record=registry["plugins"]["hello"])
    assert "hello" not in json.loads(registry_path.read_text())["enable_order"]
