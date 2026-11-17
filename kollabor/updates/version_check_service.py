"""Version check service for Kollab.

Checks GitHub Releases API for new versions, caches results in config,
and provides release information for user notifications.
"""

import asyncio
import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Optional

import aiohttp
from packaging.version import InvalidVersion, Version

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


@dataclass
class UpdateCheckResult:
    """A truthful result from one release lookup."""

    status: str
    release: Optional[ReleaseInfo]
    checked_at: Optional[float]
    last_successful_check: Optional[float]
    retry_after_seconds: Optional[int] = None
    retry_at: Optional[float] = None
    retry_reason: Optional[str] = None


def _cache_shows_current_or_older(cached: str, current: str) -> bool:
    """True when a cached "latest version" should be treated as stale.

    The cache is worthless whenever it does not show a release strictly
    newer than the running version: after an upgrade the cached value is
    the pre-upgrade release, and keeping it would suppress the update
    notice for a full interval (issue #169). Empty or unparseable cache
    entries count as stale; only a strictly newer cached version is
    trusted for the long TTL.
    """
    if not cached:
        return True
    try:
        return Version(cached.lstrip("v")) <= Version(current.lstrip("v"))
    except InvalidVersion:
        return True


class VersionCheckService:
    """Service for checking GitHub releases and notifying of updates.

    This service:
    - Fetches latest release info from GitHub API
    - Caches results in config for configurable TTL (default 24 hours)
    - Compares versions using semantic versioning
    - Handles network errors gracefully
    - Never blocks application startup

    Configuration keys:
        kollabor.updates.check_enabled (bool): Enable update checking
        kollabor.updates.auto_update_enabled (bool): Install updates automatically
        kollabor.updates.check_interval_hours (int): Cache TTL in hours
        kollabor.updates.github_repo (str): Repository path (owner/repo)
        kollabor.updates.timeout_seconds (int): HTTP request timeout
        kollabor.updates.include_prereleases (bool): Include pre-release versions

    Cache keys (stored in config):
        kollabor.updates.last_check_timestamp (int): Unix timestamp of last check
        kollabor.updates.cached_latest_version (str): Cached version string
        kollabor.updates.cached_release_url (str): Cached release URL
        kollabor.updates.cached_release_name (str): Cached release name
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
        self._check_lock = asyncio.Lock()
        self._retry_until = 0.0
        self._rate_limit_retry_until = 0.0
        self._last_result: Optional[UpdateCheckResult] = None
        self._forced_result_until = 0.0
        self._forced_result: Optional[UpdateCheckResult] = None

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
        if self.session is not None:
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

    async def check_status(self, *, force: bool = False) -> UpdateCheckResult:
        """Return current, available, disabled, or failed without hiding errors.

        Successful current results are fresh for at most one hour; available
        releases use the configured interval (24 hours by default). Concurrent
        callers share this service's in-flight result, including forced refreshes.
        Rate limits honor Retry-After, defaulting to a 60-second cooldown.
        All failures have a five-second retry gate (forced refreshes included)
        and never enter the success cache. Successful forced refreshes are reused
        for one minute to prevent repeated manual requests from exhausting quota.
        """
        if not self.check_enabled:
            result = UpdateCheckResult("disabled", None, None, None)
            self._last_result = result
            return result

        waiting_for_check = self._check_lock.locked()
        async with self._check_lock:
            if waiting_for_check and self._last_result is not None:
                result = self._last_result
                if force and result.status in {"current", "available"}:
                    self._forced_result = result
                    self._forced_result_until = time.monotonic() + 60
                return result

            now = time.time()
            last_successful_check = self.config.get(
                "kollabor.updates.last_check_timestamp"
            )
            monotonic_now = time.monotonic()
            if force and monotonic_now < self._forced_result_until:
                if self._forced_result is not None:
                    self._last_result = self._forced_result
                    return self._forced_result

            if not force and self._is_cache_valid():
                release = self._get_cached_release()
                if release:
                    status = self._status_for_release(release)
                    if status is None:
                        result = UpdateCheckResult(
                            "failed", None, None, last_successful_check
                        )
                        self._last_result = result
                        return result
                    result = UpdateCheckResult(
                        status, release, last_successful_check, last_successful_check
                    )
                    self._last_result = result
                    return result

            if monotonic_now < self._rate_limit_retry_until:
                retry_after = math.ceil(self._rate_limit_retry_until - monotonic_now)
                result = UpdateCheckResult(
                    "failed",
                    None,
                    None,
                    last_successful_check,
                    retry_after,
                    time.time() + retry_after,
                    "rate_limit",
                )
                self._last_result = result
                return result
            if monotonic_now < self._retry_until:
                retry_after = math.ceil(self._retry_until - monotonic_now)
                result = UpdateCheckResult(
                    "failed",
                    None,
                    None,
                    last_successful_check,
                    retry_after,
                    time.time() + retry_after,
                    "retry",
                )
                self._last_result = result
                return result

            try:
                self._retry_until = 0.0
                release_data = await self._fetch_latest_release()
                release = self._parse_release_data(release_data) if release_data else None
            except Exception as exc:
                logger.warning("Update check failed for %s: %s", self.github_repo, exc)
                release = None

            if release is None:
                self._retry_until = time.monotonic() + 5
                rate_limited = self._rate_limit_retry_until > time.monotonic()
                retry_after = (
                    math.ceil(self._rate_limit_retry_until - time.monotonic())
                    if rate_limited
                    else 5
                )
                result = UpdateCheckResult(
                    "failed",
                    None,
                    None,
                    last_successful_check,
                    retry_after,
                    time.time() + retry_after,
                    "rate_limit" if rate_limited else "retry",
                )
                self._last_result = result
                return result

            status = self._status_for_release(release)
            if status is None:
                self._retry_until = time.monotonic() + 5
                result = UpdateCheckResult(
                    "failed",
                    None,
                    None,
                    last_successful_check,
                    5,
                    time.time() + 5,
                    "retry",
                )
                self._last_result = result
                return result

            try:
                self._update_cache(release)
                checked_at = self.config.get(
                    "kollabor.updates.last_check_timestamp", now
                )
            except Exception as exc:
                logger.warning("Could not cache update result: %s", exc)
                self._retry_until = time.monotonic() + 5
                result = UpdateCheckResult(
                    "failed",
                    None,
                    None,
                    last_successful_check,
                    5,
                    time.time() + 5,
                    "retry",
                )
                self._last_result = result
                return result

            result = UpdateCheckResult(status, release, checked_at, checked_at)
            self._last_result = result
            if force:
                self._forced_result = result
                self._forced_result_until = time.monotonic() + 60
            return result

    async def _fetch_latest_release(self) -> Optional[dict]:
        """Fetch latest release from GitHub API.

        Returns:
            Parsed JSON response or None on error
        """
        if not self.session:
            await self.initialize()
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
                elif response.status == 429 or (
                    response.status == 403
                    and (
                        response.headers.get("Retry-After") is not None
                        or response.headers.get("X-RateLimit-Remaining") == "0"
                    )
                ):
                    retry_after = response.headers.get("Retry-After")
                    try:
                        if retry_after:
                            try:
                                retry_seconds = max(0, int(retry_after))
                            except ValueError:
                                retry_time = parsedate_to_datetime(retry_after)
                                retry_seconds = max(
                                    0,
                                    int(
                                        (
                                            retry_time
                                            - datetime.now(timezone.utc)
                                        ).total_seconds()
                                    ),
                                )
                        else:
                            reset_at = response.headers.get("X-RateLimit-Reset")
                            if reset_at and response.headers.get("X-RateLimit-Remaining") == "0":
                                retry_seconds = max(0, int(reset_at) - int(time.time()))
                            else:
                                retry_seconds = 60
                    except (TypeError, ValueError, OverflowError):
                        retry_seconds = 60
                    retry_seconds = min(retry_seconds, 3600)
                    self._rate_limit_retry_until = time.monotonic() + retry_seconds
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
        if release_info.is_prerelease and not self.include_prereleases:
            return False
        try:
            return is_newer_version(self.current_version, release_info.version)
        except ValueError as e:
            logger.warning(f"Version comparison failed: {e}")
            return False

    def _status_for_release(self, release_info: ReleaseInfo) -> Optional[str]:
        """Classify a successful release only when its version is comparable."""
        try:
            is_newer = is_newer_version(self.current_version, release_info.version)
        except ValueError as exc:
            logger.warning("Version comparison failed: %s", exc)
            return None
        if release_info.is_prerelease and not self.include_prereleases:
            return "current"
        return "available" if is_newer else "current"

    def _is_cache_valid(self) -> bool:
        """Check if cached data is still valid based on TTL.

        Uses a shorter TTL (1 hour) when the cached version is not a
        release strictly newer than the running version (it matches,
        predates an upgrade, or is unparseable), so new releases are
        detected promptly. Uses the full configured TTL only when the
        cache already shows a newer release, to avoid re-fetching the
        same release info repeatedly.

        Returns:
            True if cache is valid (age < TTL)
        """
        last_check: int = self.config.get("kollabor.updates.last_check_timestamp", 0)
        current_time = int(time.time())
        ttl_seconds = self.check_interval_hours * 3600

        age_seconds = current_time - last_check

        # A cache that does not show a release strictly newer than the
        # running version is stale (an upgrade leaves the pre-upgrade
        # release behind): re-check within ~1 hour so users learn about
        # new releases promptly instead of after the full interval.
        cached_version = self.config.get("kollabor.updates.cached_latest_version", "")
        cached_is_filtered_prerelease = bool(
            self.config.get("kollabor.updates.cached_release_is_prerelease", False)
        ) and not self.include_prereleases
        if cached_is_filtered_prerelease or _cache_shows_current_or_older(
            cached_version, self.current_version
        ):
            ttl_seconds = min(ttl_seconds, 3600)  # cap at 1 hour

        is_valid: bool = age_seconds < ttl_seconds

        logger.debug(
            f"Cache validity: age={age_seconds}s, ttl={ttl_seconds}s, "
            f"cached={cached_version}, current={self.current_version}, "
            f"valid={is_valid}"
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
            is_prerelease=bool(
                self.config.get("kollabor.updates.cached_release_is_prerelease", False)
            ),
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
        self.config.save_key(
            "kollabor.updates.cached_release_is_prerelease",
            release_info.is_prerelease,
        )

        logger.debug(
            f"Updated cache with version {release_info.version} at timestamp {current_time}"
        )

    async def shutdown(self) -> None:
        """Close HTTP session and cleanup resources."""
        if self.session:
            await self.session.close()
            logger.debug("VersionCheckService session closed")
