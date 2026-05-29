"""Configuration service providing high-level configuration operations."""

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

try:
    from watchdog.events import FileSystemEventHandler  # type: ignore[import-not-found]
    from watchdog.observers import Observer  # type: ignore[import-not-found]

    WATCHDOG_AVAILABLE = True
except ImportError:
    WATCHDOG_AVAILABLE = False
    Observer = None
    FileSystemEventHandler = None

from .loader import ConfigLoader
from .manager import ConfigManager
from .config_utils import get_global_config_path, get_local_config_path

logger = logging.getLogger(__name__)


if WATCHDOG_AVAILABLE:

    class ConfigFileWatcher(FileSystemEventHandler):
        """File system event handler for configuration file changes."""

        def __init__(self, config_service: "ConfigService"):
            super().__init__()
            self.config_service = config_service
            self.last_modified = 0
            self.debounce_delay = 0.5  # 500ms debounce

        def on_modified(self, event):
            """Handle file modification events."""
            if event.is_directory:
                return

            if event.src_path == str(self.config_service.config_manager.config_path):
                current_time = time.time()

                # Skip if we wrote the file ourselves (within 2s window)
                if current_time - self.config_service._last_self_write < 2.0:
                    logger.debug("Ignoring config change from self-write")
                    return

                # Debounce rapid file changes
                if current_time - self.last_modified > self.debounce_delay:
                    self.last_modified = current_time
                    logger.info("Configuration file changed, triggering reload")
                    # Schedule the reload in a thread-safe way
                    try:
                        loop = asyncio.get_running_loop()
                        loop.call_soon_threadsafe(
                            lambda: asyncio.create_task(
                                self.config_service._handle_file_change()
                            )
                        )
                    except RuntimeError:
                        # No event loop running, fall back to sync reload
                        logger.warning(
                            "No event loop available, performing synchronous reload"
                        )
                        self.config_service.reload()

else:

    class ConfigFileWatcher:  # type: ignore[no-redef]
        """Stub class when watchdog is not available."""

        def __init__(self, config_service: "ConfigService"):
            pass


class ConfigService:
    """High-level configuration service providing a clean API.

    This service coordinates between the file-based ConfigManager and
    the plugin-aware ConfigLoader to provide a simple interface for
    all configuration operations.
    """

    def __init__(
        self, config_path: Path, plugin_registry=None, fast_mode: bool = False
    ):
        """Initialize the configuration service.

        Args:
            config_path: Path to the configuration file.
            plugin_registry: Optional plugin registry for plugin configs.
            fast_mode: If True, skip expensive operations like system prompt loading.
        """
        self.config_manager = ConfigManager(config_path)
        self.config_loader = ConfigLoader(
            self.config_manager, plugin_registry, fast_mode=fast_mode
        )
        self.plugin_registry = plugin_registry

        # Cached configuration for fallback
        self._cached_config: Optional[Dict[str, Any]] = None
        self._config_error: Optional[str] = None
        self._reload_callbacks: list = []

        # Self-write tracking (prevents file watcher from reacting to our own saves)
        self._last_self_write: float = 0

        # File watching setup
        self._file_watcher: Any = None
        self._observer: Any = None

        # Load initial configuration
        self._initialize_config()

        # Start file watching if successful
        self._start_file_watching()

        logger.info(f"Configuration service initialized: {config_path}")

    def _initialize_config(self) -> None:
        """Initialize configuration on service startup."""
        try:
            if self.config_manager.config_path.exists():
                # Load existing config and merge with defaults
                complete_config = self.config_loader.load_complete_config()
                self.config_manager.config = complete_config
                self._cached_config = complete_config.copy()
                self._config_error = None
                self._migrate_config_if_needed()
                self._cached_config = self.config_manager.config.copy()
                logger.info("Loaded and merged existing configuration")
            else:
                # Create new config with defaults and plugin configs
                complete_config = self.config_loader.load_complete_config()
                self.config_manager.config = complete_config
                self._cached_config = complete_config.copy()
                self.config_loader.save_merged_config(complete_config)
                self._migrate_config_if_needed()
                self._cached_config = self.config_manager.config.copy()
                self._config_error = None
                logger.info("Created new configuration file")
        except Exception as e:
            self._config_error = str(e)
            logger.error(f"Failed to initialize configuration: {e}")
            if self._cached_config:
                logger.warning("Using cached configuration as fallback")
                self.config_manager.config = self._cached_config
            else:
                # Use minimal base config as last resort
                base_config = self.config_loader.get_base_config()
                self.config_manager.config = base_config
                self._cached_config = base_config.copy()
                logger.warning("Using base configuration as fallback")

    def _migrate_config_if_needed(self) -> None:
        """Fill missing config keys for the current app version.

        Migrations are additive only: an explicit user value, including
        ``False``, is preserved. The stored config version tracks the app
        version so new releases can add missing keys without rewriting a
        user's chosen settings.
        """
        import json

        from kollabor_events.dict_utils import safe_get, safe_set

        try:
            from kollabor.version import __version__ as app_version
        except Exception:
            app_version = "unknown"

        missing = object()
        save_path = get_global_config_path()
        try:
            if save_path.exists():
                raw = json.loads(save_path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    raw = {}
            else:
                raw = {}
        except Exception as e:
            logger.warning("Could not read config for migration: %s", e)
            raw = {}

        additive_defaults = {
            "plugins.hub.enabled": True,
            "plugins.hub.project_scoped": True,
            "plugins.context_service.hub_broadcast_enabled": True,
            "kollabor.updates.check_enabled": True,
            "kollabor.updates.auto_update_enabled": False,
            "kollabor.updates.check_interval_hours": 24,
            "kollabor.updates.github_repo": "kollaborai/kollab",
            "kollabor.updates.timeout_seconds": 5,
            "kollabor.updates.include_prereleases": False,
            "kollabor.llm.default_agent": {
                "name": "koordinator",
                "level": "global",
            },
        }

        changed = False
        for key_path, default_value in additive_defaults.items():
            if safe_get(raw, key_path, missing) is missing:
                safe_set(raw, key_path, default_value)
                safe_set(self.config_manager.config, key_path, default_value)
                changed = True

        if safe_get(raw, "config_version", None) != app_version:
            safe_set(raw, "config_version", app_version)
            safe_set(self.config_manager.config, "config_version", app_version)
            changed = True
        if safe_get(raw, "last_app_version", None) != app_version:
            safe_set(raw, "last_app_version", app_version)
            safe_set(self.config_manager.config, "last_app_version", app_version)
            changed = True

        if not changed:
            return

        try:
            save_path.parent.mkdir(parents=True, exist_ok=True)
            self._last_self_write = time.time()
            save_path.write_text(
                json.dumps(raw, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            logger.info("Migrated config defaults for app version %s", app_version)
        except Exception as e:
            logger.error("Failed to write migrated config: %s", e)

    def get(self, key_path: str, default: Any = None) -> Any:
        """Get a configuration value using dot notation.

        Args:
            key_path: Dot-separated path to the config value.
            default: Default value if key not found.

        Returns:
            Configuration value or default.
        """
        return self.config_manager.get(key_path, default)

    def set(self, key_path: str, value: Any) -> bool:
        """Set a configuration value using dot notation (in-memory only).

        CRITICAL: This only updates the in-memory config. Call save_config()
        to persist changes to disk. This prevents multiple file writes when
        setting multiple values (e.g., when saving modal changes).

        Args:
            key_path: Dot-separated path to the config value.
            value: Value to set.

        Returns:
            True if set successful, False otherwise.
        """
        from kollabor_events.dict_utils import safe_set

        if safe_set(self.config_manager.config, key_path, value):
            logger.debug(f"Configuration updated in memory: {key_path}")
            return True

        logger.error(f"Failed to set config key: {key_path}")
        return False

    def save_config(self, save_target: Optional[str] = None) -> bool:
        """Persist in-memory config changes to disk.

        Saves to the appropriate location based on the layered config system:
        1. Local .kollab/config.json (if local config exists)
        2. Project config (if exists)
        3. Global config (fallback)

        Args:
            save_target: Optional target override ("local" or "global").
                         None uses existing auto-detect behavior.

        Returns:
            True if save successful, False otherwise.
        """
        self._last_self_write = time.time()
        success = self.config_loader.save_merged_config(
            self.config_manager.config, save_target=save_target
        )
        if success:
            logger.debug("Configuration saved to disk")
            return True

        logger.error("Failed to save configuration to disk")
        return False

    def save_key(
        self, key_path: str, value: Any, save_target: Optional[str] = None
    ) -> bool:
        """Persist a single config key to disk without touching anything else.

        Reads the existing config file, patches in the one key, writes it
        back.  Never computes diffs, never dumps the full in-memory config.
        This is the SAFE way to persist a setting from plugin code.

        Also updates the in-memory config so callers don't need a separate
        set() call.

        Args:
            key_path: Dot-separated path (e.g. "plugins.hub.user_name").
            value: Value to persist.
            save_target: "local", "global", or None (auto-detect).

        Returns:
            True if saved, False on error.
        """
        import json

        from kollabor_events.dict_utils import safe_set

        # 1. Update in-memory config
        safe_set(self.config_manager.config, key_path, value)

        # 2. Determine file path
        if save_target == "local":
            save_path = get_local_config_path()
        elif save_target == "global":
            save_path = get_global_config_path()
        else:
            save_path = self.config_loader._get_config_save_path()

        # 3. Read existing file (or start empty)
        existing: Dict[str, Any] = {}
        if save_path.exists():
            try:
                with open(save_path) as f:
                    existing = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Could not read {save_path}, starting fresh: {e}")

        # 4. Patch the single key into the existing dict
        safe_set(existing, key_path, value)

        # 5. Write back
        save_path.parent.mkdir(parents=True, exist_ok=True)
        self._last_self_write = time.time()
        try:
            with open(save_path, "w") as f:
                json.dump(existing, f, indent=2, ensure_ascii=False)
            logger.info(f"Saved {key_path} to {save_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to save {key_path} to {save_path}: {e}")
            return False

    def reload(self) -> bool:
        """Reload configuration from file and plugins.

        Returns:
            True if reload successful, False otherwise.
        """
        try:
            complete_config = self.config_loader.load_complete_config()

            # Validate the new configuration
            old_config = self.config_manager.config
            self.config_manager.config = complete_config
            validation_result = self.validate_config()

            if validation_result["valid"]:
                # Success - update cache and clear error
                self._cached_config = complete_config.copy()
                self._config_error = None
                logger.info("Configuration reloaded successfully")
                self._notify_reload_callbacks()
                return True
            else:
                # Validation failed - revert to cached config
                self.config_manager.config = old_config
                error_msg = f"Invalid configuration: {validation_result['errors']}"
                self._config_error = error_msg
                logger.error(error_msg)
                return False

        except Exception as e:
            error_msg = f"Failed to reload configuration: {e}"
            self._config_error = error_msg
            logger.error(error_msg)

            # Fallback to cached config if available
            if self._cached_config:
                logger.warning("Using cached configuration as fallback")
                self.config_manager.config = self._cached_config

            return False

    def update_from_plugins(self) -> bool:
        """Update configuration with newly discovered plugins.

        Returns:
            True if update successful, False otherwise.
        """
        self._last_self_write = time.time()
        return self.config_loader.update_with_plugins()

    def get_config_summary(self) -> Dict[str, Any]:
        """Get a summary of the current configuration.

        Returns:
            Dictionary with configuration metadata.
        """
        config = self.config_manager.config
        plugin_count = (
            len(self.plugin_registry.list_plugins()) if self.plugin_registry else 0
        )

        return {
            "config_file": str(self.config_manager.config_path),
            "file_exists": self.config_manager.config_path.exists(),
            "plugin_count": plugin_count,
            "config_sections": list(config.keys()) if config else [],
            "total_keys": self._count_keys(config),
        }

    def _count_keys(self, config: Dict[str, Any]) -> int:
        """Recursively count all keys in configuration."""
        count = 0
        for key, value in config.items():
            count += 1
            if isinstance(value, dict):
                count += self._count_keys(value)
        return count

    def validate_config(self) -> Dict[str, Any]:
        """Validate current configuration structure.

        Returns:
            Dictionary with validation results.
        """
        validation_result: Dict[str, Any] = {
            "valid": True,
            "errors": [],
            "warnings": [],
        }

        config = self.config_manager.config

        # Check for required sections
        required_sections = ["terminal", "input", "logging", "application"]
        for section in required_sections:
            if section not in config:
                validation_result["errors"].append(
                    f"Missing required section: {section}"
                )
                validation_result["valid"] = False

        # Check for required terminal settings
        if "terminal" in config:
            required_terminal_keys = ["render_fps", "thinking_effect"]
            for key in required_terminal_keys:
                if key not in config["terminal"]:
                    validation_result["warnings"].append(
                        f"Missing terminal.{key}, using default"
                    )

        # Check for valid FPS value
        fps = self.get("terminal.render_fps")
        if fps is not None and (not isinstance(fps, int) or fps <= 0 or fps > 120):
            validation_result["warnings"].append(
                f"Invalid render_fps: {fps}, should be 1-120"
            )

        logger.debug(f"Configuration validation: {validation_result}")
        return validation_result

    def backup_config(self, backup_suffix: str = ".backup") -> Optional[Path]:
        """Create a backup of the current configuration file.

        Args:
            backup_suffix: Suffix to add to backup filename.

        Returns:
            Path to backup file if successful, None otherwise.
        """
        if not self.config_manager.config_path.exists():
            logger.warning("Cannot backup non-existent config file")
            return None

        try:
            backup_path = self.config_manager.config_path.with_suffix(
                self.config_manager.config_path.suffix + backup_suffix
            )

            import shutil

            shutil.copy2(self.config_manager.config_path, backup_path)

            logger.info(f"Configuration backed up to: {backup_path}")
            return backup_path

        except Exception as e:
            logger.error(f"Failed to backup configuration: {e}")
            return None

    def restore_from_backup(self, backup_path: Path) -> bool:
        """Restore configuration from a backup file.

        Args:
            backup_path: Path to backup file.

        Returns:
            True if restore successful, False otherwise.
        """
        if not backup_path.exists():
            logger.error(f"Backup file does not exist: {backup_path}")
            return False

        try:
            import shutil

            shutil.copy2(backup_path, self.config_manager.config_path)

            # Reload configuration after restore
            return self.reload()

        except Exception as e:
            logger.error(f"Failed to restore from backup: {e}")
            return False

    def _start_file_watching(self) -> None:
        """Start watching the configuration file for changes."""
        if not WATCHDOG_AVAILABLE:
            logger.debug("Watchdog not available, file watching disabled")
            return

        # Prevent duplicate watchers
        if self._observer is not None:
            logger.debug("File watcher already running, skipping initialization")
            return

        try:
            self._file_watcher = ConfigFileWatcher(self)
            self._observer = Observer()
            self._observer.schedule(
                self._file_watcher,
                str(self.config_manager.config_path.parent),
                recursive=False,
            )
            self._observer.start()
            logger.debug("Configuration file watcher started")
        except RuntimeError as e:
            if "already scheduled" in str(e):
                logger.debug(
                    "File watcher path already being watched by another instance"
                )
            else:
                logger.warning(f"Could not start configuration file watcher: {e}")
        except Exception as e:
            logger.warning(f"Could not start configuration file watcher: {e}")

    def _stop_file_watching(self) -> None:
        """Stop watching the configuration file."""
        if self._observer:
            self._observer.stop()
            self._observer.join()
            self._observer = None
            self._file_watcher = None
            logger.debug("Configuration file watcher stopped")

    async def _handle_file_change(self) -> None:
        """Handle configuration file changes with hot reload."""
        success = self.reload()
        if not success:
            logger.warning("Configuration reload failed, using cached fallback")

    def register_reload_callback(self, callback: Callable[[], None]) -> None:
        """Register a callback to be notified when configuration reloads.

        Args:
            callback: Function to call after successful configuration reload.
        """
        self._reload_callbacks.append(callback)

    def _notify_reload_callbacks(self) -> None:
        """Notify all registered callbacks about configuration reload."""
        for callback in self._reload_callbacks:
            try:
                callback()
            except Exception as e:
                logger.error(f"Config reload callback failed: {e}")

    def get_config_error(self) -> Optional[str]:
        """Get the current configuration error, if any.

        Returns:
            Error message string if there's a config error, None otherwise.
        """
        return self._config_error

    def has_config_error(self) -> bool:
        """Check if there's a current configuration error.

        Returns:
            True if there's an error, False otherwise.
        """
        return self._config_error is not None

    def shutdown(self) -> None:
        """Shutdown the configuration service and file watcher."""
        self._stop_file_watching()
        logger.info("Configuration service shutdown")
