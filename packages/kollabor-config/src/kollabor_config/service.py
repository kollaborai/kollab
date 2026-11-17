"""Configuration service providing high-level configuration operations."""

import asyncio
import logging
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .atomic_write import keep_unreadable_copy, write_text_atomic
from .config_utils import (
    ProjectStateLocationError,
    get_global_config_path,
    get_global_config_path_candidates,
    get_local_config_path,
    get_local_config_path_candidates,
    get_project_data_dir,
    get_project_data_dir_candidates,
)
from .loader import ConfigLoader
from .manager import ConfigManager

logger = logging.getLogger(__name__)


class ConfigService:
    """High-level configuration service providing a clean API.

    This service coordinates between the file-based ConfigManager and
    the plugin-aware ConfigLoader to provide a simple interface for
    all configuration operations.

    Every process that holds a ConfigService (terminal, attach client, daemon)
    polls the config files' mtimes once a second on its event loop, so a save
    made by any process reaches all of them.
    """

    # Seconds between mtime checks.
    POLL_SECONDS = 1.0

    def __init__(
        self, config_path: Path, plugin_registry=None, fast_mode: bool = False, *, initialize: bool = True
    ):
        """Initialize the configuration service.

        Args:
            config_path: Path to the configuration file.
            plugin_registry: Optional plugin registry for plugin configs.
            fast_mode: If True, skip expensive operations like system prompt loading.
            initialize: If False, skip loading, migrating, and polling configuration.
                Intended for targeted writes using an explicit save target.
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

        # mtime poll: last seen mtime per config file, and the on-loop task
        self._mtimes: Dict[Path, Optional[int]] = {}
        self._poll_task: Optional[asyncio.Task] = None

        if initialize:
            # Load initial configuration and poll for other processes' saves.
            self._initialize_config()
            self._start_polling()

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
        except ProjectStateLocationError:
            raise
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
        # This service's own file: reading and rewriting the global one would stamp (or replace) the
        # real config from a service built on some other path (#967).
        save_path = self.config_manager.config_path
        try:
            if save_path.exists():
                raw = json.loads(save_path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    raw = {}
            else:
                raw = {}
        except Exception as e:
            logger.warning("Could not read config for migration: %s", e)
            keep_unreadable_copy(save_path)
            raw = {}

        additive_defaults = {
            "plugins.hub.enabled": True,
            "plugins.hub.project_scoped": True,
            "plugins.context_service.hub_broadcast_enabled": True,
            "kollabor.updates.check_enabled": True,
            "kollabor.updates.auto_update_enabled": False,
            "kollabor.updates.restart_agents": False,
            "kollabor.updates.check_interval_hours": 24,
            "kollabor.updates.github_repo": "kollaborai/kollab",
            "kollabor.updates.timeout_seconds": 5,
            "kollabor.updates.include_prereleases": False,
            "kollabor.llm.default_agent": {
                "name": "default",
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
            write_text_atomic(save_path, json.dumps(raw, indent=2, ensure_ascii=False))
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
        success = self.config_loader.save_merged_config(
            self.config_manager.config, save_target=save_target
        )
        if success:
            if self._mtimes:
                self._mtimes = self._stat_mtimes()  # our write is not news
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
        return self.save_keys({key_path: value}, save_target=save_target)

    def save_keys(
        self, changes: Dict[str, Any], save_target: Optional[str] = None, *, strict: bool = False
    ) -> bool:
        """Persist several config keys in one locked atomic replacement.

        Strict writes refuse unreadable files and conflicting non-object parents.
        """
        import json

        from kollabor_events.dict_utils import safe_set

        # Determine file path
        if save_target == "local":
            save_path = get_local_config_path()
        elif save_target == "global":
            save_path = get_global_config_path()
        else:
            save_path = self.config_loader._get_config_save_path()

        # The shared UI/plugin writer must serialize the read-modify-write, not
        # just replace the file atomically: otherwise concurrent edits lose keys.
        from .file_lock import locked_file

        try:
            save_path.parent.mkdir(parents=True, exist_ok=True)
            with locked_file(save_path.with_name(save_path.name + ".lock"), private=True):
                if strict and save_path.is_symlink():
                    raise ValueError("Config file cannot be a symlink")
                existing: Dict[str, Any] = {}
                if save_path.exists():
                    try:
                        with open(save_path) as f:
                            existing = json.load(f)
                    except (json.JSONDecodeError, UnicodeError, OSError) as e:
                        if strict:
                            raise ValueError("Cannot read config file") from e
                        logger.warning(f"Could not read {save_path}, starting fresh: {e}")
                        keep_unreadable_copy(save_path)
                if strict and not isinstance(existing, dict):
                    raise ValueError("Config file must contain an object")
                for key_path, value in changes.items():
                    if strict:
                        node = existing
                        for segment in key_path.split(".")[:-1]:
                            if segment not in node:
                                break
                            node = node[segment]
                            if not isinstance(node, dict):
                                raise ValueError("Config path conflicts with an existing value")
                    if not safe_set(existing, key_path, value):
                        raise ValueError("Invalid config path")
                write_text_atomic(save_path, json.dumps(existing, indent=2, ensure_ascii=False))
                for key_path, value in changes.items():
                    safe_set(self.config_manager.config, key_path, value)
                self._note_own_write(save_path)
            logger.info("Saved %d config key(s) to %s", len(changes), save_path)
            return True
        except Exception as e:
            logger.error("Failed to save config keys to %s: %s", save_path, e)
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
        updated = self.config_loader.update_with_plugins()
        if self._mtimes:
            self._mtimes = self._stat_mtimes()  # our write is not news
        return updated

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

    def _watched_paths(self) -> list[Path]:
        """Every config file the loader may merge, including ones not created yet."""
        paths = [
            self.config_manager.config_path,
            *get_global_config_path_candidates(),
            *(path / "config.json" for path in get_project_data_dir_candidates()),
            get_project_data_dir() / "config.json",
            *get_local_config_path_candidates(),
        ]
        return list(dict.fromkeys(paths))

    def _stat_mtimes(self) -> Dict[Path, Optional[int]]:
        """mtime (ns) of every watched file, None where it does not exist."""
        mtimes: Dict[Path, Optional[int]] = {}
        for path in self._watched_paths():
            try:
                mtimes[path] = path.stat().st_mtime_ns
            except OSError:
                mtimes[path] = None
        return mtimes

    def _note_own_write(self, path: Path) -> None:
        """Record the mtime of a file this process just wrote.

        The poll then ignores our own save but still sees any other process's.
        """
        if path in self._mtimes:
            try:
                self._mtimes[path] = path.stat().st_mtime_ns
            except OSError:
                pass

    def _start_polling(self) -> None:
        """Start the mtime poll on the running loop (idempotent).

        A service built before any loop exists starts polling at its next
        register_reload_callback().
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = self._poll_task
        if task is not None and not task.done() and task.get_loop() is loop:
            return
        self._mtimes = self._stat_mtimes()
        self._poll_task = loop.create_task(
            self._poll_config_files(), name="config-mtime-poll"
        )

    async def _poll_config_files(self) -> None:
        """Reload whenever any config file's mtime changes (every POLL_SECONDS)."""
        while True:
            await asyncio.sleep(self.POLL_SECONDS)
            try:
                current = self._stat_mtimes()
                if current != self._mtimes:
                    self._mtimes = current  # before reload: a bad file is retried
                    logger.info("Configuration file changed on disk, reloading")
                    self.reload()  # notifies the reload callbacks on success
            except Exception:
                logger.exception("Configuration poll failed")

    def register_reload_callback(self, callback: Callable[[], None]) -> None:
        """Register a callback to be notified when configuration reloads.

        Args:
            callback: Function to call after successful configuration reload.
        """
        self._reload_callbacks.append(callback)
        self._start_polling()

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
        """Shutdown the configuration service and its mtime poll."""
        task, self._poll_task = self._poll_task, None
        if task is not None:
            try:
                task.cancel()
            except RuntimeError:  # its loop is already closed
                pass
        logger.info("Configuration service shutdown")
