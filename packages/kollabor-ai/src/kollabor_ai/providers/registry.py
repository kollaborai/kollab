"""
Provider registry for LLM integration.

Centralized registry for provider implementations with decorator-based
registration. Handles provider discovery, creation, and singleton instances.
"""

import asyncio
import hashlib
import json
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple, Type

from .base import LLMProvider
from .models import (
    AnthropicConfig,
    AzureOpenAIConfig,
    CustomConfig,
    GeminiConfig,
    OpenAIConfig,
    OpenAIResponsesConfig,
    OpenRouterConfig,
    ProviderConfig,
    ProviderType,
)

logger = logging.getLogger(__name__)


class ProviderRegistry:
    """
    Centralized registry for provider implementations.

    Providers self-register using the @register_provider decorator.
    The registry manages provider class registration and creates
    cached instances per full provider configuration.

    Thread Safety:
        All operations use asyncio.Lock for thread safety.

    Example:
        @ProviderRegistry.register(ProviderType.OPENAI)
        class OpenAIProvider(LLMProvider):
            ...
    """

    # Registered provider classes (singleton at class level)
    _providers: Dict[ProviderType, Type[LLMProvider]] = {}

    # Provider instances (singleton per provider config)
    _instances: Dict[Tuple[ProviderType, str], LLMProvider] = {}

    # Lock for thread-safe operations
    _lock: asyncio.Lock = asyncio.Lock()

    @classmethod
    def register(
        cls, provider_type: ProviderType
    ) -> Callable[[Type[LLMProvider]], Type[LLMProvider]]:
        """
        Decorator to register a provider class.

        Usage:
            @ProviderRegistry.register(ProviderType.OPENAI)
            class OpenAIProvider(LLMProvider):
                ...

        Args:
            provider_type: Provider type enum value

        Returns:
            Decorator function that registers the class
        """

        def decorator(provider_class: Type[LLMProvider]) -> Type[LLMProvider]:
            cls._providers[provider_type] = provider_class
            logger.info(
                f"Registered provider '{provider_type.value}': {provider_class.__name__}"
            )
            return provider_class

        return decorator

    @classmethod
    def get_provider_class(
        cls, provider_type: ProviderType
    ) -> Optional[Type[LLMProvider]]:
        """
        Get provider class for type.

        Args:
            provider_type: Provider type enum

        Returns:
            Provider class or None if not registered
        """
        return cls._providers.get(provider_type)

    @classmethod
    async def get_provider(cls, config: ProviderConfig) -> LLMProvider:
        """
        Get or create provider instance.

        Returns an existing instance only when the full provider
        configuration matches. This keeps sessions with different models,
        API keys, base URLs, or headers from sharing a stale provider.

        Args:
            config: Provider configuration

        Returns:
            Provider instance (cached for matching provider configuration)

        Raises:
            ValueError: If provider type not registered
            ProviderError: If provider creation fails
        """
        provider_type = config.provider
        cache_key = cls._get_cache_key(config)

        # Check if already have instance
        async with cls._lock:
            if cache_key in cls._instances:
                instance = cls._instances[cache_key]
                logger.debug(
                    f"Returning existing {provider_type.value} provider instance"
                )
                return instance

        # Create new instance
        provider_class = cls.get_provider_class(provider_type)

        if provider_class is None:
            available = ", ".join(p.value for p in cls._providers.keys())
            raise ValueError(
                f"Provider '{provider_type.value}' not registered.\n"
                f"Available providers: {available or 'None'}"
            )

        logger.info(f"Creating new {provider_type.value} provider instance")

        try:
            # Create instance
            instance = provider_class(config)

            # Initialize the provider
            await instance.initialize()

            # Store as singleton
            async with cls._lock:
                cls._instances[cache_key] = instance

            logger.info(f"Created and initialized {provider_type.value} provider")
            return instance

        except Exception as e:
            logger.error(f"Failed to create {provider_type.value} provider: {e}")
            raise

    @classmethod
    def _get_cache_key(cls, config: ProviderConfig) -> Tuple[ProviderType, str]:
        """Return a stable cache key for a provider configuration."""
        data = config.model_dump(mode="json")
        encoded = json.dumps(data, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        return (config.provider, digest)

    @classmethod
    async def create_provider(cls, config: ProviderConfig) -> LLMProvider:
        """
        Create a new provider instance (non-singleton).

        Unlike get_provider(), this always creates a new instance.
        Useful for testing or when you need multiple instances.

        Args:
            config: Provider configuration

        Returns:
            New provider instance

        Raises:
            ValueError: If provider type not registered
            ProviderError: If provider creation fails
        """
        provider_type = config.provider
        provider_class = cls.get_provider_class(provider_type)

        if provider_class is None:
            available = ", ".join(p.value for p in cls._providers.keys())
            raise ValueError(
                f"Provider '{provider_type.value}' not registered.\n"
                f"Available providers: {available or 'None'}"
            )

        logger.info(f"Creating new {provider_type.value} provider (non-singleton)")

        try:
            instance = provider_class(config)
            await instance.initialize()
            return instance
        except Exception as e:
            logger.error(f"Failed to create {provider_type.value} provider: {e}")
            raise

    @classmethod
    def list_providers(cls) -> List[ProviderType]:
        """
        List all registered provider types.

        Returns:
            List of registered provider types
        """
        return list(cls._providers.keys())

    @classmethod
    def is_registered(cls, provider_type: ProviderType) -> bool:
        """
        Check if provider type is registered.

        Args:
            provider_type: Provider type to check

        Returns:
            True if registered, False otherwise
        """
        return provider_type in cls._providers

    @classmethod
    async def shutdown_all(cls) -> None:
        """
        Shutdown all provider instances.

        Calls shutdown() on all singleton instances and clears the registry.
        Safe to call multiple times.
        """
        async with cls._lock:
            if not cls._instances:
                logger.debug("No provider instances to shut down")
                return

            logger.info(f"Shutting down {len(cls._instances)} provider instances")

            for cache_key, instance in cls._instances.items():
                provider_type = cache_key[0]
                try:
                    await instance.shutdown()
                except Exception as e:
                    logger.error(
                        f"Error shutting down {provider_type.value} provider: {e}"
                    )

            cls._instances.clear()
            logger.info("All provider instances shut down")

    @classmethod
    async def shutdown_provider(cls, provider_type: ProviderType) -> bool:
        """
        Shutdown a specific provider instance.

        Args:
            provider_type: Provider type to shut down

        Returns:
            True if provider was shut down, False if not found
        """
        async with cls._lock:
            keys = [key for key in cls._instances if key[0] == provider_type]
            if not keys:
                logger.debug(f"No {provider_type.value} provider instance to shut down")
                return False

            success = True
            for key in keys:
                instance = cls._instances.pop(key)
                try:
                    await instance.shutdown()
                except Exception as e:
                    logger.error(
                        f"Error shutting down {provider_type.value} provider: {e}"
                    )
                    success = False

            if success:
                logger.info(
                    f"Shut down {len(keys)} {provider_type.value} provider instance(s)"
                )
            return success

    @classmethod
    def reset(cls) -> None:
        """
        Reset the registry (clear all registrations and instances).

        WARNING: This is primarily for testing. Use with caution.

        Clears all registered provider classes and instances.
        """
        cls._providers.clear()
        cls._instances.clear()
        logger.warning("Provider registry reset (all providers cleared)")


# Convenience function for decorator
def register_provider(
    provider_type: ProviderType,
) -> Callable[[Type[LLMProvider]], Type[LLMProvider]]:
    """
    Alias for ProviderRegistry.register decorator.

    Usage:
        @register_provider(ProviderType.OPENAI)
        class OpenAIProvider(LLMProvider):
            ...

    Args:
        provider_type: Provider type enum

    Returns:
        Decorator function
    """
    return ProviderRegistry.register(provider_type)


def detect_provider_from_profile(profile: Dict[str, Any]) -> ProviderType:
    """
    Detect provider type from profile configuration.

    Priority:
    1. Explicit provider field
    2. API key format detection
    3. API base URL detection
    4. Default to anthropic (for legacy configs)

    Args:
        profile: Profile configuration dict

    Returns:
        Detected ProviderType

    Raises:
        ValueError: If provider cannot be determined
    """
    # 1. Explicit provider field (highest priority)
    if "provider" in profile:
        provider_str = profile["provider"]
        if provider_str != "auto":
            try:
                return ProviderType(provider_str)
            except ValueError:
                raise ValueError(
                    f"Unknown provider type: '{provider_str}'. "
                    f"Must be one of: {[p.value for p in ProviderType]}"
                )
        # "auto" falls through to detection logic below

    # 1a. Check for use_responses_api flag (OpenAI Responses API)
    if profile.get("use_responses_api", False):
        return ProviderType.OPENAI_RESPONSES

    api_key = profile.get("api_key", "")
    api_base = profile.get("api_base", "")

    # 2. API key format detection (in correct order!)

    # Anthropic FIRST (more specific prefix)
    if api_key.startswith("sk-ant-"):
        return ProviderType.ANTHROPIC

    # Azure OpenAI (specific format)
    if api_base and "azure.com" in api_base:
        return ProviderType.AZURE_OPENAI

    # OpenAI (generic sk- prefix)
    if api_key.startswith("sk-"):
        return ProviderType.OPENAI

    # 3. API base URL detection

    # Gemini (most specific domain match)
    if api_base and "generativelanguage.googleapis.com" in api_base:
        return ProviderType.GEMINI

    # OpenRouter
    if api_base and "openrouter.ai" in api_base:
        return ProviderType.OPENROUTER

    if "anthropic.com" in api_base:
        return ProviderType.ANTHROPIC

    if "openai.com" in api_base or "azure.com" in api_base:
        return ProviderType.OPENAI

    # 4. "auto" with no detectable provider - raise so caller can handle gracefully
    is_auto = profile.get("provider") == "auto"
    if is_auto:
        raise ValueError(
            "No provider could be auto-detected. "
            "Set a provider API key env var (ANTHROPIC_API_KEY, OPENAI_API_KEY, etc.) "
            "or use /profile to configure a provider manually."
        )

    # 5. Default for legacy configs (explicit provider was set but not recognized above)
    logger.debug("Could not detect provider, defaulting to anthropic")
    return ProviderType.ANTHROPIC


def create_config_from_profile(
    profile: Dict[str, Any], provider_type: Optional[ProviderType] = None
) -> ProviderConfig:
    """
    Create provider config from profile dict.

    Handles legacy config format and auto-detects provider if not specified.

    Args:
        profile: Profile configuration dict
        provider_type: Optional provider type (auto-detect if None)

    Returns:
        Validated ProviderConfig (OpenAI, Anthropic, or Azure)

    Raises:
        ValueError: If config is invalid or provider cannot be determined
    """
    # Detect provider if not specified
    if provider_type is None:
        provider_type = detect_provider_from_profile(profile)

    # Common fields
    # Set default model based on provider type
    default_model = "claude-sonnet-4-6"  # Default for Anthropic
    if provider_type == ProviderType.OPENAI:
        default_model = "gpt-5.4"
    elif provider_type == ProviderType.OPENAI_RESPONSES:
        default_model = "codex-mini"
    elif provider_type == ProviderType.GEMINI:
        default_model = "gemini-3.1-pro-preview"
    elif provider_type == ProviderType.AZURE_OPENAI:
        default_model = "gpt-5.4"

    base_fields = {
        "provider": provider_type,
        "model": profile.get("model", default_model),
        "temperature": profile.get("temperature", 0.7),
    }

    # Pass through auth_type if specified (e.g. OAuth profiles)
    auth_type_str = profile.get("auth_type")
    if auth_type_str:
        from .models import AuthType

        try:
            base_fields["auth_type"] = AuthType(auth_type_str)
        except ValueError:
            pass

    # Handle max_tokens (None means use API default, so we don't include it)
    max_tokens = profile.get("max_tokens")
    if max_tokens is not None:
        base_fields["max_tokens"] = max_tokens

    # Handle timeout (0 in legacy means no timeout, provider defaults to 60s if not specified)
    timeout = profile.get("timeout") or profile.get("request_timeout")
    if timeout and timeout > 0:
        base_fields["timeout"] = timeout

    # Bound the context-budget guard to the model's real window: an explicit
    # profile value wins, otherwise resolve it from the model registry by
    # name/provider. Falls through to the config default if unresolved.
    context_window = profile.get("context_window")
    if context_window:
        base_fields["context_window"] = int(context_window)
    else:
        try:
            from kollabor_ai.model_registry import resolve_context_window

            resolved = resolve_context_window(
                base_fields["model"], provider_type.value
            )
            if resolved:
                base_fields["context_window"] = resolved
        except Exception:  # registry is best-effort; never block config creation
            pass

    # API key is required for cloud providers, optional for custom
    api_key = profile.get("api_key") or profile.get("api_token", "")
    if not api_key and provider_type != ProviderType.CUSTOM:
        raise ValueError(
            f"Profile missing required 'api_key' or 'api_token' field. "
            f"Provider: {provider_type.value}"
        )
    base_fields["api_key"] = api_key

    # Base URL (optional) - handle both old 'api_url' and new 'base_url'/'api_base'
    if "api_base" in profile and profile["api_base"]:
        base_fields["base_url"] = profile["api_base"]
    elif "base_url" in profile and profile["base_url"]:
        base_fields["base_url"] = profile["base_url"]
    elif "api_url" in profile and profile["api_url"]:
        base_fields["base_url"] = profile["api_url"]

    # Extra headers (e.g. ChatGPT-Account-Id for OAuth profiles)
    extra_headers = profile.get("extra_headers")
    if extra_headers and isinstance(extra_headers, dict):
        base_fields["extra_headers"] = extra_headers

    # Provider-specific fields
    if provider_type == ProviderType.OPENAI:
        base_fields["organization"] = profile.get("organization")
        return OpenAIConfig(**base_fields)

    elif provider_type == ProviderType.ANTHROPIC:
        base_fields["api_version"] = profile.get("api_version", "2023-06-01")
        base_fields["max_retries"] = profile.get("max_retries", 2)
        return AnthropicConfig(**base_fields)

    elif provider_type == ProviderType.AZURE_OPENAI:
        if "azure_endpoint" not in profile:
            raise ValueError(
                "Azure OpenAI requires 'azure_endpoint' in profile configuration"
            )
        base_fields["azure_endpoint"] = profile["azure_endpoint"]
        base_fields["api_version"] = profile.get("api_version", "2024-02-15-preview")
        base_fields["deployment_id"] = profile.get("deployment_id")
        return AzureOpenAIConfig(**base_fields)

    elif provider_type == ProviderType.CUSTOM:
        base_fields["base_url"] = profile.get("base_url")
        return CustomConfig(**base_fields)

    elif provider_type == ProviderType.OPENROUTER:
        # Set default base URL if not specified
        if "base_url" not in base_fields:
            base_fields["base_url"] = "https://openrouter.ai/api/v1"

        # Optional OpenRouter-specific headers
        base_fields["http_referer"] = profile.get("http_referer") or profile.get(
            "referer"
        )
        base_fields["x_title"] = profile.get("x_title") or profile.get("title")

        return OpenRouterConfig(**base_fields)

    elif provider_type == ProviderType.GEMINI:
        # Optional Gemini-specific fields
        base_fields["project_id"] = profile.get("project_id")
        base_fields["location"] = profile.get("location")

        return GeminiConfig(**base_fields)

    elif provider_type == ProviderType.OPENAI_RESPONSES:
        # Optional Responses API-specific fields
        base_fields["store_responses"] = profile.get("store_responses", False)
        base_fields["model"] = profile.get("model", "gpt-5.4")

        return OpenAIResponsesConfig(**base_fields)

    else:
        raise ValueError(f"Unsupported provider type: {provider_type}")
