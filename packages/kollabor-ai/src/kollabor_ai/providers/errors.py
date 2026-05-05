"""
Unified error hierarchy for LLM provider exceptions.

Provides standardized error types for OpenAI, Anthropic, and Azure OpenAI providers.
Maps provider-specific exceptions to unified error types with safe user messages.
"""

import re
from typing import Any, Dict, Optional


class ProviderError(Exception):
    """
    Base exception for all provider errors.

    Stores error context including provider name, error code, and original exception.
    Provides safe user-facing messages that don't leak sensitive data.

    Attributes:
        message: Original error message (may contain sensitive data)
        provider: Provider name (openai, anthropic, azure_openai)
        error_code: Machine-readable error code
        original_error: Original exception that caused this error
        safe_message: User-safe message (no API keys, tokens, etc.)
    """

    def __init__(
        self,
        message: str,
        provider: str,
        error_code: Optional[str] = None,
        original_error: Optional[Exception] = None,
        safe_message: Optional[str] = None,
    ):
        """
        Initialize provider error.

        Args:
            message: Original error message (may contain sensitive data)
            provider: Provider name
            error_code: Machine-readable error code
            original_error: Original exception
            safe_message: User-safe message (defaults to sanitized message)
        """
        super().__init__(message)
        self.message = message
        self.provider = provider
        self.error_code = error_code
        self.original_error = original_error
        self.safe_message = safe_message or self._sanitize_message(message)

    def _sanitize_message(self, message: str) -> str:
        """
        Sanitize error message to remove sensitive data.

        Removes API keys, bearer tokens, and authorization headers.

        Args:
            message: Original error message

        Returns:
            Sanitized message safe for user display
        """
        # Remove Anthropic API keys first (sk-ant-*, must come before sk-*)
        message = re.sub(r"sk-ant-[a-zA-Z0-9\-_]{20,}", "sk-ant-****", message)

        # Remove OpenAI project keys (sk-proj-*, must come before sk-*)
        message = re.sub(r"sk-proj-[a-zA-Z0-9\-_]{20,}", "sk-proj-****", message)

        # Remove OpenAI API keys (sk-*)
        message = re.sub(r"sk-[a-zA-Z0-9\-_]{20,}", "sk-****", message)

        # Remove bearer tokens (before Authorization header)
        message = re.sub(
            r"Bearer\s+[a-zA-Z0-9\-._~+/]+=*", "Bearer [REDACTED]", message
        )

        # Remove authorization headers
        message = re.sub(
            r"Authorization:\s+[^\s]+", "Authorization: [REDACTED]", message
        )

        return message

    def to_dict(self) -> Dict[str, Any]:
        """
        Serialize error to dictionary.

        Returns:
            Dictionary with error context (only safe data)
        """
        return {
            "error_type": self.__class__.__name__,
            "provider": self.provider,
            "error_code": self.error_code,
            "safe_message": self.safe_message,
        }

    def __str__(self) -> str:
        """
        Return safe string representation.

        Returns:
            Safe message (never leaks sensitive data)
        """
        return self.safe_message


class AuthenticationError(ProviderError):
    """
    Authentication failed (invalid API key, token, or credentials).

    Safe message: "Invalid API key. Please check your API key."
    """

    def __init__(
        self,
        message: str,
        provider: str,
        error_code: Optional[str] = None,
        original_error: Optional[Exception] = None,
        safe_message: Optional[str] = None,
    ):
        """
        Initialize authentication error.

        Args:
            message: Original error message
            provider: Provider name
            error_code: Machine-readable error code
            original_error: Original exception
            safe_message: Optional custom safe message
        """
        if safe_message is None:
            safe_message = f"Invalid {provider} API key. Please check your API key."
        super().__init__(message, provider, error_code, original_error, safe_message)


class RateLimitError(ProviderError):
    """
    Rate limit exceeded (too many requests).

    Stores retry_after duration if available in response headers.

    Attributes:
        retry_after: Seconds to wait before retrying (optional)
    """

    def __init__(
        self,
        message: str,
        provider: str,
        retry_after: Optional[float] = None,
        error_code: Optional[str] = None,
        original_error: Optional[Exception] = None,
        safe_message: Optional[str] = None,
    ):
        """
        Initialize rate limit error.

        Args:
            message: Original error message
            provider: Provider name
            retry_after: Seconds to wait before retrying
            error_code: Machine-readable error code
            original_error: Original exception
            safe_message: Optional custom safe message
        """
        self.retry_after = retry_after

        if safe_message is None:
            if retry_after:
                safe_message = f"Rate limit exceeded. Retry after {retry_after}s."
            else:
                safe_message = "Rate limit exceeded. Please try again later."

        super().__init__(message, provider, error_code, original_error, safe_message)

    def to_dict(self) -> Dict[str, Any]:
        """
        Serialize error to dictionary.

        Returns:
            Dictionary with error context including retry_after
        """
        data = super().to_dict()
        if self.retry_after is not None:
            data["retry_after"] = self.retry_after
        return data


class InvalidRequestError(ProviderError):
    """
    Invalid request (malformed request, invalid parameters, etc.).

    Safe message: "Invalid request. Check your request parameters."
    """

    def __init__(
        self,
        message: str,
        provider: str,
        error_code: Optional[str] = None,
        original_error: Optional[Exception] = None,
        safe_message: Optional[str] = None,
    ):
        """
        Initialize invalid request error.

        Args:
            message: Original error message
            provider: Provider name
            error_code: Machine-readable error code
            original_error: Original exception
            safe_message: Optional custom safe message
        """
        if safe_message is None:
            safe_message = (
                self._sanitize_message(message)
                if message
                else "Invalid request. Check your request parameters."
            )
        super().__init__(message, provider, error_code, original_error, safe_message)


class ContextLengthExceededError(InvalidRequestError):
    """
    Message exceeds model's context length.

    Subclass of InvalidRequestError for specific handling.

    Safe message: "Message exceeds model's context length. Please reduce message size."
    """

    def __init__(
        self,
        message: str,
        provider: str,
        error_code: Optional[str] = None,
        original_error: Optional[Exception] = None,
        safe_message: Optional[str] = None,
    ):
        """
        Initialize context length exceeded error.

        Args:
            message: Original error message
            provider: Provider name
            error_code: Machine-readable error code
            original_error: Original exception
            safe_message: Optional custom safe message
        """
        if safe_message is None:
            safe_message = (
                "Message exceeds model's context length. Please reduce message size."
            )
        super().__init__(message, provider, error_code, original_error, safe_message)


class APITimeoutError(ProviderError):
    """
    API request timed out.

    Safe message: "Request timed out. Please try again."
    """

    def __init__(
        self,
        message: str,
        provider: str,
        error_code: Optional[str] = None,
        original_error: Optional[Exception] = None,
        safe_message: Optional[str] = None,
    ):
        """
        Initialize timeout error.

        Args:
            message: Original error message
            provider: Provider name
            error_code: Machine-readable error code
            original_error: Original exception
            safe_message: Optional custom safe message
        """
        if safe_message is None:
            safe_message = "Request timed out. Please try again."
        super().__init__(message, provider, error_code, original_error, safe_message)


class APIConnectionError(ProviderError):
    """
    Connection failed (network error, DNS failure, etc.).

    Safe message: "Connection failed. Please check your network."
    """

    def __init__(
        self,
        message: str,
        provider: str,
        error_code: Optional[str] = None,
        original_error: Optional[Exception] = None,
        safe_message: Optional[str] = None,
    ):
        """
        Initialize connection error.

        Args:
            message: Original error message
            provider: Provider name
            error_code: Machine-readable error code
            original_error: Original exception
            safe_message: Optional custom safe message
        """
        if safe_message is None:
            safe_message = "Connection failed. Please check your network."
        super().__init__(message, provider, error_code, original_error, safe_message)


class ServerError(ProviderError):
    """
    Provider server error (5xx status codes).

    Attributes:
        status_code: HTTP status code (500-599)
    """

    def __init__(
        self,
        message: str,
        provider: str,
        status_code: int,
        error_code: Optional[str] = None,
        original_error: Optional[Exception] = None,
        safe_message: Optional[str] = None,
    ):
        """
        Initialize server error.

        Args:
            message: Original error message
            provider: Provider name
            status_code: HTTP status code (500-599)
            error_code: Machine-readable error code
            original_error: Original exception
            safe_message: Optional custom safe message
        """
        self.status_code = status_code
        if safe_message is None:
            safe_message = f"{provider.capitalize()} server error ({status_code}). Please try again later."
        super().__init__(message, provider, error_code, original_error, safe_message)

    def to_dict(self) -> Dict[str, Any]:
        """
        Serialize error to dictionary.

        Returns:
            Dictionary with error context including status_code
        """
        data = super().to_dict()
        data["status_code"] = self.status_code
        return data


def map_openai_error(error: Exception, provider: str = "openai") -> ProviderError:
    """
    Map OpenAI API exceptions to unified error types.

    Handles OpenAI SDK exceptions, httpx errors, and generic exceptions.

    Args:
        error: Original exception from OpenAI SDK
        provider: Provider name (default: "openai")

    Returns:
        Mapped ProviderError subclass
    """
    error_message = str(error)

    # Import OpenAI SDK exceptions
    try:
        from openai import APIConnectionError as OpenAIConnectionError
        from openai import APIStatusError
        from openai import APITimeoutError as OpenAITimeoutError
        from openai import AuthenticationError as OpenAIAuthError
        from openai import BadRequestError as OpenAIBadRequestError
        from openai import NotFoundError as OpenAINotFoundError
        from openai import RateLimitError as OpenAIRateLimitError
    except ImportError:
        # SDK not installed, fall back to generic handling
        OpenAIAuthError = None  # type: ignore[assignment,misc]
        OpenAIRateLimitError = None  # type: ignore[assignment,misc]
        OpenAIBadRequestError = None  # type: ignore[assignment,misc]
        OpenAINotFoundError = None  # type: ignore[assignment,misc]
        APIStatusError = None  # type: ignore[assignment,misc]
        OpenAITimeoutError = None  # type: ignore[assignment,misc]
        OpenAIConnectionError = None  # type: ignore[assignment,misc]

    # OpenAI SDK errors
    if OpenAIAuthError is not None and isinstance(error, OpenAIAuthError):
        return AuthenticationError(
            error_message,
            provider,
            error_code="authentication_error",
            original_error=error,
        )

    if OpenAIRateLimitError is not None and isinstance(error, OpenAIRateLimitError):
        # Extract retry-after from response headers if available
        retry_after = None
        if hasattr(error, "response") and error.response is not None:
            headers = getattr(error.response, "headers", {})
            retry_after = headers.get("retry-after")
            if retry_after:
                try:
                    retry_after = float(retry_after)
                except (ValueError, TypeError):
                    retry_after = None

        return RateLimitError(
            error_message,
            provider,
            retry_after=retry_after,
            error_code="rate_limit_error",
            original_error=error,
        )

    if OpenAIBadRequestError is not None and isinstance(error, OpenAIBadRequestError):
        # Check for context length exceeded
        if (
            "context_length" in error_message.lower()
            or "max_tokens" in error_message.lower()
        ):
            return ContextLengthExceededError(
                error_message,
                provider,
                error_code="context_length_exceeded",
                original_error=error,
            )

        return InvalidRequestError(
            error_message,
            provider,
            error_code="invalid_request",
            original_error=error,
        )

    if OpenAINotFoundError is not None and isinstance(error, OpenAINotFoundError):
        return InvalidRequestError(
            error_message,
            provider,
            error_code="not_found",
            original_error=error,
        )

    if APIStatusError is not None and isinstance(error, APIStatusError):
        status_code = getattr(error, "status_code", None)

        if status_code and 500 <= status_code < 600:
            return ServerError(
                error_message,
                provider,
                status_code=status_code,
                error_code=f"server_error_{status_code}",
                original_error=error,
            )

    if OpenAITimeoutError is not None and isinstance(error, OpenAITimeoutError):
        return APITimeoutError(
            error_message,
            provider,
            error_code="timeout",
            original_error=error,
        )

    if OpenAIConnectionError is not None and isinstance(error, OpenAIConnectionError):
        return APIConnectionError(
            error_message,
            provider,
            error_code="connection_error",
            original_error=error,
        )

    # httpx errors (if SDK exceptions not available or not matched)
    try:
        import httpx

        if isinstance(error, httpx.TimeoutException):
            return APITimeoutError(
                error_message,
                provider,
                error_code="timeout",
                original_error=error,
            )

        if isinstance(error, httpx.ConnectError):
            return APIConnectionError(
                error_message,
                provider,
                error_code="connection_error",
                original_error=error,
            )

        if isinstance(error, httpx.HTTPStatusError):
            status_code = error.response.status_code

            # 401 - Authentication
            if status_code == 401:
                return AuthenticationError(
                    error_message,
                    provider,
                    error_code="authentication_error",
                    original_error=error,
                )

            # 429 - Rate limit
            if status_code == 429:
                retry_after = error.response.headers.get("retry-after")
                retry_seconds = float(retry_after) if retry_after else None

                return RateLimitError(
                    error_message,
                    provider,
                    retry_after=retry_seconds,
                    error_code="rate_limit_error",
                    original_error=error,
                )

            # 400 - Bad request
            if status_code == 400:
                if (
                    "context_length" in error_message.lower()
                    or "max_tokens" in error_message.lower()
                ):
                    return ContextLengthExceededError(
                        error_message,
                        provider,
                        error_code="context_length_exceeded",
                        original_error=error,
                    )

                return InvalidRequestError(
                    error_message,
                    provider,
                    error_code="invalid_request",
                    original_error=error,
                )

            # 404 - Not found
            if status_code == 404:
                return InvalidRequestError(
                    error_message,
                    provider,
                    error_code="not_found",
                    original_error=error,
                )

            # 500-599 - Server errors
            if 500 <= status_code < 600:
                return ServerError(
                    error_message,
                    provider,
                    status_code=status_code,
                    error_code=f"server_error_{status_code}",
                    original_error=error,
                )
    except ImportError:
        pass

    # Generic error
    return ProviderError(
        error_message,
        provider,
        original_error=error,
    )


def map_anthropic_error(error: Exception, provider: str = "anthropic") -> ProviderError:
    """
    Map Anthropic API exceptions to unified error types.

    Handles httpx errors from Anthropic SDK requests.

    Args:
        error: Original exception from Anthropic SDK
        provider: Provider name (default: "anthropic")

    Returns:
        Mapped ProviderError subclass
    """
    error_message = str(error)

    # Import httpx for error handling
    try:
        from httpx import ConnectError, HTTPStatusError, TimeoutException
    except ImportError:
        # httpx not available, return generic error
        return ProviderError(
            error_message,
            provider,
            original_error=error,
        )

    # HTTP status errors
    if isinstance(error, HTTPStatusError):
        status_code = error.response.status_code

        # 401 - Authentication
        if status_code == 401:
            return AuthenticationError(
                error_message,
                provider,
                error_code="authentication_error",
                original_error=error,
                safe_message="Invalid Anthropic API key. Please check your API key.",
            )

        # 429 - Rate limit
        if status_code == 429:
            # Extract retry-after from headers
            retry_after = error.response.headers.get("retry-after")
            retry_seconds = float(retry_after) if retry_after else None

            if retry_seconds:
                safe_message = f"Rate limit exceeded. Retry after {retry_seconds}s."
            else:
                safe_message = "Rate limit exceeded. Please try again later."

            return RateLimitError(
                error_message,
                provider,
                retry_after=retry_seconds,
                error_code="rate_limit_error",
                original_error=error,
                safe_message=safe_message,
            )

        # 400 - Bad request
        if status_code == 400:
            # Check for specific error types
            if "context_length" in error_message.lower():
                return ContextLengthExceededError(
                    error_message,
                    provider,
                    error_code="context_length_exceeded",
                    original_error=error,
                )

            return InvalidRequestError(
                error_message,
                provider,
                error_code="invalid_request",
                original_error=error,
            )

        # 404 - Not found (model not found)
        if status_code == 404:
            return InvalidRequestError(
                error_message,
                provider,
                error_code="not_found",
                original_error=error,
                safe_message="Model or endpoint not found. Check your configuration.",
            )

        # 500-599 - Server errors
        if 500 <= status_code < 600:
            return ServerError(
                error_message,
                provider,
                status_code=status_code,
                error_code=f"server_error_{status_code}",
                original_error=error,
            )

        # Other HTTP errors
        return ProviderError(
            error_message,
            provider,
            error_code=f"http_error_{status_code}",
            original_error=error,
        )

    # Timeout errors
    if isinstance(error, TimeoutException):
        return APITimeoutError(
            error_message,
            provider,
            error_code="timeout",
            original_error=error,
            safe_message="Request timed out. Please try again.",
        )

    # Connection errors
    if isinstance(error, ConnectError):
        return APIConnectionError(
            error_message,
            provider,
            error_code="connection_error",
            original_error=error,
            safe_message="Connection failed. Please check your network.",
        )

    # Generic error
    return ProviderError(
        error_message,
        provider,
        original_error=error,
    )
