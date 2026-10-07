"""OAuth token storage with file-based backend.

Stores/loads OAuth tokens (access_token, refresh_token, expires_at)
 as JSON files in ~/.kollab/oauth/ with 600 permissions.
Falls back gracefully - no keyring dependency required.

Auto-refreshes expired tokens transparently. Every write holds an
exclusive flock on <provider>.lock in the same directory (skipped where
fcntl is unavailable) and replaces the file atomically, so hub agents on
one machine can never spend the same single-use refresh token and readers
never see a partially written file.
"""

import asyncio
import json
import logging
import os
import stat
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

try:
    import fcntl
except ImportError:  # pragma: no cover - platforms without flock (Windows)
    fcntl = None

from kollabor_config.config_utils import (
    get_config_directory,
    get_config_directory_candidates,
)

from .openai_oauth import OAuthError, OAuthTokens, OpenAIOAuthClient

logger = logging.getLogger(__name__)

# Refresh buffer: refresh 5 minutes before actual expiry
DEFAULT_EXPIRY_BUFFER = 300


def _get_oauth_dir() -> Path:
    """Get the OAuth token storage directory."""
    base = get_config_directory() / "oauth"
    base.mkdir(parents=True, exist_ok=True)
    # Restrict directory permissions (owner only)
    try:
        os.chmod(base, stat.S_IRWXU)
    except OSError:
        pass
    return base


class OAuthTokenStorage:
    """Persistent file-based storage for OAuth tokens.

    Stores tokens as JSON files in ~/.kollab/oauth/
    with restricted file permissions (0600).

    Storage layout:
      ~/.kollab/oauth/openai.json
      ~/.kollab/oauth/anthropic.json  (future)
    """

    def __init__(self, expiry_buffer: int = DEFAULT_EXPIRY_BUFFER):
        self._expiry_buffer = expiry_buffer
        self._oauth_dir = _get_oauth_dir()

    def _token_path(self, provider: str) -> Path:
        """Get the file path for a provider's tokens."""
        return self._oauth_dir / f"{provider}.json"

    def _token_path_candidates(self, provider: str) -> list[Path]:
        """Get token paths in read precedence order."""
        return [
            directory / "oauth" / f"{provider}.json"
            for directory in get_config_directory_candidates()
        ]

    def _lock_path(self, provider: str) -> Path:
        """Get the cross-process lock file for a provider's tokens."""
        return self._oauth_dir / f"{provider}.lock"

    @asynccontextmanager
    async def _exclusive_lock(self, provider: str):
        """Hold the provider's cross-process lock without blocking the loop.

        Polls a non-blocking flock() so a task cancelled while it waits (an
        interrupted turn) holds nothing. A blocking flock() in a worker thread
        would take the lock after the cancel and never release it.
        """
        if fcntl is None:
            yield
            return
        self._oauth_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(
            self._lock_path(provider),
            os.O_CREAT | os.O_RDWR,
            stat.S_IRUSR | stat.S_IWUSR,
        )
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    await asyncio.sleep(0.05)
            yield
        finally:
            os.close(fd)  # closing the only descriptor releases the lock

    @staticmethod
    def _atomic_write(path: Path, data: str) -> None:
        """Replace path's contents atomically, keeping the file at mode 0600."""
        fd, tmp_name = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
        )
        try:
            os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def _read_tokens_file(self, path: Path, provider: str) -> Optional[OAuthTokens]:
        """Read and parse a token file, returning None if missing or corrupt."""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return OAuthTokens.from_dict(data)
        except (json.JSONDecodeError, KeyError, TypeError, OSError, ValueError) as e:
            logger.warning(f"Corrupt OAuth token data for {provider}: {e}")
            return None

    async def store_tokens(
        self,
        provider: str,
        tokens: OAuthTokens,
        *,
        profile_name: str | None = None,
    ) -> None:
        """Store OAuth tokens for a provider.

        Args:
            provider: Provider name (e.g. "openai").
            tokens: Token set to store.
        """
        if profile_name is not None:
            if provider != "openai":
                raise OAuthError("profile-scoped OAuth is only supported for OpenAI")
            from kollabor_config.provisioned_state import ProvisionedStateFile

            if not ProvisionedStateFile().update_profile_oauth_tokens(
                profile_name, tokens.to_dict()
            ):
                raise OAuthError("provisioned OAuth profile is unavailable")
            logger.info("Stored OAuth tokens for profile-scoped OpenAI profile")
            return

        path = self._token_path(provider)
        data = json.dumps(tokens.to_dict(), indent=2)
        async with self._exclusive_lock(provider):
            self._atomic_write(path, data)
        logger.info(f"Stored OAuth tokens for {provider}")

    async def load_tokens(
        self,
        provider: str,
        auto_refresh: bool = True,
        *,
        profile_name: str | None = None,
    ) -> Optional[OAuthTokens]:
        """Load OAuth tokens for a provider, refreshing if needed.

        Args:
            provider: Provider name (e.g. "openai").
            auto_refresh: If True, auto-refresh expired tokens.

        Returns:
            OAuthTokens if found and valid, None otherwise.
        """
        if profile_name is not None:
            if provider != "openai":
                return None
            try:
                from kollabor_config.provisioned_state import ProvisionedStateFile

                data = ProvisionedStateFile().get_profile_oauth_tokens(profile_name)
                tokens = OAuthTokens.from_dict(data) if data is not None else None
            except (OSError, ValueError, KeyError, TypeError) as e:
                logger.warning("Invalid profile-scoped OAuth tokens: %s", e)
                return None
            if tokens is None:
                return None
            if auto_refresh and self._needs_refresh(tokens):
                if not tokens.refresh_token:
                    return None
                refreshed = await self._try_refresh(
                    provider, tokens, profile_name=profile_name
                )
                if refreshed is None:
                    logger.warning("OAuth token refresh failed for profile-scoped OpenAI profile")
                return refreshed
            return tokens

        path = next(
            (candidate for candidate in self._token_path_candidates(provider) if candidate.exists()),
            self._token_path(provider),
        )
        if not path.exists():
            return None

        tokens = self._read_tokens_file(path, provider)
        if tokens is None:
            return None

        # Check if token needs refresh
        if auto_refresh and self._needs_refresh(tokens):
            if not tokens.refresh_token:
                logger.warning(f"OAuth token for {provider} expired, no refresh_token")
                return None

            refreshed = await self._try_refresh(provider, tokens)
            if refreshed:
                return refreshed

            # Refresh failed, return None (needs re-login)
            logger.warning(f"OAuth token refresh failed for {provider}")
            return None

        return tokens

    async def clear_tokens(
        self, provider: str, *, profile_name: str | None = None
    ) -> bool:
        """Clear stored OAuth tokens for a provider.

        Args:
            provider: Provider name (e.g. "openai").

        Returns:
            True if tokens existed and were cleared.
        """
        if profile_name is not None:
            if provider != "openai":
                return False
            from kollabor_config.provisioned_state import ProvisionedStateFile

            return ProvisionedStateFile().update_profile_oauth_tokens(
                profile_name, None, disabled=True
            )

        cleared = False
        async with self._exclusive_lock(provider):
            for path in self._token_path_candidates(provider):
                if path.exists():
                    path.unlink()
                    cleared = True
        if cleared:
            logger.info(f"Cleared OAuth tokens for {provider}")
        return cleared

    async def has_tokens(self, provider: str, *, profile_name: str | None = None) -> bool:
        """Check if tokens exist for a provider (without loading/refreshing).

        Args:
            provider: Provider name.

        Returns:
            True if tokens are stored.
        """
        if profile_name is not None:
            if provider != "openai":
                return False
            try:
                from kollabor_config.provisioned_state import ProvisionedStateFile

                return (
                    ProvisionedStateFile().get_profile_oauth_tokens(profile_name)
                    is not None
                )
            except (OSError, ValueError):
                return False
        return self._token_path(provider).exists()

    def _needs_refresh(self, tokens: OAuthTokens) -> bool:
        """Check if tokens are expired or near expiry."""
        return time.time() >= (tokens.expires_at - self._expiry_buffer)

    async def _try_refresh(
        self,
        provider: str,
        tokens: OAuthTokens,
        *,
        profile_name: str | None = None,
    ) -> Optional[OAuthTokens]:
        """Attempt to refresh expired tokens.

        For the global path this holds the provider's cross-process lock from
        the re-read of the token file through the refresh endpoint call and
        the write, so two processes on one machine can never spend the same
        single-use refresh token. If another process already wrote tokens
        that differ from ``tokens`` and are not near expiry, those are
        returned without calling the refresh endpoint.

        Profile-scoped tokens go through ProvisionedStateFile, which has its
        own lock, so they refresh as before.

        Args:
            provider: Provider name for storage.
            tokens: Current tokens with refresh_token.

        Returns:
            New tokens if refresh succeeded, None otherwise.
        """
        try:
            if profile_name is not None:
                client = OpenAIOAuthClient()
                new_tokens = await client.refresh_access_token(
                    tokens.refresh_token,
                    previous_account_id=tokens.account_id,
                )
                await self.store_tokens(
                    provider, new_tokens, profile_name=profile_name
                )
                logger.info(f"Auto-refreshed OAuth token for {provider}")
                return new_tokens

            async with self._exclusive_lock(provider):
                path = next(
                    (
                        candidate
                        for candidate in self._token_path_candidates(provider)
                        if candidate.exists()
                    ),
                    self._token_path(provider),
                )
                if not path.exists():
                    # Logged out while this process waited: don't write it back.
                    return None
                current = self._read_tokens_file(path, provider) or tokens
                if current.to_dict() != tokens.to_dict() and not self._needs_refresh(
                    current
                ):
                    logger.info(
                        f"Using newer OAuth tokens for {provider} "
                        "written by another process"
                    )
                    return current
                # Refresh with the file's token: another process may have
                # rotated the one this process read.
                client = OpenAIOAuthClient()
                new_tokens = await client.refresh_access_token(
                    current.refresh_token,
                    previous_account_id=current.account_id,
                )
                self._atomic_write(path, json.dumps(new_tokens.to_dict(), indent=2))
                logger.info(f"Auto-refreshed OAuth token for {provider}")
                return new_tokens
        except OAuthError as exc:
            # Provider response bodies and exception strings may echo tokens.
            logger.error(
                "Token refresh failed for %s (profile %s, %s)",
                provider,
                profile_name or "global",
                type(exc).__name__,
            )
            return None
        except Exception as exc:
            logger.error(
                "Unexpected token refresh failure for %s (profile %s, %s)",
                provider,
                profile_name or "global",
                type(exc).__name__,
            )
            return None
