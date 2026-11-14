"""A damaged registered package must never fall back to loose discovery."""

import json


def test_registered_directory_with_missing_manifest_is_not_imported(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    monkeypatch.chdir(tmp_path)
    from kollabor_plugins import PluginRegistry

    builtin = tmp_path / "bundled"
    builtin.mkdir()
    package = tmp_path / ".kollab" / "plugins" / "hello"
    package.mkdir(parents=True)
    marker = tmp_path / "executed"
    (package / "__init__.py").write_text(f"open({str(marker)!r},'w').close()\n")
    (package / "bad_plugin.py").write_text("class BadPlugin:\n @staticmethod\n def get_default_config(): return {}\n")
    (tmp_path / ".kollab" / "plugins.json").write_text(
        json.dumps({"plugins": {"hello": {"enabled": False}}, "dev": {}})
    )
    PluginRegistry(builtin).load_all_plugins()
    assert not marker.exists()
