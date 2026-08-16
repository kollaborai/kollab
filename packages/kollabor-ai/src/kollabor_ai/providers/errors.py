"""
Unified error hierarchy for LLM provider exceptions.

Provides standardized error types for OpenAI, Anthropic, and Azure OpenAI providers.
Maps provider-specific exceptions to unified error types with safe user messages.
"""

import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from math import isfinite
from typing import Any, Dict, Mapping, Optional


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

        # Remove authorization headers, including optional Basic/Bearer schemes.
        message = re.sub(
            r"Authorization:\s*(?:(?:Bearer|Basic)\s+)?[^\s,;]+",
            "Authorization: [REDACTED]",
            message,
            flags=re.IGNORECASE,
        )

        # Remove provider-specific API key headers.
        message = re.sub(
            r"\b(x-goog-api-key|x-api-key|api-key)\s*:\s*[^\s,;]+",
            r"\1: [REDACTED]",
            message,
            flags=re.IGNORECASE,
        )

        # Remove query credentials while preserving surrounding URL context.
        message = re.sub(
            r"([?&](?:api[_-]?key|key|access[_-]?token|refresh[_-]?token|"
            r"client[_-]?secret|token|credentials?)=)[^&#\s]+",
            r"\1[REDACTED]",
            message,
            flags=re.IGNORECASE,
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
            if retry_after is not None:
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
        retry_after: Optional[float] = None,
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
        self.retry_after = retry_after
        if safe_message is None:
            safe_message = "Request timed out. Please try again."
        super().__init__(message, provider, error_code, original_error, safe_message)

    def to_dict(self) -> Dict[str, Any]:
        data = super().to_dict()
        if self.retry_after is not None:
            data["retry_after"] = self.retry_after
        return data


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
        retry_after: Optional[float] = None,
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
        self.retry_after = retry_after
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
        if self.retry_after is not None:
            data["retry_after"] = self.retry_after
        return data


class TransientHTTPError(ProviderError):
    """A retryable HTTP status outside the timeout and 5xx error classes."""

    def __init__(
        self,
        message: str,
        provider: str,
        status_code: int,
        error_code: Optional[str] = None,
        original_error: Optional[Exception] = None,
        safe_message: Optional[str] = None,
        retry_after: Optional[float] = None,
    ):
        self.status_code = status_code
        self.retry_after = retry_after
        if safe_message is None:
            safe_message = (
                f"{provider.capitalize()} temporarily unavailable ({status_code}). "
                "Please try again later."
            )
        super().__init__(message, provider, error_code, original_error, safe_message)

    def to_dict(self) -> Dict[str, Any]:
        data = super().to_dict()
        data["status_code"] = self.status_code
        if self.retry_after is not None:
            data["retry_after"] = self.retry_after
        return data


class EmptyResponseError(ProviderError):
    """Provider returned no content, tool calls, usage, or raw chunks."""

    def __init__(
        self,
        message: str,
        provider: str,
        error_code: Optional[str] = None,
        original_error: Optional[Exception] = None,
        safe_message: Optional[str] = None,
    ):
        if safe_message is None:
            safe_message = (
                f"{provider.capitalize()} returned an empty response. "
                "Please try again."
            )
        super().__init__(message, provider, error_code, original_error, safe_message)


def parse_retry_after(
    value: Any,
    *,
    now: Optional[datetime] = None,
) -> Optional[float]:
    """Parse Retry-After delta-seconds or HTTP-date without raising."""
    if value is None:
        return None

    raw_value = str(value).strip()
    if not raw_value:
        return None

    try:
        seconds = float(raw_value)
        if isfinite(seconds) and seconds >= 0:
            return seconds
    except (TypeError, ValueError):
        pass

    try:
        retry_at = parsedate_to_datetime(raw_value)
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        current_time = now or datetime.now(timezone.utc)
        return max(0.0, (retry_at - current_time).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None


def parse_retry_after_headers(
    headers: Optional[Mapping[str, Any]] = None,
    *,
    now: Optional[datetime] = None,
) -> Optional[float]:
    """Parse provider retry headers, preferring millisecond precision."""
    if not headers:
        return None

    def get_header(name: str) -> Any:
        value = headers.get(name)
        if value is not None:
            return value
        for key, candidate in headers.items():
            if str(key).lower() == name:
                return candidate
        return None

    raw_milliseconds = get_header("retry-after-ms")
    if raw_milliseconds is not None:
        try:
            milliseconds = float(str(raw_milliseconds).strip())
            if isfinite(milliseconds) and milliseconds >= 0:
                return milliseconds / 1000.0
        except (TypeError, ValueError):
            pass

    return parse_retry_after(get_header("retry-after"), now=now)


def map_http_status_error(
    error: Exception,
    provider: str,
    status_code: int,
    headers: Optional[Mapping[str, Any]] = None,
    *,
    authentication_safe_message: Optional[str] = None,
    not_found_safe_message: Optional[str] = None,
    detail: str = "",
) -> ProviderError:
    """Map an HTTP status while preserving response headers for retry policy.

    ``detail`` is the provider's own explanation from the response body. It is
    appended to the message so the error says what was actually wrong, and so
    the body-text checks below (context length) can match on real content
    instead of on httpx's generic status line.
    """
    error_message = str(error)
    if detail:
        error_message = f"{error_message}: {detail}"
    response_headers = headers or {}
    retry_after = parse_retry_after_headers(response_headers)

    if status_code == 401:
        return AuthenticationError(
            error_message,
            provider,
            error_code="authentication_error",
            original_error=error,
            safe_message=authentication_safe_message,
        )

    if status_code == 429:
        return RateLimitError(
            error_message,
            provider,
            retry_after=retry_after,
            error_code="rate_limit_error",
            original_error=error,
        )

    if status_code == 408:
        return APITimeoutError(
            error_message,
            provider,
            error_code="timeout",
            original_error=error,
            retry_after=retry_after,
        )

    if status_code == 409:
        return TransientHTTPError(
            error_message,
            provider,
            status_code=status_code,
            error_code="transient_http_409",
            original_error=error,
            retry_after=retry_after,
        )

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

    if status_code == 404:
        return InvalidRequestError(
            error_message,
            provider,
            error_code="not_found",
            original_error=error,
            safe_message=not_found_safe_message,
        )

    if 500 <= status_code < 600:
        return ServerError(
            error_message,
            provider,
            status_code=status_code,
            error_code=f"server_error_{status_code}",
            original_error=error,
            retry_after=retry_after,
        )

    return ProviderError(
        error_message,
        provider,
        error_code=f"http_error_{status_code}",
        original_error=error,
    )


def map_httpx_error(
    error: Exception,
    provider: str,
    *,
    authentication_safe_message: Optional[str] = None,
    not_found_safe_message: Optional[str] = None,
) -> ProviderError:
    """Map httpx transport/status failures to the shared provider hierarchy."""
    try:
        import httpx
    except ImportError:
        return ProviderError(str(error), provider, original_error=error)

    error_message = str(error)
    if isinstance(error, httpx.TimeoutException):
        return APITimeoutError(
            error_message,
            provider,
            error_code="timeout",
            original_error=error,
        )

    if isinstance(error, httpx.TransportError):
        return APIConnectionError(
            error_message,
            provider,
            error_code="connection_error",
            original_error=error,
        )

    if isinstance(error, httpx.HTTPStatusError):
        response = error.response
        return map_http_status_error(
            error,
            provider,
            response.status_code,
            response.headers,
            authentication_safe_message=authentication_safe_message,
            not_found_safe_message=not_found_safe_message,
            detail=_response_detail(response),
        )

    return ProviderError(error_message, provider, original_error=error)


def _response_detail(response: Any) -> str:
    """Provider-supplied explanation from an error response body.

    ``str(httpx.HTTPStatusError)`` is only ``"Client error '400 Bad Request'
    for url ..."`` -- it never says *why*. Every provider states the actual
    reason in the response body (Google's ``error.message``, OpenAI's
    ``error.message``, plain text elsewhere), and discarding it turns every
    4xx into an unactionable status line. Messages are redacted downstream by
    ``ProviderError._sanitize_message``.
    """
    try:
        body = response.json()
    except Exception:  # noqa: BLE001 -- not JSON, fall back to text
        try:
            text = (response.text or "").strip()
        except Exception:  # noqa: BLE001
            return ""
        return text[:500]

    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            message = error.get("message") or error.get("status") or ""
            if message:
                return str(message)[:500]
        if isinstance(error, str) and error:
            return error[:500]
        for key in ("message", "detail", "error_description"):
            value = body.get(key)
            if isinstance(value, str) and value:
                return value[:500]
    return str(body)[:500] if body else ""


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
            retry_after = parse_retry_after_headers(headers)

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
        if status_code is not None:
            response = getattr(error, "response", None)
            headers = getattr(response, "headers", {}) if response is not None else {}
            return map_http_status_error(
                error,
                provider,
                status_code,
                headers,
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
    except ImportError:
        pass
    else:
        if isinstance(
            error,
            (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError),
        ):
            return map_httpx_error(error, provider)

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
    return map_httpx_error(
        error,
        provider,
        authentication_safe_message="Invalid Anthropic API key. Please check your API key.",
        not_found_safe_message="Model or endpoint not found. Check your configuration.",
    )
