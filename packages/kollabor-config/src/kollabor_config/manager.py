"""Configuration management system for Kollab."""

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from kollabor_events.dict_utils import safe_get, safe_set
from kollabor_events.error_utils import safe_execute

from .atomic_write import write_text_atomic
from .file_lock import locked_file

logger = logging.getLogger(__name__)

# Configuration version tracking
CONFIG_VERSION = 2  # Current version
VERSION_KEY = "_config_version"
_UNTRACKED_CONFIG = object()
_UNREADABLE_CONFIG = object()


class ConfigurationVersionError(Exception):
    """Raised when configuration version is invalid or unsupported."""

    pass


class ConfigManager:
    """Configuration management system.

    Handles loading and saving JSON configuration files with defaults.
    """

    def __init__(self, config_path: Path) -> None:
        """Initialize the config manager.

        Args:
            config_path: Path to the config JSON file.
        """
        self.config_path = config_path
        self.config: Dict[str, Any] = {}
        self._loaded_config_snapshot: bytes | None | object = _UNTRACKED_CONFIG
        logger.info(f"Config manager initialized: {config_path}")

    def get_config_version(self) -> int:
        """
        Get the configuration version.

        Returns:
            Configuration version (defaults to 1 if not set)
        """
        return int(self.config.get(VERSION_KEY, 1))

    def set_config_version(self, version: int) -> None:
        """
        Set the configuration version.

        Args:
            version: Version number to set
        """
        self.config[VERSION_KEY] = version

    def is_config_version_current(self) -> bool:
        """
        Check if configuration is at current version.

        Returns:
            True if version matches current, False otherwise
        """
        return self.get_config_version() == CONFIG_VERSION

    def get_min_supported_version(self) -> int:
        """
        Get the minimum supported configuration version.

        Returns:
            Minimum version number (currently 1)
        """
        return 1

    def is_config_version_supported(self, version: Optional[int] = None) -> bool:
        """
        Check if configuration version is supported.

        Args:
            version: Version to check (defaults to current config version)

        Returns:
            True if version is supported, False otherwise
        """
        if version is None:
            version = self.get_config_version()

        min_version = self.get_min_supported_version()
        return min_version <= version <= CONFIG_VERSION

    def validate_config_version(self) -> None:
        """
        Validate configuration version is supported.

        Raises:
            ConfigurationVersionError: If version is not supported
        """
        version = self.get_config_version()

        if not self.is_config_version_supported(version):
            raise ConfigurationVersionError(
                f"Unsupported configuration version: {version}. "
                f"Supported versions: {self.get_min_supported_version()}-{CONFIG_VERSION}"
            )

    def load_config_file(self) -> Dict[str, Any]:
        """Load configuration from file.

        Returns:
            Configuration dictionary from file, or empty dict if load fails.
        """
        try:
            contents = self.config_path.read_bytes()
        except FileNotFoundError:
            self._loaded_config_snapshot = None
            logger.debug(f"Config file does not exist: {self.config_path}")
            return {}
        except OSError as e:
            self._loaded_config_snapshot = _UNREADABLE_CONFIG
            logger.warning(
                "Could not read configuration file (%s)", type(e).__name__
            )
            return {}

        self._loaded_config_snapshot = contents
        try:
            config = json.loads(contents.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            logger.warning("Could not parse configuration file (%s)", type(e).__name__)
            return {}
        if not isinstance(config, dict):
            logger.warning("Configuration file has an invalid shape")
            return {}

        if config:
            # Store config in instance variable
            self.config = config
            logger.info("Loaded configuration from file")

            # Log configuration version
            version = self.get_config_version()
            logger.debug(f"Configuration version: {version}")

        return config  # type: ignore[no-any-return]

    def save_config_file(self, config: Dict[str, Any]) -> bool:
        """Save configuration to file.

        Args:
            config: Configuration dictionary to save.

        Returns:
            True if save successful, False otherwise.
        """

        def save_json():
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            lock_path = self.config_path.with_name(self.config_path.name + ".lock")
            with locked_file(lock_path, private=True):
                expected = self._loaded_config_snapshot
                if expected is _UNREADABLE_CONFIG:
                    logger.warning(
                        "Refusing to overwrite configuration that could not be read"
                    )
                    return False
                try:
                    current = self.config_path.read_bytes()
                except FileNotFoundError:
                    current = None
                except OSError as e:
                    logger.warning(
                        "Refusing to overwrite unreadable configuration (%s)",
                        type(e).__name__,
                    )
                    return False

                if expected is not _UNTRACKED_CONFIG and current != expected:
                    logger.warning(
                        "Refusing to overwrite configuration changed since it was loaded"
                    )
                    return False
                if current is not None:
                    try:
                        existing = json.loads(current.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as e:
                        logger.warning(
                            "Refusing to overwrite unreadable configuration (%s)",
                            type(e).__name__,
                        )
                        return False
                    if not isinstance(existing, dict):
                        logger.warning(
                            "Refusing to overwrite configuration with an invalid shape"
                        )
                        return False

                contents = json.dumps(config, indent=2)
                try:
                    latest = self.config_path.read_bytes()
                except FileNotFoundError:
                    latest = None
                except OSError as e:
                    logger.warning(
                        "Refusing to overwrite unreadable configuration (%s)",
                        type(e).__name__,
                    )
                    return False
                if latest != current:
                    logger.warning(
                        "Refusing to overwrite configuration changed during save"
                    )
                    return False

                write_text_atomic(self.config_path, contents)
                self._loaded_config_snapshot = contents.encode("utf-8")
                return True

        success = safe_execute(
            save_json,
            f"saving config to {self.config_path}",
            default=False,
            logger_instance=logger,
        )

        if success is not False:
            logger.debug("Configuration saved to file")
            return True
        return False

    def get(self, key_path: str, default: Any = None) -> Any:
        """Get a configuration value using dot notation.

        Args:
            key_path: Dot-separated path to the config value (e.g., "llm.api_url").
            default: Default value if key not found.

        Returns:
            Configuration value or default.
        """
        return safe_get(self.config, key_path, default)

    def set(self, key_path: str, value: Any) -> bool:
        """Set a configuration value using dot notation.

        Args:
            key_path: Dot-separated path to the config value.
            value: Value to set.

        Returns:
            True if set and save successful, False otherwise.
        """
        if safe_set(self.config, key_path, value):
            success = self.save_config_file(self.config)
            if success:
                logger.debug(f"Set config: {key_path} = {value}")
                return True

        logger.error(f"Failed to set config key: {key_path}")
        return False
