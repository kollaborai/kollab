"""The emergency plugin-off switch prevents discovery, not just initialization."""


def test_no_plugins_prevents_imports(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    from kollabor.cli import parse_arguments
    from kollabor_plugins import PluginRegistry

    marker = tmp_path / "executed"
    root = tmp_path / "plugins"
    root.mkdir()
    (root / "danger_plugin.py").write_text(f"open({str(marker)!r}, 'w').close()\nclass DangerPlugin: pass\n")
    registry = PluginRegistry(root, no_plugins=True)
    assert registry.discover_classes_only() == []
    registry.load_all_plugins()
    assert not marker.exists()
    assert parse_arguments([], ["--no-plugins"]).no_plugins is True
