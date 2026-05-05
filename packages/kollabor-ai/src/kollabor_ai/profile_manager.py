"""
LLM Profile Manager.

Manages named LLM configuration profiles that define:
- Provider type (OpenAI, Anthropic, Azure, Custom)
- Model name
- Temperature and other parameters
- API key

Profiles can be defined in config.json under core.llm.profiles.
"""

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from kollabor_config.config_utils import (
    get_existing_global_config_path,
    get_global_config_path,
    get_global_config_path_candidates,
    get_local_config_path,
    get_local_config_path_candidates,
)

logger = logging.getLogger(__name__)

# Sentinel prefix for keys stored in OS keyring
KEYRING_SENTINEL_PREFIX = "secret:keyring:"


def _keyring_get(key_name: str) -> Optional[str]:
    """Retrieve a value from OS keyring (sync, graceful fallback).

    Uses the same service name as APIKeyManager from security.py
    so keys are interchangeable between the async and sync paths.
    """
    try:
        import keyring
        from kollabor_ai.providers.security import APIKeyManager

        return keyring.get_password(APIKeyManager.SERVICE_NAME, key_name)
    except Exception:
        return None


def _keyring_set(key_name: str, value: str) -> bool:
    """Store a value in OS keyring (sync, graceful fallback).

    Returns True on success, False if keyring is unavailable or fails.
    """
    try:
        import keyring
        from kollabor_ai.providers.security import APIKeyManager

        keyring.set_password(APIKeyManager.SERVICE_NAME, key_name, value)
        return True
    except Exception:
        return False


@dataclass
class EnvVarHint:
    """Information about a profile's env var."""

    name: str  # e.g., "KOLLAB_CLAUDE_TOKEN"
    is_set: bool  # True if env var exists and is non-empty


@dataclass
class LLMProfile:
    """
    Configuration profile for LLM settings.

    Attributes:
        name: Profile identifier
        provider: Provider type (openai, anthropic, azure_openai, gemini,
            openai_responses, openrouter, custom)
        model: Model name/identifier
        temperature: Sampling temperature (0.0-1.0)
        max_tokens: Maximum tokens to generate (None = no limit)
        timeout: Request timeout in milliseconds (0 = no timeout)
        description: Human-readable description
        extra_headers: Additional HTTP headers to include
        api_key: API key for provider

    API keys are resolved via environment variables using pattern:
    KOLLAB_{PROFILE_NAME}_API_KEY (e.g., KOLLAB_CLAUDE_API_KEY)
    or stored directly in config.json.
    """

    name: str
    provider: str  # "openai", "anthropic", "azure_openai", "gemini", "openai_responses", "openrouter", "custom"
    model: str = ""
    temperature: float = 0.7
    max_tokens: Optional[int] = None
    timeout: int = 0
    description: str = ""
    extra_headers: Dict[str, str] = field(default_factory=dict)
    api_key: str = field(default="", repr=False)
    base_url: str = ""  # For custom providers
    top_p: Optional[float] = None  # Nucleus sampling (0.0-1.0)
    streaming: bool = True  # Enable streaming responses
    supports_tools: bool = True  # Enable tool/function calling
    auth_type: str = ""  # "oauth" for OAuth tokens, empty for api_key

    def _get_env_key(self, field: str) -> str:
        """Generate env var key for this profile and field.

        Normalizes profile name: strip whitespace, all non-alphanumeric chars become underscore.
        Examples:
            my-local-llm -> KOLLAB_MY_LOCAL_LLM_{FIELD}
            my.profile   -> KOLLAB_MY_PROFILE_{FIELD}
            My Profile!  -> KOLLAB_MY_PROFILE__{FIELD}
            "  fast  "   -> KOLLAB_FAST_{FIELD}
        """
        # Strip whitespace, replace all non-alphanumeric with underscore, then uppercase
        name_stripped = self.name.strip()
        name_normalized = re.sub(r"[^a-zA-Z0-9]", "_", name_stripped).upper()
        return f"KOLLAB_{name_normalized}_{field}"

    def _get_env_value(self, field: str) -> Optional[str]:
        """Get env var value, treating empty/whitespace-only as unset.

        Returns:
            The env var value if set and non-empty, None otherwise.
            Note: "0" is a valid value and will be returned (not treated as falsy).
        """
        env_key = self._get_env_key(field)
        env_val = os.environ.get(env_key)
        # Check for None (unset) or empty/whitespace-only
        if env_val is None or not env_val.strip():
            return None
        return env_val

    @staticmethod
    def _get_global_env_value(field: str) -> Optional[str]:
        """Get global KOLLAB_{FIELD} env var value (no profile name).

        This allows users to set overrides like KOLLAB_MODEL=gpt-5.4
        that apply to whatever profile is active, without needing to know
        the profile name.

        Resolution order in getters:
            KOLLAB_{NAME}_{FIELD} > KOLLAB_{FIELD} > config value > default

        Returns:
            The env var value if set and non-empty, None otherwise.
        """
        env_key = f"KOLLAB_{field}"
        env_val = os.environ.get(env_key)
        if env_val is None or not env_val.strip():
            return None
        return env_val

    def get_model(self) -> str:
        """Get model, checking env var first. REQUIRED field."""
        env_val = self._get_env_value("MODEL")
        if env_val:
            return env_val
        global_val = self._get_global_env_value("MODEL")
        if global_val:
            return global_val
        if self.model:
            return self.model
        # All sources empty - warn user
        logger.warning(
            f"Profile '{self.name}': No model configured. "
            f"Set {self._get_env_key('MODEL')} or configure in config.json"
        )
        return ""

    def get_max_tokens(self) -> Optional[int]:
        """Get max tokens, checking env var first. OPTIONAL field."""
        env_key = self._get_env_key("MAX_TOKENS")
        env_val = self._get_env_value("MAX_TOKENS")
        if env_val:
            try:
                return int(env_val)
            except ValueError:
                logger.warning(
                    f"Profile '{self.name}': {env_key}='{env_val}' is not a valid integer, "
                    f"using config value"
                )
        global_val = self._get_global_env_value("MAX_TOKENS")
        if global_val:
            try:
                return int(global_val)
            except ValueError:
                logger.warning(
                    f"KOLLAB_MAX_TOKENS='{global_val}' is not a valid integer, "
                    f"using config value"
                )
        return self.max_tokens  # Returns None if not configured (uses API default)

    def get_temperature(self) -> float:
        """Get temperature, checking env var first. OPTIONAL field (default: 0.7)."""
        env_key = self._get_env_key("TEMPERATURE")
        env_val = self._get_env_value("TEMPERATURE")
        if env_val:
            try:
                return float(env_val)
            except ValueError:
                logger.warning(
                    f"Profile '{self.name}': {env_key}='{env_val}' is not a valid float, "
                    f"using config value"
                )
        global_val = self._get_global_env_value("TEMPERATURE")
        if global_val:
            try:
                return float(global_val)
            except ValueError:
                logger.warning(
                    f"KOLLAB_TEMPERATURE='{global_val}' is not a valid float, "
                    f"using config value"
                )
        return self.temperature if self.temperature is not None else 0.7

    def get_timeout(self) -> int:
        """Get timeout in seconds, checking env var first. OPTIONAL field (default: 120s).

        Note: 0 means no timeout (infinity), not a fallback value.
        """
        env_key = self._get_env_key("TIMEOUT")
        env_val = self._get_env_value("TIMEOUT")
        if env_val is not None:
            try:
                return int(env_val)
            except ValueError:
                logger.warning(
                    f"Profile '{self.name}': {env_key}='{env_val}' is not a valid integer, "
                    f"using config value"
                )
        global_val = self._get_global_env_value("TIMEOUT")
        if global_val is not None:
            try:
                return int(global_val)
            except ValueError:
                logger.warning(
                    f"KOLLAB_TIMEOUT='{global_val}' is not a valid integer, "
                    f"using config value"
                )
        # 0 is valid (no timeout), only use default if truly None
        if self.timeout is not None:
            return self.timeout
        return 30000

    def get_endpoint(self) -> str:
        """Get endpoint URL, checking env var first. OPTIONAL field."""
        env_val = self._get_env_value("BASE_URL")
        if env_val:
            return env_val
        global_val = self._get_global_env_value("BASE_URL")
        if global_val:
            return global_val
        return self.base_url or ""

    def get_api_key(self) -> str:
        """Get API key with tiered resolution.

        Resolution order:
        1. Profile-specific env var (KOLLAB_{NAME}_API_KEY)
        2. Global env var (KOLLAB_API_KEY)
        3. Sentinel in config -> resolve from OS keyring
        4. Keyring lookup by profile name (auto-migration path)
        5. Plaintext from config (backwards compat fallback)
        """
        # 1-2. Environment variables (highest priority)
        env_val = self._get_env_value("API_KEY")
        if env_val:
            return env_val
        global_val = self._get_global_env_value("API_KEY")
        if global_val:
            return global_val

        raw = self.api_key or ""

        # 3. Sentinel string -> resolve from keyring
        if raw.startswith(KEYRING_SENTINEL_PREFIX):
            keyring_key = raw[len(KEYRING_SENTINEL_PREFIX):]
            resolved = _keyring_get(keyring_key)
            if resolved:
                return resolved
            logger.warning(
                f"Profile '{self.name}': sentinel found but keyring "
                f"lookup failed for '{keyring_key}'"
            )
            return ""

        # 4. Plaintext key in config -- try to auto-migrate to keyring (once)
        if raw:
            if not getattr(self, '_keyring_migrated', False):
                migrated = _keyring_set(self.name, raw)
                if migrated:
                    self._keyring_migrated = True
                    logger.info(
                        f"Profile '{self.name}': auto-migrated API key to OS keyring"
                    )
            return raw

        return ""

    def get_top_p(self) -> Optional[float]:
        """Get top_p, checking env var first. OPTIONAL field."""
        env_val = self._get_env_value("TOP_P")
        if env_val:
            try:
                return float(env_val)
            except ValueError:
                pass
        global_val = self._get_global_env_value("TOP_P")
        if global_val:
            try:
                return float(global_val)
            except ValueError:
                pass
        return self.top_p

    def get_streaming(self) -> bool:
        """Get streaming setting, checking env var first. Default: True."""
        env_val = self._get_env_value("STREAMING")
        if env_val is not None:
            return env_val.lower() in ("true", "1", "yes", "on")
        global_val = self._get_global_env_value("STREAMING")
        if global_val is not None:
            return global_val.lower() in ("true", "1", "yes", "on")
        return self.streaming

    def get_supports_tools(self) -> bool:
        """Get supports_tools setting, checking env var first. Default: True."""
        env_val = self._get_env_value("SUPPORTS_TOOLS")
        if env_val is not None:
            return env_val.lower() in ("true", "1", "yes", "on")
        global_val = self._get_global_env_value("SUPPORTS_TOOLS")
        if global_val is not None:
            return global_val.lower() in ("true", "1", "yes", "on")
        return self.supports_tools

    def get_env_var_hints(self) -> Dict[str, EnvVarHint]:
        """Get env var names and status for this profile.

        Includes both profile-specific (KOLLAB_{NAME}_{FIELD}) and
        global (KOLLAB_{FIELD}) env var hints. Global vars use the
        key prefix "global_" (e.g., "global_model").
        """
        fields = [
            "MODEL",
            "PROVIDER",
            "BASE_URL",
            "API_KEY",
            "MAX_TOKENS",
            "TEMPERATURE",
            "TIMEOUT",
            "TOP_P",
            "STREAMING",
            "SUPPORTS_TOOLS",
            "DESCRIPTION",
            "EXTRA_HEADERS",
        ]
        hints = {}
        for field in fields:  # noqa: F402
            # Profile-specific hint
            hints[field.lower()] = EnvVarHint(
                name=self._get_env_key(field),
                is_set=self._get_env_value(field) is not None,
            )
            # Global hint
            hints[f"global_{field.lower()}"] = EnvVarHint(
                name=f"KOLLAB_{field}",
                is_set=self._get_global_env_value(field) is not None,
            )
        return hints

    def get_provider(self) -> str:
        """Get provider, checking env var first. OPTIONAL field (default: openai).

        Supported values: openai, anthropic, azure_openai, gemini, openai_responses, openrouter, custom
        """
        env_val = self._get_env_value("PROVIDER")
        if env_val:
            return env_val.lower()
        global_val = self._get_global_env_value("PROVIDER")
        if global_val:
            return global_val.lower()
        return self.provider or "openai"

    def get_provider_type(self) -> str:
        """
        Get provider type from profile.

        Returns:
            Provider type string from the profile's provider field.
            (Uses get_provider() which checks env var first.)
        """
        return self.get_provider()

    def to_dict(self) -> Dict[str, Any]:
        """Convert profile to dictionary representation.

        Always uses provider format (all profiles must have provider field).
        Uses getter methods to resolve env var overrides.
        """
        result = {
            "name": self.name,
            "provider": self.get_provider(),
            "model": self.get_model(),
            "temperature": self.get_temperature(),
            "max_tokens": self.get_max_tokens(),
            "timeout": self.get_timeout(),
            "description": self.description,
        }

        # Include api_key (resolved from env var if set)
        api_key = self.get_api_key()
        if api_key:
            result["api_key"] = api_key

        # Include base_url for custom providers
        base_url = self.get_endpoint()
        if base_url:
            result["base_url"] = base_url

        # Include optional fields
        top_p = self.get_top_p()
        if top_p is not None:
            result["top_p"] = top_p

        if self.extra_headers:
            result["extra_headers"] = self.extra_headers

        # Feature flags
        result["streaming"] = self.get_streaming()
        result["supports_tools"] = self.get_supports_tools()

        # Auth type (oauth profiles)
        if self.auth_type:
            result["auth_type"] = self.auth_type

        return result

    @classmethod
    def from_dict(cls, name: str, data: Dict[str, Any]) -> "LLMProfile":
        """
        Create profile from dictionary.

        Silently ignores unknown fields for forward compatibility.

        Args:
            name: Profile name
            data: Profile configuration dictionary (must have provider field)

        Returns:
            LLMProfile instance

        Raises:
            ValueError: If provider field is missing
        """
        # Provider field is required
        provider = data.get("provider")
        if not provider:
            raise ValueError(f"Profile '{name}' is missing required 'provider' field")

        return cls(
            name=name,
            provider=provider,
            api_key=data.get("api_key", ""),
            model=data.get("model", ""),
            temperature=data.get("temperature", 0.7),
            max_tokens=data.get("max_tokens"),
            timeout=data.get("timeout", 0),
            description=data.get("description", ""),
            extra_headers=data.get("extra_headers", {}),
            base_url=data.get("base_url", ""),
            top_p=data.get("top_p"),
            streaming=data.get("streaming", True),
            supports_tools=data.get("supports_tools", True),
            auth_type=data.get("auth_type", ""),
        )


class ProfileManager:
    """
    Manages LLM configuration profiles.

    Features:
    - Built-in default profiles (default, fast, claude, openai)
    - User-defined profiles from config.json
    - Active profile switching
    - Adapter instantiation for profiles
    """

    # Standard provider env vars -> auto-profile defaults
    # Order matters: first match wins when multiple keys are set
    PROVIDER_ENV_MAP: List[Dict[str, Any]] = [
        {
            "env_var": "ANTHROPIC_API_KEY",
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
            "profile_name": "anthropic-auto",
            "description": "Auto-detected from ANTHROPIC_API_KEY",
            "base_url_env": "ANTHROPIC_BASE_URL",
        },
        {
            "env_var": "OPENAI_API_KEY",
            "provider": "openai",
            "model": "gpt-5.4",
            "profile_name": "openai-auto",
            "description": "Auto-detected from OPENAI_API_KEY",
        },
        {
            "env_var": "AZURE_OPENAI_API_KEY",
            "provider": "azure_openai",
            "model": "gpt-5.4",
            "profile_name": "azure-auto",
            "description": "Auto-detected from AZURE_OPENAI_API_KEY",
            "requires_env": "AZURE_OPENAI_ENDPOINT",
        },
        {
            "env_var": "GEMINI_API_KEY",
            "provider": "gemini",
            "model": "gemini-3.1-pro-preview",
            "profile_name": "gemini-auto",
            "description": "Auto-detected from GEMINI_API_KEY",
        },
        {
            "env_var": "OPENROUTER_API_KEY",
            "provider": "openrouter",
            "model": "anthropic/claude-sonnet-4-6",
            "profile_name": "openrouter-auto",
            "description": "Auto-detected from OPENROUTER_API_KEY",
        },
        {
            "env_var": "XAI_API_KEY",
            "provider": "custom",
            "model": "grok-4-1-fast-reasoning",
            "base_url": "https://api.x.ai/v1",
            "profile_name": "xai-auto",
            "description": "Auto-detected from XAI_API_KEY",
        },
        {
            "env_var": "ZAI_API_KEY",
            "provider": "custom",
            "model": "glm-5",
            "base_url": "https://api.z.ai/api/paas/v4",
            "profile_name": "zai-auto",
            "description": "Auto-detected from ZAI_API_KEY",
        },
        {
            "env_var": "MOONSHOT_API_KEY",
            "provider": "custom",
            "model": "kimi-k2.5",
            "base_url": "https://api.moonshot.ai/v1",
            "profile_name": "kimi-auto",
            "description": "Auto-detected from MOONSHOT_API_KEY",
        },
    ]

    # Built-in default profiles (all use provider format)
    DEFAULT_PROFILES: Dict[str, Dict[str, Any]] = {
        "default": {
            "provider": "auto",
            "model": "",
            "temperature": 0.7,
            "description": "Auto-detect from env vars, fallback to local LLM",
        },
        "local": {
            "provider": "custom",
            "base_url": "http://localhost:1234/v1",
            "model": "qwen3-4b",
            "temperature": 0.7,
            "description": "Local LLM via LM Studio / Ollama",
        },
        "claude": {
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
            "temperature": 0.7,
            "description": "Anthropic Claude for complex tasks",
        },
        "openai": {
            "provider": "openai",
            "model": "gpt-5.4",
            "temperature": 0.7,
            "description": "OpenAI GPT-5.3 for general tasks",
        },
    }

    def __init__(self, config=None, cli_profile: Optional[str] = None):
        """
        Initialize profile manager.

        Args:
            config: Configuration object with get() method
            cli_profile: CLI --profile override (skips auto-detection when set)
        """
        self.config = config
        self._profiles: Dict[str, LLMProfile] = {}
        self._active_profile_name: str = "default"
        # Treat a CLI --profile selection as "explicitly set" so the
        # oauth auto-activation path below won't override it. The actual
        # activation happens later via set_active_profile(cli_profile)
        # in application.py __init__.
        self._profile_explicitly_set: bool = bool(cli_profile)
        self._is_auto_detected: bool = False
        self._auto_detected_source: Optional[str] = None
        self._load_profiles()

        # Always register OAuth profiles -- they're discoverable via the
        # token file regardless of whether the user asked for one. The
        # auto-activation guard inside _detect_oauth_provider checks
        # _profile_explicitly_set (which we just flipped to True above
        # when cli_profile is set) so it won't clobber a CLI selection.
        # Env detection, on the other hand, creates an ephemeral profile
        # and auto-activates unconditionally, so we still skip it when
        # the user explicitly asked for a profile by name.
        #
        # Bug history: previously both detections were gated behind
        # `not cli_profile`, which meant `--profile openai-oauth` would
        # skip the oauth registration step -- the profile would not exist
        # in the registry, set_active_profile would fall back to "default",
        # and the user would see their explicit flag silently ignored.
        self._detect_oauth_provider()

        # Now that oauth profiles are registered, apply the persisted
        # active_profile from config. Previously _load_profiles tried to
        # apply it directly but oauth profiles weren't registered yet,
        # so "openai-oauth" in active_profile was silently dropped and
        # env detection won the race.
        self._apply_pending_active_profile()

        if not cli_profile:
            self._detect_env_provider()
        # Note: Default profile initialization is now handled by config_utils.initialize_config()
        # which runs earlier in app startup and creates global/local config with profiles

    def reload(self) -> None:
        """Reload profiles from config file.

        Order matches __init__: load files, register oauth, apply the
        persisted active_profile (which may BE an oauth profile), then
        env detection. The persisted active_profile must be applied
        AFTER oauth registration so oauth-only profile names resolve.
        """
        self._profiles.clear()
        self._is_auto_detected = False
        self._auto_detected_source = None
        self._load_profiles()
        self._detect_oauth_provider()
        self._apply_pending_active_profile()
        self._detect_env_provider()
        logger.info("Profiles reloaded from config")

    @property
    def is_auto_detected(self) -> bool:
        """True if the active profile was auto-detected from a provider env var."""
        return self._is_auto_detected

    @property
    def auto_detected_source(self) -> Optional[str]:
        """The env var name that triggered auto-detection, or None."""
        return self._auto_detected_source

    def _detect_env_provider(self) -> None:
        """Auto-detect LLM providers from standard env vars.

        Registers an ephemeral auto-profile for EVERY provider env var
        found in the environment, so users can pick any of them via
        /profile set. The first match (in PROVIDER_ENV_MAP priority
        order) is also auto-activated, but only when no profile has
        been explicitly selected (CLI flag or persisted config).

        Guard conditions (all must pass to do anything):
          - KOLLAB_NO_AUTO_DETECT not set
          - config kollabor.llm.auto_detect_provider is true (default)
          - KOLLAB_MODEL not set (global overrides handle that case)
        """
        # Guard: opt-out env var
        if os.environ.get("KOLLAB_NO_AUTO_DETECT"):
            logger.debug("Auto-detect skipped: KOLLAB_NO_AUTO_DETECT is set")
            return

        # Guard: config opt-out
        if self.config:
            auto_detect = self.config.get("kollabor.llm.auto_detect_provider", True)
            if not auto_detect:
                logger.debug("Auto-detect skipped: auto_detect_provider is false")
                return

        # Guard: KOLLAB_MODEL is set (global overrides apply instead)
        if os.environ.get("KOLLAB_MODEL", "").strip():
            logger.debug("Auto-detect skipped: KOLLAB_MODEL is set")
            return

        # Scan all provider env vars. Register each match as a profile
        # so /profile list shows them; only activate the first one when
        # nothing else is already selected.
        first_registered: Optional[str] = None
        first_registered_env: Optional[str] = None

        for provider_info in self.PROVIDER_ENV_MAP:
            env_var = provider_info["env_var"]
            api_key = os.environ.get(env_var, "").strip()
            if not api_key:
                continue

            # Check required companion env vars (e.g., Azure needs endpoint)
            requires_env = provider_info.get("requires_env")
            if requires_env and not os.environ.get(requires_env, "").strip():
                logger.debug(
                    f"Auto-detect skipped {env_var}: "
                    f"required {requires_env} not set"
                )
                continue

            # Build auto-profile
            profile_name = provider_info["profile_name"]
            profile_data = {
                "provider": provider_info["provider"],
                "model": provider_info["model"],
                "api_key": api_key,
                "description": provider_info["description"],
            }

            # Azure needs base_url from AZURE_OPENAI_ENDPOINT
            if requires_env and provider_info["provider"] == "azure_openai":
                profile_data["base_url"] = os.environ.get(requires_env, "").strip()

            # Openrouter uses a known base_url
            if provider_info["provider"] == "openrouter":
                profile_data["base_url"] = "https://openrouter.ai/api/v1"

            # Pass through base_url from provider info (xAI, Z.AI, Kimi, etc.)
            if "base_url" in provider_info:
                profile_data["base_url"] = provider_info["base_url"]

            # Optional per-provider base_url env var override
            # (e.g. ANTHROPIC_BASE_URL for proxies / self-hosted gateways)
            base_url_env = provider_info.get("base_url_env")
            if base_url_env:
                override = os.environ.get(base_url_env, "").strip()
                if override:
                    profile_data["base_url"] = override

            # Register ephemeral profile (don't persist). Skip if a
            # user-defined profile with the same name already exists --
            # their config wins.
            if profile_name in self._profiles:
                logger.debug(
                    f"Auto-detect: {profile_name} already in registry, "
                    f"skipping ephemeral registration"
                )
                continue

            profile = LLMProfile.from_dict(profile_name, profile_data)
            self._profiles[profile_name] = profile
            logger.info(
                f"Registered auto-profile from {env_var}: "
                f"{profile_name} ({provider_info['model']})"
            )

            if first_registered is None:
                first_registered = profile_name
                first_registered_env = env_var

        if first_registered is None:
            logger.debug("Auto-detect: no provider env vars found")
            return

        # Only auto-activate when the user hasn't made a selection
        # (CLI flag or persisted active_profile).
        if self._profile_explicitly_set:
            logger.debug(
                "Auto-detect: registered profiles but skipping activation "
                "(profile explicitly set by user)"
            )
            return

        self._active_profile_name = first_registered
        self._is_auto_detected = True
        self._auto_detected_source = first_registered_env
        logger.info(
            f"Auto-detected provider from {first_registered_env}: "
            f"profile={first_registered}"
        )

    def _detect_oauth_provider(self) -> None:
        """Register OAuth profiles from stored tokens.

        Always registers the oauth profile if valid tokens exist,
        so it appears in /profile list. Only auto-activates if no
        other profile was already detected or explicitly set.

        ChatGPT OAuth tokens are used against chatgpt.com/backend-api/codex
        (NOT api.openai.com). The raw OIDC access_token works as Bearer
        on the ChatGPT backend, with ChatGPT-Account-Id header.
        """
        # Check for stored OAuth tokens (file-based, no async needed)
        try:
            import json

            from kollabor_ai.oauth.openai_oauth import (
                CODEX_API_BASE_URL,
                OAuthTokens,
            )
            from kollabor_ai.oauth.token_storage import OAuthTokenStorage

            storage = OAuthTokenStorage()
            token_path = storage._token_path("openai")

            if not token_path.exists():
                logger.debug("OAuth detect: no stored OpenAI tokens")
                return

            try:
                raw = token_path.read_text(encoding="utf-8")
                data = json.loads(raw)
                tokens = OAuthTokens.from_dict(data)
            except (json.JSONDecodeError, KeyError, OSError) as e:
                logger.debug(f"OAuth detect: corrupt token file: {e}")
                return

            if tokens.is_expired and not tokens.refresh_token:
                logger.debug("OAuth detect: tokens expired, no refresh token")
                return

            # Build extra headers for ChatGPT backend API
            extra_headers = {
                "originator": "kollabor",
            }
            if tokens.account_id:
                extra_headers["ChatGPT-Account-Id"] = tokens.account_id

            # Register or update the oauth profile
            # If user already saved customizations (model, temp, etc.) via
            # /profile edit + Ctrl+S, those are loaded by _load_profiles()
            # and we must NOT overwrite them -- only refresh auth fields.
            profile_name = "openai-oauth"
            existing = self._profiles.get(profile_name)

            if existing:
                # Profile was loaded from config -- only refresh OAuth fields
                existing.api_key = tokens.access_token
                existing.extra_headers = extra_headers
                existing.auth_type = "oauth"
                # Ensure provider and base_url stay correct for OAuth
                existing.provider = existing.provider or "openai_responses"
                existing.base_url = existing.base_url or CODEX_API_BASE_URL
                logger.info(
                    f"Refreshed OAuth tokens for existing profile: {profile_name}"
                )
            else:
                # No saved profile -- create fresh with defaults
                profile = LLMProfile(
                    name=profile_name,
                    provider="openai_responses",
                    model="gpt-5.4",
                    api_key=tokens.access_token,
                    base_url=CODEX_API_BASE_URL,
                    extra_headers=extra_headers,
                    description="OpenAI OAuth (ChatGPT account)",
                    temperature=0.7,
                    auth_type="oauth",
                )
                self._profiles[profile_name] = profile
                logger.info(f"Registered new OAuth profile: {profile_name}")

            # Auto-activate only if nothing else is set
            if (
                not self._is_auto_detected
                and not self._profile_explicitly_set
                and not os.environ.get("KOLLAB_MODEL", "").strip()
            ):
                self._active_profile_name = profile_name
                self._is_auto_detected = True
                self._auto_detected_source = "oauth:openai"
                logger.info(f"Auto-activated OAuth profile: {profile_name}")

        except ImportError:
            logger.debug("OAuth detect: oauth module not available")
        except Exception as e:
            logger.debug(f"OAuth detect failed: {e}")

    @property
    def is_oauth_profile(self) -> bool:
        """True if the active profile was auto-detected from OAuth tokens."""
        return self._auto_detected_source == "oauth:openai"

    def _load_profiles(self) -> None:
        """Load profiles from defaults and config file.

        Reads directly from config FILE (not cached config object) to ensure
        we always get the latest saved values.

        Stores the persisted active_profile name on self for later
        application. It's NOT applied here because oauth-registered
        profiles like "openai-oauth" aren't in self._profiles yet --
        _detect_oauth_provider runs AFTER _load_profiles. The init path
        applies self._pending_active_profile after detection finishes.
        """
        # Start with built-in defaults
        for name, data in self.DEFAULT_PROFILES.items():
            self._profiles[name] = LLMProfile.from_dict(name, data)

        # Read profiles directly from config file (not cached config object)
        # This ensures we get the latest saved values after save_profile_values_to_config
        user_profiles, active_profile, default_profile = self._read_profiles_from_file()

        if user_profiles:
            for name, data in user_profiles.items():
                if isinstance(data, dict):
                    self._profiles[name] = LLMProfile.from_dict(name, data)
                    logger.debug(f"Loaded user profile: {name}")

        # Heal persisted auto-profiles: if a user-saved profile shares a
        # name with an auto-profile entry (e.g. "openrouter-auto") and the
        # provider env var is set in the environment, inject the env key
        # at load time. This recovers from older bugs that persisted masked
        # api_key values, and makes rotating the env var Just Work.
        for provider_info in self.PROVIDER_ENV_MAP:
            profile_name = provider_info["profile_name"]
            loaded = self._profiles.get(profile_name)
            if loaded is None:
                continue

            env_api_key = os.environ.get(provider_info["env_var"], "").strip()
            if env_api_key:
                loaded.api_key = env_api_key

            base_url_env = provider_info.get("base_url_env")
            if base_url_env:
                env_base_url = os.environ.get(base_url_env, "").strip()
                if env_base_url:
                    loaded.base_url = env_base_url

        # Stash the persisted active_profile name so the init path can
        # apply it AFTER oauth detection registers oauth-only profiles.
        # We don't touch self._active_profile_name here -- it stays at
        # "default" until _apply_pending_active_profile runs.
        self._pending_active_profile: Optional[str] = active_profile
        self._pending_default_profile: Optional[str] = default_profile

        logger.info(
            f"Loaded {len(self._profiles)} profiles "
            f"(pending active: {active_profile or 'default'})"
        )

    def _apply_pending_active_profile(self) -> None:
        """Activate the persisted active_profile saved by _load_profiles.

        Runs AFTER _detect_oauth_provider so oauth-registered profiles
        like "openai-oauth" are present in self._profiles when we check
        whether to activate them. This fixes the bug where the persisted
        active_profile was silently dropped because oauth detection
        hadn't registered the profile yet at _load_profiles time.
        """
        active = getattr(self, "_pending_active_profile", None)
        default = getattr(self, "_pending_default_profile", None)

        if active and active in self._profiles:
            self._active_profile_name = active
            if active != "default":
                self._profile_explicitly_set = True
            logger.info(f"Activated persisted profile: {active}")
        elif default and default != "default" and default in self._profiles:
            self._active_profile_name = default
            self._profile_explicitly_set = True
            logger.info(f"Activated persisted default profile: {default}")
        elif active:
            # The persisted active profile wasn't found -- log so users
            # know why their selection was dropped.
            logger.warning(
                f"Persisted active profile {active!r} not found in "
                f"registry; falling back to current active "
                f"{self._active_profile_name!r}"
            )

    def _read_profiles_from_file(self) -> tuple:
        """Read profiles from global and local config files.

        Local config overrides global config when both exist.

        Returns:
            Tuple of (profiles_dict, active_profile, default_profile)
        """
        def _read_config(path: Path) -> Dict[str, Any]:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
            except Exception as e:
                logger.warning(f"Failed to read config from {path}: {e}")
                return {}

        global_profiles: Dict[str, Any] = {}
        local_profiles: Dict[str, Any] = {}
        active = None
        default = "default"

        global_config = get_existing_global_config_path()
        if global_config.exists():
            config_data = _read_config(global_config)
            llm_config = config_data.get("kollabor", {}).get("llm", {})
            global_profiles = llm_config.get("profiles", {}) or {}
            active = llm_config.get("active_profile") or active
            _dp = llm_config.get("default_profile", default)
            default = _dp.get("name") if isinstance(_dp, dict) else _dp
            if global_profiles:
                logger.debug(f"Loaded profiles from: {global_config}")

        local_config = next(
            (path for path in get_local_config_path_candidates() if path.exists()),
            get_local_config_path(),
        )
        if local_config.exists():
            config_data = _read_config(local_config)
            llm_config = config_data.get("kollabor", {}).get("llm", {})
            local_profiles = llm_config.get("profiles", {}) or {}
            # Local overrides global for active/default if present
            if llm_config.get("active_profile"):
                active = llm_config.get("active_profile")
            if llm_config.get("default_profile"):
                _dp_local = llm_config.get("default_profile")
                default = (
                    _dp_local.get("name") if isinstance(_dp_local, dict) else _dp_local
                )
            if local_profiles:
                logger.debug(f"Loaded profiles from: {local_config}")

        if global_profiles or local_profiles:
            merged_profiles = {**global_profiles, **local_profiles}
            return merged_profiles, active, default

        # Fallback to config object if file read fails
        if self.config:
            return (
                self.config.get("kollabor.llm.profiles", {}),
                self.config.get("kollabor.llm.active_profile"),
                self.config.get("kollabor.llm.default_profile", "default"),
            )

        return {}, None, "default"

    def get_profile(self, name: str) -> Optional[LLMProfile]:
        """
        Get a profile by name.

        Args:
            name: Profile name

        Returns:
            LLMProfile or None if not found
        """
        return self._profiles.get(name)

    def get_active_profile(self) -> LLMProfile:
        """
        Get the currently active profile.

        Returns:
            Active LLMProfile (falls back to "default" if needed)
        """
        profile = self._profiles.get(self._active_profile_name)
        if not profile:
            logger.warning(
                f"Active profile '{self._active_profile_name}' not found, "
                "falling back to 'default'"
            )
            profile = self._profiles.get("default")
            if not profile:
                # Create minimal default profile
                profile = LLMProfile(
                    name="default",
                    provider="custom",
                    base_url="http://localhost:1234",
                    model="default",
                )
        return profile

    def set_active_profile(self, name: str, persist: bool = True) -> bool:
        """
        Set the active profile.

        If profile doesn't exist but env vars are set (KOLLAB_{NAME}_MODEL),
        auto-creates the profile from env vars.

        If profile exists AND env vars are set, env vars override the stored config
        (creates a new profile from env vars, replacing the stored one).

        Args:
            name: Profile name to activate
            persist: If True, save the selection to config for next startup

        Returns:
            True if successful, False if profile not found and can't be created
        """
        # Always try to create/update from env vars first (env vars take priority)
        if self._try_create_profile_from_env(name):
            logger.info(f"Profile '{name}' loaded from environment variables")
        elif name not in self._profiles:
            logger.error(f"Profile not found: {name}")
            return False

        old_profile = self._active_profile_name
        self._active_profile_name = name
        logger.info(f"Switched profile: {old_profile} -> {name}")

        # Persist to config so it survives restart
        if persist:
            self._save_active_profile_to_config(name)

        return True

    def _try_create_profile_from_env(self, name: str) -> bool:
        """
        Try to create a profile from environment variables.

        Looks for KOLLAB_{NAME}_* env vars and creates profile if found.
        Required: at least MODEL must be set.

        Supported env vars:
            KOLLAB_{NAME}_MODEL          - Model name (required)
            KOLLAB_{NAME}_PROVIDER       - Provider type (default: custom)
            KOLLAB_{NAME}_BASE_URL       - API endpoint URL
            KOLLAB_{NAME}_API_KEY        - API key/token
            KOLLAB_{NAME}_MAX_TOKENS     - Max tokens (integer)
            KOLLAB_{NAME}_TEMPERATURE    - Temperature (float, 0.0-2.0)
            KOLLAB_{NAME}_TIMEOUT        - Timeout in ms (integer)
            KOLLAB_{NAME}_TOP_P          - Nucleus sampling (float, 0.0-1.0)
            KOLLAB_{NAME}_STREAMING      - Enable streaming (true/false)
            KOLLAB_{NAME}_SUPPORTS_TOOLS - Enable tool calling (true/false)
            KOLLAB_{NAME}_DESCRIPTION    - Human-readable description
            KOLLAB_{NAME}_EXTRA_HEADERS  - JSON object of extra headers

        Args:
            name: Profile name to create

        Returns:
            True if profile was created successfully
        """
        import os
        import re

        # Normalize name for env var lookup
        name_normalized = re.sub(r"[^a-zA-Z0-9]", "_", name.strip()).upper()
        prefix = f"KOLLAB_{name_normalized}_"

        # Check for required field (MODEL)
        model = os.environ.get(f"{prefix}MODEL", "").strip()
        if not model:
            return False

        # Get string fields
        base_url = os.environ.get(f"{prefix}BASE_URL", "").strip()
        api_key = os.environ.get(f"{prefix}API_KEY", "").strip()
        provider = os.environ.get(f"{prefix}PROVIDER", "").strip() or "custom"
        description = os.environ.get(f"{prefix}DESCRIPTION", "").strip()

        # Parse numeric fields
        max_tokens = None
        max_tokens_str = os.environ.get(f"{prefix}MAX_TOKENS", "").strip()
        if max_tokens_str:
            try:
                max_tokens = int(max_tokens_str)
            except ValueError:
                logger.warning(f"Invalid MAX_TOKENS value: {max_tokens_str}")

        temperature = 0.7
        temp_str = os.environ.get(f"{prefix}TEMPERATURE", "").strip()
        if temp_str:
            try:
                temperature = float(temp_str)
            except ValueError:
                logger.warning(f"Invalid TEMPERATURE value: {temp_str}")

        timeout = 30000
        timeout_str = os.environ.get(f"{prefix}TIMEOUT", "").strip()
        if timeout_str:
            try:
                timeout = int(timeout_str)
            except ValueError:
                logger.warning(f"Invalid TIMEOUT value: {timeout_str}")

        top_p = None
        top_p_str = os.environ.get(f"{prefix}TOP_P", "").strip()
        if top_p_str:
            try:
                top_p = float(top_p_str)
            except ValueError:
                logger.warning(f"Invalid TOP_P value: {top_p_str}")

        # Parse boolean fields
        streaming = True
        streaming_str = os.environ.get(f"{prefix}STREAMING", "").strip().lower()
        if streaming_str:
            streaming = streaming_str in ("true", "1", "yes", "on")

        supports_tools = True
        tools_str = os.environ.get(f"{prefix}SUPPORTS_TOOLS", "").strip().lower()
        if tools_str:
            supports_tools = tools_str in ("true", "1", "yes", "on")

        # Parse extra headers (JSON)
        extra_headers = {}
        headers_str = os.environ.get(f"{prefix}EXTRA_HEADERS", "").strip()
        if headers_str:
            try:
                extra_headers = json.loads(headers_str)
                if not isinstance(extra_headers, dict):
                    extra_headers = {}
                    logger.warning("EXTRA_HEADERS must be a JSON object")
            except json.JSONDecodeError:
                logger.warning(f"Invalid EXTRA_HEADERS JSON: {headers_str}")

        # Create the profile
        profile = LLMProfile(
            name=name,
            provider=provider,
            model=model,
            base_url=base_url,
            api_key=api_key,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
            top_p=top_p,
            streaming=streaming,
            supports_tools=supports_tools,
            description=description or "Created from env vars",
            extra_headers=extra_headers,
        )

        self._profiles[name] = profile
        logger.info(
            f"Created profile '{name}' from env vars: model={model}, provider={provider}"
        )
        return True

    def _save_active_profile_to_config(self, name: str) -> bool:
        """
        Save the active profile name to global config.json.

        Profiles are user-wide settings, so they're saved to global config
        (~/.kollab/config.json) to be available across all projects.

        Args:
            name: Profile name to save as active

        Returns:
            True if saved successfully
        """
        try:
            # Profiles are user-wide, always save to global config
            config_path = get_global_config_path()
            read_path = get_existing_global_config_path()

            if not read_path.exists():
                logger.warning(f"Config file not found: {read_path}")
                return False

            config_data = json.loads(read_path.read_text(encoding="utf-8"))

            # Ensure core.llm exists
            if "kollabor" not in config_data:
                config_data["kollabor"] = {}
            if "llm" not in config_data["kollabor"]:
                config_data["kollabor"]["llm"] = {}

            # Save active profile
            config_data["kollabor"]["llm"]["active_profile"] = name

            config_path.write_text(
                json.dumps(config_data, indent=2, ensure_ascii=False), encoding="utf-8"
            )

            logger.debug(f"Saved active profile to config: {name}")
            return True

        except Exception as e:
            logger.error(f"Failed to save active profile to config: {e}")
            return False

    def save_profile_values_to_config(
        self,
        profile: "LLMProfile",
    ) -> bool:
        """
        Save a profile's values to config.json.

        Always saves to global config. Also updates local project config
        if it already exists and contains LLM profiles.

        Args:
            profile: Profile to save

        Returns:
            True if saved successfully
        """
        try:
            # Look up auto-profile metadata if this profile is an
            # env-sourced auto-profile (openrouter-auto, anthropic-auto,
            # etc.) -- we need to skip persisting the api_key and the
            # env-driven base_url so config.json stays free of secrets
            # and ANTHROPIC_BASE_URL stays env-controlled.
            auto_info = next(
                (p for p in self.PROVIDER_ENV_MAP if p["profile_name"] == profile.name),
                None,
            )

            def _build_profile_dict() -> dict:
                profile_dict = {
                    "provider": profile.provider,
                    "model": profile.model,
                    "temperature": profile.temperature,
                    "timeout": profile.timeout,
                    "description": profile.description,
                }
                if profile.max_tokens is not None:
                    profile_dict["max_tokens"] = profile.max_tokens

                # base_url: skip if this profile's base_url is env-driven
                skip_base_url = False
                if auto_info and auto_info.get("base_url_env"):
                    env_base_url = os.environ.get(auto_info["base_url_env"], "").strip()
                    if env_base_url and profile.base_url == env_base_url:
                        skip_base_url = True
                if profile.base_url and not skip_base_url:
                    profile_dict["base_url"] = profile.base_url

                if profile.api_key:
                    # Don't persist api_key if it came from env var or OAuth
                    # OAuth tokens rotate and are sourced from token file
                    if getattr(profile, "auth_type", None) == "oauth":
                        pass  # OAuth tokens refreshed from token storage
                    elif auto_info:
                        # Auto-profile from a provider env var -- never
                        # persist the key, even if the user edited this
                        # profile via /config. The env var is source of
                        # truth and the key rotates.
                        pass
                    else:
                        env_key = profile._get_env_value("API_KEY")
                        if not env_key or profile.api_key != env_key:
                            key_val = profile.api_key
                            if key_val.startswith(KEYRING_SENTINEL_PREFIX):
                                # Already a sentinel -- pass through
                                profile_dict["api_key"] = key_val
                            elif _keyring_set(profile.name, key_val):
                                # Stored in keyring -- write sentinel
                                sentinel = f"{KEYRING_SENTINEL_PREFIX}{profile.name}"
                                profile_dict["api_key"] = sentinel
                                logger.info(
                                    f"Profile '{profile.name}': stored API key "
                                    f"in OS keyring, writing sentinel to config"
                                )
                            else:
                                # Keyring unavailable -- fall back to plaintext
                                profile_dict["api_key"] = key_val
                                logger.warning(
                                    f"Profile '{profile.name}': keyring unavailable, "
                                    f"API key saved in plaintext"
                                )
                if profile.top_p is not None:
                    profile_dict["top_p"] = profile.top_p
                if not profile.streaming:
                    profile_dict["streaming"] = profile.streaming
                if not profile.supports_tools:
                    profile_dict["supports_tools"] = profile.supports_tools
                if profile.extra_headers:
                    profile_dict["extra_headers"] = profile.extra_headers
                if getattr(profile, "auth_type", None):
                    profile_dict["auth_type"] = profile.auth_type
                return profile_dict

            def _write_profile_config(config_path: Path, location: str) -> bool:
                config_path.parent.mkdir(parents=True, exist_ok=True)

                if config_path.exists():
                    config_data = json.loads(config_path.read_text(encoding="utf-8"))
                else:
                    config_data = {}

                if "kollabor" not in config_data:
                    config_data["kollabor"] = {}
                if "llm" not in config_data["kollabor"]:
                    config_data["kollabor"]["llm"] = {}
                if "profiles" not in config_data["kollabor"]["llm"]:
                    config_data["kollabor"]["llm"]["profiles"] = {}

                config_data["kollabor"]["llm"]["profiles"][
                    profile.name
                ] = _build_profile_dict()

                config_path.write_text(
                    json.dumps(config_data, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )

                logger.info(
                    f"Saved profile '{profile.name}' to {location} config: {config_path}"
                )
                return True

            global_path = get_global_config_path()
            _write_profile_config(global_path, "global")

            # Also update local config if it already has LLM profiles
            local_path = next(
                (path for path in get_local_config_path_candidates() if path.exists()),
                get_local_config_path(),
            )
            if local_path.exists():
                try:
                    local_data = json.loads(local_path.read_text(encoding="utf-8"))
                    local_profiles = (
                        local_data.get("kollabor", {}).get("llm", {}).get("profiles")
                    )
                    if local_profiles:
                        _write_profile_config(local_path, "local")
                except Exception as e:
                    logger.debug(f"Skipping local config update: {e}")

            return True

        except Exception as e:
            logger.error(f"Failed to save profile to config: {e}")
            return False

    def list_profiles(self) -> List[LLMProfile]:
        """
        List all available profiles.

        Returns:
            List of LLMProfile instances
        """
        return list(self._profiles.values())

    def get_profile_names(self) -> List[str]:
        """
        Get list of profile names.

        Returns:
            List of profile name strings
        """
        return list(self._profiles.keys())

    def add_profile(self, profile: LLMProfile) -> bool:
        """
        Add a new profile.

        Args:
            profile: LLMProfile to add

        Returns:
            True if added, False if name already exists
        """
        if profile.name in self._profiles:
            logger.warning(f"Profile already exists: {profile.name}")
            return False

        self._profiles[profile.name] = profile
        logger.info(f"Added profile: {profile.name}")
        return True

    def create_profile(
        self,
        name: str,
        base_url: str = "",
        model: str = "",
        api_key: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        provider: str = "custom",
        supports_tools: bool = True,
        description: str = "",
        timeout: int = 0,
        streaming: bool = True,
        save_to_config: bool = False,
    ) -> Optional[LLMProfile]:
        """
        Create and add a new profile.

        Args:
            name: Profile name
            base_url: API endpoint URL
            model: Model name
            api_key: API key (optional)
            temperature: Temperature setting (default 0.7)
            max_tokens: Max tokens limit (optional)
            provider: Provider type (default "custom")
            supports_tools: Enable tool calling (default True)
            description: Profile description
            timeout: Request timeout in milliseconds (0 = no timeout)
            streaming: Enable streaming responses (default True)
            save_to_config: Whether to save to config file

        Returns:
            Created LLMProfile if successful, None if name already exists
        """
        if name in self._profiles:
            logger.warning(f"Profile already exists: {name}")
            return None

        profile = LLMProfile(
            name=name,
            provider=provider,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            description=description,
            api_key=api_key or "",
            base_url=base_url,
            supports_tools=supports_tools,
            timeout=timeout,
            streaming=streaming,
        )

        self._profiles[name] = profile
        logger.info(f"Created profile: {name}")

        if save_to_config:
            self.save_profile_values_to_config(profile)

        return profile

    def remove_profile(self, name: str) -> bool:
        """
        Remove a profile.

        Cannot remove built-in profiles or the current active profile.

        Args:
            name: Profile name to remove

        Returns:
            True if removed, False if protected or not found
        """
        if name in self.DEFAULT_PROFILES:
            logger.error(f"Cannot remove built-in profile: {name}")
            return False

        if name == self._active_profile_name:
            logger.error(f"Cannot remove active profile: {name}")
            return False

        if name not in self._profiles:
            logger.error(f"Profile not found: {name}")
            return False

        del self._profiles[name]
        logger.info(f"Removed profile: {name}")
        return True

    def update_profile(
        self,
        original_name: str,
        new_name: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        temperature: Optional[float] = None,
        provider: Optional[str] = None,
        supports_tools: Optional[bool] = None,
        description: Optional[str] = None,
        save_to_config: bool = False,
    ) -> bool:
        """
        Update an existing profile.

        Args:
            original_name: Current name of the profile to update
            new_name: New name for the profile (optional, for renaming)
            base_url: API endpoint URL
            model: Model name
            api_key: API key (None to keep existing)
            temperature: Temperature setting
            provider: Provider type (openai, anthropic, etc.)
            supports_tools: Enable tool/function calling
            description: Profile description
            save_to_config: Whether to persist changes to config file

        Returns:
            True if updated successfully, False otherwise
        """
        if original_name not in self._profiles:
            logger.error(f"Profile not found: {original_name}")
            return False

        profile = self._profiles[original_name]

        # Update fields if provided
        if base_url is not None:
            profile.base_url = base_url
        if model is not None:
            profile.model = model
        if api_key is not None:
            profile.api_key = api_key
        if temperature is not None:
            profile.temperature = temperature
        if provider is not None:
            profile.provider = provider
        if supports_tools is not None:
            profile.supports_tools = supports_tools
        if description is not None:
            profile.description = description

        # Handle rename
        if new_name and new_name != original_name:
            if new_name in self._profiles:
                logger.error(f"Cannot rename: profile '{new_name}' already exists")
                return False
            profile.name = new_name
            self._profiles[new_name] = profile
            del self._profiles[original_name]

            # Update active profile name if needed
            if self._active_profile_name == original_name:
                self._active_profile_name = new_name

            logger.info(f"Renamed profile: {original_name} -> {new_name}")

        # Save to config if requested
        if save_to_config:
            self.save_profile_values_to_config(profile)

        logger.info(f"Updated profile: {profile.name}")
        return True

    def delete_profile(self, name: str) -> bool:
        """
        Delete a profile from memory and config file.

        Cannot delete built-in profiles or the current active profile.

        Args:
            name: Profile name to delete

        Returns:
            True if deleted successfully, False otherwise
        """
        if name in self.DEFAULT_PROFILES:
            logger.error(f"Cannot delete built-in profile: {name}")
            return False

        if name == self._active_profile_name:
            logger.error(f"Cannot delete active profile: {name}")
            return False

        if name not in self._profiles:
            logger.error(f"Profile not found: {name}")
            return False

        # Remove from memory
        del self._profiles[name]

        # Remove from config file
        self._delete_profile_from_config(name)

        logger.info(f"Deleted profile: {name}")
        return True

    def _delete_profile_from_config(self, name: str) -> bool:
        """
        Delete a profile from global config.json.

        Profiles are user-wide settings, so they're deleted from global config
        (~/.kollab/config.json).

        Args:
            name: Profile name to delete

        Returns:
            True if deleted successfully from config
        """
        try:
            # Profiles are user-wide, always use global config
            config_path = next(
                (path for path in get_global_config_path_candidates() if path.exists()),
                get_global_config_path(),
            )

            if not config_path.exists():
                logger.warning(f"Config file not found: {config_path}")
                return True  # No config file, nothing to delete

            # Load current config
            config_data = json.loads(config_path.read_text(encoding="utf-8"))

            # Check if profile exists in config
            profiles = (
                config_data.get("kollabor", {}).get("llm", {}).get("profiles", {})
            )
            if name not in profiles:
                logger.debug(f"Profile '{name}' not in config file")
                return True  # Not in config, nothing to delete

            # Remove profile from config
            del config_data["kollabor"]["llm"]["profiles"][name]

            # Write back
            config_path.write_text(
                json.dumps(config_data, indent=2, ensure_ascii=False), encoding="utf-8"
            )

            logger.info(f"Deleted profile from config: {name}")
            return True

        except Exception as e:
            logger.error(f"Failed to delete profile from config: {e}")
            return False

    def get_adapter_for_profile(self, profile: Optional[LLMProfile] = None):
        """
        Get the appropriate API adapter for a profile.

        Args:
            profile: Profile to get adapter for (default: active profile)

        Returns:
            Configured API adapter instance
        """
        if profile is None:
            profile = self.get_active_profile()

        from kollabor_ai.adapters import get_adapter

        return get_adapter(
            "native" if profile.get_supports_tools() else "xml",
            base_url=profile.get_endpoint(),
        )

    def is_active(self, name: str) -> bool:
        """
        Check if a profile is the active one.

        Args:
            name: Profile name

        Returns:
            True if this is the active profile
        """
        return name == self._active_profile_name

    @property
    def active_profile_name(self) -> str:
        """Get the name of the active profile."""
        return self._active_profile_name

    def _get_normalized_name(self, name: str) -> str:
        """Get normalized profile name for env var prefix.

        Strips whitespace and replaces all non-alphanumeric characters with
        underscores, then uppercases the result.

        Args:
            name: The profile name to normalize

        Returns:
            Normalized name suitable for env var prefix

        Examples:
            "my-profile" -> "MY_PROFILE"
            "my.profile" -> "MY_PROFILE"
            "My Profile!" -> "MY_PROFILE_"
            "  fast  " -> "FAST"
        """
        return re.sub(r"[^a-zA-Z0-9]", "_", name.strip()).upper()

    def _check_name_collision(
        self, new_name: str, exclude_name: Optional[str] = None
    ) -> Optional[str]:
        """Check if new profile name would collide with existing profiles.

        Two profile names collide if they normalize to the same env var prefix,
        which would cause them to share the same environment variables.

        Args:
            new_name: The proposed profile name
            exclude_name: Profile name to exclude from check (for renames)

        Returns:
            Name of colliding profile if collision found, None otherwise.
        """
        new_normalized = self._get_normalized_name(new_name)
        for existing_name in self._profiles:
            if existing_name == exclude_name:
                continue
            if self._get_normalized_name(existing_name) == new_normalized:
                return existing_name
        return None

    def get_profile_summary(self, name: Optional[str] = None) -> str:
        """
        Get a human-readable summary of a profile.

        Args:
            name: Profile name (default: active profile)

        Returns:
            Formatted summary string
        """
        profile = self._profiles.get(name) if name else self.get_active_profile()
        if not profile:
            return f"Profile '{name}' not found"

        hints = profile.get_env_var_hints()
        token_status = "[set]" if hints["token"].is_set else "[not set]"

        tool_mode = "native" if profile.get_supports_tools() else "xml"
        lines = [
            f"Profile: {profile.name}",
            f"  Endpoint: {profile.get_endpoint() or '(not configured)'}",
            f"  Model: {profile.get_model() or '(not configured)'}",
            f"  Token: {hints['token'].name} {token_status}",
            f"  Temperature: {profile.get_temperature()}",
            f"  Max Tokens: {profile.get_max_tokens() or '(API default)'}",
            f"  Timeout: {profile.get_timeout()}ms",
            f"  Tool Calling: {tool_mode}",
        ]
        if profile.description:
            lines.append(f"  Description: {profile.description}")

        return "\n".join(lines)
