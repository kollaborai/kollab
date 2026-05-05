"""OpenAI OAuth device code flow.

Implements OpenAI's custom device authorization flow for ChatGPT
Plus/Pro users, matching the OpenCode/Codex CLI implementation.

Uses the same public client_id as OpenAI's Codex CLI.

Flow (4 steps):
  1. POST auth.openai.com/api/accounts/deviceauth/usercode
     -> device_auth_id, user_code, interval
  2. User visits auth.openai.com/codex/device and enters code
  3. Poll auth.openai.com/api/accounts/deviceauth/token
     -> authorization_code, code_verifier
  4. Exchange auth code at auth.openai.com/oauth/token (PKCE)
     -> access_token, refresh_token

The raw access_token (OIDC JWT) is used as Bearer token against
chatgpt.com/backend-api/codex/responses -- NOT api.openai.com.
The ChatGPT backend accepts the OIDC session token directly.

Sources:
  github.com/anomalyco/opencode (packages/opencode/src/plugin/codex.ts)
  github.com/openai/codex/blob/main/codex-rs/login/src/device_code_auth.rs
"""

import asyncio
import base64
import json as _json
import logging
import time
import webbrowser
from dataclasses import dataclass, field
from typing import Any, List, Optional
from urllib.parse import quote as urlquote

import aiohttp

logger = logging.getLogger(__name__)

# Constants from Codex CLI source (codex-rs/core/src/auth.rs)
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"

# Endpoints - all derived from issuer
AUTH_ISSUER_URL = "https://auth.openai.com"
API_BASE_URL = f"{AUTH_ISSUER_URL}/api/accounts"

DEVICE_USERCODE_URL = f"{API_BASE_URL}/deviceauth/usercode"
DEVICE_TOKEN_URL = f"{API_BASE_URL}/deviceauth/token"
DEVICE_VERIFY_URL = f"{AUTH_ISSUER_URL}/codex/device"
TOKEN_EXCHANGE_URL = f"{AUTH_ISSUER_URL}/oauth/token"

# Redirect URI for device code PKCE exchange
DEVICE_CODE_REDIRECT_URI = f"{AUTH_ISSUER_URL}/deviceauth/callback"

# ChatGPT backend API - where OAuth tokens actually work
# (NOT api.openai.com which requires scoped API keys)
CODEX_API_BASE_URL = "https://chatgpt.com/backend-api/codex"

# Timeouts
DEVICE_CODE_TIMEOUT = 900  # 15 minutes (matches Codex CLI)
HTTP_TIMEOUT = 30

# Token lifetime
TOKEN_REFRESH_DAYS = 8


def _parse_jwt_claims(token: str) -> Optional[dict[str, Any]]:
    """Parse claims from a JWT without verifying signature."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        # Add padding for base64url decoding
        payload = parts[1]
        payload += "=" * (4 - len(payload) % 4)
        decoded = base64.urlsafe_b64decode(payload)
        result: dict[str, Any] = _json.loads(decoded)
        return result
    except Exception:
        return None


def _extract_account_id(claims: dict[str, Any]) -> Optional[str]:
    """Extract ChatGPT account ID from JWT claims."""
    # Try multiple claim locations (matches OpenCode's extractAccountIdFromClaims)
    account_id = claims.get("chatgpt_account_id")
    if account_id:
        return str(account_id)

    auth_claims = claims.get("https://api.openai.com/auth", {})
    if isinstance(auth_claims, dict):
        account_id = auth_claims.get("chatgpt_account_id")
        if account_id:
            return str(account_id)

    orgs = claims.get("organizations", [])
    if isinstance(orgs, list) and orgs:
        first_org = orgs[0]
        if isinstance(first_org, dict):
            return first_org.get("id")

    return None


def extract_account_id_from_token(token: str) -> Optional[str]:
    """Extract ChatGPT account ID from an OAuth token JWT."""
    claims = _parse_jwt_claims(token)
    if not claims:
        return None
    return _extract_account_id(claims)


@dataclass
class OAuthTokens:
    """OAuth token set returned after successful authentication."""

    access_token: str
    refresh_token: str
    expires_at: float  # unix timestamp
    account_id: Optional[str] = field(default=None)

    @property
    def is_expired(self) -> bool:
        return time.time() >= self.expires_at

    def expires_in_seconds(self) -> float:
        return max(0, self.expires_at - time.time())

    def to_dict(self) -> dict:
        result = {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
        }
        if self.account_id:
            result["account_id"] = self.account_id
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "OAuthTokens":
        return cls(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_at=data["expires_at"],
            account_id=data.get("account_id"),
        )


class OAuthError(Exception):
    """OAuth flow error."""

    pass


@dataclass
class DeviceCodeResponse:
    """Response from the device usercode endpoint."""

    device_auth_id: str
    user_code: str
    interval: int


@dataclass
class DeviceTokenResponse:
    """Response from device token polling (before PKCE exchange)."""

    authorization_code: str
    code_verifier: str


class OpenAIOAuthClient:
    """OpenAI OAuth device code flow client.

    Implements OpenAI's custom device authorization flow.
    Terminal-friendly: no local HTTP server needed.

    Flow:
      1. Request device code (usercode endpoint)
      2. User visits verification URL and enters code
      3. Poll token endpoint until user completes
      4. Exchange authorization_code via PKCE for real tokens
    """

    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT)
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                headers={"User-Agent": "kollab/1.0"},
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def device_code_flow(
        self,
        open_browser: bool = True,
    ) -> OAuthTokens:
        """Run the full device code authorization flow.

        Args:
            open_browser: Auto-open browser to verification URL.

        Returns:
            OAuthTokens with access and refresh tokens.

        Raises:
            OAuthError: If flow fails or times out.
        """
        try:
            # Step 1: Request device code
            device = await self._request_device_code()
            logger.info(f"Device code received, user_code={device.user_code}")

            # Step 2: Open browser to verification URL
            if open_browser:
                try:
                    webbrowser.open(DEVICE_VERIFY_URL)
                except Exception as e:
                    logger.warning(f"Could not open browser: {e}")

            # Step 3: Poll for authorization_code
            auth_resp = await self._poll_for_auth_code(device)

            # Step 4: Exchange authorization_code for real tokens via PKCE
            tokens = await self._exchange_code(
                auth_resp.authorization_code,
                auth_resp.code_verifier,
            )
            return tokens

        finally:
            await self.close()

    async def _request_device_code(self) -> DeviceCodeResponse:
        """Request a device code.

        POST https://auth.openai.com/api/accounts/deviceauth/usercode
        Body: {"client_id": "..."}

        Returns:
            DeviceCodeResponse with device_auth_id, user_code, interval.
        """
        session = await self._get_session()

        # Only send client_id - scope/audience are ignored by the server
        # for device code flow (matches OpenCode's implementation)
        payload = {
            "client_id": CLIENT_ID,
        }

        async with session.post(
            DEVICE_USERCODE_URL,
            json=payload,
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise OAuthError(
                    f"Device code request failed (HTTP {resp.status}): {text[:200]}"
                )

            body = await resp.json()

            device_auth_id = body.get("device_auth_id")
            user_code = body.get("user_code")
            interval = body.get("interval", 5)

            if not device_auth_id or not user_code:
                raise OAuthError(
                    f"Missing fields in device code response: {list(body.keys())}"
                )

            return DeviceCodeResponse(
                device_auth_id=device_auth_id,
                user_code=user_code,
                interval=int(interval) if isinstance(interval, (int, float)) else 5,
            )

    async def _poll_for_auth_code(
        self,
        device: DeviceCodeResponse,
    ) -> DeviceTokenResponse:
        """Poll chatgpt.com until user completes authorization.

        POST https://chatgpt.com/api/accounts/deviceauth/token
        Body: {"device_auth_id": "...", "user_code": "..."}

        Returns:
            DeviceTokenResponse with authorization_code and code_verifier.
        """
        session = await self._get_session()
        deadline = time.time() + DEVICE_CODE_TIMEOUT
        interval = device.interval

        payload = {
            "device_auth_id": device.device_auth_id,
            "user_code": device.user_code,
        }

        while time.time() < deadline:
            await asyncio.sleep(interval)

            try:
                async with session.post(
                    DEVICE_TOKEN_URL,
                    json=payload,
                ) as resp:
                    body = await resp.json()

                    if resp.status == 200:
                        auth_code = body.get("authorization_code")
                        code_verifier = body.get("code_verifier", "")

                        if not auth_code:
                            raise OAuthError(
                                "No authorization_code in device token response"
                            )

                        return DeviceTokenResponse(
                            authorization_code=auth_code,
                            code_verifier=code_verifier,
                        )

                    # Not ready yet - check for specific error states
                    error = body.get("error", "")
                    detail = body.get("detail", "")

                    if resp.status == 403 or "pending" in str(detail).lower():
                        # User hasn't completed auth yet, keep polling
                        continue
                    elif "expired" in str(detail).lower():
                        raise OAuthError("Device code expired. Please try again.")
                    elif "denied" in str(detail).lower():
                        raise OAuthError("Authorization was denied by the user.")
                    elif resp.status == 400:
                        # Likely still pending, keep polling
                        continue
                    else:
                        # Unknown non-200 status, might be transient
                        logger.debug(
                            f"Device token poll: HTTP {resp.status}, "
                            f"error={error}, detail={detail}"
                        )
                        continue

            except aiohttp.ClientError as e:
                logger.warning(f"Network error during polling: {e}")
                await asyncio.sleep(interval)
                continue

        raise OAuthError(
            f"Timed out waiting for authorization ({DEVICE_CODE_TIMEOUT}s). "
            "Please try again."
        )

    async def _exchange_code(
        self,
        authorization_code: str,
        code_verifier: str,
    ) -> OAuthTokens:
        """Exchange authorization_code for OAuth tokens via PKCE.

        The raw access_token (OIDC JWT) is used as-is as a Bearer token
        against chatgpt.com/backend-api/codex/responses. No API key
        exchange is needed - the ChatGPT backend accepts OIDC tokens.

        Returns:
            OAuthTokens with access_token, refresh_token, and account_id.
        """
        session = await self._get_session()

        form_body = (
            f"grant_type=authorization_code"
            f"&code={urlquote(authorization_code)}"
            f"&redirect_uri={urlquote(DEVICE_CODE_REDIRECT_URI)}"
            f"&client_id={urlquote(CLIENT_ID)}"
            f"&code_verifier={urlquote(code_verifier)}"
        )

        async with session.post(
            TOKEN_EXCHANGE_URL,
            data=form_body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise OAuthError(
                    f"Token exchange failed (HTTP {resp.status}): {text[:300]}"
                )

            body = await resp.json()

            access_token = body.get("access_token")
            refresh_token = body.get("refresh_token", "")
            id_token = body.get("id_token", "")

            if not access_token:
                raise OAuthError("No access_token in exchange response")

            expires_in = body.get("expires_in", TOKEN_REFRESH_DAYS * 86400)

        # Extract account_id from id_token or access_token JWT claims
        account_id = None
        if id_token:
            account_id = extract_account_id_from_token(id_token)
        if not account_id:
            account_id = extract_account_id_from_token(access_token)

        if account_id:
            logger.info("Extracted account_id from JWT claims")

        return OAuthTokens(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=time.time() + expires_in,
            account_id=account_id,
        )

    async def refresh_access_token(
        self,
        refresh_token: str,
        previous_account_id: Optional[str] = None,
    ) -> OAuthTokens:
        """Refresh an expired access token.

        Args:
            refresh_token: The refresh token from initial auth.
            previous_account_id: Account ID from previous tokens (preserved if
                new token doesn't contain one).

        Returns:
            New OAuthTokens with fresh access_token.

        Raises:
            OAuthError: If refresh fails (user needs to re-authenticate).
        """
        try:
            session = await self._get_session()

            form_body = (
                f"grant_type=refresh_token"
                f"&client_id={urlquote(CLIENT_ID)}"
                f"&refresh_token={urlquote(refresh_token)}"
            )

            async with session.post(
                TOKEN_EXCHANGE_URL,
                data=form_body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise OAuthError(
                        f"Refresh failed (HTTP {resp.status}): {text[:300]}"
                    )

                body = await resp.json()

                access_token = body.get("access_token")
                new_refresh = body.get("refresh_token", refresh_token)
                expires_in = body.get("expires_in", TOKEN_REFRESH_DAYS * 86400)

                if not access_token:
                    raise OAuthError("No access_token in refresh response")

                # Extract account_id from new token, fall back to previous
                account_id = extract_account_id_from_token(access_token)
                if not account_id:
                    account_id = previous_account_id

                return OAuthTokens(
                    access_token=access_token,
                    refresh_token=new_refresh,
                    expires_at=time.time() + expires_in,
                    account_id=account_id,
                )
        finally:
            await self.close()


async def query_codex_models(
    access_token: str,
    account_id: Optional[str] = None,
) -> List[str]:
    """Query available models from the ChatGPT codex backend.

    GET {CODEX_API_BASE_URL}/models with Bearer token.

    Args:
        access_token: Valid OAuth access token.
        account_id: Optional ChatGPT account ID for header.

    Returns:
        List of model ID strings, or empty on failure.
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "User-Agent": "kollab/1.0",
        "originator": "kollabor",
    }
    if account_id:
        headers["ChatGPT-Account-Id"] = account_id

    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(f"{CODEX_API_BASE_URL}/models") as resp:
                if resp.status != 200:
                    logger.warning(f"Models query failed (HTTP {resp.status})")
                    return []

                body = await resp.json()

                # OpenAI /models returns {"data": [{"id": "...", ...}]}
                # or could be a flat list of strings
                models = []
                data = body if isinstance(body, list) else body.get("data", [])
                for item in data:
                    if isinstance(item, str):
                        models.append(item)
                    elif isinstance(item, dict) and "id" in item:
                        models.append(item["id"])

                logger.info(f"Codex models available: {models}")
                return models

    except Exception as e:
        logger.warning(f"Failed to query codex models: {e}")
        return []


def pick_best_model(models: List[str], fallback: str = "codex") -> str:
    """Pick the best codex model from a list.

    Prefers codex-suffixed models, then highest version number.

    Args:
        models: List of model ID strings.
        fallback: Model to use if list is empty.

    Returns:
        Best model ID string.
    """
    if not models:
        return fallback

    import re

    # Prefer codex models
    codex_models = [m for m in models if "codex" in m.lower()]
    pool = codex_models if codex_models else models

    def version_key(model_id: str):
        nums = re.findall(r"[\d]+(?:\.[\d]+)?", model_id)
        return [float(n) for n in nums] if nums else [0.0]

    pool.sort(key=version_key, reverse=True)
    return pool[0]
