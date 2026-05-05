"""OAuth token storage with file-based backend.

Stores/loads OAuth tokens (access_token, refresh_token, expires_at)
 as JSON files in ~/.kollab/oauth/ with 600 permissions.
Falls back gracefully - no keyring dependency required.

Auto-refreshes expired tokens transparently.
"""

import json
import logging
import os
import stat
import time
from pathlib import Path
from typing import Optional

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

    async def store_tokens(self, provider: str, tokens: OAuthTokens) -> None:
        """Store OAuth tokens for a provider.

        Args:
            provider: Provider name (e.g. "openai").
            tokens: Token set to store.
        """
        path = self._token_path(provider)
        data = json.dumps(tokens.to_dict(), indent=2)
        path.write_text(data, encoding="utf-8")
        # Restrict file permissions (owner read/write only)
        try:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        logger.info(f"Stored OAuth tokens for {provider}")

    async def load_tokens(
        self,
        provider: str,
        auto_refresh: bool = True,
    ) -> Optional[OAuthTokens]:
        """Load OAuth tokens for a provider, refreshing if needed.

        Args:
            provider: Provider name (e.g. "openai").
            auto_refresh: If True, auto-refresh expired tokens.

        Returns:
            OAuthTokens if found and valid, None otherwise.
        """
        path = next(
            (candidate for candidate in self._token_path_candidates(provider) if candidate.exists()),
            self._token_path(provider),
        )
        if not path.exists():
            return None

        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
            tokens = OAuthTokens.from_dict(data)
        except (json.JSONDecodeError, KeyError, OSError) as e:
            logger.warning(f"Corrupt OAuth token data for {provider}: {e}")
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

    async def clear_tokens(self, provider: str) -> bool:
        """Clear stored OAuth tokens for a provider.

        Args:
            provider: Provider name (e.g. "openai").

        Returns:
            True if tokens existed and were cleared.
        """
        cleared = False
        for path in self._token_path_candidates(provider):
            if path.exists():
                path.unlink()
                cleared = True
        if cleared:
            logger.info(f"Cleared OAuth tokens for {provider}")
        return cleared

    async def has_tokens(self, provider: str) -> bool:
        """Check if tokens exist for a provider (without loading/refreshing).

        Args:
            provider: Provider name.

        Returns:
            True if tokens are stored.
        """
        return self._token_path(provider).exists()

    def _needs_refresh(self, tokens: OAuthTokens) -> bool:
        """Check if tokens are expired or near expiry."""
        return time.time() >= (tokens.expires_at - self._expiry_buffer)

    async def _try_refresh(
        self, provider: str, tokens: OAuthTokens
    ) -> Optional[OAuthTokens]:
        """Attempt to refresh expired tokens.

        Args:
            provider: Provider name for storage.
            tokens: Current tokens with refresh_token.

        Returns:
            New tokens if refresh succeeded, None otherwise.
        """
        try:
            client = OpenAIOAuthClient()
            new_tokens = await client.refresh_access_token(
                tokens.refresh_token,
                previous_account_id=tokens.account_id,
            )
            await self.store_tokens(provider, new_tokens)
            logger.info(f"Auto-refreshed OAuth token for {provider}")
            return new_tokens
        except OAuthError as e:
            logger.error(f"Token refresh failed for {provider}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error refreshing {provider} token: {e}")
            return None
