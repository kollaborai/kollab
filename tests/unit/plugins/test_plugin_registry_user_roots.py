"""Plugin registry user-root regressions."""

import sys

from kollabor_plugins import PluginRegistry


def _drop_plugins_modules() -> None:
    for module_name in list(sys.modules):
        if module_name == "plugins" or module_name.startswith("plugins."):
            sys.modules.pop(module_name, None)


def _snapshot_plugins_modules() -> dict[str, object]:
    return {
        module_name: module
        for module_name, module in sys.modules.items()
        if module_name == "plugins" or module_name.startswith("plugins.")
    }


def _restore_plugins_modules(snapshot: dict[str, object]) -> None:
    _drop_plugins_modules()
    sys.modules.update(snapshot)


def test_registry_loads_global_user_plugins(
    tmp_path,
    monkeypatch,
    request,
) -> None:
    """Plugins in ~/.kollab/plugins should load without a source checkout."""
    snapshot = _snapshot_plugins_modules()
    request.addfinalizer(lambda: _restore_plugins_modules(snapshot))

    package_plugins = tmp_path / "site-packages" / "plugins"
    package_plugins.mkdir(parents=True)
    (package_plugins / "__init__.py").write_text("", encoding="utf-8")
    (package_plugins / "builtin_plugin.py").write_text(
        """
class BuiltinPlugin:
    @staticmethod
    def get_default_config():
        return {"plugins": {"builtin": {"enabled": True}}}
""",
        encoding="utf-8",
    )

    home = tmp_path / "home"
    global_plugins = home / ".kollab" / "plugins"
    global_plugins.mkdir(parents=True)
    (global_plugins / "global_plugin.py").write_text(
        """
class GlobalPlugin:
    @staticmethod
    def get_default_config():
        return {"plugins": {"global_plugin": {"enabled": True}}}
""",
        encoding="utf-8",
    )

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    _drop_plugins_modules()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(workspace)

    registry = PluginRegistry(package_plugins)
    registry.load_all_plugins()

    assert "BuiltinPlugin" in registry.list_plugins()
    assert "GlobalPlugin" in registry.list_plugins()
    assert registry.get_merged_config()["plugins"]["global_plugin"]["enabled"] is True
