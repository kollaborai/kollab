"""Arbitrary project-local plugin directories are not implicit trust roots."""
from kollabor_plugins.registry import PluginRegistry


def test_project_plugins_are_not_scanned(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    local = project / ".kollab" / "plugins"
    local.mkdir(parents=True)
    monkeypatch.chdir(project)
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    (local / "untrusted_plugin.py").write_text("raise RuntimeError('must not import project plugins')")
    registry = PluginRegistry(bundled)
    assert local not in [path.resolve() for path in registry.plugin_dirs]
    registry.discover_plugins()
    assert "untrusted_plugin" not in registry.discovery.discovered_modules
