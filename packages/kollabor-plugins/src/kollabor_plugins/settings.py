"""Validated storage for installed package settings."""

import json
import os
from pathlib import Path

from kollabor_config.atomic_write import write_text_atomic
from kollabor_config.file_lock import locked_file

from .packages import _ID, _read_registry, _recover_locked, _within, _write_json


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read plugin settings: {path.name}") from exc
    if not isinstance(data, dict):
        raise ValueError("Plugin settings must be an object")
    return data


def _validate(widget: dict, value: object) -> None:
    kind = widget.get("type")
    if kind == "checkbox":
        valid = type(value) is bool
    elif kind in {"spinbox", "slider"}:
        valid = type(value) in (int, float)
        if kind == "spinbox":
            valid = valid and type(value) is int
        if valid:
            valid = widget.get("min_value", float("-inf")) <= value <= widget.get("max_value", float("inf"))
    elif kind == "dropdown":
        valid = value in widget.get("options", [])
    elif kind in {"text_input", "text"}:
        valid = isinstance(value, str)
    else:
        valid = False
    if not valid:
        raise ValueError(f"Invalid value for {widget.get('config_path', 'setting')}")


def save(plugin_id: str, values: dict) -> None:
    """Merge validated values under the installed plugin's sibling lock."""
    if not isinstance(plugin_id, str) or not _ID.fullmatch(plugin_id):
        raise ValueError("Invalid plugin id")
    if not isinstance(values, dict):
        raise ValueError("Plugin values must be an object")
    home = Path.home() / ".kollab"
    registry = _read_registry(home / "plugins.json")
    if plugin_id not in registry["plugins"]:
        raise ValueError("Plugin is not installed")
    root = home / "plugins" / plugin_id
    manifest = _read_json(root / "kollab-plugin.json")
    section = _read_json(_within(root.resolve(), manifest.get("settings"), "settings"))
    widgets = section.get("widgets")
    if not isinstance(widgets, list):
        raise ValueError("Invalid settings widgets")
    definitions = {}
    for widget in widgets:
        if not isinstance(widget, dict) or not isinstance(widget.get("config_path"), str):
            raise ValueError("Invalid settings widget")
        key = widget["config_path"]
        if not key or key == "enabled" or key in definitions:
            raise ValueError("Invalid settings path")
        definitions[key] = widget
    for key, value in values.items():
        if key not in definitions:
            raise ValueError(f"Unknown plugin setting: {key}")
        _validate(definitions[key], value)
    data_dir = home / "plugin-data" / plugin_id
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "values.json"
    with locked_file(data_dir / "values.json.lock", private=True):
        current = _read_json(path) if path.exists() else {}
        current.update(values)
        write_text_atomic(path, json.dumps(current, indent=2, ensure_ascii=False) + "\n")


def set_enabled(plugin_id: str, enabled: bool) -> None:
    """Set an installed package's enabled state and capture PATH on enable."""
    if not isinstance(plugin_id, str) or not _ID.fullmatch(plugin_id):
        raise ValueError("Invalid plugin id")
    if type(enabled) is not bool:
        raise ValueError("Enabled state must be a boolean")
    home = Path.home() / ".kollab"
    home.mkdir(parents=True, exist_ok=True)
    with locked_file(home / "plugins.json.lock", private=True):
        _recover_locked(home)
        path = home / "plugins.json"
        registry = _read_registry(path)
        record = registry["plugins"].get(plugin_id)
        if not isinstance(record, dict):
            raise ValueError("Plugin is not installed")
        record["enabled"] = enabled
        if enabled:
            record["enabled_path"] = os.environ.get("PATH", os.defpath)
        _write_json(path, registry)
    if not enabled:
        from .services import stop_services

        stop_services(plugin_id)


def apply_config_changes(changes: dict) -> dict:
    """Write installed-plugin rows and return the ordinary configuration changes.

    The UI uses namespaced paths to distinguish the plugin's own settings from
    application configuration.  Malformed reserved paths are rejected rather
    than accidentally persisted to config.json.
    """
    ordinary = {}
    values: dict[str, dict] = {}
    enabled = {}
    controls = []
    for path, value in changes.items():
        if path.startswith("plugin-values."):
            parts = path.split(".", 2)
            if len(parts) != 3 or not parts[1] or not parts[2]:
                raise ValueError("Invalid plugin settings path")
            values.setdefault(parts[1], {})[parts[2]] = value
        elif path.startswith("plugin-registry."):
            parts = path.split(".")
            if len(parts) != 3 or not parts[1] or parts[2] != "enabled":
                raise ValueError("Invalid plugin registry path")
            enabled[parts[1]] = value
        elif path.startswith("plugin-service."):
            from .packages import _SERVICE

            parts = path.split(".")
            if (
                len(parts) != 3
                or not _ID.fullmatch(parts[1])
                or not _SERVICE.fullmatch(parts[2])
                or not isinstance(value, bool)
            ):
                raise ValueError("Invalid plugin service path or value")
            controls.append((parts[1], parts[2], "start" if value else "stop"))
        elif path in {"plugin-values", "plugin-registry", "plugin-service"}:
            raise ValueError("Invalid plugin configuration path")
        else:
            ordinary[path] = value
    for plugin_id, plugin_values in values.items():
        save(plugin_id, plugin_values)
    for plugin_id, value in enabled.items():
        set_enabled(plugin_id, value)
    if controls:
        from .services import control_service

        for plugin_id, service_name, action in controls:
            control_service(plugin_id, service_name, action)
    return ordinary


def config_value(path: str, default: object = None) -> object:
    """Read a namespaced installed-plugin field from its authoritative store."""
    parts = path.split(".", 2)
    if len(parts) != 3 or not _ID.fullmatch(parts[1]):
        raise ValueError("Invalid plugin configuration path")
    home = Path.home() / ".kollab"
    if parts[0] == "plugin-registry" and parts[2] == "enabled":
        record = _read_registry(home / "plugins.json")["plugins"].get(parts[1])
        if not isinstance(record, dict):
            raise ValueError("Plugin is not installed")
        return record.get("enabled", False)
    if parts[0] == "plugin-values" and parts[2]:
        file = home / "plugin-data" / parts[1] / "values.json"
        return _read_json(file).get(parts[2], default) if file.exists() else default
    if parts[0] == "plugin-service":
        from .packages import _SERVICE

        if not _SERVICE.fullmatch(parts[2]):
            raise ValueError("Invalid service name")
        file = home / "plugin-data" / parts[1] / "run" / f"{parts[2]}.state"
        return _read_json(file).get("desired") == "running" if file.exists() else False
    raise ValueError("Invalid plugin configuration path")
