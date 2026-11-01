"""Request-only image parts for one native goal attempt."""

import asyncio
import logging
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, Iterator


def _current_task() -> asyncio.Task | None:
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


@dataclass
class GoalImageScope:
    parts: list[dict[str, Any]]
    owner_queue: Any
    owner_api: Any
    owner_task: asyncio.Task | None = field(default_factory=_current_task)
    marker: str = field(default_factory=lambda: uuid.uuid4().hex)
    carrier: Any = None
    active: bool = True

    def close(self) -> None:
        self.active = False
        self.parts = []


_current: ContextVar[GoalImageScope | None] = ContextVar("goal_image_scope", default=None)
_request: ContextVar[GoalImageScope | None] = ContextVar("goal_image_request", default=None)
_diagnostics: ContextVar[GoalImageScope | None] = ContextVar("goal_image_diagnostics", default=None)


def current_goal_image_scope() -> GoalImageScope | None:
    scope = _current.get()
    if scope is None or not scope.active or scope.owner_task is not _current_task():
        return None
    return scope


def current_goal_image_request_scope(owner_api: Any) -> GoalImageScope | None:
    scope = _request.get()
    if scope is not current_goal_image_scope() or scope is None:
        return None
    return scope if scope.owner_api is owner_api else None


@contextmanager
def goal_image_scope(parts: list[dict[str, Any]], owner_queue: Any, owner_api: Any) -> Iterator[GoalImageScope | None]:
    scope = GoalImageScope(parts, owner_queue, owner_api) if parts else None
    # Empty nested scopes must shadow an outer goal, too.
    token = _current.set(scope)
    try:
        yield scope
    finally:
        if scope is not None:
            scope.close()
        _current.reset(token)


@contextmanager
def goal_image_request_scope(owner_queue: Any) -> Iterator[None]:
    scope = current_goal_image_scope()
    token = _request.set(scope if scope is not None and scope.owner_queue is owner_queue else None)
    try:
        yield
    finally:
        _request.reset(token)



class _ImageRequestLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not goal_image_diagnostics_active()


_sdk_image_filter = _ImageRequestLogFilter()


def _protect_sdk_request_logs() -> None:
    # The SDK logs its request body at DEBUG on these module loggers. An
    # owner-scoped filter avoids changing logging for concurrent sessions.
    for name in (
        "openai._base_client", "openai._response", "openai._legacy_response",
        "openai._streaming", "httpx", "httpcore.http11", "httpcore.http2",
        "httpcore.connection", "httpcore.proxy", "aiohttp.client",
    ):
        logger = logging.getLogger(name)
        if _sdk_image_filter not in logger.filters:
            logger.addFilter(_sdk_image_filter)


@contextmanager
def goal_image_diagnostics_scope(owner_api: Any) -> Iterator[None]:
    # Only the owning API request installs the guard. A different API or an
    # unrelated child task shadows any inherited guard with None.
    token = _diagnostics.set(current_goal_image_request_scope(owner_api))
    try:
        yield
    finally:
        _diagnostics.reset(token)


def guard_goal_image_diagnostics(method):
    @wraps(method)
    async def guarded(owner_api, *args, **kwargs):
        with goal_image_diagnostics_scope(owner_api):
            if not goal_image_diagnostics_active():
                return await method(owner_api, *args, **kwargs)
            _protect_sdk_request_logs()
            clean_failure = None
            try:
                return await method(owner_api, *args, **kwargs)
            except Exception as exc:
                # Raising a fresh exception outside this handler avoids Python
                # retaining an unsafe original as __context__, even if its
                # traceback was suppressed with ``raise ... from None``.
                from kollabor_ai.providers.errors import ProviderError
                code = getattr(exc, "error_code", None) or getattr(exc, "code", None)
                provider = str(getattr(owner_api, "provider_type", "provider"))
                clean_failure = ProviderError(
                    "provider request failed", provider, error_code=code
                )
            raise clean_failure from None
    return guarded


def goal_image_diagnostics_active() -> bool:
    """Provider child tasks inherit this guard but not the request grant."""
    scope = _diagnostics.get()
    return scope is not None and scope.active and bool(scope.parts)


def safe_goal_error_cause(error: BaseException | None) -> BaseException | None:
    # Provider exception internals (including group headers and SDK objects)
    # can retain echoed images even when their displayed message is sanitized.
    return None if goal_image_diagnostics_active() else error
