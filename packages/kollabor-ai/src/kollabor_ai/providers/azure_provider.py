"""
Azure OpenAI provider implementation.

Inherits from OpenAIProvider and overrides base_url construction
for Azure-specific endpoint format.
"""

import logging
from typing import Any, AsyncIterator, Dict, List, Optional

from .base import LLMProvider
from .models import (
    AzureOpenAIConfig,
    ProviderType,
    StreamingResponse,
    UnifiedResponse,
)
from .openai_provider import OpenAIProvider
from .registry import register_provider

logger = logging.getLogger(__name__)


@register_provider(ProviderType.AZURE_OPENAI)
class AzureOpenAIProvider(OpenAIProvider):
    """
    Azure OpenAI provider extending OpenAI provider.

    Azure OpenAI requires specific base_url format and authentication.
    This provider inherits from OpenAIProvider and customizes:

    - Base URL construction from azure_endpoint
    - API version handling
    - Deployment ID vs model name

    Configuration:
        api_key: Azure OpenAI API key
        azure_endpoint: Azure OpenAI endpoint URL
        api_version: API version (default: 2024-02-15-preview)
        deployment_id: Optional deployment ID (overrides model)
        model: Model/deployment name
        base_url: Optional (auto-constructed from azure_endpoint)
        temperature: Sampling temperature
        max_tokens: Maximum tokens to generate
        timeout: Request timeout in seconds
    """

    def __init__(self, config: AzureOpenAIConfig):
        """
        Initialize Azure OpenAI provider.

        Args:
            config: Validated Azure OpenAI configuration
        """
        # Initialize as OpenAI provider (sets up common attributes)
        LLMProvider.__init__(self, config)
        self.config: AzureOpenAIConfig = config  # type: ignore[assignment]

        # OpenAI client (initialized in initialize())
        self._client: Optional[Any] = None

        logger.debug(
            f"Azure OpenAI provider created (endpoint={config.azure_endpoint}, model={config.model})"
        )

    async def initialize(self) -> None:
        """
        Initialize Azure OpenAI client.

        Creates AsyncOpenAI client with Azure-specific configuration.

        Raises:
            ProviderError: If client initialization fails
        """
        if self._initialized:
            logger.debug("Azure OpenAI provider already initialized")
            return

        try:
            from openai import AsyncOpenAI

            # Construct Azure-specific base URL
            base_url = self._construct_azure_base_url()

            # Create client
            client_kwargs = {
                "api_key": self.config.api_key,
                "base_url": base_url,
                "timeout": self.config.timeout,
                "default_headers": {"api-key": self.config.api_key},
                "max_retries": 0,
            }

            self._client = AsyncOpenAI(**client_kwargs)  # type: ignore[arg-type]

            self._initialized = True
            logger.info(f"Azure OpenAI provider initialized (model={self.model})")

        except ImportError as e:
            raise ImportError(
                "OpenAI SDK not installed. Install with: pip install openai"
            ) from e
        except Exception as e:
            logger.error(f"Failed to initialize Azure OpenAI client: {e}")
            # Import here to avoid circular dependency
            from .errors import map_openai_error

            raise map_openai_error(e, "azure_openai") from e

    async def call(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> UnifiedResponse:
        """
        Make non-streaming API call to Azure OpenAI.

        Uses deployment_id if provided, otherwise uses model name.

        Args:
            messages: Conversation messages
            tools: Optional tool definitions (Anthropic format, will be transformed)
            **kwargs: Additional Azure-specific parameters

        Returns:
            UnifiedResponse with normalized response format

        Raises:
            ProviderError: If API call fails
        """
        self._validate_initialized()
        self._validate_not_shutdown()

        # Use deployment_id if provided, otherwise use model
        model = self.config.deployment_id or self.model

        # Override model in kwargs for this request
        kwargs_with_model = {"model": model, **kwargs}

        # Call parent implementation with overridden model
        return await super().call(messages, tools, **kwargs_with_model)

    async def stream(  # type: ignore[override, misc]
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamingResponse]:
        """
        Make streaming API call to Azure OpenAI.

        Uses deployment_id if provided, otherwise uses model name.

        Args:
            messages: Conversation messages
            tools: Optional tool definitions (Anthropic format, will be transformed)
            **kwargs: Additional Azure-specific parameters

        Yields:
            StreamingResponse chunks as they arrive

        Raises:
            ProviderError: If API call fails
        """
        self._validate_initialized()
        self._validate_not_shutdown()

        # Use deployment_id if provided, otherwise use model
        model = self.config.deployment_id or self.model

        # Override model in kwargs for this request
        kwargs_with_model = {"model": model, **kwargs}

        # Call parent implementation with overridden model
        async for chunk in super().stream(messages, tools, **kwargs_with_model):
            yield chunk

    def _construct_azure_base_url(self) -> str:
        """
        Construct Azure OpenAI base URL from endpoint.

        Azure format: https://{resource}.openai.azure.com/openai/deployments/{deployment}/
                     or with api-version: ?api-version={version}

        Args:
            config: Azure OpenAI configuration

        Returns:
            Constructed base URL
        """
        endpoint = self.config.azure_endpoint.rstrip("/")

        # Azure OpenAI base URL format
        base_url = f"{endpoint}/openai/deployments/{self.model}"

        # Add API version as query parameter
        # Note: The SDK handles this differently for Azure vs OpenAI
        # For Azure, we often need to append api-version to requests

        return base_url

    def _prepare_request_params(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        stream: bool,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Build the request like OpenAIProvider, addressed to the deployment.

        Sharing the parent's builder keeps Azure on the same stream_options
        (usage and cached_tokens on streams), local-metadata stripping and
        sampling rules; only the model name differs.
        """
        kwargs.setdefault("model", self.config.deployment_id or self.model)
        return super()._prepare_request_params(messages, tools, stream, **kwargs)
