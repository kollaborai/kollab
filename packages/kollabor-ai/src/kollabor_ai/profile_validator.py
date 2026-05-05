"""Profile validation functions for profile wizard.

Extracted from kollabor/commands/profile_command.py Phase 8A.
Pure validator functions with no UI dependencies.

NOTE: Add these exports to __init__.py:
    validate_profile_name,
    validate_provider,
    validate_api_key,
    validate_temperature,
    validate_max_tokens,
    validate_base_url,
    validate_yes_no,
    validate_timeout,
    validate_profile_config,
    build_profile_config,
    test_profile,
    get_provider_display_name,
    detect_provider_from_api_key,
    ProfileValidationError,
"""

import logging
import re
from typing import Any, Dict, List, Optional

from kollabor_events.models import CommandResult

logger = logging.getLogger(__name__)


# =============================================================================
# Exceptions
# =============================================================================


class ProfileValidationError(Exception):
    """Raised when profile validation fails."""

    pass


# =============================================================================
# Provider Detection (extracted from kollabor.config.loader)
# =============================================================================


def detect_provider_from_api_key(api_key: str) -> str:
    """
    Detect provider type from API key format.

    Args:
        api_key: API key to analyze

    Returns:
        Provider type: 'openai', 'anthropic', or 'auto' if unknown
    """
    if not api_key:
        return "auto"

    api_key_lower = api_key.lower()

    if api_key_lower.startswith("sk-ant-"):
        return "anthropic"
    elif api_key_lower.startswith("sk-"):
        return "openai"
    else:
        return "auto"


# =============================================================================
# Profile Validators (pure functions)
# =============================================================================


def validate_profile_name(
    value: str, profile_manager=None, existing_profiles: Optional[List[str]] = None
) -> CommandResult:
    """Validate profile name.

    Args:
        value: Profile name to validate
        profile_manager: Optional profile manager for checking existing names
        existing_profiles: Optional list of existing profile names

    Returns:
        Command result with validation status
    """
    if not value:
        return CommandResult(
            success=False,
            message="\n[err] Profile name is required",
            display_type="error",
        )

    if len(value) < 3:
        return CommandResult(
            success=False,
            message="\n[err] Profile name must be at least 3 characters",
            display_type="error",
        )

    if not re.match(r"^[a-zA-Z0-9_-]+$", value):
        return CommandResult(
            success=False,
            message="\n[err] Profile name can only contain letters, numbers, hyphens, and underscores",
            display_type="error",
        )

    # Check if profile already exists
    if profile_manager:
        existing = profile_manager.get_profile_names()
    elif existing_profiles is not None:
        existing = existing_profiles
    else:
        existing = []

    if value in existing:
        return CommandResult(
            success=False,
            message=f"\n[err] Profile '{value}' already exists. Choose a different name.",
            display_type="error",
        )

    return CommandResult(success=True, message="", display_type="info")


def validate_provider(value: str) -> CommandResult:
    """Validate provider type.

    Args:
        value: Provider to validate

    Returns:
        Command result with validation status
    """
    if not value:
        # Use default
        return CommandResult(success=True, message="", display_type="info")

    value_lower = value.lower()
    valid_providers = [
        "auto",
        "openai",
        "anthropic",
        "azure_openai",
        "gemini",
        "openai_responses",
        "openrouter",
        "custom",
    ]

    if value_lower not in valid_providers:
        return CommandResult(
            success=False,
            message=f"\n[err] Invalid provider '{value}'. Must be one of: {', '.join(valid_providers)}",
            display_type="error",
        )

    return CommandResult(success=True, message="", display_type="info")


def validate_api_key(
    value: str, current_provider: str = "auto", logger: Optional[logging.Logger] = None
) -> CommandResult:
    """Validate API key format.

    Args:
        value: API key to validate
        current_provider: Current provider selection (for warning if mismatch)
        logger: Optional logger for warnings

    Returns:
        Command result with validation status
    """
    if not value:
        return CommandResult(
            success=False,
            message="\n[err] API key is required",
            display_type="error",
        )

    # Check minimum length
    if len(value) < 10:
        return CommandResult(
            success=False,
            message="\n[err] API key appears too short (min 10 characters)",
            display_type="error",
        )

    # Detect provider from key format (for warning)
    detected = detect_provider_from_api_key(value)
    if (
        detected != "auto"
        and current_provider != "auto"
        and current_provider != detected
        and logger
    ):
        logger.warning(
            f"API key format suggests '{detected}' but provider is set to '{current_provider}'"
        )

    return CommandResult(success=True, message="", display_type="info")


def validate_temperature(value: str) -> CommandResult:
    """Validate temperature value.

    Args:
        value: Temperature to validate

    Returns:
        Command result with validation status
    """
    try:
        temp = float(value)
        if not 0.0 <= temp <= 2.0:
            return CommandResult(
                success=False,
                message=f"\n[err] Temperature must be between 0.0 and 2.0, got {temp}",
                display_type="error",
            )
        return CommandResult(success=True, message="", display_type="info")
    except ValueError:
        return CommandResult(
            success=False,
            message=f"\n[err] Temperature must be a number, got '{value}'",
            display_type="error",
        )


def validate_max_tokens(value: str) -> CommandResult:
    """Validate max tokens value.

    Args:
        value: Max tokens to validate

    Returns:
        Command result with validation status
    """
    try:
        tokens = int(value)
        if tokens < 1:
            return CommandResult(
                success=False,
                message=f"\n[err] Max tokens must be >= 1, got {tokens}",
                display_type="error",
            )
        return CommandResult(success=True, message="", display_type="info")
    except ValueError:
        return CommandResult(
            success=False,
            message=f"\n[err] Max tokens must be an integer, got '{value}'",
            display_type="error",
        )


def validate_base_url(value: str) -> CommandResult:
    """Validate base URL.

    Args:
        value: Base URL to validate

    Returns:
        Command result with validation status
    """
    if not value:
        return CommandResult(success=True, message="", display_type="info")

    # Basic URL validation
    url_pattern = re.compile(
        r"^(https?://)?"  # http:// or https:// (optional)
        r"([a-zA-Z0-9-]+\.)+"  # domain
        r"[a-zA-Z]{2,}"  # TLD
        r"(:\d+)?"  # optional port
        r"(/.*)?$"  # optional path
    )

    if (
        not url_pattern.match(value)
        and "localhost" not in value
        and "127.0.0.1" not in value
    ):
        return CommandResult(
            success=False,
            message=f"\n[err] Invalid URL format: {value}",
            display_type="error",
        )

    return CommandResult(success=True, message="", display_type="info")


def validate_yes_no(value: str) -> CommandResult:
    """Validate yes/no input.

    Args:
        value: Yes/no value to validate

    Returns:
        Command result with validation status
    """
    if not value:
        # Use default
        return CommandResult(success=True, message="", display_type="info")

    value_lower = value.lower()
    if value_lower not in ("y", "n", "yes", "no"):
        return CommandResult(
            success=False,
            message="\n[err] Please enter 'y' or 'n'",
            display_type="error",
        )

    return CommandResult(success=True, message="", display_type="info")


def validate_timeout(value: str) -> CommandResult:
    """Validate timeout value.

    Args:
        value: Timeout to validate (milliseconds, 0 = no timeout)

    Returns:
        Command result with validation status
    """
    if not value:
        # Use default
        return CommandResult(success=True, message="", display_type="info")

    try:
        timeout = int(value)
        if timeout < 0:
            return CommandResult(
                success=False,
                message=f"\n[err] Timeout must be >= 0 (0 = no timeout), got {timeout}",
                display_type="error",
            )
        return CommandResult(success=True, message="", display_type="info")
    except ValueError:
        return CommandResult(
            success=False,
            message=f"\n[err] Timeout must be an integer (milliseconds), got '{value}'",
            display_type="error",
        )


def validate_profile_config(config: Dict[str, Any]) -> CommandResult:
    """Validate complete profile configuration.

    Args:
        config: Profile configuration to validate

    Returns:
        Command result with validation status
    """
    provider = config.get("provider", "auto")

    try:
        # Validate provider type
        _validate_provider_type_internal(provider)

        # Provider-specific validation
        if provider == "openai":
            _validate_openai_config_internal(config)

        return CommandResult(success=True, message="", display_type="info")

    except ProfileValidationError as e:
        return CommandResult(
            success=False,
            message=f"\n[err] Configuration validation failed: {str(e)}",
            display_type="error",
        )


def _validate_provider_type_internal(provider: str) -> bool:
    """
    Validate provider type is supported.

    Args:
        provider: Provider type string

    Returns:
        True if valid, raises ProfileValidationError otherwise
    """
    valid_providers = [
        "openai",
        "anthropic",
        "azure_openai",
        "gemini",
        "openai_responses",
        "openrouter",
        "custom",
        "auto",
    ]
    if provider not in valid_providers:
        raise ProfileValidationError(
            f"Invalid provider '{provider}'. Must be one of: {', '.join(valid_providers)}"
        )
    return True


def _validate_openai_config_internal(config: Dict[str, Any]) -> None:
    """
    Validate OpenAI-specific configuration fields.

    Args:
        config: Configuration dictionary to validate

    Raises:
        ProfileValidationError: If validation fails
    """
    # Validate api_key format if present
    api_key = config.get("api_key")
    if api_key and not isinstance(api_key, str):
        raise ProfileValidationError("api_key must be a string")

    # Validate model is present and is a string
    if "model" not in config:
        raise ProfileValidationError("model field is required for OpenAI")

    model = config.get("model")
    if not model or not isinstance(model, str):
        raise ProfileValidationError("model must be a non-empty string")

    # Validate temperature range if present
    if "temperature" in config:
        temperature = config["temperature"]
        if not isinstance(temperature, (int, float)):
            raise ProfileValidationError("temperature must be a number")
        if not 0.0 <= temperature <= 2.0:
            raise ProfileValidationError(
                f"temperature must be between 0.0 and 2.0, got {temperature}"
            )

    # Validate max_tokens if present
    if "max_tokens" in config:
        max_tokens = config["max_tokens"]
        if not isinstance(max_tokens, int):
            raise ProfileValidationError("max_tokens must be an integer")


# =============================================================================
# Profile Builder
# =============================================================================


def build_profile_config(state: Any) -> Dict[str, Any]:
    """Build profile configuration from wizard state.

    Args:
        state: Wizard state object with attributes:
            - provider: str
            - model: str
            - temperature: float
            - max_tokens: int
            - base_url: str (optional)
            - organization: str (optional)
            - api_key: str (optional)
            - description: str (optional)
            - timeout: int
            - supports_tools: bool
            - streaming: bool

    Returns:
        Profile configuration dictionary
    """
    config = {
        "provider": state.provider,
        "model": state.model,
        "temperature": state.temperature,
        "max_tokens": state.max_tokens,
    }

    if hasattr(state, "base_url") and state.base_url:
        config["base_url"] = state.base_url

    if hasattr(state, "organization") and state.organization:
        config["organization"] = state.organization

    if hasattr(state, "api_key") and state.api_key:
        config["api_key"] = state.api_key

    # Advanced settings
    if hasattr(state, "description") and state.description:
        config["description"] = state.description
    else:
        config["description"] = "Created via profile wizard"

    config["timeout"] = state.timeout
    config["supports_tools"] = state.supports_tools
    config["streaming"] = state.streaming

    return config


# =============================================================================
# Profile Testing
# =============================================================================


def test_profile(profile: Any) -> CommandResult:
    """Test profile with optional API call.

    Args:
        profile: Profile to test (must have model, api_token, get_endpoint attributes)

    Returns:
        Command result with test status
    """
    # For now, just validate the profile structure
    # Actual API testing can be added later as optional
    try:
        # Check if profile has required fields
        if not profile.model:
            return CommandResult(
                success=False,
                message="\n[warn] Profile test: missing model",
                display_type="warning",
                data={"tested": False, "reason": "missing_model"},
            )

        # Check if API key is available
        api_key = profile.api_token or profile.get_endpoint()
        if not api_key:
            return CommandResult(
                success=False,
                message="\n[warn] Profile test: no API key (will use env var)",
                display_type="warning",
                data={"tested": False, "reason": "no_api_key"},
            )

        return CommandResult(
            success=True,
            message="\n[ok] Profile validation passed",
            display_type="success",
            data={"tested": True, "validation": "passed"},
        )

    except Exception as e:
        return CommandResult(
            success=False,
            message=f"\n[warn] Profile test error: {str(e)}",
            display_type="warning",
            data={"tested": False, "error": str(e)},
        )


# =============================================================================
# Helper Functions
# =============================================================================


def get_provider_display_name(provider: str) -> str:
    """Get display name for provider.

    Args:
        provider: Provider type

    Returns:
        Display name
    """
    display_names = {
        "openai": "OpenAI",
        "anthropic": "Anthropic",
        "azure_openai": "Azure OpenAI",
        "auto": "Auto-detect",
    }
    return display_names.get(provider, provider)
