"""Config widget plugin discovery regressions."""

import importlib
import sys
from pathlib import Path

import kollabor_tui.config_widgets as config_widgets_module
from kollabor_tui.config_widgets import ConfigWidgetDefinitions


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


def test_config_sections_discover_hub_when_repo_plugins_dir_is_unavailable(
    monkeypatch,
) -> None:
    """Installed wheels must not depend on a repo-relative ./plugins path."""
    monkeypatch.setattr(Path, "exists", lambda self: False)

    sections = ConfigWidgetDefinitions.get_plugin_config_sections()

    hub_section = next(
        (section for section in sections if section.get("title") == "Hub (Agent Mesh)"),
        None,
    )
    assert hub_section is not None
    widget_paths = {
        widget.get("config_path") for widget in hub_section.get("widgets", [])
    }
    assert "plugins.hub.enabled" in widget_paths
    assert "plugins.hub.notify_telegram_token" in widget_paths


def test_plugin_settings_discover_hub_when_repo_plugins_dir_is_unavailable(
    monkeypatch,
) -> None:
    """The generic plugin enable list should include package-style plugins."""
    monkeypatch.setattr(Path, "exists", lambda self: False)

    widgets = ConfigWidgetDefinitions.get_available_plugins()

    assert any(widget.get("config_path") == "plugins.hub.enabled" for widget in widgets)


def test_config_discovery_prefers_packaged_plugins_over_cwd_shadow(
    tmp_path,
    monkeypatch,
    request,
) -> None:
    """A project ./plugins package must not hide packaged /config widgets."""
    snapshot = _snapshot_plugins_modules()
    request.addfinalizer(lambda: _restore_plugins_modules(snapshot))

    site_packages = tmp_path / "site-packages"
    tui_dir = site_packages / "kollabor_tui"
    installed_hub = site_packages / "plugins" / "hub"
    installed_hub.mkdir(parents=True)
    tui_dir.mkdir()
    (site_packages / "plugins" / "__init__.py").write_text("", encoding="utf-8")
    (installed_hub / "__init__.py").write_text("", encoding="utf-8")
    (installed_hub / "plugin.py").write_text(
        """
class HubPlugin:
    name = "hub"
    description = "Installed hub"

    @staticmethod
    def get_config_widgets():
        return {
            "title": "Installed Hub",
            "widgets": [
                {
                    "type": "checkbox",
                    "label": "Enabled",
                    "config_path": "plugins.hub.enabled",
                    "help": "Enable installed hub",
                }
            ],
        }
""",
        encoding="utf-8",
    )

    workspace = tmp_path / "workspace"
    cwd_hub = workspace / "plugins" / "hub"
    cwd_hub.mkdir(parents=True)
    (workspace / "plugins" / "__init__.py").write_text("", encoding="utf-8")
    (cwd_hub / "__init__.py").write_text("", encoding="utf-8")
    (cwd_hub / "plugin.py").write_text(
        """
class HubPlugin:
    pass
""",
        encoding="utf-8",
    )

    _drop_plugins_modules()
    monkeypatch.syspath_prepend(str(site_packages))
    monkeypatch.syspath_prepend(str(workspace))
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(
        config_widgets_module,
        "__file__",
        str(tui_dir / "config_widgets.py"),
    )

    sections = ConfigWidgetDefinitions.get_plugin_config_sections()
    widgets = ConfigWidgetDefinitions.get_available_plugins()

    assert any(section.get("title") == "Installed Hub" for section in sections)
    assert any(widget.get("config_path") == "plugins.hub.enabled" for widget in widgets)


def test_config_discovery_preserves_unrelated_loaded_plugin_modules(
    tmp_path,
    monkeypatch,
    request,
) -> None:
    """Opening /config should not evict unrelated live plugin modules."""
    snapshot = _snapshot_plugins_modules()
    request.addfinalizer(lambda: _restore_plugins_modules(snapshot))

    original_root = tmp_path / "original-site"
    original_plugins = original_root / "plugins"
    original_plugins.mkdir(parents=True)
    (original_plugins / "__init__.py").write_text("", encoding="utf-8")
    (original_plugins / "other_plugin.py").write_text(
        """
class OtherPlugin:
    pass
""",
        encoding="utf-8",
    )

    installed_root = tmp_path / "installed-site"
    tui_dir = installed_root / "kollabor_tui"
    installed_hub = installed_root / "plugins" / "hub"
    installed_hub.mkdir(parents=True)
    tui_dir.mkdir()
    (installed_root / "plugins" / "__init__.py").write_text("", encoding="utf-8")
    (installed_hub / "__init__.py").write_text("", encoding="utf-8")
    (installed_hub / "plugin.py").write_text(
        """
class HubPlugin:
    @staticmethod
    def get_config_widgets():
        return {"title": "Installed Hub", "widgets": []}
""",
        encoding="utf-8",
    )

    _drop_plugins_modules()
    monkeypatch.syspath_prepend(str(original_root))
    other_module = importlib.import_module("plugins.other_plugin")
    monkeypatch.syspath_prepend(str(installed_root))
    monkeypatch.setattr(
        config_widgets_module,
        "__file__",
        str(tui_dir / "config_widgets.py"),
    )

    sections = ConfigWidgetDefinitions.get_plugin_config_sections()

    assert any(section.get("title") == "Installed Hub" for section in sections)
    assert sys.modules["plugins.other_plugin"] is other_module
