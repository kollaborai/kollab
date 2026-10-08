"""Opt-in, workspace-bound A2A 1.0 service using the official Python SDK.

The Agent Card is public. Every task RPC requires paired membership, a purpose-
and-conversation grant, and a fresh device proof covering the exact HTTP body.
This is a provider-free skill adapter; it does not interpret arbitrary prompts.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from a2a.auth.user import User
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_jsonrpc_routes
from a2a.server.routes.common import ServerCallContextBuilder
from a2a.server.tasks import TaskUpdater
from a2a.types import AgentCard, Part, Task, TaskState, TaskStatus
from google.protobuf.json_format import MessageToDict, ParseDict
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .a2a_task_store import BoundedTaskStore
from .a2a_workspace_runtime import (
    SKILL_TO_TOOL,
    WorkspaceRequestError,
    WorkspaceSkillRequest,
    WorkspaceToolRuntime,
)

RPC_PATH = "/a2a"
CARD_PATH = "/.well-known/agent-card.json"
MAX_REQUEST_BYTES = 65_536
SUPPORTED_METHODS = frozenset({"SendMessage", "GetTask", "ListTasks", "CancelTask"})
REQUEST_BODY_TIMEOUT_SECONDS = 10


class _NoStoreMiddleware:
    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        async def send_no_store(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() != b"cache-control"
                ]
                message = {
                    **message,
                    "headers": headers + [(b"cache-control", b"no-store")],
                }
            await send(message)

        await self.app(scope, receive, send_no_store)


def _decode_request(raw: bytes) -> Any:
    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON keys are not accepted")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ValueError("Non-finite JSON values are not accepted")

    return json.loads(
        raw, object_pairs_hook=unique_pairs, parse_constant=reject_constant
    )


@dataclass(frozen=True)
class A2AWorkspaceConfig:
    workspace_id: str
    public_url: str
    workspace_label: str = "Kollab Workspace"
    pairing_challenge_id: str | None = None
    locator_state_path: Path | None = None
    max_tasks: int = 256
    max_active_tasks: int = 8
    terminal_task_ttl_seconds: float = 900

    def __post_init__(self) -> None:
        from .dns.a2a_signing import normalize_https_origin

        if not self.workspace_id or len(self.workspace_id) > 200:
            raise ValueError("workspace_id must be nonempty and bounded")
        object.__setattr__(self, "public_url", normalize_https_origin(self.public_url))
        if not self.workspace_label or len(self.workspace_label) > 120:
            raise ValueError("workspace_label must contain 1..120 characters")


class _PairedUser(User):
    def __init__(self, principal: Any):
        # Scope task IDs/history to the exact authenticated device, workspace,
        # conversation and purpose. A guessed task ID cannot cross any boundary.
        scope = [
            principal.owner_id,
            principal.device_id,
            principal.workspace_id,
            principal.conversation_id,
            principal.purpose,
        ]
        self._name = hashlib.sha256(json.dumps(scope).encode()).hexdigest()

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def user_name(self) -> str:
        return self._name


class _CallContextBuilder(ServerCallContextBuilder):
    def build(self, request: Request) -> ServerCallContext:
        return ServerCallContext(
            user=_PairedUser(request.state.principal),
            state={
                "principal": request.state.principal,
                "skill": request.state.skill,
                "headers": {"a2a-version": request.headers.get("a2a-version", "")},
                "task_reservation": request.state.task_reservation,
            },
        )


class WorkspaceAgentExecutor(AgentExecutor):
    """Translate two advertised skills into permission-checked local tools."""

    def __init__(self, runtime: WorkspaceToolRuntime, directory: Any):
        self.runtime = runtime
        self.directory = directory

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        await event_queue.enqueue_event(
            Task(
                id=context.task_id,
                context_id=context.context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
                history=[context.message] if context.message else [],
            )
        )
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.start_work()
        try:
            request = context.call_context.state["skill"]
            result = await self.runtime.execute(
                request,
                context.task_id,
                authorization_check=lambda: self.directory.revalidate(
                    context.call_context.state["principal"]
                ),
            )
            payload = {
                "skill": request.skill,
                "path": request.path,
                "success": result.success,
                "output": result.output,
                "error": result.error,
                "toolType": result.tool_type,
            }
            await updater.add_artifact(
                [ParseDict({"data": payload, "mediaType": "application/json"}, Part())],
                name="workspace-tool-result",
                last_chunk=True,
            )
            if result.success:
                await updater.complete()
            else:
                await updater.failed(
                    updater.new_agent_message([Part(text=result.error)])
                )
        except WorkspaceRequestError as exc:
            await updater.reject(updater.new_agent_message([Part(text=str(exc))]))
        except Exception:
            # The SDK logs transport/runtime failures; do not reveal credentials,
            # local traceback text, or host configuration in a remote response.
            await updater.failed(
                updater.new_agent_message([Part(text="Local tool execution failed")])
            )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        # File operations run in worker threads and cannot be atomically undone.
        # Do not publish a canceled status that could conceal a completed write.
        from a2a.utils.errors import TaskNotCancelableError

        raise TaskNotCancelableError(
            message="Atomic file operations cannot be canceled"
        )


def _skill_from_message(
    body: dict[str, Any], conversation_id: str, message_id: str
) -> WorkspaceSkillRequest | None:
    if body.get("method") != "SendMessage":
        return None
    params = body.get("params", {})
    message = params.get("message", {})
    if set(params) - {"message", "configuration"} or set(message) - {
        "messageId",
        "contextId",
        "role",
        "parts",
    }:
        raise WorkspaceRequestError("Unsupported message fields")
    if set(params.get("configuration", {})) - {
        "acceptedOutputModes",
        "historyLength",
        "returnImmediately",
    }:
        raise WorkspaceRequestError("Unsupported send configuration")
    if (
        message.get("contextId") != conversation_id
        or message.get("messageId") != message_id
    ):
        raise WorkspaceRequestError("Message and proof identities must match")
    if (
        message.get("role") != "ROLE_USER"
        or message.get("taskId")
        or message.get("referenceTaskIds")
    ):
        raise WorkspaceRequestError(
            "A new user task without task references is required"
        )
    if params.get("tenant") or params.get("configuration", {}).get(
        "taskPushNotificationConfig"
    ):
        raise WorkspaceRequestError("Tenants and push notifications are not supported")
    parts = message.get("parts")
    if not isinstance(parts, list) or len(parts) != 1 or not isinstance(parts[0], dict):
        raise WorkspaceRequestError("One structured data part is required")
    if set(parts[0]) - {"data", "mediaType"} or "data" not in parts[0]:
        raise WorkspaceRequestError("One structured data part is required")
    return WorkspaceSkillRequest.parse(parts[0]["data"])


def build_workspace_app(
    config: A2AWorkspaceConfig,
    *,
    runtime: WorkspaceToolRuntime,
    directory: Any,
    card_private_key: Any,
) -> Starlette:
    """Build an opt-in service. Importing identity discovery never starts it."""
    from .dns.a2a_signing import sign_agent_card

    if directory.workspace_id != config.workspace_id:
        raise ValueError("Private directory must be bound to the configured workspace")

    card_dict = {
        "name": "Kollab Workspace",
        "description": "Explicitly granted text-file skills in one local Kollab workspace.",
        "version": "1.0.0",
        "supportedInterfaces": [
            {
                "url": config.public_url.rstrip("/") + RPC_PATH,
                "protocolBinding": "JSONRPC",
                "protocolVersion": "1.0",
            }
        ],
        "capabilities": {},
        "defaultInputModes": ["application/json"],
        "defaultOutputModes": ["application/json"],
        "skills": [
            {
                "id": skill,
                "name": (
                    "Read Text File"
                    if skill == "workspace.read"
                    else "Create Text File"
                ),
                "description": (
                    "Read an existing UTF-8 text file."
                    if skill == "workspace.read"
                    else "Create a new UTF-8 text file; existing files are not overwritten."
                ),
                "tags": ["workspace", "text"],
            }
            for skill in sorted(runtime.allowed_skills)
        ],
        "securitySchemes": {
            "membership": {
                "httpAuthSecurityScheme": {
                    "scheme": "bearer",
                    "bearerFormat": "JWT",
                    "description": "Human-approved device membership credential.",
                }
            },
            "grant": {
                "apiKeySecurityScheme": {
                    "location": "header",
                    "name": "X-Kollab-Grant",
                    "description": "Owner-signed workspace, purpose and conversation grant.",
                }
            },
            "proof": {
                "apiKeySecurityScheme": {
                    "location": "header",
                    "name": "X-Kollab-Proof",
                    "description": "Device-signed HTTP body proof; scope headers follow the Kollab pairing profile.",
                }
            },
        },
        "securityRequirements": [
            {
                "schemes": {
                    "membership": {"list": []},
                    "grant": {"list": []},
                    "proof": {"list": []},
                }
            }
        ],
    }
    card = ParseDict(card_dict, AgentCard())
    signed_card = sign_agent_card(MessageToDict(card), card_private_key)
    task_store = BoundedTaskStore(
        max_tasks=config.max_tasks,
        max_active=config.max_active_tasks,
        terminal_ttl=config.terminal_task_ttl_seconds,
    )
    handler = DefaultRequestHandler(
        agent_executor=WorkspaceAgentExecutor(runtime, directory),
        task_store=task_store,
        agent_card=card,
    )
    sdk_route = create_jsonrpc_routes(
        handler, RPC_PATH, context_builder=_CallContextBuilder()
    )[0]

    def credential_headers(request: Request) -> tuple[str, dict[str, str]]:
        authorization = request.headers.get("authorization", "")
        headers = {
            name: request.headers.get(name, "")
            for name in (
                "x-kollab-grant",
                "x-kollab-proof",
                "x-kollab-purpose",
                "x-kollab-conversation",
                "x-kollab-message-id",
            )
        }
        return authorization, headers

    async def read_body(
        request: Request, limit: int = MAX_REQUEST_BYTES
    ) -> bytes | None:
        raw = bytearray()
        try:
            async with asyncio.timeout(REQUEST_BODY_TIMEOUT_SECONDS):
                async for chunk in request.stream():
                    raw.extend(chunk)
                    if len(raw) > limit:
                        return None
        except TimeoutError as exc:
            raise HTTPException(408, "Request body timeout") from exc
        # Preserve the exact signed body for both verifier and SDK deserialization.
        request._body = bytes(raw)
        return bytes(raw)

    def authorize(
        authorization: str, headers: dict[str, str], raw: bytes, path: str
    ) -> Any:
        return directory.authorize_request(
            authorization[7:],
            headers["x-kollab-grant"],
            headers["x-kollab-proof"],
            body=raw,
            method="POST",
            path=path,
            target_uri=config.public_url.rstrip("/") + path,
            recipient_workspace_id=config.workspace_id,
            purpose=headers["x-kollab-purpose"],
            conversation_id=headers["x-kollab-conversation"],
            message_id=headers["x-kollab-message-id"],
        )

    async def rpc(request: Request) -> JSONResponse:
        authorization, headers = credential_headers(request)
        if not authorization.startswith("Bearer ") or not all(headers.values()):
            return JSONResponse(
                {"error": "Paired membership, grant and request proof required"},
                status_code=401,
            )
        raw = await read_body(request)
        if raw is None:
            return JSONResponse({"error": "Request too large"}, status_code=413)
        try:
            body = _decode_request(raw)
            if (
                not isinstance(body, dict)
                or body.get("method") not in SUPPORTED_METHODS
            ):
                raise WorkspaceRequestError("Unsupported A2A method")
            if set(body) - {"jsonrpc", "id", "method", "params"} or request.url.query:
                raise WorkspaceRequestError("Unsupported RPC envelope or query")
            if not isinstance(body.get("params", {}), dict):
                raise WorkspaceRequestError("RPC params must be an object")
            skill = _skill_from_message(
                body, headers["x-kollab-conversation"], headers["x-kollab-message-id"]
            )
            if skill and skill.skill != headers["x-kollab-purpose"]:
                raise WorkspaceRequestError("Purpose must match the requested skill")
        except (ValueError, TypeError, AttributeError, RecursionError):
            return JSONResponse(
                {"error": "Invalid workspace task request"}, status_code=400
            )
        try:
            principal = authorize(authorization, headers, raw, RPC_PATH)
        except ValueError:
            return JSONResponse(
                {"error": "Request authorization denied"}, status_code=403
            )
        if skill:
            try:
                runtime.validate(skill)
            except WorkspaceRequestError as exc:
                return JSONResponse({"error": str(exc)}, status_code=403)
        request.state.principal = principal
        request.state.skill = skill
        reservation = await task_store.reserve() if skill else None
        if skill and reservation is None:
            return JSONResponse(
                {"error": "Workspace task capacity reached; retry later"},
                status_code=429,
            )
        request.state.task_reservation = reservation
        try:
            return await sdk_route.endpoint(request)
        finally:
            if reservation:
                await task_store.release_reservation(reservation)

    async def workspace_directory(request: Request) -> JSONResponse:
        authorization, headers = credential_headers(request)
        if not authorization.startswith("Bearer ") or not all(headers.values()):
            return JSONResponse(
                {"error": "Paired membership, grant and request proof required"},
                status_code=401,
            )
        raw = await read_body(request, 1024)
        if raw is None:
            return JSONResponse({"error": "Request too large"}, status_code=413)
        if headers["x-kollab-purpose"] != "directory.read":
            return JSONResponse(
                {"error": "A directory.read grant is required"}, status_code=403
            )
        try:
            if _decode_request(raw) != {} or request.url.query:
                raise ValueError("Empty object required")
            authorize(authorization, headers, raw, "/kollab/directory")
        except (ValueError, RecursionError):
            return JSONResponse(
                {"error": "Request authorization denied"}, status_code=403
            )
        return JSONResponse(
            {
                "workspaces": [
                    {
                        "workspace_id": config.workspace_id,
                        "label": config.workspace_label,
                        "agent_card": config.public_url.rstrip("/") + CARD_PATH,
                    }
                ]
            }
        )

    async def pairing_challenge(request: Request) -> JSONResponse:
        try:
            challenge = directory.get_pairing_challenge(config.pairing_challenge_id)
        except ValueError:
            return JSONResponse(
                {"error": "No pending pairing challenge"}, status_code=404
            )
        return JSONResponse(
            {"challenge": challenge.token, "expires_at": challenge.expires_at}
        )

    async def pairing_proof(request: Request) -> JSONResponse:
        raw = await read_body(request, 16_384)
        if raw is None:
            return JSONResponse({"error": "Request too large"}, status_code=413)
        try:
            payload = _decode_request(raw)
            if not isinstance(payload, dict) or "proof" not in payload:
                raise ValueError("Only a pairing proof is accepted")
            challenge = directory.get_pairing_challenge(config.pairing_challenge_id)
            proof = directory.record_pairing_proof(challenge, payload["proof"])
        except (ValueError, TypeError, RecursionError):
            return JSONResponse({"error": "Pairing proof rejected"}, status_code=403)
        return JSONResponse(
            {"status": "pending_local_approval", "device_id": proof.device_id},
            status_code=202,
        )

    async def agent_card(request: Request) -> JSONResponse:
        # Serve the exact signed v1 payload. The SDK convenience card route adds
        # v0.3 compatibility fields after serialization, invalidating this JWS.
        return JSONResponse(signed_card)

    async def service_locator(request: Request) -> JSONResponse:
        from .dns.service_locator import build_a2a_service_locator

        document = await asyncio.to_thread(
            build_a2a_service_locator,
            config.public_url,
            card_private_key,
            config.locator_state_path,
        )
        return JSONResponse(document)

    @asynccontextmanager
    async def lifespan(app: Starlette):
        await runtime.start()
        try:
            yield
        finally:
            await handler.aclose()

    routes = [
        Route(CARD_PATH, agent_card),
        Route(RPC_PATH, rpc, methods=["POST"]),
        Route("/kollab/directory", workspace_directory, methods=["POST"]),
    ]
    if config.pairing_challenge_id:
        routes.extend(
            [
                Route("/kollab/pairing/challenge", pairing_challenge),
                Route("/kollab/pairing/proof", pairing_proof, methods=["POST"]),
            ]
        )
    if config.locator_state_path is not None:
        routes.extend(
            [
                Route("/.well-known/agent-keys.json", service_locator),
                Route("/.well-known/agent-keys", service_locator),
            ]
        )

    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse({"error": str(exc.detail)}, status_code=exc.status_code)

    app = Starlette(
        routes=routes,
        lifespan=lifespan,
        middleware=[Middleware(_NoStoreMiddleware)],
        exception_handlers={HTTPException: http_error},
    )
    app.state.runtime = runtime
    app.state.a2a_handler = handler
    app.state.signed_card = signed_card
    app.state.task_store = task_store
    return app


def main() -> None:
    """Explicit standalone receiver; production access requires an HTTPS proxy."""
    import uvicorn

    from .dns.private_directory import PrivateDirectory

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--workspace-label", default="Kollab Workspace")
    parser.add_argument("--pairing-challenge-id")
    parser.add_argument("--locator-state", type=Path)
    parser.add_argument("--directory-state", required=True, type=Path)
    parser.add_argument("--owner-public-key-file", required=True, type=Path)
    parser.add_argument("--card-key-file", required=True, type=Path)
    parser.add_argument("--public-url", required=True)
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument(
        "--allow-skill",
        action="append",
        choices=sorted(SKILL_TO_TOOL),
        required=True,
    )
    args = parser.parse_args()
    config = A2AWorkspaceConfig(
        args.workspace_id,
        args.public_url,
        args.workspace_label,
        args.pairing_challenge_id,
        args.locator_state
        or args.directory_state.with_name(
            args.directory_state.stem + ".a2a-locator.json"
        ),
    )
    runtime = WorkspaceToolRuntime(args.workspace, tuple(args.allow_skill))
    directory = PrivateDirectory(
        args.directory_state,
        owner_public_key=bytes.fromhex(args.owner_public_key_file.read_text().strip()),
        workspace_id=args.workspace_id,
    )
    app = build_workspace_app(
        config,
        runtime=runtime,
        directory=directory,
        card_private_key=args.card_key_file.read_text().strip(),
    )
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
