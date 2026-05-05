"""Semantic version comparison utilities for Kollab.

Provides version comparison logic using the packaging library to handle
semantic versioning correctly (including pre-releases, build metadata, etc.).
"""

import logging

from packaging.version import InvalidVersion, Version

logger = logging.getLogger(__name__)


def is_newer_version(current: str, latest: str) -> bool:
    """Check if latest version is newer than current version.

    Uses semantic versioning comparison via the packaging library.
    Handles version prefixes (v0.4.11), pre-releases (0.5.0-beta),
    and build metadata (0.5.0+build123).

    Args:
        current: Current version string (e.g., "0.4.11" or "v0.4.11")
        latest: Latest version string from GitHub API

    Returns:
        True if latest > current, False otherwise

    Raises:
        ValueError: If version format is invalid

    Examples:
        >>> is_newer_version("0.4.11", "0.5.0")
        True
        >>> is_newer_version("v0.5.0", "0.4.11")
        False
        >>> is_newer_version("0.5.0-beta", "0.5.0")
        True
    """
    try:
        # Normalize versions (strip 'v' prefix if present)
        current_clean = current.lstrip("v")
        latest_clean = latest.lstrip("v")

        # Use packaging.version for robust semantic versioning
        current_ver = Version(current_clean)
        latest_ver = Version(latest_clean)

        result = latest_ver > current_ver

        logger.debug(
            f"Version comparison: {current} vs {latest} -> "
            f"latest is newer: {result}"
        )

        return result

    except InvalidVersion as e:
        error_msg = f"Invalid version format: {e}"
        logger.warning(error_msg)
        raise ValueError(error_msg) from e


def compare_versions(current: str, latest: str) -> int:
    """Compare two version strings using semantic versioning.

    Args:
        current: Current version string
        latest: Latest version string

    Returns:
        -1 if current < latest
         0 if current == latest
         1 if current > latest

    Raises:
        ValueError: If version format is invalid

    Examples:
        >>> compare_versions("0.4.11", "0.5.0")
        -1
        >>> compare_versions("0.5.0", "0.5.0")
        0
        >>> compare_versions("0.6.0", "0.5.0")
        1
    """
    try:
        # Normalize versions
        current_clean = current.lstrip("v")
        latest_clean = latest.lstrip("v")

        current_ver = Version(current_clean)
        latest_ver = Version(latest_clean)

        if current_ver < latest_ver:
            return -1
        elif current_ver > latest_ver:
            return 1
        else:
            return 0

    except InvalidVersion as e:
        error_msg = f"Invalid version format: {e}"
        logger.warning(error_msg)
        raise ValueError(error_msg) from e
