"""Release update checking system for Kollab.

This module provides functionality to check for new releases via GitHub API,
cache results, and notify users of available updates.
"""

from .auto_update import AutoUpdateResult, run_auto_update
from .version_check_service import ReleaseInfo, VersionCheckService
from .version_comparator import is_newer_version

__all__ = [
    "AutoUpdateResult",
    "ReleaseInfo",
    "VersionCheckService",
    "is_newer_version",
    "run_auto_update",
]
