"""Plugin discovery for file system scanning and module loading."""

import importlib
import importlib.util
import inspect
import logging
import re
import sys
import types
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

from kollabor_events.error_utils import safe_execute

from .plugin_utils import get_plugin_config_safely, has_method

logger = logging.getLogger(__name__)

# Platform check
IS_WINDOWS = sys.platform == "win32"


class PluginDiscovery:
    """Handles plugin discovery and module loading from the file system.

    This class is responsible for scanning directories for plugin files,
    loading Python modules, and extracting plugin classes and configurations.
    """

    def __init__(self, plugins_dir: Path):
        """Initialize plugin discovery.

        Args:
            plugins_dir: Directory containing plugin modules.
        """
        self.plugins_dir = plugins_dir
        self.discovered_modules: List[str] = []
        self.loaded_classes: Dict[str, Type] = {}
        self.plugin_configs: Dict[str, Dict[str, Any]] = {}

        # Security validation patterns
        self.valid_plugin_name_pattern = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*$")
        self.max_plugin_name_length = 50
        self.blocked_names = {
            "__init__",
            "__pycache__",
            "system",
            "os",
            "sys",
            "subprocess",
            "eval",
            "exec",
            "compile",
            "open",
            "file",
            "input",
            "raw_input",
        }

        logger.info(f"PluginDiscovery initialized with directory: {plugins_dir}")

    def _sanitize_plugin_name(self, plugin_name: str) -> Optional[str]:
        """Sanitize and validate plugin name for security."""
        if not plugin_name:
            logger.warning("Empty plugin name rejected")
            return None

        # Length check
        if len(plugin_name) > self.max_plugin_name_length:
            logger.warning(f"Plugin name too long: {plugin_name}")
            return None

        # Pattern validation (letters, numbers, underscores only)
        if not self.valid_plugin_name_pattern.match(plugin_name):
            logger.warning(f"Invalid plugin name pattern: {plugin_name}")
            return None

        # Block dangerous names
        if plugin_name.lower() in self.blocked_names:
            logger.warning(f"Blocked plugin name: {plugin_name}")
            return None

        # Block path traversal attempts
        if ".." in plugin_name or "/" in plugin_name or "\\" in plugin_name:
            logger.warning(f"Path traversal attempt in plugin name: {plugin_name}")
            return None

        # Block shell metacharacters
        if any(char in plugin_name for char in [";", "&", "|", "`", "$", '"', "'"]):
            logger.warning(f"Shell metacharacters in plugin name: {plugin_name}")
            return None

        return plugin_name

    def _verify_plugin_location(self, plugin_path: str) -> bool:
        """Verify plugin file or directory exists in expected location.

        Args:
            plugin_path: Plugin path relative to plugins dir (e.g., 'fullscreen.matrix_plugin')
        """
        try:
            plugins_dir = self.plugins_dir.resolve()

            # Convert module path to file path (e.g., 'fullscreen.matrix_plugin' -> 'fullscreen/matrix_plugin')
            file_path_parts = plugin_path.split(".")

            # Check for file-based plugin (e.g., fullscreen/matrix_plugin.py)
            plugin_file = (
                self.plugins_dir
                / "/".join(file_path_parts[:-1])
                / f"{file_path_parts[-1]}.py"
            )
            if plugin_file.exists():
                plugin_file = plugin_file.resolve()

                # Verify it's within the plugins directory
                if not str(plugin_file).startswith(str(plugins_dir)):
                    logger.error(
                        f"Plugin file outside plugins directory: {plugin_file}"
                    )
                    return False

                # Verify file exists and is a regular file
                if not plugin_file.is_file():
                    logger.error(f"Plugin file not found: {plugin_file}")
                    return False

                # Additional security: check file permissions (Unix only)
                if not IS_WINDOWS:
                    if plugin_file.stat().st_mode & 0o777 != 0o644:
                        logger.warning(
                            f"Plugin file has unusual permissions: {plugin_file}"
                        )

                return True

            # Check for directory-based plugin (plugin_name/__init__.py)
            plugin_dir = self.plugins_dir / "/".join(file_path_parts)
            if plugin_dir.exists() and plugin_dir.is_dir():
                plugin_dir = plugin_dir.resolve()

                # Verify it's within the plugins directory
                if not str(plugin_dir).startswith(str(plugins_dir)):
                    logger.error(
                        f"Plugin directory outside plugins directory: {plugin_dir}"
                    )
                    return False

                # Verify __init__.py exists
                init_file = plugin_dir / "__init__.py"
                if not init_file.exists():
                    logger.error(f"Plugin directory missing __init__.py: {plugin_dir}")
                    return False

                return True

            logger.error(f"Plugin not found as file or directory: {plugin_path}")
            return False

        except Exception as e:
            logger.error(f"Error verifying plugin location: {e}")
            return False

    def _verify_loaded_module(self, module, module_path: str) -> bool:
        """Verify the loaded module is actually our plugin.

        Args:
            module: The loaded module object
            module_path: Module path (e.g., 'fullscreen.matrix_plugin')
        """
        try:
            # Check module name matches (plugins.fullscreen.matrix_plugin)
            expected_module_name = f"plugins.{module_path}"
            if module.__name__ != expected_module_name:
                logger.error(
                    f"Module name mismatch: {module.__name__} != {expected_module_name}"
                )
                return False

            # Check module file location
            if hasattr(module, "__file__"):
                module_file = Path(module.__file__).resolve()
                plugins_dir = self.plugins_dir.resolve()

                if not str(module_file).startswith(str(plugins_dir)):
                    logger.error(
                        f"Module file outside plugins directory: {module_file}"
                    )
                    return False

            # Verify module has expected plugin attributes
            if not hasattr(module, "__dict__"):
                logger.error(f"Module {module_path} has no __dict__ attribute")
                return False

            return True

        except Exception as e:
            logger.error(f"Error verifying loaded module {module_path}: {e}")
            return False

    def _module_is_from_plugins_dir(self, module) -> bool:
        """Return whether an imported module belongs to this discovery root."""
        try:
            plugins_dir = self.plugins_dir.resolve()
            module_file = getattr(module, "__file__", None)
            if module_file:
                return Path(module_file).resolve().is_relative_to(plugins_dir)

            module_paths = getattr(module, "__path__", [])
            for module_path in module_paths:
                resolved_path = Path(module_path).resolve()
                if resolved_path == plugins_dir or resolved_path.is_relative_to(
                    plugins_dir
                ):
                    return True
        except Exception as e:
            logger.debug(f"Could not verify plugin module root: {e}")

        return False

    def _drop_stale_module_cache(self, module_path: str) -> None:
        """Drop cached modules for this plugin path when they came from another root."""
        parts = ["plugins", *module_path.split(".")]
        stale_roots: list[str] = []

        for index in range(2, len(parts) + 1):
            module_name = ".".join(parts[:index])
            module = sys.modules.get(module_name)
            if module and not self._module_is_from_plugins_dir(module):
                stale_roots.append(module_name)

        if not stale_roots:
            return

        for module_name in list(sys.modules):
            if any(
                module_name == stale_root or module_name.startswith(f"{stale_root}.")
                for stale_root in stale_roots
            ):
                del sys.modules[module_name]

    def _prepare_plugins_import_root(self, module_path: str | None = None) -> None:
        """Make import plugins.* resolve from this discovery root."""
        try:
            plugins_dir = self.plugins_dir.resolve()

            current_plugins = sys.modules.get("plugins")
            if current_plugins and hasattr(current_plugins, "__path__"):
                root_path = str(plugins_dir)
                search_paths = [
                    str(Path(path).resolve())
                    for path in getattr(current_plugins, "__path__", [])
                    if Path(path).resolve() != plugins_dir
                ]
                current_plugins.__path__ = [root_path, *search_paths]  # type: ignore[attr-defined]
                if module_path:
                    self._drop_stale_module_cache(module_path)
                importlib.invalidate_caches()
                return

            init_file = plugins_dir / "__init__.py"
            if init_file.exists():
                spec = importlib.util.spec_from_file_location(
                    "plugins",
                    init_file,
                    submodule_search_locations=[str(plugins_dir)],
                )
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    sys.modules["plugins"] = module
                    spec.loader.exec_module(module)
            else:
                module = types.ModuleType("plugins")
                module.__file__ = str(plugins_dir)
                module.__path__ = [str(plugins_dir)]  # type: ignore[attr-defined]
                sys.modules["plugins"] = module

            if module_path:
                self._drop_stale_module_cache(module_path)
            importlib.invalidate_caches()
        except Exception as e:
            logger.debug(f"Could not prepare plugin import root: {e}")

    def scan_plugin_files(self) -> List[str]:
        """Scan the plugins directory recursively for plugin files with security validation.

        Returns:
            List of discovered plugin module paths (e.g., ['plugin_name', 'fullscreen.matrix_plugin']).
        """
        discovered: List[str] = []

        if not self.plugins_dir.exists():
            logger.warning(f"Plugins directory does not exist: {self.plugins_dir}")
            return discovered

        # Resolve plugins directory to prevent symlink attacks
        try:
            plugins_dir = self.plugins_dir.resolve()

            # Verify directory permissions (Unix only - Windows doesn't use these bits)
            if not IS_WINDOWS:
                if plugins_dir.stat().st_mode & 0o002:
                    logger.error(f"Plugins directory is world-writable: {plugins_dir}")
                    return discovered

        except Exception as e:
            logger.error(f"Error resolving plugins directory: {e}")
            return discovered

        # Recursively scan for *_plugin.py files
        for plugin_file in plugins_dir.rglob("*_plugin.py"):
            try:
                # Calculate relative path from plugins directory
                rel_path = plugin_file.relative_to(plugins_dir)

                # Skip if in __pycache__ or other special directories
                if any(
                    part.startswith(("_", ".")) and part != plugin_file.stem + ".py"
                    for part in rel_path.parts[:-1]
                ):
                    continue

                # Convert file path to module path
                # e.g., fullscreen/matrix_plugin.py -> fullscreen.matrix_plugin
                module_parts = list(rel_path.parts[:-1]) + [rel_path.stem]
                module_path = ".".join(module_parts)

                # Validate each part of the path
                all_parts_valid = True
                for part in module_parts:
                    if not self._sanitize_plugin_name(part):
                        logger.warning(f"Invalid path component in plugin: {part}")
                        all_parts_valid = False
                        break

                if not all_parts_valid:
                    continue

                # Verify plugin location
                if not self._verify_plugin_location(module_path):
                    logger.warning(
                        f"Plugin location verification failed: {module_path}"
                    )
                    continue

                discovered.append(module_path)
                logger.debug(f"Discovered valid plugin file: {module_path}")

            except Exception as e:
                logger.error(f"Error processing plugin file {plugin_file}: {e}")
                continue

        # Recursively scan for directory-based plugins (packages with __init__.py)
        for plugin_dir in plugins_dir.rglob("*/__init__.py"):
            try:
                # Get the directory containing __init__.py
                dir_path = plugin_dir.parent

                # Calculate relative path from plugins directory
                rel_path = dir_path.relative_to(plugins_dir)

                # Skip special directories
                if any(part.startswith(("_", ".")) for part in rel_path.parts):
                    continue

                # Convert directory path to module path
                # e.g., fullscreen -> fullscreen
                module_path = ".".join(rel_path.parts)

                # Validate each part of the path
                all_parts_valid = True
                for part in rel_path.parts:
                    if not self._sanitize_plugin_name(part):
                        logger.warning(
                            f"Invalid path component in plugin package: {part}"
                        )
                        all_parts_valid = False
                        break

                if not all_parts_valid:
                    continue

                # Skip if already discovered (file-based plugin takes precedence)
                if module_path in discovered:
                    continue

                # Verify plugin location
                if not self._verify_plugin_location(module_path):
                    logger.warning(
                        f"Plugin package location verification failed: {module_path}"
                    )
                    continue

                discovered.append(module_path)
                logger.debug(f"Discovered valid plugin package: {module_path}")

            except Exception as e:
                logger.error(f"Error processing plugin directory {plugin_dir}: {e}")
                continue

        self.discovered_modules = discovered
        logger.info(
            f"Discovered {len(discovered)} validated plugin modules (including subdirectories)"
        )
        return discovered

    def load_module(self, module_path: str) -> bool:
        """Load a single plugin module and extract plugin classes.

        Args:
            module_path: Path of the plugin module to load (e.g., 'fullscreen.matrix_plugin').

        Returns:
            True if module loaded successfully, False otherwise.
        """
        # Validate each component of the module path
        path_parts = module_path.split(".")
        for part in path_parts:
            if not self._sanitize_plugin_name(part):
                logger.error(
                    f"Invalid plugin path component rejected: {part} in {module_path}"
                )
                return False

        # Verify plugin location again for safety
        if not self._verify_plugin_location(module_path):
            logger.error(
                f"Plugin location verification failed during loading: {module_path}"
            )
            return False

        def _import_and_extract():
            # Import the plugin module with security validation
            full_module_path = f"plugins.{module_path}"

            try:
                self._prepare_plugins_import_root(module_path)
                module = importlib.import_module(full_module_path)
            except ImportError as e:
                logger.error(f"Failed to import plugin module {module_path}: {e}")
                raise
            except Exception as e:
                logger.error(
                    f"Unexpected error importing plugin module {module_path}: {e}"
                )
                raise

            # Verify the loaded module is actually our plugin
            if not self._verify_loaded_module(module, module_path):
                raise ValueError(f"Module verification failed: {module_path}")

            # Find classes that look like plugins (end with 'Plugin')
            found_plugins = False
            for name, obj in inspect.getmembers(module, inspect.isclass):
                # Skip the base class itself (imported into every plugin module)
                if name == "BasePlugin":
                    continue
                # Skip example/demo plugins
                if name == "ExampleContextPlugin":
                    continue
                if name.endswith("Plugin") and has_method(obj, "get_default_config"):
                    # Store the plugin class
                    self.loaded_classes[name] = obj

                    # Get and store plugin configuration
                    config = get_plugin_config_safely(obj)
                    self.plugin_configs[name] = config

                    if config:
                        logger.info(
                            f"Loaded plugin class: {name} (from {module_path}) with config keys: {list(config.keys())}"
                        )
                    else:
                        logger.info(
                            f"Loaded plugin class: {name} (from {module_path}) with no configuration"
                        )

                    found_plugins = True

            return found_plugins

        result = safe_execute(
            _import_and_extract,
            f"loading plugin module {module_path}",
            default=False,
            logger_instance=logger,
        )

        return result

    def load_all_modules(self) -> int:
        """Load all discovered plugin modules.

        Returns:
            Number of successfully loaded plugin classes.
        """
        initial_count = len(self.loaded_classes)

        for module_name in self.discovered_modules:
            self.load_module(module_name)

        loaded_count = len(self.loaded_classes) - initial_count
        logger.info(
            f"Loaded {loaded_count} plugin classes from {len(self.discovered_modules)} modules"
        )

        return loaded_count

    def discover_and_load(self) -> Dict[str, Type]:
        """Perform complete discovery and loading process.

        Returns:
            Dictionary mapping plugin names to their classes.
        """
        # Scan for plugin files
        self.scan_plugin_files()

        # Load all discovered modules
        self.load_all_modules()

        logger.info(f"Discovery complete: {len(self.loaded_classes)} plugins loaded")
        return self.loaded_classes

    def discover_classes_only(self) -> List[Type]:
        """Lightweight discovery that only loads plugin classes.

        Does NOT instantiate plugins. Used for CLI arg registration
        before app initialization. This allows plugins to register
        custom CLI arguments without full plugin initialization.

        Returns:
            List of plugin class types.

        Note:
            This is a minimal discovery that loads modules and extracts
            plugin classes but does not perform full instantiation or
            configuration merging.
        """
        # Scan for plugin files
        self.scan_plugin_files()

        plugin_classes = []

        for module_name in self.discovered_modules:
            try:
                # Load each module and extract plugin classes
                if self.load_module(module_name):
                    # Get all loaded plugin classes from this module
                    for class_name, class_obj in self.loaded_classes.items():
                        if class_obj not in plugin_classes:
                            plugin_classes.append(class_obj)
            except Exception as e:
                logger.warning(
                    f"Failed to load plugin {module_name} for CLI arg discovery: {e}"
                )

        logger.info(f"CLI arg discovery: {len(plugin_classes)} plugin classes found")
        return plugin_classes

    def get_plugin_class(self, plugin_name: str) -> Type:
        """Get a loaded plugin class by name.

        Args:
            plugin_name: Name of the plugin class.

        Returns:
            Plugin class if found.

        Raises:
            KeyError: If plugin class not found.
        """
        if plugin_name not in self.loaded_classes:
            raise KeyError(f"Plugin class '{plugin_name}' not found")

        return self.loaded_classes[plugin_name]

    def get_plugin_config(self, plugin_name: str) -> Dict[str, Any]:
        """Get configuration for a specific plugin.

        Args:
            plugin_name: Name of the plugin.

        Returns:
            Plugin configuration dictionary, or empty dict if not found.
        """
        return self.plugin_configs.get(plugin_name, {})

    def get_all_configs(self) -> Dict[str, Dict[str, Any]]:
        """Get configurations for all loaded plugins.

        Returns:
            Dictionary mapping plugin names to their configurations.
        """
        return self.plugin_configs.copy()

    def get_discovery_stats(self) -> Dict[str, Any]:
        """Get statistics about the discovery process.

        Returns:
            Dictionary with discovery statistics.
        """
        return {
            "plugins_directory": str(self.plugins_dir),
            "directory_exists": self.plugins_dir.exists(),
            "discovered_modules": len(self.discovered_modules),
            "loaded_classes": len(self.loaded_classes),
            "plugins_with_config": sum(1 for c in self.plugin_configs.values() if c),
            "module_names": self.discovered_modules,
            "class_names": list(self.loaded_classes.keys()),
        }

    def has_plugin_method(self, plugin_name: str, method_name: str) -> bool:
        """Check if a loaded plugin has a specific method.

        Args:
            plugin_name: Name of the plugin class.
            method_name: Name of the method to check for.

        Returns:
            True if plugin has the method, False otherwise.
        """
        if plugin_name not in self.loaded_classes:
            return False

        plugin_class = self.loaded_classes[plugin_name]
        return has_method(plugin_class, method_name)

    def call_plugin_method(
        self, plugin_name: str, method_name: str, *args, **kwargs
    ) -> Any:
        """Safely call a method on a loaded plugin class.

        Args:
            plugin_name: Name of the plugin class.
            method_name: Name of the method to call.
            *args: Positional arguments to pass.
            **kwargs: Keyword arguments to pass.

        Returns:
            Method result or None if method doesn't exist or call failed.
        """
        if plugin_name not in self.loaded_classes:
            logger.warning(
                f"Plugin {plugin_name} not found for method call: {method_name}"
            )
            return None

        plugin_class = self.loaded_classes[plugin_name]

        try:
            if has_method(plugin_class, method_name):
                method = getattr(plugin_class, method_name)
                return method(*args, **kwargs)
            else:
                logger.debug(f"Plugin {plugin_name} has no method: {method_name}")
                return None
        except Exception as e:
            logger.error(f"Failed to call {plugin_name}.{method_name}: {e}")
            return None
