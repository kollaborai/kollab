"""Plugin registry system for Kollab."""

import hashlib
import importlib.util
import inspect
import json
import logging
import math
import re
import sys
import types
from itertools import islice
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Sequence, Type

from kollabor_events.dict_utils import deep_merge

from .base import BasePlugin
from .collector import PluginStatusCollector
from .discovery import PluginDiscovery
from .factory import PluginFactory
from .packages import (
    _ID,
    _MAX_ENABLE_ORDER,
    _MAX_PLUGIN_SERVICE_SECRETS,
    _MAX_PLUGIN_SERVICES,
    _MAX_PLUGIN_USES,
    _SYMBOL,
    _read_registry,
    _within,
    files_sha256,
    heartbeat_seconds,
    recover_installs,
    validate_manifest,
)

logger = logging.getLogger(__name__)

_INVENTORY_TEXT_LIMIT = 4096
_INVENTORY_SENSITIVE_TEXT = re.compile(
    r"(?:\b(?:secret|token|password|credential|authorization|bearer|api[_-]?key)[-_][A-Za-z0-9_-]{6,}"
    r"|\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|secret|password|credential|authorization)\s*[:=]\s*\S+)",
    re.IGNORECASE,
)
_INVENTORY_INVISIBLE_CONTROLS = re.compile(r"[\u200b\u202a-\u202e\u2066-\u2069]")
_PLUGIN_INVENTORY_STATES = {"ok", "error", "off", "stopping", "stop-failed", "changed-on-disk", "skipped"}
_SERVICE_INVENTORY_STATES = {
    "starting",
    "running",
    "backoff",
    "stopping",
    "stop-failed",
    "exited",
    "stopped",
    "crash-stopped",
    "error",
    "unknown",
    "failed",
}
_PLUGIN_INVENTORY_ERRORS = {
    "Plugin files changed since approval",
    "Plugin metadata could not be loaded",
    "Development plugin metadata could not be loaded",
    "Python entry failed while loading; see the process log for details.",
}
_SERVICE_INVENTORY_ERRORS = {
    "Could not prepare plugin engine credentials",
    "Could not start this process. View Log for details.",
    "Could not stop this process. Retry stopping it.",
    "Could not verify the stop. Check the process before retrying.",
}

# These paths remain available for imports performed after plugin initialization.
_vendor_owners: dict[str, Path] = {}


def _prepare_vendor(root: Path) -> tuple[Path | None, set[str]]:
    vendor = root / "python" / "vendor"
    if not vendor.is_dir():
        return None, set()
    vendor = vendor.resolve()
    if not vendor.is_relative_to(root.resolve()):
        raise ValueError("Vendor directory escapes the plugin")
    names = set()
    for item in vendor.iterdir():
        name = item.stem if item.is_file() and item.suffix == ".py" else item.name if item.is_dir() else ""
        if not name.isidentifier() or name == "__pycache__":
            continue
        if not item.resolve().is_relative_to(vendor):
            raise ValueError(f"Vendored module escapes the plugin: {name}")
        owner = _vendor_owners.get(name)
        if (owner is not None and owner != vendor) or (name in sys.modules and owner != vendor):
            raise ValueError(f"Vendored module name clashes: {name}")
        names.add(name)
    return vendor, names


class _RegistryDiscovery(PluginDiscovery):
    """Legacy discovery that keeps registered and manifest packages isolated."""

    @staticmethod
    def _is_valid_module_name(module_name: str) -> bool:
        return bool(module_name) and all(part.isidentifier() for part in module_name.split("."))

    def _verify_plugin_location(self, module_name: str) -> bool:
        if not self._is_valid_module_name(module_name):
            return False
        root = self.plugins_dir.resolve()
        relative = Path(*module_name.split("."))
        file = (root / relative).with_suffix(".py")
        package = root / relative / "__init__.py"
        candidate = file if file.is_file() else package
        return candidate.is_file() and candidate.resolve().is_relative_to(root)

    def scan_plugin_files(self) -> list[str]:
        root = self.plugins_dir.resolve()
        if not root.is_dir():
            return []
        found = []
        blocked = getattr(self, "blocked_plugin_dirs", set())

        def skip(path: Path) -> bool:
            relative = path.relative_to(root)
            if any(part.startswith(".") for part in relative.parts):
                return True
            if len(relative.parts) > 1 and (getattr(self, "block_all_subdirs", False) or relative.parts[0] in blocked):
                return True
            parent = path.parent
            while parent != root and parent.is_relative_to(root):
                if (parent / "kollab-plugin.json").is_file():
                    return True
                parent = parent.parent
            return not path.resolve().is_relative_to(root)

        for path in root.rglob("*_plugin.py"):
            if not skip(path) and not path.name.startswith("_"):
                module = ".".join(path.relative_to(root).with_suffix("").parts)
                if self._is_valid_module_name(module):
                    found.append(module)
        for init in root.rglob("*/__init__.py"):
            if skip(init):
                continue
            relative = init.parent.relative_to(root)
            if any(part.startswith("_") for part in relative.parts):
                continue
            module = ".".join(relative.parts)
            if (init.parent / "plugin.py").is_file():
                module += ".plugin"
            if self._is_valid_module_name(module):
                found.append(module)
        self.discovered_modules = list(dict.fromkeys(found))
        return self.discovered_modules

    def load_module(self, module_name: str) -> bool:
        result = super().load_module(module_name)
        if result:
            return True
        module = sys.modules.get(f"plugins.{module_name}") or sys.modules.get(module_name)
        if module is None:
            return False
        for name, cls in inspect.getmembers(module, inspect.isclass):
            if name.endswith("Plugin") and callable(getattr(cls, "get_config_widgets", None)):
                self.loaded_classes[name] = cls
                self.plugin_configs[name] = {}
                return True
        return False


def _plugin_code_error(plugin_id: str, exc: Exception) -> ValueError:
    """Keep text raised by plugin code out of metadata; it can hold secrets (#1401)."""
    logger.error("Plugin %s raised while loading", plugin_id, exc_info=exc)
    return ValueError("Python entry failed while loading; see the process log for details.")


class PluginRegistry:
    """Simplified registry coordinating plugin discovery, instantiation, and status collection.

    This class coordinates between three specialized components:
    - PluginDiscovery: File system scanning and module loading
    - PluginFactory: Plugin instantiation with dependencies
    - PluginStatusCollector: Status aggregation from plugin instances
    """

    def __init__(
        self,
        plugins_dir: Path,
        extra_plugin_dirs: Sequence[Path] | None = None,
        *,
        no_plugins: bool = False,
    ) -> None:
        """Initialize the plugin registry with specialized components.

        Args:
            plugins_dir: Directory containing plugin modules.
            extra_plugin_dirs: Additional user/project plugin roots.
        """
        self.plugins_dir = plugins_dir
        self.no_plugins = no_plugins
        self.plugin_dirs = self._resolve_plugin_dirs(plugins_dir, extra_plugin_dirs)
        self.discoveries = [_RegistryDiscovery(plugin_dir) for plugin_dir in self.plugin_dirs]
        self.discovery = self.discoveries[0]
        if not no_plugins:
            global_root = (Path.home() / ".kollab" / "plugins").resolve()
            for discovery in self.discoveries:
                if discovery.plugins_dir.resolve() == global_root:
                    try:
                        records = _read_registry(global_root.parent / "plugins.json")
                        discovery.blocked_plugin_dirs = set(records["plugins"])
                    except OSError, ValueError:
                        # Corrupt authority must not make installed directories
                        # eligible for unchecked legacy imports.
                        discovery.block_all_subdirs = True
        self.factory = PluginFactory()
        self.collector = PluginStatusCollector()
        self.package_errors: dict[str, str] = {}
        self._packages_loaded = False
        logger.info(
            "Plugin registry initialized with specialized components: "
            f"{', '.join(str(plugin_dir) for plugin_dir in self.plugin_dirs)}"
        )

    @staticmethod
    def _resolve_plugin_dirs(
        plugins_dir: Path,
        extra_plugin_dirs: Sequence[Path] | None = None,
    ) -> List[Path]:
        candidates = [
            plugins_dir,
            *(extra_plugin_dirs or []),
            Path.home() / ".kollab" / "plugins",
        ]

        resolved_dirs: list[Path] = []
        seen: set[Path] = set()
        for candidate in candidates:
            resolved = candidate.expanduser().resolve()
            if resolved in seen:
                continue
            if candidate == plugins_dir or resolved.is_dir():
                resolved_dirs.append(candidate)
                seen.add(resolved)

        return resolved_dirs

    def _merge_discovery_results(self) -> None:
        primary = self.discovery

        for discovery in self.discoveries[1:]:
            for module_name in discovery.discovered_modules:
                if module_name not in primary.discovered_modules:
                    primary.discovered_modules.append(module_name)
            primary.loaded_classes.update(discovery.loaded_classes)
            primary.plugin_configs.update(discovery.plugin_configs)

    def _load_packages(self) -> list[Type]:
        """Import registered package entries only after integrity verification."""
        if self._packages_loaded:
            return []
        self._packages_loaded = True
        loaded: list[Type] = []
        home = Path.home() / ".kollab"
        try:
            recover_installs()
            registry = _read_registry(home / "plugins.json")
        except (OSError, ValueError) as exc:
            logger.warning("Cannot read installed plugins: %s", exc)
            return loaded
        candidates = [
            (plugin_id, record, home / "plugins" / plugin_id, False)
            for plugin_id, record in registry["plugins"].items()
            if isinstance(plugin_id, str) and plugin_id not in registry["dev"]
        ]
        candidates.extend(
            (plugin_id, {"enabled": True}, Path(folder), True)
            for plugin_id, folder in registry["dev"].items()
            if isinstance(plugin_id, str) and isinstance(folder, str)
        )
        # Earliest-enabled loads first, so it wins slash-command conflicts (docs/specs/plugins.md).
        order = registry.get("enable_order")
        order = order if isinstance(order, dict) else {}

        def _enabled_rank(candidate):
            rank = order.get(f"{candidate[0]}@dev" if candidate[3] else candidate[0])
            return rank if type(rank) is int else float("inf")

        candidates.sort(key=_enabled_rank)
        for plugin_id, record, root, is_dev in candidates:
            if not _ID.fullmatch(plugin_id):
                continue
            if not isinstance(record, dict) or record.get("enabled") is not True:
                continue
            added_vendor: Path | None = None
            reserved_names: set[str] = set()
            try:
                if is_dev:
                    from kollabor.version import get_runtime_version

                    digest = "dev_" + hashlib.sha256(str(root).encode()).hexdigest()
                    manifest = validate_manifest(root, get_runtime_version())
                else:
                    digest = record.get("files_sha256")
                    if not isinstance(digest, str) or files_sha256(root) != digest:
                        raise ValueError("Changed on Disk")
                    manifest = json.loads((root / "kollab-plugin.json").read_text(encoding="utf-8"))
                if manifest.get("id") != plugin_id:
                    raise ValueError("Manifest id differs from registry")
                entry = manifest.get("python")
                if not entry:
                    continue
                if not isinstance(entry, str) or entry.count(":") != 1:
                    raise ValueError("Invalid Python entry")
                filename, symbol = entry.split(":")
                if not _SYMBOL.fullmatch(symbol):
                    raise ValueError("Invalid Python entry symbol")
                path = _within(root.resolve(), filename, "python")
                vendor, vendor_names = _prepare_vendor(root)
                if vendor is not None:
                    if str(vendor) not in sys.path:
                        sys.path.insert(0, str(vendor))
                        added_vendor = vendor
                    for name in vendor_names:
                        if name not in _vendor_owners:
                            reserved_names.add(name)
                        _vendor_owners[name] = vendor
                module_name = f"_kollab_package_{plugin_id.replace('-', '_')}_{digest}"
                package = types.ModuleType(module_name)
                package.__path__ = [str(path.parent)]
                package.__package__ = module_name
                sys.modules[module_name] = package
                spec = importlib.util.spec_from_file_location(f"{module_name}.entry", path)
                if spec is None or spec.loader is None:
                    raise ValueError("Cannot load Python entry")
                module = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = module
                previous_bytecode = sys.dont_write_bytecode
                # Everything in this block can run plugin code (import, module __getattr__,
                # metaclass checks, descriptors), so any exception raised here is plugin text.
                # Kollab's own refusals are recorded in `refusal` and raised after the block.
                refusal = None
                try:
                    sys.dont_write_bytecode = True
                    try:
                        spec.loader.exec_module(module)
                    finally:
                        sys.dont_write_bytecode = previous_bytecode
                    plugin_class = getattr(module, symbol)
                    if not inspect.isclass(plugin_class) or not issubclass(plugin_class, BasePlugin):
                        refusal = "Python entry must be a BasePlugin subclass"
                    elif symbol in self.discovery.loaded_classes:
                        refusal = "Python entry class conflicts with another plugin"
                    else:
                        get_config = getattr(plugin_class, "get_default_config", None)
                        config = get_config() if callable(get_config) else {}
                except Exception as exc:
                    raise _plugin_code_error(plugin_id, exc) from exc
                if refusal:
                    raise ValueError(refusal)
                self.discovery.loaded_classes[symbol] = plugin_class
                self.discovery.plugin_configs[symbol] = config
                loaded.append(plugin_class)
            except Exception as exc:
                for name in reserved_names:
                    _vendor_owners.pop(name, None)
                if added_vendor is not None:
                    sys.path.remove(str(added_vendor))
                self.package_errors[plugin_id] = str(exc)
                logger.warning("Plugin %s could not be loaded: %s", plugin_id, exc)
        return loaded

    def discover_plugins(self) -> List[str]:
        """Discover available plugins in the plugins directory.

        Returns:
            List of discovered plugin module names.
        """
        if self.no_plugins:
            return []
        discovered: list[str] = []
        for discovery in self.discoveries:
            discovered.extend(discovery.scan_plugin_files())
        self._merge_discovery_results()
        return discovered

    def load_plugin(self, module_name: str) -> None:
        """Load a plugin module and register its configuration.

        Args:
            module_name: Name of the plugin module to load.
        """
        if not self.no_plugins:
            self.discovery.load_module(module_name)

    def load_all_plugins(self) -> None:
        """Discover and load all available plugins."""
        if self.no_plugins:
            return
        for discovery in self.discoveries:
            discovery.discover_and_load()
        self._merge_discovery_results()
        self._load_packages()
        logger.info(f"Plugin registry loaded {len(self.discovery.loaded_classes)} plugins")

    def discover_classes_only(self) -> List[Type]:
        """Discover plugin classes across all configured roots."""
        if self.no_plugins:
            return []
        plugin_classes: list[Type] = []

        for discovery in self.discoveries:
            for plugin_class in discovery.discover_classes_only():
                if plugin_class not in plugin_classes:
                    plugin_classes.append(plugin_class)

        self._merge_discovery_results()
        plugin_classes.extend(self._load_packages())
        return plugin_classes

    def get_merged_config(self) -> Dict[str, Any]:
        """Get merged configuration from all registered plugins.

        Returns:
            Merged configuration dictionary from all plugins.
        """
        merged_config: Dict[str, Any] = {}
        plugin_configs = self.discovery.get_all_configs()

        for plugin_name, plugin_config in plugin_configs.items():
            # Deep merge plugin config into merged_config
            merged_config = deep_merge(merged_config, plugin_config)
            logger.debug(f"Merged config from plugin: {plugin_name}")

        return merged_config

    def get_plugin_class(self, plugin_name: str) -> Type:
        """Get a registered plugin class by name.

        Args:
            plugin_name: Name of the plugin class.

        Returns:
            Plugin class if found.

        Raises:
            KeyError: If plugin is not registered.
        """
        return self.discovery.get_plugin_class(plugin_name)

    def get_plugin_startup_info(self, plugin_name: str, config) -> List[str]:
        """Get startup information for a plugin.

        Args:
            plugin_name: Name of the plugin class.
            config: Configuration manager instance.

        Returns:
            List of startup info strings, or empty list if no info available.
        """
        try:
            plugin_class = self.discovery.get_plugin_class(plugin_name)
            return self.collector.get_plugin_startup_info(plugin_name, plugin_class, config)
        except KeyError:
            logger.warning(f"Plugin {plugin_name} not found for startup info")
            return []

    def list_plugins(self) -> List[str]:
        """Get list of registered plugin names.

        Returns:
            List of registered plugin names.
        """
        return list(self.discovery.loaded_classes.keys())

    def instantiate_plugins(self, event_bus, renderer, config) -> Dict[str, Any]:
        """Create instances of all registered plugins that can be instantiated.

        Args:
            event_bus: Event bus for hook registration.
            renderer: Terminal renderer.
            config: Configuration manager.

        Returns:
            Dictionary mapping plugin names to their instances.
        """
        plugin_classes = self.discovery.loaded_classes
        return self.factory.instantiate_all(plugin_classes, event_bus, renderer, config)

    def get_registry_stats(self) -> Dict[str, Any]:
        """Get comprehensive statistics about the registry and its components.

        Returns:
            Dictionary with detailed registry statistics.
        """
        return {
            "plugins_directory": str(self.plugins_dir),
            "plugin_directories": [str(plugin_dir) for plugin_dir in self.plugin_dirs],
            "discovery_stats": self.discovery.get_discovery_stats(),
            "factory_stats": self.factory.get_factory_stats(),
            "collector_stats": self.collector.get_collector_stats(),
        }


def _web_entry(root: Path, manifest: dict) -> dict | None:
    from .packages import _within, files_sha256

    web = manifest.get("web")
    if not isinstance(web, dict):
        return None
    try:
        dist = _within(root.resolve(), "web/dist", "web assets")
        return {"entry": web.get("entry"), "style": web.get("style"), "hash": files_sha256(dist)}
    except (OSError, ValueError):
        return None


def _settings_fields(plugin_id: str, widgets: list[dict]) -> list[dict]:
    """Expose panel fields while retaining widget definitions for the terminal."""
    from kollabor.panels.config import _field

    fields = []
    for widget in widgets:
        if not isinstance(widget, dict) or not isinstance(widget.get("config_path"), str):
            continue
        row = {**widget, "config_path": f"plugin-values.{plugin_id}.{widget['config_path']}"}
        fields.append({**widget, **_field(row, None, None, {})})
    return fields


def _service_entries(home: Path, plugin_id: str, manifest: dict) -> list[dict]:
    from .packages import _SERVICE
    from .services import _identity_alive, _verified_wrapper

    result = []
    declared = manifest.get("services", {})
    if not isinstance(declared, dict):
        return result
    for name in islice(declared, _MAX_PLUGIN_SERVICES):
        if not isinstance(name, str) or not _SERVICE.fullmatch(name):
            continue
        data_dir = home / "plugin-data"
        run_dir = data_dir / plugin_id / "run"
        state_file = run_dir / f"{name}.state"
        pid_file = run_dir / f"{name}.pid"
        unsafe_run = any(path.is_symlink() for path in (data_dir, data_dir / plugin_id, run_dir))
        try:
            if unsafe_run or state_file.is_symlink():
                raise ValueError("Unsafe service state path")
            state = json.loads(state_file.read_text(encoding="utf-8"))
            if not isinstance(state, dict):
                state = {}
        except OSError, ValueError:
            state = {"status": "unknown"} if unsafe_run or state_file.is_symlink() else {}
        history = state.get("exit_history", [])
        identity = None
        try:
            if unsafe_run or pid_file.is_symlink():
                raise ValueError("Unsafe service pid path")
            recorded = json.loads(pid_file.read_text(encoding="utf-8"))
            if isinstance(recorded, dict):
                child = recorded.get("child")
                wrapper = recorded.get("wrapper")
                if isinstance(child, dict) and _identity_alive(child):
                    identity = child
                elif isinstance(wrapper, dict) and _verified_wrapper(wrapper):
                    identity = wrapper
        except (OSError, ValueError):
            pass
        status = state.get("status", "stopped")
        if not isinstance(status, str) or status not in {
            "starting",
            "running",
            "backoff",
            "stopping",
            "stop-failed",
            "exited",
            "stopped",
            "crash-stopped",
            "error",
            "unknown",
            "failed",
        }:
            status = "unknown"
        desired = state.get("desired", "stopped")
        if not isinstance(desired, str) or desired not in {"running", "stopped"}:
            desired = "stopped"
        pid = identity.get("pid") if identity else None
        since = _finite_service_number(identity.get("start_time")) if identity else None
        if type(pid) is not int or pid <= 0 or since is None:
            identity = None
            pid = None
            since = None
        definition = declared[name]
        heartbeat_s = heartbeat_seconds(definition)
        raw_secrets = definition.get("secrets", []) if isinstance(definition, dict) else []
        secrets_omitted = (
            max(0, len(raw_secrets) - _MAX_PLUGIN_SERVICE_SECRETS)
            if isinstance(raw_secrets, list)
            else 0
        )
        if status == "running" and identity is None:
            status = "unknown"
        if status == "stopping" and identity is None:
            status = "unknown" if pid_file.exists() or pid_file.is_symlink() else "stopped"
        if status == "stop-failed" and identity is None and not (pid_file.exists() or pid_file.is_symlink()):
            status = "stopped"
        service_entry = _normalize_service_entry(
            {
                "name": name,
                "state": status,
                "desired": desired,
                "pid": pid,
                "since": since,
                "restarts": len(history) if isinstance(history, list) else 0,
                "secrets": _service_secrets(definition),
                "secrets_omitted": secrets_omitted,
                "heartbeat": None,
                "heartbeat_s": heartbeat_s,
                "heartbeat_monitor_since": None,
                "stop_deadline": _finite_service_number(state.get("stop_deadline")) if status == "stopping" else None,
                "error": state.get("error") if isinstance(state.get("error"), str) else None,
            }
        )
        if service_entry is not None:
            result.append(service_entry)
    return result


def _service_secrets(definition: object) -> list[str]:
    raw = definition.get("secrets", []) if isinstance(definition, dict) else []
    if not isinstance(raw, list):
        return []
    return [
        secret
        for secret in raw[:_MAX_PLUGIN_SERVICE_SECRETS]
        if isinstance(secret, str) and _SYMBOL.fullmatch(secret) and len(secret) <= 64
    ]


def _omitted_service_count(manifest: dict) -> int:
    services = manifest.get("services")
    return max(0, len(services) - _MAX_PLUGIN_SERVICES) if isinstance(services, dict) else 0


def _finite_service_number(value: object) -> float | None:
    if type(value) not in (int, float):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def _bounded_inventory_text(value: object, limit: int = _INVENTORY_TEXT_LIMIT) -> str | None:
    if (
        not isinstance(value, str)
        or len(value) > limit
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
        or _INVENTORY_INVISIBLE_CONTROLS.search(value)
        or _INVENTORY_SENSITIVE_TEXT.search(value)
    ):
        return None
    clean = value.strip()
    return clean or None


def _normalize_web_dist_path(value: object) -> str | None:
    if not isinstance(value, str) or "\\" in value or value.startswith("/"):
        return None
    path = PurePosixPath(value)
    parts = path.parts
    if len(parts) < 3 or parts[:2] != ("web", "dist") or ".." in parts:
        return None
    return path.as_posix()


def _bounded_values(values: list, limit: int, normalize) -> list:
    return [clean for item in islice(values, limit) if (clean := normalize(item)) is not None]


def _bounded_unique(values: list, limit: int, normalize) -> list:
    result = []
    seen = set()
    for item in islice(values, limit):
        clean = normalize(item)
        if clean is None or clean in seen:
            continue
        seen.add(clean)
        result.append(clean)
    return result


def _inventory_number(value: object, minimum: float = 0, maximum: float = float("inf")) -> int | float | None:
    if type(value) not in (int, float):
        return None
    try:
        return value if math.isfinite(value) and minimum <= value <= maximum else None
    except (OverflowError, TypeError):
        return None


def _normalize_service_entry(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    name = _bounded_inventory_text(value.get("name"), 40)
    from .packages import _SERVICE

    if not isinstance(name, str) or not _SERVICE.fullmatch(name):
        return None
    raw_state = value.get("state")
    state = raw_state if isinstance(raw_state, str) and raw_state in _SERVICE_INVENTORY_STATES else "unknown"
    raw_desired = value.get("desired")
    desired = raw_desired if raw_desired in ("running", "stopped") else "stopped"
    raw_secrets = value.get("secrets")
    secrets = _bounded_unique(
        raw_secrets,
        _MAX_PLUGIN_SERVICE_SECRETS,
        lambda secret: secret if isinstance(secret, str) and _SYMBOL.fullmatch(secret) and len(secret) <= 64 else None,
    ) if isinstance(raw_secrets, list) else []
    inherited_omissions = value.get("secrets_omitted")
    secrets_omitted = inherited_omissions if type(inherited_omissions) is int and inherited_omissions > 0 else 0
    if isinstance(raw_secrets, list):
        secrets_omitted += max(0, len(raw_secrets) - _MAX_PLUGIN_SERVICE_SECRETS)
    raw_error = value.get("error")
    error = raw_error if isinstance(raw_error, str) and raw_error in _SERVICE_INVENTORY_ERRORS else None
    return {
        "name": name,
        "state": state,
        "desired": desired,
        "pid": value.get("pid") if type(value.get("pid")) is int and value["pid"] > 0 else None,
        "since": _inventory_number(value.get("since")),
        "restarts": min(value["restarts"], 2_147_483_647)
        if type(value.get("restarts")) is int and value["restarts"] >= 0
        else 0,
        "heartbeat": _inventory_number(value.get("heartbeat")),
        "heartbeat_s": _inventory_number(value.get("heartbeat_s"), 0.0000000001, 86_400),
        "heartbeat_monitor_since": _inventory_number(value.get("heartbeat_monitor_since")),
        "stop_deadline": _inventory_number(value.get("stop_deadline")),
        "error": error,
        "secrets": secrets,
        "secrets_omitted": secrets_omitted,
    }


def _normalize_capability(value: object) -> str | None:
    if value in ("agents", "agents:unattended"):
        return value
    if not isinstance(value, str) or len(value) > 1024:
        return None
    if value.startswith("secret:"):
        secret = value[7:]
        return value if len(secret) <= 64 and _SYMBOL.fullmatch(secret) else None
    kind, separator, detail = value.partition(":")
    if separator and kind in {"run", "net", "files"}:
        safe = _bounded_inventory_text(detail, 1000)
        return f"{kind}:{safe}" if safe else None
    if separator and re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", kind) and re.fullmatch(r"[A-Za-z0-9_./,-]{1,256}", detail):
        return value if _bounded_inventory_text(value, 1024) else None
    return None


def _normalize_declared(value: object) -> str | dict | None:
    if isinstance(value, str):
        safe = _bounded_inventory_text(value, 512)
        return safe if safe and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", safe) else None
    if not isinstance(value, dict):
        return None
    result: dict[str, str] = {}
    for key in ("id", "name", "tool", "tag", "label", "title"):
        if key not in value:
            continue
        safe = _bounded_inventory_text(value[key], 512)
        if not safe or (key not in {"label", "title"} and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", safe)):
            return None
        result[key] = safe
    return result if any(key in result for key in ("id", "name", "tool", "tag")) else None


def _normalize_contributes(value: object) -> dict:
    kinds = ("menu", "views", "pages", "modals", "slash", "renderers")
    result = {kind: [] for kind in kinds}
    if not isinstance(value, dict):
        return result
    for kind in kinds:
        items = value.get(kind)
        if not isinstance(items, list):
            continue
        for item in items[:256]:
            if kind != "menu":
                declared = _normalize_declared(item)
                if declared is not None:
                    result[kind].append(declared)
                continue
            if not isinstance(item, dict):
                continue
            row_id = _bounded_inventory_text(item.get("id"), 128)
            label = _bounded_inventory_text(item.get("label"), 256)
            raw_open = item.get("open")
            if not row_id or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", row_id) or not label:
                continue
            if (
                not isinstance(raw_open, dict)
                or len(raw_open) != 1
                or next(iter(raw_open), None) not in {"page", "modal", "view", "url"}
            ):
                continue
            target_key = next(iter(raw_open))
            target_value = _bounded_inventory_text(raw_open[target_key], 512)
            icon = _bounded_inventory_text(item.get("icon"), 64) if item.get("icon") is not None else None
            if not target_value or (item.get("icon") is not None and not icon):
                continue
            result["menu"].append({
                "id": row_id,
                "label": label,
                "open": {target_key: target_value},
                **({"icon": icon} if icon else {}),
            })
    return result


def _normalize_setting(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    field_type = value.get("type")
    config_path = _bounded_inventory_text(value.get("config_path"), 512)
    path = _bounded_inventory_text(value.get("path"), 512) or config_path
    label = _bounded_inventory_text(value.get("label"), 256)
    allowed_types = {"checkbox", "slider", "spinbox", "dropdown", "text_input", "textarea", "label"}
    if not isinstance(field_type, str) or field_type not in allowed_types or not path or not label:
        return None
    secret = value.get("secret") is True
    raw_value = value.get("value")
    if secret or raw_value is None or type(raw_value) is bool:
        normalized_value = None if secret else raw_value
    elif type(raw_value) in (int, float):
        normalized_value = _inventory_number(raw_value, -9_007_199_254_740_991, 9_007_199_254_740_991)
    elif isinstance(raw_value, str):
        normalized_value = _bounded_inventory_text(raw_value)
    else:
        normalized_value = None

    raw_default = value.get("default")
    if secret or raw_default is None or type(raw_default) is bool:
        normalized_default = None if secret else raw_default
    elif type(raw_default) in (int, float):
        normalized_default = _inventory_number(raw_default, -9_007_199_254_740_991, 9_007_199_254_740_991)
    elif isinstance(raw_default, str):
        normalized_default = _bounded_inventory_text(raw_default)
    else:
        normalized_default = None

    def optional_text(key: str, limit: int = 1024):
        raw = value.get(key)
        return None if raw is None else _bounded_inventory_text(raw, limit)

    def optional_number(key: str):
        raw = value.get(key)
        return None if raw is None else _inventory_number(raw, -9_007_199_254_740_991, 9_007_199_254_740_991)

    raw_options = value.get("options")
    options = (
        _bounded_values(raw_options, 256, lambda item: _bounded_inventory_text(item, 256))
        if isinstance(raw_options, list)
        else None
    )
    raw_labels = value.get("option_labels")
    option_labels = {}
    if isinstance(raw_labels, dict):
        for key, raw_label in islice(raw_labels.items(), 256):
            safe_key = _bounded_inventory_text(key, 256)
            safe_label = _bounded_inventory_text(raw_label, 256)
            if safe_key and safe_label:
                option_labels[safe_key] = safe_label
    return {
        "path": path,
        "config_path": config_path or path,
        "type": field_type,
        "label": label,
        "help": optional_text("help") or "",
        "value": normalized_value,
        "default": normalized_default,
        "min_value": optional_number("min_value"),
        "max_value": optional_number("max_value"),
        "step": optional_number("step"),
        "options": options,
        "option_labels": option_labels if isinstance(raw_labels, dict) else None,
        "save_path": optional_text("save_path", 512),
        "placeholder": optional_text("placeholder"),
        "editable": value.get("editable") is True,
        "managed_by": optional_text("managed_by", 256),
        "secret": secret,
        "is_set": value.get("is_set") is True,
        **({"action": optional_text("action", 128)} if value.get("action") is not None else {}),
    }


def _normalize_plugin_entry(value: object) -> dict | None:
    """Give the API one bounded, typed shape for every plugin source."""
    if not isinstance(value, dict):
        return None
    plugin_id = value.get("id")
    if not isinstance(plugin_id, str):
        return None
    base_id = plugin_id.removesuffix("@dev")
    from .packages import _ID

    kind = value.get("kind")
    kind = kind if isinstance(kind, str) and kind in {"built-in", "installed", "dev"} else "installed"
    builtin_id = re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,39}", base_id, re.ASCII) is not None
    valid_id = builtin_id if kind == "built-in" else _ID.fullmatch(base_id) is not None
    if not valid_id or plugin_id not in {base_id, f"{base_id}@dev"}:
        return None
    raw_state = value.get("state")
    state = raw_state if isinstance(raw_state, str) and raw_state in _PLUGIN_INVENTORY_STATES else "off"
    raw_error = value.get("error")
    error = raw_error if isinstance(raw_error, str) and raw_error in _PLUGIN_INVENTORY_ERRORS else None
    if state == "error" and error is None:
        error = "Plugin metadata could not be loaded"
    elif state == "changed-on-disk" and error is None:
        error = "Plugin files changed since approval"

    raw_web = value.get("web")
    web = None
    if isinstance(raw_web, dict):
        entry = _normalize_web_dist_path(_bounded_inventory_text(raw_web.get("entry"), 512))
        raw_style = raw_web.get("style")
        style = None if raw_style is None else _normalize_web_dist_path(_bounded_inventory_text(raw_style, 512))
        digest = raw_web.get("hash")
        if (
            entry
            and (raw_style is None or style)
            and isinstance(digest, str)
            and re.fullmatch(r"[0-9a-fA-F]{64}", digest)
        ):
            web = {"entry": entry, "style": style, "hash": digest}

    raw_services = value.get("services")
    services = (
        [
            service
            for item in raw_services[:_MAX_PLUGIN_SERVICES]
            if (service := _normalize_service_entry(item)) is not None
        ]
        if isinstance(raw_services, list)
        else []
    )
    reported_omissions = value.get("omitted")
    reported_services = reported_omissions.get("services") if isinstance(reported_omissions, dict) else 0
    omitted_services = (reported_services if type(reported_services) is int and reported_services > 0 else 0)
    if isinstance(raw_services, list):
        omitted_services += max(0, len(raw_services) - _MAX_PLUGIN_SERVICES)
    raw_uses = value.get("uses")
    uses = _bounded_unique(raw_uses, _MAX_PLUGIN_USES, _normalize_capability) if isinstance(raw_uses, list) else []
    omitted_uses = max(0, len(raw_uses) - _MAX_PLUGIN_USES) if isinstance(raw_uses, list) else 0
    raw_granted = value.get("granted")
    granted = (
        _bounded_unique(raw_granted, _MAX_PLUGIN_USES, _normalize_capability)
        if isinstance(raw_granted, list)
        else []
    )
    omitted_granted = max(0, len(raw_granted) - _MAX_PLUGIN_USES) if isinstance(raw_granted, list) else 0
    raw_settings = value.get("settings")
    settings = (
        [field for item in raw_settings[:256] if (field := _normalize_setting(item)) is not None]
        if isinstance(raw_settings, list)
        else []
    )
    section = value.get("settings_section")
    section_title = _bounded_inventory_text(section.get("title"), 256) if isinstance(section, dict) else None
    section_widgets = (
        [field for item in section.get("widgets", [])[:256] if (field := _normalize_setting(item)) is not None]
        if isinstance(section, dict) and isinstance(section.get("widgets"), list)
        else []
    )
    version = _bounded_inventory_text(value.get("version"), 256)
    source = _bounded_inventory_text(value.get("source"))
    raw_commit = value.get("commit")
    commit = (
        raw_commit
        if isinstance(raw_commit, str) and re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", raw_commit)
        else None
    )
    raw_order = value.get("enable_order")
    enable_order = raw_order if type(raw_order) is int and 0 < raw_order <= _MAX_ENABLE_ORDER else None
    return {
        "id": plugin_id,
        "name": _bounded_inventory_text(value.get("name"), 256) or plugin_id,
        "version": version,
        "kind": kind,
        "source": source,
        "commit": commit,
        "enabled": value.get("enabled") is True,
        "enable_order": enable_order,
        "shadowed": value.get("shadowed") is True,
        "state": state,
        "error": error,
        "web": web,
        "contributes": _normalize_contributes(value.get("contributes")),
        "services": services,
        "settings": settings,
        "settings_section": {"title": section_title or "Settings", "widgets": section_widgets}
        if isinstance(section, dict)
        else None,
        "uses": uses,
        "granted": granted,
        "omitted": {"services": omitted_services, "uses": omitted_uses, "granted": omitted_granted},
    }


def plugin_entries(plugins_dir: Path | None = None) -> list[dict]:
    """Describe built-in, loose, installed and development plugin entries."""
    if plugins_dir is None:
        plugins_dir = next(
            (
                parent / "plugins"
                for parent in Path(__file__).resolve().parents
                if (parent / "plugins" / "__init__.py").is_file()
            ),
            Path.cwd() / "plugins",
        )
    registry = PluginRegistry(plugins_dir, no_plugins="--no-plugins" in sys.argv[1:])
    classes = registry.discover_classes_only()
    from kollabor.panels.config import _field, read_managed_state
    from kollabor_config.loader import ConfigLoader
    from kollabor_config.manager import ConfigManager

    config = ConfigManager(Path.home() / ".kollab" / "config.json")
    config.config = ConfigLoader(config, registry, fast_mode=True).load_complete_config()
    managed, global_settings = read_managed_state()
    entries: list[dict] = []
    for cls in classes:
        if cls.__module__.startswith("_kollab_package_"):
            continue
        defaults = registry.discovery.plugin_configs.get(cls.__name__, {})
        plugin_defaults = defaults.get("plugins", {}) if isinstance(defaults, dict) else {}
        if isinstance(plugin_defaults, dict) and plugin_defaults:
            plugin_id = next(iter(plugin_defaults))
        else:
            plugin_id = getattr(cls, "name", cls.__name__.removesuffix("Plugin").lower())
        if not isinstance(plugin_id, str):
            plugin_id = cls.__name__.removesuffix("Plugin").lower()
        try:
            section = cls.get_config_widgets() if callable(getattr(cls, "get_config_widgets", None)) else None
        except Exception as exc:
            logger.warning("Cannot describe plugin %s settings: %s", plugin_id, exc)
            section = None
        default_enabled = (
            plugin_defaults.get(plugin_id, {}).get("enabled", True)
            if isinstance(plugin_defaults.get(plugin_id), dict)
            else True
        )
        enabled = config.get(f"plugins.{plugin_id}.enabled", default_enabled)
        widgets = section.get("widgets", []) if isinstance(section, dict) else []
        fields = [{**row, **_field(row, config, managed, global_settings)} for row in widgets if isinstance(row, dict)]
        entries.append(
            {
                "id": plugin_id,
                "name": getattr(cls, "display_name", None)
                or " ".join("MCP" if word.lower() == "mcp" else word.capitalize() for word in plugin_id.split("_")),
                "version": None,
                "kind": "built-in",
                "source": inspect.getfile(cls),
                "commit": None,
                "enabled": enabled,
                "enable_order": None,
                "shadowed": False,
                "state": "ok" if enabled else "off",
                "error": None,
                "settings": fields,
                "settings_section": section,
                "web": None,
                "contributes": {},
                "services": [],
                "uses": [],
                "granted": [],
            }
        )
    home = Path.home() / ".kollab"
    try:
        data = _read_registry(home / "plugins.json")
    except ValueError as exc:
        logger.warning("Cannot list installed plugins: %s", exc)
        return [normalized for entry in entries if (normalized := _normalize_plugin_entry(entry)) is not None]
    enable_order = data.get("enable_order", {})
    if not isinstance(enable_order, dict):
        enable_order = {}

    def order_for(plugin_id: str) -> int | None:
        value = enable_order.get(plugin_id)
        return value if type(value) is int and 0 < value <= _MAX_ENABLE_ORDER else None

    for plugin_id, record in data["plugins"].items():
        if not isinstance(plugin_id, str) or not _ID.fullmatch(plugin_id) or not isinstance(record, dict):
            continue
        root = home / "plugins" / plugin_id
        manifest: dict = {}
        error = None
        state = "off" if not record.get("enabled") else "ok"
        settings = []
        try:
            if files_sha256(root) != record.get("files_sha256"):
                state = "changed-on-disk"
                error = "Plugin files changed since approval"
            manifest = json.loads((root / "kollab-plugin.json").read_text(encoding="utf-8"))
            if not isinstance(manifest, dict):
                raise ValueError("Invalid manifest")
            from .settings import setting_widgets

            settings = setting_widgets(root, manifest)
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("Failed to load plugin metadata for %s", plugin_id, exc_info=exc)
            state, error = "error", "Plugin metadata could not be loaded"
        if plugin_id in registry.package_errors and state == "ok":
            state, error = "error", registry.package_errors[plugin_id]
        service_entries = _service_entries(home, plugin_id, manifest)
        if state in {"ok", "off"}:
            if any(service["state"] == "stopping" for service in service_entries):
                state = "stopping"
            elif any(service["state"] == "stop-failed" for service in service_entries):
                state = "stop-failed"
        entries.append(
            {
                "id": plugin_id,
                "name": manifest.get("name", plugin_id),
                "version": manifest.get("version"),
                "kind": "installed",
                "source": record.get("source"),
                "commit": record.get("commit"),
                "enabled": record.get("enabled", False),
                "enable_order": order_for(plugin_id),
                "shadowed": plugin_id in data["dev"],
                "state": state,
                "error": error,
                "settings": _settings_fields(plugin_id, settings),
                "web": _web_entry(root, manifest),
                "contributes": manifest.get("contributes", {}),
                "services": service_entries,
                "omitted": {"services": _omitted_service_count(manifest)},
                "uses": manifest.get("uses", []),
                "granted": record.get("granted", []),
            }
        )
    for plugin_id, folder in data["dev"].items():
        if not isinstance(plugin_id, str) or not _ID.fullmatch(plugin_id) or not isinstance(folder, str):
            continue
        root = Path(folder)
        manifest = {}
        dev_settings = []
        granted = []
        error = registry.package_errors.get(plugin_id)
        try:
            from .services import _approved_service_root

            _, approval = _approved_service_root(home, data, f"{plugin_id}@dev", stopping=True)
            if isinstance(approval.get("granted"), list):
                granted = approval["granted"]
        except (OSError, ValueError):
            pass
        try:
            manifest = json.loads((Path(folder) / "kollab-plugin.json").read_text(encoding="utf-8"))
            if not isinstance(manifest, dict):
                raise ValueError("Invalid development manifest")
            from .settings import setting_widgets

            dev_settings = setting_widgets(Path(folder), manifest)
        except (OSError, ValueError) as exc:
            logger.warning("Failed to load development plugin metadata for %s", plugin_id, exc_info=exc)
            manifest, error = {}, "Development plugin metadata could not be loaded"
        service_entries = _service_entries(home, f"{plugin_id}@dev", manifest)
        state = "error" if error else "ok"
        if state == "ok":
            if any(service["state"] == "stopping" for service in service_entries):
                state = "stopping"
            elif any(service["state"] == "stop-failed" for service in service_entries):
                state = "stop-failed"
        entries.append(
            {
                "id": f"{plugin_id}@dev",
                "enable_order": order_for(f"{plugin_id}@dev"),
                "name": f"{manifest.get('name', plugin_id)}@dev",
                "version": manifest.get("version"),
                "kind": "dev",
                "source": folder,
                "commit": None,
                "enabled": True,
                "shadowed": False,
                "state": state,
                "error": error,
                "settings": _settings_fields(f"{plugin_id}@dev", dev_settings),
                "web": _web_entry(root, manifest),
                "contributes": manifest.get("contributes", {}),
                "services": service_entries,
                "omitted": {"services": _omitted_service_count(manifest)},
                "uses": manifest.get("uses", []),
                "granted": granted,
            }
        )
    normalized = [_normalize_plugin_entry(entry) for entry in entries]
    return [entry for entry in normalized if entry is not None]
