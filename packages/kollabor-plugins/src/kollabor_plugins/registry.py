"""Plugin registry system for Kollab."""

import hashlib
import importlib.util
import inspect
import json
import logging
import sys
import types
from pathlib import Path
from typing import Any, Dict, List, Sequence, Type

from kollabor_events.dict_utils import deep_merge

from .base import BasePlugin
from .collector import PluginStatusCollector
from .discovery import PluginDiscovery
from .factory import PluginFactory
from .packages import _ID, _SYMBOL, _read_registry, _within, files_sha256, recover_installs, validate_manifest

logger = logging.getLogger(__name__)

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
                try:
                    sys.dont_write_bytecode = True
                    spec.loader.exec_module(module)
                finally:
                    sys.dont_write_bytecode = previous_bytecode
                plugin_class = getattr(module, symbol)
                if not inspect.isclass(plugin_class) or not issubclass(plugin_class, BasePlugin):
                    raise ValueError("Python entry must be a BasePlugin subclass")
                if symbol in self.discovery.loaded_classes:
                    raise ValueError("Python entry class conflicts with another plugin")
                get_config = getattr(plugin_class, "get_default_config", None)
                config = get_config() if callable(get_config) else {}
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
    for name in declared:
        if not isinstance(name, str) or not _SERVICE.fullmatch(name):
            continue
        declaration = declared.get(name)
        state_file = home / "plugin-data" / plugin_id / "run" / f"{name}.state"
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
            if not isinstance(state, dict):
                state = {}
        except OSError, ValueError:
            state = {}
        history = state.get("exit_history", [])
        identity = None
        try:
            recorded = json.loads((state_file.parent / f"{name}.pid").read_text(encoding="utf-8"))
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
        if status == "running" and identity is None:
            status = "unknown"
        result.append(
            {
                "name": name,
                "state": status,
                "desired": state.get("desired", "stopped"),
                "pid": identity.get("pid") if identity else None,
                "since": identity.get("start_time") if identity else None,
                "restarts": len(history) if isinstance(history, list) else 0,
                "heartbeat_s": declaration.get("heartbeat_s") if isinstance(declaration, dict) else None,
                "heartbeat": None,
                "error": state.get("error"),
            }
        )
    return result


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
                "name": getattr(cls, "name", plugin_id),
                "version": None,
                "kind": "built-in",
                "source": inspect.getfile(cls),
                "commit": None,
                "enabled": enabled,
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
        return entries
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
        entries.append(
            {
                "id": plugin_id,
                "name": manifest.get("name", plugin_id),
                "version": manifest.get("version"),
                "kind": "installed",
                "source": record.get("source"),
                "commit": record.get("commit"),
                "enabled": record.get("enabled", False),
                "shadowed": plugin_id in data["dev"],
                "state": state,
                "error": error,
                "settings": _settings_fields(plugin_id, settings),
                "web": _web_entry(root, manifest),
                "contributes": manifest.get("contributes", {}),
                "services": service_entries,
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
        entries.append(
            {
                "id": f"{plugin_id}@dev",
                "name": f"{manifest.get('name', plugin_id)}@dev",
                "version": manifest.get("version"),
                "kind": "dev",
                "source": folder,
                "commit": None,
                "enabled": True,
                "shadowed": False,
                "state": "error" if error else "ok",
                "error": error,
                "settings": _settings_fields(f"{plugin_id}@dev", dev_settings),
                "web": _web_entry(root, manifest),
                "contributes": manifest.get("contributes", {}),
                "services": _service_entries(home, f"{plugin_id}@dev", manifest),
                "uses": manifest.get("uses", []),
                "granted": granted,
            }
        )
    return entries
