"""
Base provider class for LLM integration.

Defines the abstract interface that all providers must implement.
Handles lifecycle management, request tracking, and atomic operations.
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, AsyncIterator, Dict, List, Optional

from .errors import ProviderError
from .models import (
    ProviderConfig,
    ProviderType,
    StreamingResponse,
    UnifiedResponse,
)

if TYPE_CHECKING:
    pass


logger = logging.getLogger(__name__)


class LLMProvider(ABC):
    """
    Abstract base class for LLM providers.

    All provider implementations must inherit from this class and implement
    the abstract methods. Handles lifecycle management, request tracking,
    and safe shutdown semantics.

    Thread Safety:
        Providers use asyncio.Lock for atomic operations when needed.
        Request tracking is protected by lock to ensure accurate counts.

    Lifecycle:
        1. __init__ - Store config, initialize state
        2. initialize() - Connect to API, validate credentials
        3. call() / stream() - Make requests
        4. shutdown() - Cleanup resources, wait for active requests
    """

    def __init__(self, config: ProviderConfig):
        """
        Initialize provider with configuration.

        Args:
            config: Validated provider configuration
        """
        self.config = config
        self.provider_type: ProviderType = config.provider
        self.model: str = config.model

        # Request tracking for safe shutdown
        self._active_requests: int = 0
        self._lock: asyncio.Lock = asyncio.Lock()
        self._initialized: bool = False
        self._shutdown: bool = False

        # Provider metadata
        self._provider_name: str = self.provider_type.value
        self._supports_streaming: bool = True
        self._supports_tools: bool = True

        # Last wire request payload — the exact dict handed to the HTTP
        # client / SDK immediately before transport. Captured by each
        # provider in call() and stream() so api_communication_service
        # can include it in the raw conversation log. Provider-native
        # shape; consumers tag with self.provider_type to interpret.
        self.last_request_payload: Optional[Dict[str, Any]] = None

        logger.debug(
            f"Initialized {self._provider_name} provider (model: {self.model})"
        )

    @abstractmethod
    async def initialize(self) -> None:
        """
        Initialize the provider connection.

        This method is called after __init__ to establish API connections,
        validate credentials, and prepare the provider for use.

        Raises:
            ProviderError: If initialization fails
        """
        pass

    @abstractmethod
    async def call(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> UnifiedResponse:
        """
        Make a non-streaming API call to the LLM.

        Args:
            messages: Conversation messages
            tools: Optional tool definitions for function calling
            **kwargs: Additional provider-specific parameters

        Returns:
            UnifiedResponse with normalized response format

        Raises:
            ProviderError: If the API call fails
        """
        pass

    @abstractmethod
    async def stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamingResponse]:
        """
        Make a streaming API call to the LLM.

        Args:
            messages: Conversation messages
            tools: Optional tool definitions for function calling
            **kwargs: Additional provider-specific parameters

        Yields:
            StreamingResponse chunks as they arrive

        Raises:
            ProviderError: If the API call fails
        """
        pass

    @abstractmethod
    def validate_config(self, config: ProviderConfig) -> None:
        """
        Validate provider-specific configuration.

        Called during initialization to ensure configuration is valid.
        Subclasses should override to add provider-specific validation.

        Args:
            config: Configuration to validate

        Raises:
            ValueError: If configuration is invalid
        """
        pass

    async def shutdown(self) -> None:
        """
        Shutdown the provider and wait for active requests to complete.

        This method:
        1. Marks provider as shut down
        2. Waits for all active requests to complete
        3. Closes any open connections
        4. Cleans up resources

        Safe to call multiple times (idempotent).
        """
        if self._shutdown:
            logger.debug(f"{self._provider_name} provider already shut down")
            return

        logger.info(f"Shutting down {self._provider_name} provider")

        # Mark as shutdown (rejects new requests)
        self._shutdown = True

        # Wait for active requests to complete
        if self._active_requests > 0:
            logger.info(
                f"Waiting for {self._active_requests} active requests to complete"
            )
            max_wait = 30  # seconds
            waited: float = 0.0
            while self._active_requests > 0 and waited < max_wait:
                await asyncio.sleep(0.1)
                waited += 0.1

            if self._active_requests > 0:
                logger.warning(
                    f"Shutdown timeout: {self._active_requests} requests still active"
                )

        # Cleanup subclass resources
        await self._cleanup()

        logger.info(f"{self._provider_name} provider shutdown complete")

    async def _cleanup(self) -> None:
        """
        Cleanup provider-specific resources.

        Subclasses can override to close connections, release resources, etc.
        Default implementation does nothing.

        This is called during shutdown after active requests complete.
        """
        pass

    async def _track_request_start(self) -> None:
        """
        Mark the start of a request.

        Increments active request counter. Raises error if provider is shut down.

        Raises:
            ProviderError: If provider is shut down
        """
        async with self._lock:
            if self._shutdown:
                raise ProviderError(
                    f"Cannot start new request: {self._provider_name} provider is shut down",
                    provider=self._provider_name,
                )
            self._active_requests += 1

    async def _track_request_end(self) -> None:
        """Mark the end of a request by decrementing active request counter."""
        async with self._lock:
            if self._active_requests > 0:
                self._active_requests -= 1

    @property
    def is_initialized(self) -> bool:
        """Check if provider has been initialized."""
        return self._initialized

    @property
    def is_shutdown(self) -> bool:
        """Check if provider has been shut down."""
        return self._shutdown

    @property
    def active_requests(self) -> int:
        """Get number of active requests."""
        return self._active_requests

    @property
    def provider_name(self) -> str:
        """Get provider name."""
        return self._provider_name

    @property
    def supports_streaming(self) -> bool:
        """Check if provider supports streaming."""
        return self._supports_streaming

    @property
    def supports_tools(self) -> bool:
        """Check if provider supports function calling."""
        return self._supports_tools

    def get_metadata(self) -> Dict[str, Any]:
        """
        Get provider metadata.

        Returns:
            Dictionary with provider information
        """
        return {
            "provider": self._provider_name,
            "provider_type": self.provider_type.value,
            "model": self.model,
            "supports_streaming": self._supports_streaming,
            "supports_tools": self._supports_tools,
            "initialized": self._initialized,
            "shutdown": self._shutdown,
            "active_requests": self._active_requests,
        }

    def _validate_initialized(self) -> None:
        """
        Validate that provider is initialized.

        Raises:
            ProviderError: If provider not initialized
        """
        if not self._initialized:
            raise ProviderError(
                f"{self._provider_name} provider not initialized. "
                f"Call initialize() first.",
                provider=self._provider_name,
            )

    def _validate_not_shutdown(self) -> None:
        """
        Validate that provider is not shut down.

        Raises:
            ProviderError: If provider is shut down
        """
        if self._shutdown:
            raise ProviderError(
                f"{self._provider_name} provider is shut down. "
                f"Cannot make new requests.",
                provider=self._provider_name,
            )

    def __repr__(self) -> str:
        """String representation of provider."""
        return (
            f"{self.__class__.__name__}("
            f"provider={self._provider_name}, "
            f"model={self.model}, "
            f"initialized={self._initialized}, "
            f"active={self._active_requests})"
        )
