"""Message/turn endpoint - the core SSE stream.

The turn itself runs inside the session's daemon. This route submits the user
message, then relays the daemon's structured events until the turn completes.
The wire format is unchanged from when the engine ran its own turn loop, so
clients need no modification.
"""

import asyncio
import json
import logging
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException  # type: ignore[import-not-found]
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse  # type: ignore[import-not-found]

from .. import sse
from ..server import get_session_registry

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sessions", tags=["messages"])

# A turn can legitimately run for a long time (big builds, long tool chains).
# This only bounds silence: any event from the daemon resets it.
EVENT_IDLE_TIMEOUT_SECONDS = 600.0


class MessageRequest(BaseModel):
    content: str
    continuation: bool = False


@router.post("/{session_id}/message")
async def send_message(session_id: str, body: MessageRequest):
    registry = get_session_registry()
    session = registry.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if not session.alive:
        raise HTTPException(status_code=409, detail="Session daemon is not running")

    # Subscribe before submitting so no event can land in the gap.
    queue = session.subscribe()

    try:
        accepted = await session.send_message(body.content)
    except Exception as e:
        session.unsubscribe(queue)
        logger.error("send_message failed for %s: %s", session_id, e)
        raise HTTPException(status_code=502, detail=f"daemon unreachable: {e}")

    if not accepted.get("accepted"):
        session.unsubscribe(queue)
        reason = accepted.get("reason", "rejected")
        status = 409 if "in flight" in reason else 400
        raise HTTPException(status_code=status, detail=reason)

    async def event_generator():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=EVENT_IDLE_TIMEOUT_SECONDS
                    )
                except asyncio.TimeoutError:
                    yield {
                        "data": sse.serialize(
                            sse.error(
                                session_id,
                                "turn_timeout",
                                f"no daemon activity for {EVENT_IDLE_TIMEOUT_SECONDS}s",
                            )
                        )
                    }
                    return

                event.setdefault("session_id", session_id)

                if event.get("type") == "daemon_closed":
                    yield {
                        "data": sse.serialize(
                            sse.error(session_id, "daemon_closed", "daemon exited")
                        )
                    }
                    return

                yield {"data": sse.serialize(event)}

                if event.get("type") == "turn_complete":
                    return
        finally:
            session.unsubscribe(queue)

    return EventSourceResponse(event_generator())


@router.get("/{session_id}/events")
async def stream_events(session_id: str):
    """Follow a session's event stream without submitting a turn.

    A client that reloads mid-turn loses the POST /message stream, but the
    daemon keeps working - and if it is blocked on a permission prompt, the
    answer never produces visible output. This lets a reconnecting client pick
    the turn back up. Ends at turn_complete so it does not leak subscriptions.
    """
    registry = get_session_registry()
    session = registry.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if not session.alive:
        raise HTTPException(status_code=409, detail="Session daemon is not running")

    queue = session.subscribe()

    async def event_generator():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=EVENT_IDLE_TIMEOUT_SECONDS
                    )
                except asyncio.TimeoutError:
                    return

                event.setdefault("session_id", session_id)
                if event.get("type") == "daemon_closed":
                    yield {
                        "data": sse.serialize(
                            sse.error(session_id, "daemon_closed", "daemon exited")
                        )
                    }
                    return

                yield {"data": sse.serialize(event)}

                if event.get("type") == "turn_complete":
                    return
        finally:
            session.unsubscribe(queue)

    return EventSourceResponse(event_generator())


class AssistantRequest(BaseModel):
    """assistant-transport request envelope.

    assistant-ui sends command objects with camelCase fields (for example,
    ``toolCallId``).  Keep commands untyped here because the transport adds
    fields as it evolves; the endpoint validates the fields it consumes.
    """

    commands: List[Dict[str, Any]] = Field(default_factory=list)
    state: Optional[Any] = None
    tools: Optional[Dict[str, Any]] = None
    system: Optional[Any] = None


def _assistant_command_type(command: Dict[str, Any]) -> str:
    return str(command.get("type") or "").strip().lower()


def _assistant_message_text(command: Dict[str, Any]) -> str:
    """Extract text from an assistant-ui ``add-message`` command."""
    message = command.get("message")
    if isinstance(message, str):
        return message
    if not isinstance(message, dict):
        content = command.get("content")
        return content if isinstance(content, str) else ""

    parts = message.get("parts", [])
    if isinstance(parts, list):
        text = "\n".join(
            str(part.get("text", ""))
            for part in parts
            if isinstance(part, dict) and part.get("type") == "text"
        )
        if text:
            return text
    content = message.get("content")
    if isinstance(content, str):
        return content
    return ""


def _assistant_tool_result(command: Dict[str, Any]) -> tuple[str, Any, str, str]:
    """Return tool-call id, result, decision, and approval scope."""
    tool_call_id = str(
        command.get("toolCallId")
        or command.get("tool_call_id")
        or command.get("tool_id")
        or ""
    )
    result = command.get("result")
    decision = ""
    scope = "once"
    if isinstance(result, dict):
        decision = str(result.get("decision") or result.get("action") or "").lower()
        scope = str(result.get("scope") or "once")
    elif isinstance(result, str):
        decision = result.lower()
    elif isinstance(result, bool):
        decision = "approve" if result else "deny"
    return tool_call_id, result, decision, scope


async def _cancel_assistant_turn(session, controller) -> None:
    """Cancel the daemon turn when assistant-ui closes the stream."""
    try:
        await session.state.cancel_current_request()
    except Exception as exc:
        logger.debug(
            "assistant turn cancellation failed for %s: %s", session.session_id, exc
        )


async def _assistant_next_event(session, queue, controller):
    """Wait for either a daemon event or assistant-stream cancellation."""
    event_task = asyncio.create_task(queue.get())
    cancel_task = asyncio.create_task(controller.cancelled_event.wait())
    try:
        done, _ = await asyncio.wait(
            {event_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if cancel_task in done:
            event_task.cancel()
            await _cancel_assistant_turn(session, controller)
            return None
        cancel_task.cancel()
        return event_task.result()
    finally:
        for task in (event_task, cancel_task):
            if not task.done():
                task.cancel()


def _assistant_set_tool_result(tool_controller, result: Any) -> None:
    """Set a tool result across assistant-stream versions."""
    set_response = getattr(tool_controller, "set_response", None)
    if set_response is not None:
        set_response(result)
    else:  # assistant-stream < 0.0.34
        tool_controller.set_result(result)


def _assistant_set_usage(controller: Any, usage: Dict[str, Any]) -> None:
    """Publish usage without assuming a plain dict state.

    assistant-stream 0.0.34 exposes ``controller.state`` as a StateProxy.  A
    request may also omit state entirely (in which case the property is None),
    and an older client may send a non-mapping root.  Update an existing
    mapping through the proxy so state patches are streamed; replace a missing
    or incompatible root through the controller setter.
    """
    state = controller.state
    if state is None:
        controller.state = {"usage": usage}
        return
    try:
        state["usage"] = usage
    except (KeyError, TypeError, AttributeError):
        controller.state = {"usage": usage}


def _assistant_copy_messages(state: Any) -> List[Dict[str, Any]]:
    """Copy the JSON message state before the StateProxy starts patching it."""
    if not isinstance(state, dict) or not isinstance(state.get("messages"), list):
        return []
    try:
        return json.loads(json.dumps(state["messages"]))
    except (TypeError, ValueError):
        return []


def _assistant_state_content(
    text: str,
    reasoning: str,
    tool_parts: List[Dict[str, Any]],
) -> Any:
    """Build a ThreadMessageLike-compatible content value."""
    parts: List[Dict[str, Any]] = []
    if reasoning:
        parts.append({"type": "reasoning", "text": reasoning})
    if text:
        parts.append({"type": "text", "text": text})
    parts.extend(tool_parts)
    return parts if parts else ""


def _assistant_update_message_state(
    messages: List[Dict[str, Any]],
    session_id: str,
    submitted_texts: List[str],
    text: str,
    reasoning: str,
    tool_parts: List[Dict[str, Any]],
    status: Dict[str, str],
) -> List[Dict[str, Any]]:
    """Persist the completed or interrupted turn in assistant-ui state."""
    if not submitted_texts:
        for message in reversed(messages):
            if message.get("role") != "assistant":
                continue
            content = message.get("content")
            existing_parts = (
                list(content)
                if isinstance(content, list)
                else ([{"type": "text", "text": content}] if content else [])
            )
            existing_tool_ids = {
                part.get("toolCallId")
                for part in existing_parts
                if isinstance(part, dict) and part.get("type") == "tool-call"
            }
            if reasoning:
                existing_parts.append({"type": "reasoning", "text": reasoning})
            if text:
                existing_parts.append({"type": "text", "text": text})
            existing_parts.extend(
                part
                for part in tool_parts
                if part.get("toolCallId") not in existing_tool_ids
            )
            message["content"] = existing_parts
            message["status"] = status
            return messages

    for index, submitted_text in enumerate(submitted_texts):
        messages.append(
            {
                "id": f"user-{session_id}-{len(messages) + index}",
                "role": "user",
                "content": submitted_text,
            }
        )

    messages.append(
        {
            "id": f"assistant-{session_id}-{len(messages)}",
            "role": "assistant",
            "content": _assistant_state_content(text, reasoning, tool_parts),
            "status": status,
        }
    )
    return messages


def _assistant_set_messages(controller: Any, messages: List[Dict[str, Any]]) -> None:
    """Publish message state while retaining the rest of the client state."""
    state = controller.state
    if state is None:
        controller.state = {"messages": messages}
    else:
        state["messages"] = messages


def _assistant_protocol_chunk(
    chunk_type: str, *, path: Optional[List[int]] = None, **fields: Any
) -> SimpleNamespace:
    """Build a current assistant-stream chunk for the transport encoder."""
    return SimpleNamespace(
        type=chunk_type,
        path=[] if path is None else path,
        **fields,
    )


def _assistant_finish_reason(value: Any) -> str:
    """Map daemon completion names to assistant-stream finish reasons."""
    normalized = str(value or "end_turn").lower()
    return {
        "end_turn": "stop",
        "stop": "stop",
        "tool_use": "tool-calls",
        "tool_calls": "tool-calls",
        "length": "length",
        "content_filter": "content-filter",
        "error": "error",
    }.get(normalized, "other")


async def _assistant_protocol_stream(stream, completion: Dict[str, Any]):
    """Adapt assistant-stream 0.0.x chunks to the current wire protocol.

    The Python dependency still emits the original flat chunk vocabulary while
    the React client consumes path-addressed parts. Keep the compatibility
    adapter here so the daemon/event contract stays unchanged.
    """
    next_part_index = 0
    append_part: Optional[tuple[str, Optional[str], List[int]]] = None
    tool_paths: Dict[str, List[int]] = {}

    def close_append_part() -> Optional[SimpleNamespace]:
        nonlocal append_part
        if append_part is None:
            return None
        path = append_part[2]
        append_part = None
        return _assistant_protocol_chunk("part-finish", path=path)

    def allocate_part_path() -> List[int]:
        nonlocal next_part_index
        path = [next_part_index]
        next_part_index += 1
        return path

    async for chunk in stream:
        chunk_type = getattr(chunk, "type", "")

        if chunk_type in ("text-delta", "reasoning-delta"):
            text_delta = str(
                getattr(chunk, "text_delta", "")
                or getattr(chunk, "reasoning_delta", "")
                or ""
            )
            if not text_delta:
                continue
            kind = "reasoning" if chunk_type == "reasoning-delta" else "text"
            parent_id = getattr(chunk, "parent_id", None)
            if append_part is None or append_part[:2] != (kind, parent_id):
                closing_chunk = close_append_part()
                if closing_chunk is not None:
                    yield closing_chunk
                path = allocate_part_path()
                part: Dict[str, Any] = {"type": kind}
                if parent_id is not None:
                    part["parentId"] = parent_id
                yield _assistant_protocol_chunk("part-start", part=part)
                append_part = (kind, parent_id, path)
            yield _assistant_protocol_chunk(
                "text-delta",
                path=append_part[2],
                textDelta=text_delta,
            )
            continue

        if chunk_type == "tool-call-begin":
            closing_chunk = close_append_part()
            if closing_chunk is not None:
                yield closing_chunk
            tool_call_id = str(getattr(chunk, "tool_call_id", "") or "")
            if not tool_call_id:
                continue
            path = allocate_part_path()
            tool_paths[tool_call_id] = path
            part = {
                "type": "tool-call",
                "toolCallId": tool_call_id,
                "toolName": str(getattr(chunk, "tool_name", "tool") or "tool"),
            }
            parent_id = getattr(chunk, "parent_id", None)
            if parent_id is not None:
                part["parentId"] = parent_id
            yield _assistant_protocol_chunk("part-start", part=part)
            continue

        if chunk_type == "tool-call-delta":
            tool_call_id = str(getattr(chunk, "tool_call_id", "") or "")
            path = tool_paths.get(tool_call_id)
            if path is None:
                logger.warning(
                    "assistant stream args for unknown tool %s", tool_call_id
                )
                continue
            yield _assistant_protocol_chunk(
                "text-delta",
                path=path,
                textDelta=str(getattr(chunk, "args_text_delta", "") or ""),
            )
            continue

        if chunk_type == "tool-result":
            tool_call_id = str(getattr(chunk, "tool_call_id", "") or "")
            path = tool_paths.pop(tool_call_id, None)
            if path is None:
                logger.warning(
                    "assistant stream result for unknown tool %s", tool_call_id
                )
                continue
            yield _assistant_protocol_chunk("tool-call-args-text-finish", path=path)
            result_fields: Dict[str, Any] = {
                "result": getattr(chunk, "result", None),
                "isError": bool(getattr(chunk, "is_error", False)),
            }
            artifact = getattr(chunk, "artifact", None)
            if artifact is not None:
                result_fields["artifact"] = artifact
            yield _assistant_protocol_chunk("result", path=path, **result_fields)
            yield _assistant_protocol_chunk("part-finish", path=path)
            continue

        if chunk_type == "update-state":
            yield _assistant_protocol_chunk(
                "update-state",
                operations=getattr(chunk, "operations", []),
            )
            continue

        if chunk_type == "data":
            data = getattr(chunk, "data", None)
            yield _assistant_protocol_chunk(
                "data",
                data=data if isinstance(data, list) else [data],
            )
            continue

        if chunk_type == "error":
            yield _assistant_protocol_chunk(
                "error",
                error=str(getattr(chunk, "error", "assistant stream error")),
            )
            continue

        if chunk_type == "source":
            closing_chunk = close_append_part()
            if closing_chunk is not None:
                yield closing_chunk
            path = allocate_part_path()
            part = {
                "type": "source",
                "sourceType": str(getattr(chunk, "source_type", "url") or "url"),
                "id": str(getattr(chunk, "id", "") or ""),
                "url": str(getattr(chunk, "url", "") or ""),
            }
            title = getattr(chunk, "title", None)
            if title is not None:
                part["title"] = title
            parent_id = getattr(chunk, "parent_id", None)
            if parent_id is not None:
                part["parentId"] = parent_id
            yield _assistant_protocol_chunk("part-start", part=part)
            yield _assistant_protocol_chunk("part-finish", path=path)
            continue

        if chunk_type:
            logger.warning("dropping unsupported assistant-stream chunk %s", chunk_type)

    closing_chunk = close_append_part()
    if closing_chunk is not None:
        yield closing_chunk
    for path in tool_paths.values():
        yield _assistant_protocol_chunk("tool-call-args-text-finish", path=path)
        yield _assistant_protocol_chunk("part-finish", path=path)

    if completion.get("completed"):
        usage = completion.get("usage") or {}
        yield _assistant_protocol_chunk(
            "message-finish",
            finishReason=_assistant_finish_reason(usage.get("stopReason")),
            usage={
                "inputTokens": int(usage.get("inputTokens", 0) or 0),
                "outputTokens": int(usage.get("outputTokens", 0) or 0),
            },
        )


@router.post("/{session_id}/assistant")
async def assistant_transport(session_id: str, body: AssistantRequest):
    """Serve one assistant-ui assistant-transport run for a session.

    The daemon remains the source of truth.  This adapter translates its
    semantic event queue into assistant-stream chunks while preserving the
    existing SSE routes for older clients.
    """
    registry = get_session_registry()
    session = registry.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if not session.alive:
        raise HTTPException(status_code=409, detail="Session daemon is not running")

    try:
        from assistant_stream import RunController, create_run
        from assistant_stream.serialization import AssistantTransportResponse
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail="assistant-stream is required for the assistant transport endpoint",
        ) from exc

    completion: Dict[str, Any] = {"completed": False}

    async def run_callback(controller: RunController):
        queue = session.subscribe()
        tool_controllers: Dict[str, Any] = {}
        permission_controllers: Dict[str, Any] = {}
        permission_tool_ids: Dict[str, str] = {}
        state_messages = _assistant_copy_messages(body.state)
        persist_message_state = isinstance(body.state, dict) and isinstance(
            body.state.get("messages"), list
        )
        submitted_texts: List[str] = []
        assistant_text = ""
        assistant_reasoning = ""
        assistant_tool_parts: Dict[str, Dict[str, Any]] = {}
        assistant_parts: List[Dict[str, Any]] = []
        saw_action = False

        try:
            for command in body.commands:
                command_type = _assistant_command_type(command)
                if command_type == "add-message":
                    text = _assistant_message_text(command)
                    if not text:
                        controller.add_error("add-message command has no text")
                        return
                    accepted = await session.send_message(text)
                    submitted_texts.append(text)
                    saw_action = True
                    if not accepted.get("accepted"):
                        controller.add_error(
                            str(accepted.get("reason", "message rejected"))
                        )
                        return
                elif command_type == "add-tool-result":
                    call_id, result, decision, scope = _assistant_tool_result(command)
                    if not call_id:
                        controller.add_error(
                            "add-tool-result command has no toolCallId"
                        )
                        return
                    source_tool_id = permission_tool_ids.get(call_id, call_id)
                    # Permission tool-call IDs are stable across requests so the
                    # browser can answer a prompt from a later add-tool-result
                    # request. Recover the original daemon tool id when this
                    # callback did not see the request_permission event itself.
                    if source_tool_id == call_id and call_id.startswith("permission_"):
                        source_tool_id = call_id.removeprefix("permission_")
                    if not decision:
                        decision = "approve" if result is True else "deny"
                    if decision not in ("approve", "deny"):
                        controller.add_error(
                            "permission decision must be approve or deny"
                        )
                        return
                    resolved = await session.resolve_permission(
                        source_tool_id, decision, scope
                    )
                    saw_action = True
                    if not resolved:
                        controller.add_error(
                            f"No pending permission for tool_id '{source_tool_id}'"
                        )
                        return
                else:
                    controller.add_error(
                        f"Unsupported assistant command: {command_type or 'unknown'}"
                    )
                    return

            if not saw_action:
                controller.add_error("assistant request has no commands")
                return

            while True:
                event = await _assistant_next_event(session, queue, controller)
                if event is None:
                    return
                event_type = str(event.get("type") or "")

                if event_type == "token":
                    token = str(event.get("text") or "")
                    assistant_text += token
                    controller.append_text(token)
                elif event_type == "thinking":
                    thinking = str(event.get("text") or "")
                    assistant_reasoning += thinking
                    controller.append_reasoning(thinking)
                elif event_type == "tool_start":
                    tool_id = str(event.get("tool_id") or "")
                    if not tool_id:
                        continue
                    tool_controller = await controller.add_tool_call(
                        str(event.get("tool_name") or "tool"), tool_id
                    )
                    tool_controllers[tool_id] = tool_controller
                    tool_input = event.get("input") or {}
                    tool_part = {
                        "type": "tool-call",
                        "toolCallId": tool_id,
                        "toolName": str(event.get("tool_name") or "tool"),
                        "args": tool_input,
                        "argsText": json.dumps(tool_input, ensure_ascii=False),
                    }
                    assistant_tool_parts[tool_id] = tool_part
                    assistant_parts.append(tool_part)
                    tool_controller.append_args_text(tool_part["argsText"])
                elif event_type == "permission_request":
                    source_tool_id = str(event.get("tool_id") or "")
                    if not source_tool_id:
                        continue
                    permission_call_id = f"permission_{source_tool_id}"
                    permission_tool_ids[permission_call_id] = source_tool_id
                    permission_controller = await controller.add_tool_call(
                        "request_permission", permission_call_id
                    )
                    permission_controllers[source_tool_id] = permission_controller
                    permission_part = {
                        "type": "tool-call",
                        "toolCallId": permission_call_id,
                        "toolName": "request_permission",
                        "args": {
                            "tool_id": source_tool_id,
                            "tool_name": event.get("tool_name"),
                            "risk_level": event.get("risk_level"),
                            "risk_reason": event.get("risk_reason"),
                            "input": event.get("input") or {},
                        },
                    }
                    permission_part["argsText"] = json.dumps(
                        permission_part["args"], ensure_ascii=False
                    )
                    assistant_tool_parts[permission_call_id] = permission_part
                    assistant_parts.append(permission_part)
                    permission_controller.append_args_text(permission_part["argsText"])
                    _assistant_update_message_state(
                        state_messages,
                        session_id,
                        submitted_texts,
                        assistant_text,
                        assistant_reasoning,
                        assistant_parts,
                        {"type": "requires-action", "reason": "tool-calls"},
                    )
                    if persist_message_state:
                        _assistant_set_messages(controller, state_messages)
                    # Return this run after exposing the prompt.  The daemon is
                    # blocked until a later add-tool-result request answers it;
                    # keeping this stream open would deadlock that follow-up.
                    return
                elif event_type == "tool_result":
                    tool_id = str(event.get("tool_id") or "")
                    tool_controller = tool_controllers.pop(tool_id, None)
                    result = {
                        "success": bool(event.get("success")),
                        "output": event.get("output", ""),
                        "error": event.get("error", ""),
                        "metadata": event.get("metadata") or {},
                    }
                    tool_part = assistant_tool_parts.get(tool_id)
                    if tool_part is not None:
                        tool_part.update(
                            {
                                "result": result,
                                "isError": not result["success"],
                            }
                        )
                    if tool_controller is not None:
                        _assistant_set_tool_result(tool_controller, result)
                    else:
                        controller.add_tool_result(tool_id, result)
                elif event_type == "permission_granted":
                    source_tool_id = str(event.get("tool_id") or "")
                    permission_part = assistant_tool_parts.get(
                        f"permission_{source_tool_id}"
                    )
                    if permission_part is not None:
                        permission_part.update(
                            {
                                "result": {
                                    "decision": "approve",
                                    "scope": event.get("scope", "once"),
                                },
                                "isError": False,
                            }
                        )
                    permission_controller = permission_controllers.pop(
                        source_tool_id, None
                    )
                    if permission_controller is not None:
                        _assistant_set_tool_result(
                            permission_controller,
                            {
                                "decision": "approve",
                                "scope": event.get("scope", "once"),
                            },
                        )
                elif event_type == "permission_denied":
                    source_tool_id = str(event.get("tool_id") or "")
                    permission_part = assistant_tool_parts.get(
                        f"permission_{source_tool_id}"
                    )
                    if permission_part is not None:
                        permission_part.update(
                            {
                                "result": {"decision": "deny"},
                                "isError": True,
                            }
                        )
                    permission_controller = permission_controllers.pop(
                        source_tool_id, None
                    )
                    if permission_controller is not None:
                        _assistant_set_tool_result(
                            permission_controller,
                            {"decision": "deny"},
                        )
                elif event_type == "error":
                    controller.add_error(str(event.get("message") or "engine error"))
                elif event_type == "turn_complete":
                    completion["usage"] = {
                        "inputTokens": int(event.get("input_tokens", 0) or 0),
                        "outputTokens": int(event.get("output_tokens", 0) or 0),
                        "toolCalls": int(event.get("tool_calls", 0) or 0),
                        "stopReason": event.get("stop_reason", "end_turn"),
                    }
                    completion["completed"] = True
                    _assistant_update_message_state(
                        state_messages,
                        session_id,
                        submitted_texts,
                        assistant_text,
                        assistant_reasoning,
                        assistant_parts,
                        {"type": "complete", "reason": "stop"},
                    )
                    if persist_message_state:
                        _assistant_set_messages(controller, state_messages)
                    _assistant_set_usage(controller, completion["usage"])
                    return
                elif event_type == "daemon_closed":
                    controller.add_error("session daemon exited")
                    return
        except asyncio.CancelledError:
            await _cancel_assistant_turn(session, controller)
            raise
        except Exception as exc:
            logger.exception("assistant transport failed for %s", session_id)
            controller.add_error(str(exc))
        finally:
            session.unsubscribe(queue)

    stream = create_run(run_callback, state=body.state)
    return AssistantTransportResponse(_assistant_protocol_stream(stream, completion))


@router.post("/{session_id}/cancel")
async def cancel_turn(session_id: str):
    registry = get_session_registry()
    session = registry.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    session.cancel_turn()
    return {"ok": True, "session_id": session_id}
