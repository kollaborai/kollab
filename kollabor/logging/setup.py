"""Centralized logging configuration and setup.

This module handles all logging configuration for the application,
reading from the config system when available and providing sensible
defaults during bootstrap.
"""

import logging as _logging
import logging.handlers  # force-load submodule for TimedRotatingFileHandler
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from kollabor_config.config_utils import get_logs_dir


class CompactFormatter(_logging.Formatter):
    """Custom formatter that compacts level names and includes file location."""

    def format(self, record):
        # Map long level names to 4-char versions
        level_mapping = {"WARNING": "WARN", "CRITICAL": "CRIT", "DEBUG": "DEBG"}
        record.levelname = level_mapping.get(record.levelname, record.levelname)
        return super().format(record)


class LoggingSetup:
    """Centralized logging configuration manager."""

    def __init__(self):
        self._configured: bool = False
        self._current_config: Dict[str, Any] = {}

    def setup_bootstrap_logging(self, log_dir: Optional[Path] = None):
        """Setup minimal logging before config system is available.

        Args:
            log_dir: Optional log directory, defaults to project-specific logs
        """
        if log_dir is None:
            log_dir = get_logs_dir()

        # Ensure log directory exists
        log_dir.mkdir(parents=True, exist_ok=True)

        # Setup with hardcoded defaults that match current behavior
        log_file = log_dir / "kollab.log"

        handler = _logging.handlers.TimedRotatingFileHandler(
            filename=str(log_file),
            when="D",  # Daily rotation
            interval=1,
            backupCount=1,
            encoding="utf-8",
        )

        # Add thread safety
        handler.lock = threading.RLock()  # type: ignore[assignment]

        # Use compact formatter
        formatter = CompactFormatter(
            "%(asctime)s - %(levelname)-4s - %(message)-100s - %(filename)s:%(lineno)04d"
        )
        handler.setFormatter(formatter)

        # Configure logging
        _logging.basicConfig(
            level=_logging.INFO,
            handlers=[handler, _logging.NullHandler()],  # Suppress console output
        )

        self._configured = True
        self._current_config = {
            "level": "INFO",
            "file": str(log_file),
            "format": "compact",
        }

        # Apply formatter to all existing handlers
        self._apply_formatter_to_all_loggers(formatter)

    def setup_from_config(self, config: Dict[str, Any]):
        """Setup logging from configuration system.

        Args:
            config: Configuration dictionary with logging settings
        """
        logging_config = config.get("logging", {})

        # Extract configuration values
        level = logging_config.get("level", "INFO").upper()
        default_log_path = get_logs_dir() / "kollab.log"
        log_file = logging_config.get("file") or str(default_log_path)
        format_type = logging_config.get("format_type", "compact")
        custom_format = logging_config.get("format", None)

        # Convert string level to logging constant
        numeric_level = getattr(_logging, level, _logging.INFO)

        # Ensure log directory exists
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        # Create new handler
        handler = _logging.handlers.TimedRotatingFileHandler(
            filename=str(log_path),
            when="D",
            interval=1,
            backupCount=1,
            encoding="utf-8",
        )

        # Add thread safety
        handler.lock = threading.RLock()  # type: ignore[assignment]

        # Choose formatter based on config
        if format_type == "compact":
            formatter: _logging.Formatter = CompactFormatter(
                "%(asctime)s - %(levelname)-4s - %(message)-100s - %(filename)s:%(lineno)04d"
            )
        elif custom_format:
            formatter = _logging.Formatter(custom_format)
        else:
            # Standard format
            formatter = _logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            )

        handler.setFormatter(formatter)

        # Clear existing handlers and setup new one
        root_logger = _logging.getLogger()
        for old_handler in root_logger.handlers[:]:
            old_handler.close()  # Close file handles before removing
            root_logger.removeHandler(old_handler)

        _logging.basicConfig(
            level=numeric_level,
            handlers=[handler, _logging.NullHandler()],
            force=True,  # Force reconfiguration
        )

        self._configured = True
        self._current_config = {
            "level": level,
            "file": str(log_path),
            "format": format_type,
            "custom_format": custom_format,
        }

        # Apply formatter to all existing loggers
        self._apply_formatter_to_all_loggers(formatter)

        _logging.getLogger(__name__).info(
            f"Logging reconfigured - Level: {level}, File: {log_file}, Format: {format_type}"
        )

    def _apply_formatter_to_all_loggers(self, formatter):
        """Apply formatter to all existing loggers and handlers."""
        # Apply to root logger handlers
        for root_handler in _logging.getLogger().handlers:
            root_handler.setFormatter(formatter)

        # Apply to all existing logger handlers
        for logger_name in _logging.Logger.manager.loggerDict:
            existing_logger = _logging.getLogger(logger_name)
            for existing_handler in existing_logger.handlers:
                existing_handler.setFormatter(formatter)

    def get_current_config(self) -> Dict[str, Any]:
        """Get current logging configuration."""
        return self._current_config.copy()

    def is_configured(self) -> bool:
        """Check if logging has been configured."""
        return self._configured

    def set_level(self, level: str) -> None:
        """Set logging level at runtime (hot reload support).

        Args:
            level: Log level string (DEBUG, INFO, WARNING, ERROR)
        """
        level = level.upper()
        numeric_level = getattr(_logging, level, _logging.INFO)

        # Update root logger
        _logging.getLogger().setLevel(numeric_level)

        # Update all existing loggers
        for logger_name in _logging.Logger.manager.loggerDict:
            existing_logger = _logging.getLogger(logger_name)
            existing_logger.setLevel(numeric_level)

        # Update current config
        self._current_config["level"] = level

        _logging.getLogger(__name__).info(f"Logging level changed to {level}")


# Global instance
logging_setup = LoggingSetup()


def setup_bootstrap_logging(log_dir: Optional[Path] = None):
    """Setup bootstrap logging before config system is available."""
    logging_setup.setup_bootstrap_logging(log_dir)


def setup_from_config(config: Dict[str, Any]):
    """Setup logging from configuration system."""
    logging_setup.setup_from_config(config)


def get_current_config() -> Dict[str, Any]:
    """Get current logging configuration."""
    return logging_setup.get_current_config()


def is_configured() -> bool:
    """Check if logging has been configured."""
    return logging_setup.is_configured()


def set_level(level: str) -> None:
    """Set logging level at runtime (hot reload support)."""
    logging_setup.set_level(level)
