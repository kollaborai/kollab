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


def setting_widgets(root: Path, manifest: dict) -> list[dict]:
    """Return the declared form plus the shared agent limits, when applicable."""
    settings_path = manifest.get("settings")
    if settings_path:
        section = _read_json(_within(root.resolve(), settings_path, "settings"))
        widgets = section.get("widgets")
        if not isinstance(widgets, list):
            raise ValueError("Invalid settings widgets")
        widgets = list(widgets)
    else:
        widgets = []
    if "agents" in manifest.get("uses", []):
        widgets.extend(
            [
                {
                    "type": "spinbox",
                    "label": "Daily Token Cap",
                    "config_path": "max_tokens_per_day",
                    "default": 2_000_000,
                    "min_value": 0,
                    "max_value": 2**63 - 1,
                },
                {
                    "type": "spinbox",
                    "label": "Max Agent Runs At Once",
                    "config_path": "max_concurrent_runs",
                    "default": 4,
                    "min_value": 1,
                    "max_value": 2**31 - 1,
                },
            ]
        )
    return widgets


def _valid_data_id(plugin_id: object) -> bool:
    if not isinstance(plugin_id, str):
        return False
    base = plugin_id.removesuffix("@dev")
    return bool(_ID.fullmatch(base))


def _builtin(plugin_id: str) -> dict | None:
    from .registry import plugin_entries

    return next(
        (entry for entry in plugin_entries() if entry.get("kind") == "built-in" and entry.get("id") == plugin_id),
        None,
    )


def _save_builtin(
    entry: dict, values: dict, *, enable: bool = False, allow_secrets: bool = False,
    config_service=None, save_target: str = "global",
) -> None:
    """Validate a built-in edit and use the same config writer as the config UI."""
    from kollabor.panels.config import apply_config_changes, read_managed_state, validate_config_changes
    from kollabor_config.config_utils import get_global_config_path, get_local_config_path
    from kollabor_config.managed_config import managed_by
    from kollabor_config.service import ConfigService

    if not isinstance(values, dict):
        raise ValueError("Plugin values must be an object")
    prefix = f"plugins.{entry['id']}."
    enabled_path = f"{prefix}enabled"
    fields = {
        field["path"]: field
        for field in entry.get("settings", [])
        if isinstance(field, dict)
        and isinstance(field.get("path"), str)
        and field["path"].startswith(prefix)
        and (allow_secrets or not field.get("secret"))
        and (enable or field["path"] != enabled_path)
    }
    if enable and enabled_path not in fields:
        managed, _ = read_managed_state()
        owner = managed_by(enabled_path, managed) if managed is not None else None
        fields[enabled_path] = {"type": "checkbox", "editable": owner is None, "managed_by": owner}
    errors = validate_config_changes(fields, values)
    if errors:
        raise ValueError("Invalid plugin settings: " + ", ".join(sorted(errors)))
    if not values:
        return
    if save_target not in {"local", "global"}:
        raise ValueError("Invalid config save target")
    path = get_local_config_path() if save_target == "local" else get_global_config_path()
    if path.is_symlink():
        raise ValueError("Config file cannot be a symlink")
    if path.exists():
        try:
            if not isinstance(json.loads(path.read_text(encoding="utf-8")), dict):
                raise ValueError("Invalid config file")
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid config file") from exc
    service = config_service if config_service is not None else ConfigService(path, fast_mode=True, initialize=False)
    try:
        if not apply_config_changes(
            service, values, save_target, route_builtin=False,
            atomic=callable(getattr(service, "save_keys", None)),
        ):
            raise OSError("Cannot save plugin config")
    finally:
        if config_service is None:
            service.shutdown()


def save(
    plugin_id: str, values: dict, *, allow_secrets: bool = False,
    config_service=None, save_target: str = "global",
) -> None:
    """Serialize validation and saving with install, update, detach and removal.

    ``allow_secrets`` is reserved for the local config panel, which already
    provides masked secret editing. Remote/plugin callers use the default.
    """
    if not isinstance(plugin_id, str):
        raise ValueError("Invalid plugin id")
    home = Path.home() / ".kollab"
    home.mkdir(parents=True, exist_ok=True)
    # Discovery can itself recover installs under plugins.json.lock; do it first.
    registry = _read_registry(home / "plugins.json")
    entry = None if plugin_id in registry["plugins"] or plugin_id.endswith("@dev") else _builtin(plugin_id)
    with locked_file(home / "plugins.json.lock", private=True):
        _recover_locked(home)
        registry = _read_registry(home / "plugins.json")
        if entry is not None and plugin_id not in registry["plugins"]:
            _save_builtin(
                entry, values, allow_secrets=allow_secrets,
                config_service=config_service, save_target=save_target,
            )
        else:
            _save_locked(plugin_id, values)


def _save_locked(plugin_id: str, values: dict) -> None:
    if not _valid_data_id(plugin_id):
        raise ValueError("Invalid plugin id")
    if not isinstance(values, dict):
        raise ValueError("Plugin values must be an object")
    home = Path.home() / ".kollab"
    registry = _read_registry(home / "plugins.json")
    if plugin_id.endswith("@dev"):
        folder = registry["dev"].get(plugin_id.removesuffix("@dev"))
        if not isinstance(folder, str):
            raise ValueError("Development plugin is not attached")
        root = Path(folder)
    else:
        if plugin_id not in registry["plugins"]:
            raise ValueError("Plugin is not installed")
        root = home / "plugins" / plugin_id
    manifest = _read_json(root / "kollab-plugin.json")
    widgets = setting_widgets(root, manifest)
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
        if plugin_id.endswith("@dev"):
            current_folder = _read_registry(home / "plugins.json")["dev"].get(plugin_id.removesuffix("@dev"))
            if current_folder != folder:
                raise ValueError("Development plugin is no longer attached to this folder")
        current = _read_json(path) if path.exists() else {}
        current.update(values)
        write_text_atomic(path, json.dumps(current, indent=2, ensure_ascii=False) + "\n")


def set_enabled(plugin_id: str, enabled: bool, *, config_service=None, save_target: str = "global") -> None:
    """Set a plugin's enabled state, capturing PATH for packages on enable."""
    if not isinstance(plugin_id, str):
        raise ValueError("Invalid plugin id")
    if type(enabled) is not bool:
        raise ValueError("Enabled state must be a boolean")
    home = Path.home() / ".kollab"
    home.mkdir(parents=True, exist_ok=True)
    registry = _read_registry(home / "plugins.json")
    entry = None if plugin_id in registry["plugins"] else _builtin(plugin_id)
    with locked_file(home / "plugins.json.lock", private=True):
        _recover_locked(home)
        path = home / "plugins.json"
        registry = _read_registry(path)
        record = registry["plugins"].get(plugin_id)
        if entry is not None and record is None:
            _save_builtin(
                entry, {f"plugins.{plugin_id}.enabled": enabled}, enable=True,
                config_service=config_service, save_target=save_target,
            )
            return
        if not _ID.fullmatch(plugin_id):
            raise ValueError("Invalid plugin id")
        if not isinstance(record, dict):
            raise ValueError("Plugin is not installed")
        if enabled and not record.get("enabled"):
            from .services import _recorded_process_alive

            run_dir = home / "plugin-data" / plugin_id / "run"
            if any(pid.is_symlink() or _recorded_process_alive(pid) for pid in run_dir.glob("*.pid")):
                raise ValueError("Plugin is stopping or still has a running service; wait before enabling it")
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
    if len(parts) != 3 or not _valid_data_id(parts[1]):
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
