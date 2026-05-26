"""Plugin discovery import-root regressions."""

import importlib
import sys

from kollabor_plugins.discovery import PluginDiscovery
from kollabor_plugins.registry import PluginRegistry


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


def test_load_module_prefers_configured_plugins_dir_over_cwd_shadow(
    tmp_path,
    monkeypatch,
    request,
) -> None:
    """Project ./plugins packages must not shadow the configured plugin root."""
    snapshot = _snapshot_plugins_modules()
    request.addfinalizer(lambda: _restore_plugins_modules(snapshot))

    installed_root = tmp_path / "site-packages"
    installed_plugins = installed_root / "plugins"
    installed_plugins.mkdir(parents=True)
    (installed_plugins / "__init__.py").write_text("", encoding="utf-8")
    (installed_plugins / "shadow_plugin.py").write_text(
        """
class ShadowPlugin:
    @staticmethod
    def get_default_config():
        return {"plugins": {"shadow": {"enabled": True}}}
""",
        encoding="utf-8",
    )

    workspace = tmp_path / "workspace"
    cwd_plugins = workspace / "plugins"
    cwd_plugins.mkdir(parents=True)
    (cwd_plugins / "__init__.py").write_text("", encoding="utf-8")
    (cwd_plugins / "shadow_plugin.py").write_text(
        """
class ShadowPlugin:
    pass
""",
        encoding="utf-8",
    )

    _drop_plugins_modules()
    monkeypatch.syspath_prepend(str(installed_root))
    monkeypatch.syspath_prepend(str(workspace))
    monkeypatch.chdir(workspace)

    discovery = PluginDiscovery(installed_plugins)

    assert discovery.load_module("shadow_plugin") is True
    assert "ShadowPlugin" in discovery.loaded_classes
    assert (
        discovery.loaded_classes["ShadowPlugin"].get_default_config()["plugins"][
            "shadow"
        ]["enabled"]
        is True
    )


def test_loading_extra_plugin_root_preserves_existing_plugin_modules(
    tmp_path,
    monkeypatch,
    request,
) -> None:
    """Loading user plugin roots must not split already-imported bundled modules."""
    snapshot = _snapshot_plugins_modules()
    request.addfinalizer(lambda: _restore_plugins_modules(snapshot))

    package_root = tmp_path / "site-packages"
    package_plugins = package_root / "plugins"
    package_plugins.mkdir(parents=True)
    (package_plugins / "__init__.py").write_text("", encoding="utf-8")
    (package_plugins / "hub_plugin.py").write_text(
        """
class HubPlugin:
    @staticmethod
    def get_default_config():
        return {"plugins": {"hub": {"enabled": True}}}
""",
        encoding="utf-8",
    )

    user_plugins = tmp_path / "home" / ".kollab" / "plugins"
    user_plugins.mkdir(parents=True)
    (user_plugins / "global_plugin.py").write_text(
        """
class GlobalPlugin:
    @staticmethod
    def get_default_config():
        return {"plugins": {"global": {"enabled": True}}}
""",
        encoding="utf-8",
    )

    _drop_plugins_modules()
    monkeypatch.syspath_prepend(str(package_root))
    imported_hub_module = importlib.import_module("plugins.hub_plugin")

    registry = PluginRegistry(package_plugins, extra_plugin_dirs=[user_plugins])
    registry.load_all_plugins()

    assert sys.modules["plugins.hub_plugin"] is imported_hub_module
    assert "HubPlugin" in registry.list_plugins()
    assert "GlobalPlugin" in registry.list_plugins()
