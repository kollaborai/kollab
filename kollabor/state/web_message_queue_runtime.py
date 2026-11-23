"""Compose the safe daemon-side management surface for WebUI message queues.

Provider dispatch stays disabled until a trusted atomic admission adapter and
bounded worker supervisor are composed by the daemon. This module registers
only queue intake, reconciliation, attachment custody, and removal RPCs.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any

from kollabor_rpc import RpcCallerContext, current_rpc_caller_context

from .web_message_attachments import (
    AttachmentAuthorizationError,
    AttachmentScopeBinding,
    WebMessageAttachmentRegistry,
)
from .web_message_queue_rpc import (
    QueueRpcAuthorizationError,
    QueueScopeBinding,
    ScopeResolver,
    WebMessageQueueRegistry,
    daemon_conversation_scope_resolver,
    register_web_message_queue_handlers,
)


@dataclass(slots=True)
class WebMessageQueueRuntime:
    """Daemon-lifetime queue state and its trusted attachment owner."""

    registry: WebMessageQueueRegistry = field(repr=False)
    attachments: WebMessageAttachmentRegistry = field(repr=False)
    dispatch_available: bool = False


def _attachment_resolver(
    registry: WebMessageQueueRegistry,
    resolve_scope: ScopeResolver,
):
    async def resolve(
        params: dict[str, Any], caller_context: object
    ) -> AttachmentScopeBinding:
        if (
            not isinstance(caller_context, RpcCallerContext)
            or not caller_context.is_local_operator
            or not isinstance(caller_context.principal_id, str)
            or not caller_context.principal_id
        ):
            raise AttachmentAuthorizationError("attachment access denied")
        try:
            value = resolve_scope(params, caller_context)
            if inspect.isawaitable(value):
                value = await value
        except Exception:
            raise AttachmentAuthorizationError("attachment access denied") from None
        if isinstance(value, QueueScopeBinding):
            scope_id = value.scope_id
            conversation_uid = value.conversation_uid
        elif isinstance(value, str):
            scope_id = value
            conversation_uid = None
        else:
            raise AttachmentAuthorizationError("attachment access denied")
        try:
            async with registry.acquire(
                scope_id,
                principal_id=caller_context.principal_id,
                conversation_uid=conversation_uid,
            ) as queue:
                return AttachmentScopeBinding(scope_id, queue.incarnation)
        except Exception:
            raise AttachmentAuthorizationError("attachment access denied") from None

    return resolve


def register_web_message_queue_runtime(
    rpc_server: Any,
    state_service: Any,
    *,
    queue_registry: WebMessageQueueRegistry | None = None,
    resolve_scope: ScopeResolver | None = None,
) -> WebMessageQueueRuntime:
    """Register authenticated queue management and attachment RPCs.

    The default resolver binds a local authenticated RPC principal to the
    daemon's current canonical conversation UID. Client fields are preconditions
    only. This composition deliberately omits queue claim/settlement methods:
    no consumer can start or falsely settle provider work until a real atomic
    admission adapter and bounded supervisor are available.
    """
    if state_service is None:
        raise ValueError("daemon state service is required")
    registry = queue_registry or WebMessageQueueRegistry()
    if not isinstance(registry, WebMessageQueueRegistry):
        raise TypeError("queue_registry must be a WebMessageQueueRegistry")
    resolver = resolve_scope or daemon_conversation_scope_resolver(state_service)
    if not callable(resolver):
        raise TypeError("resolve_scope must be callable")

    attachments = WebMessageAttachmentRegistry(
        resolve_scope=_attachment_resolver(registry, resolver),
        validate_owner_id=lambda binding, request_id: registry.validates_attachment_owner_id(
            binding.scope_id,
            binding.queue_incarnation,
            request_id,
        ),
    )
    register_web_message_queue_handlers(
        rpc_server,
        registry,
        resolve_scope=resolver,
        attachment_registry=attachments,
        include_worker_methods=False,
    )

    async def capabilities(params: dict[str, Any]) -> dict[str, bool]:
        caller_context = current_rpc_caller_context()
        if (
            not isinstance(caller_context, RpcCallerContext)
            or not caller_context.is_local_operator
            or not isinstance(caller_context.principal_id, str)
            or not caller_context.principal_id
        ):
            raise QueueRpcAuthorizationError("queue access denied")
        try:
            value = resolver(params, caller_context)
            if inspect.isawaitable(value):
                value = await value
        except Exception:
            raise QueueRpcAuthorizationError("queue access denied") from None
        if isinstance(value, QueueScopeBinding):
            if not value.scope_id or not value.conversation_uid:
                raise QueueRpcAuthorizationError("queue access denied")
        elif not isinstance(value, str) or not value:
            raise QueueRpcAuthorizationError("queue access denied")
        return {"dispatch_available": False}

    rpc_server.register("state.web_queue.capabilities", capabilities)
    return WebMessageQueueRuntime(registry=registry, attachments=attachments)
