"""Version check service for Kollab.

Checks GitHub Releases API for new versions, caches results in config,
and provides release information for user notifications.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import aiohttp

from .version_comparator import is_newer_version

logger = logging.getLogger(__name__)


@dataclass
class ReleaseInfo:
    """Information about a GitHub release.

    Attributes:
        version: Semantic version number (e.g., "0.5.0")
        tag_name: Git tag name (e.g., "v0.5.0")
        name: Human-readable release name (e.g., "Version 0.5.0")
        url: GitHub release page URL
        is_prerelease: Whether this is a pre-release version
    """

    version: str
    tag_name: str
    name: str
    url: str
    is_prerelease: bool


class VersionCheckService:
    """Service for checking GitHub releases and notifying of updates.

    This service:
    - Fetches latest release info from GitHub API
    - Caches results in config for configurable TTL (default 24 hours)
    - Compares versions using semantic versioning
    - Handles network errors gracefully
    - Never blocks application startup

    Configuration keys:
        core.updates.check_enabled (bool): Enable update checking
        core.updates.check_interval_hours (int): Cache TTL in hours
        core.updates.github_repo (str): Repository path (owner/repo)
        core.updates.timeout_seconds (int): HTTP request timeout
        core.updates.include_prereleases (bool): Include pre-release versions

    Cache keys (stored in config):
        core.updates.last_check_timestamp (int): Unix timestamp of last check
        core.updates.cached_latest_version (str): Cached version string
        core.updates.cached_release_url (str): Cached release URL
        core.updates.cached_release_name (str): Cached release name
    """

    def __init__(self, config, current_version: str):
        """Initialize version check service.

        Args:
            config: ConfigService instance for settings and cache storage
            current_version: Current application version string
        """
        self.config = config
        self.current_version = current_version

        # Load configuration with defaults
        self.check_enabled = config.get("kollabor.updates.check_enabled", True)
        self.check_interval_hours = config.get(
            "kollabor.updates.check_interval_hours", 24
        )
        self.github_repo = config.get(
            "kollabor.updates.github_repo", "kollaborai/kollab"
        )
        self.timeout_seconds = config.get("kollabor.updates.timeout_seconds", 5)
        self.include_prereleases = config.get(
            "kollabor.updates.include_prereleases", False
        )

        # HTTP session (initialized in initialize())
        self.session: Optional[aiohttp.ClientSession] = None

        logger.debug(
            f"VersionCheckService initialized: "
            f"enabled={self.check_enabled}, "
            f"interval={self.check_interval_hours}h, "
            f"repo={self.github_repo}"
        )

    async def initialize(self) -> None:
        """Initialize HTTP session and load cached data.

        Creates aiohttp ClientSession with configured timeout and headers.
        """
        if not self.check_enabled:
            logger.debug("Update checking disabled in config")
            return

        # Create HTTP session with timeout
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)

        self.session = aiohttp.ClientSession(
            timeout=timeout,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": f"Kollab/{self.current_version}",
            },
        )

        logger.debug("VersionCheckService session initialized")

    async def check_for_updates(self) -> Optional[ReleaseInfo]:
        """Check if a newer version is available.

        Workflow:
        1. Return None if disabled in config
        2. Check cache validity (24h TTL)
        3. If cache valid, return cached data
        4. If cache invalid/missing, fetch from GitHub API
        5. Parse response and compare versions
        6. Update cache if newer version found
        7. Return ReleaseInfo or None

        Returns:
            ReleaseInfo if newer version available, None otherwise

        Note:
            All errors are caught and logged as warnings. This method
            never raises exceptions to avoid blocking startup.
        """
        try:
            # Check if enabled
            if not self.check_enabled:
                logger.debug("Update checking disabled")
                return None

            # Check cache validity
            if self._is_cache_valid():
                logger.debug("Using cached release data")
                cached_release = self._get_cached_release()
                if cached_release and self._is_update_available(cached_release):
                    return cached_release
                return None

            # Fetch from GitHub API
            logger.debug(f"Fetching latest release from GitHub: {self.github_repo}")
            release_data = await self._fetch_latest_release()

            if not release_data:
                # API call failed, try cached data
                logger.debug("API call failed, attempting to use cached data")
                cached_release = self._get_cached_release()
                if cached_release and self._is_update_available(cached_release):
                    return cached_release
                return None

            # Parse release data
            release_info = self._parse_release_data(release_data)
            if not release_info:
                return None

            # Update cache
            self._update_cache(release_info)

            # Check if update available
            if self._is_update_available(release_info):
                logger.info(f"New version available: {release_info.version}")
                return release_info

            logger.debug(f"Current version {self.current_version} is up to date")
            return None

        except Exception as e:
            # Catch-all for unexpected errors
            logger.warning(
                f"Unexpected error during update check for {self.github_repo}: {e}"
            )
            return None

    async def _fetch_latest_release(self) -> Optional[dict]:
        """Fetch latest release from GitHub API.

        Returns:
            Parsed JSON response or None on error
        """
        if not self.session:
            logger.debug("HTTP session not initialized - update check disabled")
            return None

        url = f"https://api.github.com/repos/{self.github_repo}/releases/latest"

        try:
            async with self.session.get(url) as response:
                if response.status == 200:
                    data: Dict[str, Any] = await response.json()
                    logger.debug(f"GitHub API returned: {data.get('tag_name')}")
                    return data
                elif response.status == 404:
                    logger.info(
                        f"Repository {self.github_repo} not found via GitHub API "
                        f"(this is normal if the repo is private, "
                        f"rate-limited, or unreachable)"
                    )
                    return None
                elif response.status == 403:
                    logger.info(
                        f"GitHub API rate limit exceeded for {self.github_repo} "
                        f"- update check will retry on next interval"
                    )
                    return None
                else:
                    logger.info(
                        f"GitHub API returned status {response.status} "
                        f"for {self.github_repo} - skipping update check"
                    )
                    return None

        except asyncio.TimeoutError:
            logger.debug(
                f"GitHub API timeout after {self.timeout_seconds}s "
                f"- falling back to cached data"
            )
            return None

        except aiohttp.ClientError as e:
            logger.debug(
                f"GitHub API connection error: {e} "
                f"- falling back to cached data"
            )
            return None

        except Exception as e:
            logger.warning(
                f"Unexpected error fetching release from {self.github_repo}: {e}"
            )
            return None

    def _parse_release_data(self, release_json: dict) -> Optional[ReleaseInfo]:
        """Parse GitHub release API response.

        Args:
            release_json: Parsed JSON response from GitHub API

        Returns:
            ReleaseInfo instance or None on error
        """
        try:
            tag_name = release_json["tag_name"]
            name = release_json.get("name", tag_name)
            url = release_json["html_url"]
            is_prerelease = release_json.get("prerelease", False)

            # Strip 'v' prefix from tag to get version
            version = tag_name.lstrip("v")

            # Filter pre-releases if configured
            if is_prerelease and not self.include_prereleases:
                logger.debug(f"Skipping pre-release version: {version}")
                return None

            release_info = ReleaseInfo(
                version=version,
                tag_name=tag_name,
                name=name,
                url=url,
                is_prerelease=is_prerelease,
            )

            logger.debug(f"Parsed release info: {version}")
            return release_info

        except KeyError as e:
            logger.warning(f"Invalid release data: missing field {e}")
            return None

        except Exception as e:
            logger.warning(f"Error parsing release data: {e}")
            return None

    def _is_update_available(self, release_info: ReleaseInfo) -> bool:
        """Check if release is newer than current version.

        Args:
            release_info: Release information to check

        Returns:
            True if release is newer than current version
        """
        try:
            return is_newer_version(self.current_version, release_info.version)
        except ValueError as e:
            logger.warning(f"Version comparison failed: {e}")
            return False

    def _is_cache_valid(self) -> bool:
        """Check if cached data is still valid based on TTL.

        Returns:
            True if cache is valid (age < TTL)
        """
        last_check: int = self.config.get("kollabor.updates.last_check_timestamp", 0)
        current_time = int(time.time())
        ttl_seconds = self.check_interval_hours * 3600

        age_seconds = current_time - last_check
        is_valid: bool = age_seconds < ttl_seconds

        logger.debug(
            f"Cache validity: age={age_seconds}s, ttl={ttl_seconds}s, valid={is_valid}"
        )

        return is_valid

    def _get_cached_release(self) -> Optional[ReleaseInfo]:
        """Retrieve cached release information from config.

        Returns:
            ReleaseInfo from cache or None if not cached
        """
        version = self.config.get("kollabor.updates.cached_latest_version")
        if not version:
            logger.debug("No cached release data found")
            return None

        url = self.config.get("kollabor.updates.cached_release_url", "")
        name = self.config.get(
            "kollabor.updates.cached_release_name", f"Version {version}"
        )

        release_info = ReleaseInfo(
            version=version,
            tag_name=f"v{version}",
            name=name,
            url=url,
            is_prerelease=False,
        )

        logger.debug(f"Retrieved cached release: {version}")
        return release_info

    def _update_cache(self, release_info: ReleaseInfo) -> None:
        """Update cached release information in config.

        Args:
            release_info: Release information to cache
        """
        current_time = int(time.time())

        self.config.save_key("kollabor.updates.last_check_timestamp", current_time)
        self.config.save_key("kollabor.updates.cached_latest_version", release_info.version)
        self.config.save_key("kollabor.updates.cached_release_url", release_info.url)
        self.config.save_key("kollabor.updates.cached_release_name", release_info.name)

        logger.debug(
            f"Updated cache with version {release_info.version} at timestamp {current_time}"
        )

    async def shutdown(self) -> None:
        """Close HTTP session and cleanup resources."""
        if self.session:
            await self.session.close()
            logger.debug("VersionCheckService session closed")
