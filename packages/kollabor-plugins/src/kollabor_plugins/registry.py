"""Plugin registry system for Kollab."""

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
from .packages import _ID, _SYMBOL, _read_registry, _within, files_sha256, recover_installs

logger = logging.getLogger(__name__)


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
        self.discoveries = [PluginDiscovery(plugin_dir) for plugin_dir in self.plugin_dirs]
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
            Path.cwd() / ".kollab" / "plugins",
        ]

        resolved_dirs: list[Path] = []
        seen: set[Path] = set()
        for candidate in candidates:
            resolved = candidate.expanduser().resolve()
            if resolved in seen:
                continue
            if candidate == plugins_dir or resolved.exists():
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
        for plugin_id, record in registry["plugins"].items():
            if not isinstance(plugin_id, str) or not _ID.fullmatch(plugin_id):
                continue
            if not isinstance(record, dict) or record.get("enabled") is not True:
                continue
            root = home / "plugins" / plugin_id
            try:
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
        enabled = (
            plugin_defaults.get(plugin_id, {}).get("enabled", True)
            if isinstance(plugin_defaults.get(plugin_id), dict)
            else True
        )
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
                "settings": section.get("widgets", []) if isinstance(section, dict) else [],
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
            if manifest.get("settings"):
                section_path = _within(root.resolve(), manifest["settings"], "settings")
                section = json.loads(section_path.read_text(encoding="utf-8"))
                settings = section.get("widgets", []) if isinstance(section, dict) else []
        except (OSError, ValueError, TypeError) as exc:
            state, error = "error", str(exc)
        if plugin_id in registry.package_errors and state == "ok":
            state, error = "error", registry.package_errors[plugin_id]
        service_entries = []
        declared_services = manifest.get("services", {})
        if isinstance(declared_services, dict):
            for name in declared_services:
                if not isinstance(name, str):
                    continue
                state_file = home / "plugin-data" / plugin_id / "run" / f"{name}.state"
                try:
                    service_state = json.loads(state_file.read_text(encoding="utf-8"))
                except OSError, ValueError:
                    service_state = {}
                service_entries.append(
                    {
                        "name": name,
                        "state": service_state.get("status", "stopped"),
                        "desired": service_state.get("desired", "stopped"),
                        "pid": None,
                        "since": None,
                        "restarts": len(service_state.get("exit_history", [])),
                        "heartbeat": None,
                    }
                )
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
                "settings": settings,
                "web": manifest.get("web"),
                "contributes": manifest.get("contributes", {}),
                "services": service_entries,
                "uses": manifest.get("uses", []),
                "granted": record.get("granted", []),
            }
        )
    return entries
