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
