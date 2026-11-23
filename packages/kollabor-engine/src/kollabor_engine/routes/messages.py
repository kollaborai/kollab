"""Message/turn endpoint - the core SSE stream.

The turn itself runs inside the session's daemon. This route submits the user
message, then relays the daemon's structured events until the turn completes.
The wire format is unchanged from when the engine ran its own turn loop, so
clients need no modification.
"""

import asyncio
import copy
import json
import logging
import re
import time
import uuid
import weakref
from contextlib import AsyncExitStack
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import anyio
from fastapi import APIRouter, HTTPException, Request  # type: ignore[import-not-found]
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse  # type: ignore[import-not-found]

from kollabor.state.remote import ReloadNeededError
from kollabor_ai.message_content import (
    MessageContent,
    contains_image_content,
    content_to_text,
)

from .. import sse
from ..server import get_session_registry
from ..session_context import (
    PHONE_STREAM_REVOKED,
    SESSION_READ_CONTEXT_SCOPE_KEY,
    SessionReadContext,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sessions", tags=["messages"])



def _request_read_context(request: Request | None) -> SessionReadContext | None:
    """Read only the typed identity installed in the server-owned ASGI scope."""
    if request is None:
        return None
    state = request.scope.get("state")
    if not isinstance(state, dict):
        return None
    context = state.get(SESSION_READ_CONTEXT_SCOPE_KEY)
    return context if isinstance(context, SessionReadContext) else None


def _subscribe(session, context: SessionReadContext | None):
    if context is None:
        return session.subscribe()
    return session.subscribe(context=context)

# A turn can legitimately run for a long time (big builds, long tool chains).
# This only bounds silence: any event from the daemon resets it.
EVENT_IDLE_TIMEOUT_SECONDS = 600.0

# Streamed text and reasoning reach the browser through message-state publishes
# (it renders only state.messages); at most one per interval. Text held back by
# the interval goes out with the next publish, or once the stream goes quiet.
STREAM_PUBLISH_INTERVAL = 0.15
_ASSISTANT_HANDOVER_STATE_KEY = "_kollabAssistantHandover"
_ASSISTANT_HANDOVER_LEDGER_ATTR = "_assistant_transport_handover_ledger"
_ASSISTANT_HANDOVER_TTL_SECONDS = 24 * 60 * 60
_ASSISTANT_HANDOVER_TOMBSTONE_TTL_SECONDS = 24 * 60 * 60
_ASSISTANT_HANDOVER_MAX_ENTRIES = 64


def _assistant_correlation_monotonic() -> float:
    """Clock for the bounded idle wait when admitted turns lack IDs."""
    return time.monotonic()


def _assistant_handover_ledger(session: Any) -> Dict[str, Dict[str, Any]]:
    """Keep handover authority on the server-side session object."""
    ledger = getattr(session, _ASSISTANT_HANDOVER_LEDGER_ATTR, None)
    if not isinstance(ledger, dict):
        ledger = {}
        setattr(session, _ASSISTANT_HANDOVER_LEDGER_ATTR, ledger)
    return ledger


def _assistant_cancel_handover_expiry(entry: Dict[str, Any]) -> None:
    handle = entry.pop("expiry_handle", None)
    if handle is not None:
        handle.cancel()


def _assistant_expire_handover_entry(
    session: Any,
    ledger: Dict[str, Dict[str, Any]],
    token: str,
    entry: Dict[str, Any],
    now: float,
) -> None:
    """Release queue resources but retain a bounded tombstone for accepted IDs."""
    _assistant_cancel_handover_expiry(entry)
    if entry.get("status") == "expired":
        ledger.pop(token, None)
        return
    record = entry.get("record") or {}
    accepted_ids = list(record.get("request_ids") or ())
    queue = entry.get("queue")
    if queue is not None:
        session.unsubscribe(queue)
    if not accepted_ids:
        ledger.pop(token, None)
        return
    entry["status"] = "expired"
    entry["claim_id"] = None
    entry["queue"] = None
    entry["context"] = None
    entry["record"] = {
        "request_ids": accepted_ids,
        "completed_request_ids": list(record.get("completed_request_ids") or ()),
        "expired": True,
    }
    entry["expires_at"] = now + _ASSISTANT_HANDOVER_TOMBSTONE_TTL_SECONDS
    _assistant_schedule_handover_expiry(session, token, entry)


def _assistant_schedule_handover_expiry(
    session: Any, token: str, entry: Dict[str, Any]
) -> None:
    """Bound parked handover resources and retain accepted-ID expiry evidence."""
    _assistant_cancel_handover_expiry(entry)
    if entry.get("status") == "claimed":
        # A live route owns the lease; parked handovers get the idle TTL.
        entry["expires_at"] = None
        return
    try:
        loop = asyncio.get_running_loop()
        session_ref = weakref.ref(session)
    except (RuntimeError, TypeError):
        return
    expires_at = float(entry.get("expires_at") or 0)

    def expire() -> None:
        current_session = session_ref()
        if current_session is None:
            return
        ledger = getattr(current_session, _ASSISTANT_HANDOVER_LEDGER_ATTR, {})
        current_entry = ledger.get(token) if isinstance(ledger, dict) else None
        if current_entry is None:
            return
        remaining = expires_at - _assistant_correlation_monotonic()
        if remaining > 0:
            _assistant_schedule_handover_expiry(
                current_session, token, current_entry
            )
            return
        if current_entry is not None:
            _assistant_expire_handover_entry(
                current_session, ledger, token, current_entry,
                _assistant_correlation_monotonic(),
            )

    entry["expiry_handle"] = loop.call_later(
        max(0.0, expires_at - _assistant_correlation_monotonic()), expire
    )


def _assistant_prune_handover_ledger(
    session: Any, ledger: Dict[str, Dict[str, Any]], now: float
) -> None:
    """Expire old capabilities and bound abandoned handovers per session."""
    for token, entry in list(ledger.items()):
        expires_at = entry.get("expires_at")
        if expires_at is not None and float(expires_at) <= now:
            _assistant_expire_handover_entry(session, ledger, token, entry, now)


def _assistant_reserve_handover(
    session: Any,
    queue: asyncio.Queue,
    context: SessionReadContext | None,
) -> tuple[Optional[str], Optional[str]]:
    """Reserve capacity before accepting a send that may need handover."""
    ledger = _assistant_handover_ledger(session)
    now = _assistant_correlation_monotonic()
    _assistant_prune_handover_ledger(session, ledger, now)
    if len(ledger) >= _ASSISTANT_HANDOVER_MAX_ENTRIES:
        return None, None
    token = uuid.uuid4().hex
    claim_id = uuid.uuid4().hex
    entry = {
        "expires_at": now + _ASSISTANT_HANDOVER_TTL_SECONDS,
        "status": "claimed",
        "claim_id": claim_id,
        "record": {},
        "queue": queue,
        "context": context,
    }
    ledger[token] = entry
    _assistant_schedule_handover_expiry(session, token, entry)
    return token, claim_id


def _assistant_claim_handover(
    session: Any, token: Any
) -> tuple[
    Optional[Dict[str, Any]], Optional[str], Optional[str],
    Optional[asyncio.Queue], Optional[SessionReadContext],
]:
    """Lease one session-bound token; reject replay while another run owns it."""
    if not isinstance(token, str) or re.fullmatch(r"[0-9a-f]{32}", token) is None:
        return None, None, "Permission handover capability is invalid or expired.", None, None
    ledger = _assistant_handover_ledger(session)
    now = _assistant_correlation_monotonic()
    _assistant_prune_handover_ledger(session, ledger, now)
    entry = ledger.get(token)
    if entry is None:
        return None, None, "Permission handover capability is invalid or expired.", None, None
    if entry.get("status") == "expired":
        request_ids = ", ".join(
            str(request_id)
            for request_id in (entry.get("record") or {}).get("request_ids") or ()
        ) or "unknown"
        return (
            None,
            None,
            "Permission handover expired. Accepted message(s) "
            f"{request_ids} may still be running; their event stream is no longer "
            "available. No messages were resubmitted.",
            None,
            None,
        )
    if entry.get("status") == "claimed":
        return None, None, "Permission handover is already in progress; replay was rejected.", None, None
    claim_id = uuid.uuid4().hex
    entry["status"] = "claimed"
    entry["claim_id"] = claim_id
    entry["expires_at"] = now + _ASSISTANT_HANDOVER_TTL_SECONDS
    _assistant_schedule_handover_expiry(session, token, entry)
    return (
        copy.deepcopy(entry.get("record") or {}),
        claim_id,
        None,
        entry.get("queue"),
        entry.get("context"),
    )


def _assistant_update_handover(
    session: Any,
    token: Optional[str],
    claim_id: Optional[str],
    record: Dict[str, Any],
    queue: Optional[asyncio.Queue] = None,
    context: SessionReadContext | None = None,
) -> None:
    """Refresh the record only while this run holds its matching lease."""
    if not token or not claim_id:
        return
    ledger = _assistant_handover_ledger(session)
    entry = ledger.get(token)
    if entry is None or entry.get("claim_id") != claim_id:
        return
    entry["record"] = copy.deepcopy(record)
    if queue is not None:
        entry["queue"] = queue
        entry["context"] = context
    entry["expires_at"] = (
        _assistant_correlation_monotonic() + _ASSISTANT_HANDOVER_TTL_SECONDS
    )
    _assistant_schedule_handover_expiry(session, token, entry)


def _assistant_release_handover(
    session: Any,
    token: Optional[str],
    claim_id: Optional[str],
    record: Optional[Dict[str, Any]],
) -> None:
    """Return an unfinished lease to the same capability for a safe retry."""
    if not token or not claim_id:
        return
    ledger = _assistant_handover_ledger(session)
    entry = ledger.get(token)
    if entry is None or entry.get("claim_id") != claim_id:
        return
    if record is not None:
        entry["record"] = copy.deepcopy(record)
    entry["status"] = "available"
    entry["claim_id"] = None
    entry["expires_at"] = (
        _assistant_correlation_monotonic() + _ASSISTANT_HANDOVER_TTL_SECONDS
    )
    _assistant_schedule_handover_expiry(session, token, entry)


def _assistant_finish_handover(
    session: Any,
    token: Optional[str],
    claim_id: Optional[str],
) -> None:
    """Delete a fully completed or invalidated handover capability."""
    if not token or not claim_id:
        return
    ledger = _assistant_handover_ledger(session)
    entry = ledger.get(token)
    if entry is not None and entry.get("claim_id") == claim_id:
        finished = ledger.pop(token, None)
        if finished is not None:
            _assistant_cancel_handover_expiry(finished)
            if finished.get("queue") is not None:
                session.unsubscribe(finished["queue"])


def _event_request_ids(event: Dict[str, Any]) -> set[str]:
    """Return every request id carried by an event, including its primary id."""
    values = event.get("request_ids") or ()
    if isinstance(values, (str, bytes)):
        values = (values,)
    return (
        {str(event["request_id"])}
        if event.get("request_id") is not None and str(event["request_id"])
        else set()
    ) | {str(value) for value in values if value is not None and str(value)}


def _foreign_completion(event: Dict[str, Any], request_ids: set[str], tagged: bool = False) -> bool:
    """Another request's turn_complete never finishes this stream.

    A cancelled pre-start client request is one. So is an earlier turn's
    completion the daemon published while the engine was reattaching: it
    arrives after this request was accepted (#543). With ``tagged`` the daemon
    echoed this request's id, so its turn carries it: a completion naming
    none belongs to a turn that was running when this request came (#551).
    """
    if event.get("type") != "turn_complete":
        return False
    if event.get("input_source") == "acp_client" and event.get("queued") is True:
        return not _event_request_ids(event) & request_ids
    named = _event_request_ids(event)
    if not named:
        return tagged
    return bool(request_ids) and not named & request_ids


def _foreign_error(event: Dict[str, Any], request_ids: set[str]) -> bool:
    """An error that names only other requests is theirs: a frame dropped for an
    old turn, replayed after a reattach, must not fail this request (#543)."""
    if event.get("type") != "error":
        return False
    named = {str(event.get("request_id") or "")} - {""} | {str(i) for i in event.get("request_ids") or ()}
    return bool(named and request_ids) and not named & request_ids


# What a turn streams as its reply. A voice or ACP turn names its request by
# request_id alone; a turn typed in the terminal names none.
_TURN_OUTPUT = frozenset(
    {"token", "response", "thinking", "acp_update", "tool_start", "tool_result", "permission_request"}
)
_PERMISSION_ANSWERS = frozenset({"permission_granted", "permission_denied", "permission_cancelled"})


def _foreign_output(event: Dict[str, Any], request_ids: set[str], tagged: bool = False) -> bool:
    """An event a turn names for other requests only is theirs: a message sent
    while another turn runs gets none of that turn's text, tool cards or
    permission prompts (#551). With ``tagged`` this request's turn names it, so
    reply output naming none is another turn's too. Other unnamed events
    (session-wide, older daemons) pass, and so do permission answers: they
    close a card by its tool id, whichever run showed it."""
    if event.get("type") in _PERMISSION_ANSWERS:
        return False  # a parked card is answered by a later message
    named = {str(i) for i in event.get("request_ids") or ()}
    if event.get("type") in _TURN_OUTPUT:
        named |= {str(event.get("request_id") or "")} - {""}
        if not named:
            return tagged
    return bool(named and request_ids) and not named & request_ids


# A message id the page made that may name its send (#1359): letters, digits,
# dashes and underscores (assistant-ui's ids, uuids).
_CLIENT_REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


def _is_goal_message(content: Any) -> bool:
    """Recognize authored goal text without synthetic image placeholders."""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = "\n".join(
            part["text"] for part in content
            if isinstance(part, dict)
            and str(part.get("type") or "").strip().lower() == "text"
            and isinstance(part.get("text"), str)
        )
    else:
        return False
    return re.match(r"^\s*/(?:goal|goals)(?:\s+|$)", text, re.IGNORECASE) is not None


def _is_image_goal(content: Any) -> bool:
    return isinstance(content, list) and contains_image_content(content) and _is_goal_message(content)


async def _pump_goal_reply(session, reply_id: str, queue: asyncio.Queue) -> None:
    """Deliver a private goal reply only to its initiating response stream."""
    deadline = time.monotonic() + EVENT_IDLE_TIMEOUT_SECONDS
    try:
        while time.monotonic() < deadline:
            reply = await session.poll_goal_reply(reply_id)
            if reply.get("status") == "pending":
                await asyncio.sleep(0.1)
                continue
            message = str(reply.get("message") or "Goal reply unavailable.")
            meta = {
                "request_id": reply_id,
                "conversation_uid": reply.get("conversation_uid"),
                "history_recorded": reply.get("history_recorded") is True,
            }
            success = reply.get("status") == "complete" and reply.get("success") is True
            if success:
                await queue.put({"type": "token", "text": message, **meta})
            else:
                await queue.put({"type": "error", "message": message, **meta})
            await queue.put({"type": "turn_complete", "stop_reason": "end_turn" if success else "error", **meta})
            return
        message = "Goal reply timed out; check its status before retrying."
    except asyncio.CancelledError:
        raise
    except Exception:
        message = "Goal reply unavailable; check its status before retrying."
    await queue.put({"type": "error", "message": message, "request_id": reply_id})
    await queue.put({"type": "turn_complete", "stop_reason": "error", "request_id": reply_id})


async def _forward_goal_revocation(shared_queue: asyncio.Queue, private_queue: asyncio.Queue) -> None:
    """Forward a revoked phone's subscriber signal while ignoring shared output."""
    while True:
        event = await shared_queue.get()
        if event is PHONE_STREAM_REVOKED:
            await private_queue.put(PHONE_STREAM_REVOKED)
            return


def _contains_reserved_voice_origin(value: Any) -> bool:
    if isinstance(value, dict):
        origin_context = value.get("origin_context")
        if isinstance(origin_context, dict) and origin_context.get("source") == "livekit_voice":
            return True
        return any(_contains_reserved_voice_origin(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_reserved_voice_origin(item) for item in value)
    return False


class MessageRequest(BaseModel):
    content: Any
    continuation: bool = False
    request_id: str | None = None
    thread_id: str | None = None
    origin_context: dict[str, Any] | None = None
    goal_reply_id: str | None = None
    conversation_uid: str | None = None


@router.post("/{session_id}/message")
async def send_message(
    session_id: str, body: MessageRequest, request: Request = None
):
    if _contains_reserved_voice_origin(
        {"origin_context": body.origin_context, "content": body.content}
    ):
        raise HTTPException(
            status_code=403,
            detail="Voice origin context is reserved for the Engine voice session router",
        )

    registry = get_session_registry()
    session = registry.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if not session.alive:
        raise HTTPException(status_code=409, detail="Session daemon is not running")

    # Subscribe before submitting so no event can land in the gap.
    read_context = _request_read_context(request)
    queue = _subscribe(session, read_context)
    # Always an id: a daemon that echoes it stamps this turn with it (#551).
    request_id = body.request_id or uuid.uuid4().hex

    try:
        private_goal = _is_goal_message(body.content)
        kwargs = {"request_id": request_id, "thread_id": body.thread_id, "origin_context": body.origin_context}
        if private_goal:
            kwargs["private_goal_reply"] = True
            kwargs["goal_conversation_uid"] = body.conversation_uid
            if body.goal_reply_id is not None:
                kwargs["private_goal_reply_id"] = body.goal_reply_id
        accepted = await session.send_message(body.content, **kwargs)
    except ReloadNeededError as e:
        # The daemon's code no longer matches the checkout: retries cannot
        # succeed. Say so plainly instead of "daemon unreachable" (#571).
        session.unsubscribe(queue)
        logger.warning("send_message reload-needed for %s: %s", session_id, e)
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        session.unsubscribe(queue)
        logger.error("send_message failed for %s: %s", session_id, e)
        raise HTTPException(status_code=502, detail=f"daemon unreachable: {e}")

    if read_context and read_context.revoked:
        session.unsubscribe(queue)
        raise HTTPException(status_code=403, detail="Session access revoked")
    if not accepted.get("accepted"):
        session.unsubscribe(queue)
        reason = accepted.get("reason", "rejected")
        status = 409 if "in flight" in reason else 400
        raise HTTPException(status_code=status, detail=accepted.get("message") or reason)
    replayed = private_goal and accepted.get("completed") is True and accepted.get("replayed") is True
    reply_id = accepted.get("goal_reply_id") if private_goal and not replayed else None
    if private_goal and not reply_id and not replayed:
        session.unsubscribe(queue)
        raise HTTPException(status_code=502, detail="daemon did not provide a private goal reply")
    tagged = bool(accepted.get("request_id"))
    request_ids = {str(accepted.get("request_id") or request_id)}
    if reply_id:
        request_ids.add(str(reply_id))  # the private reply's events name it

    # A replay already has a durable pending request or creation receipt. Its
    # acknowledgement belongs only to this response, never the shared tap.
    replay_queue = None
    if replayed:
        replay_queue = asyncio.Queue()
        replay_queue.put_nowait({
            "type": "token", "text": str(accepted.get("message") or "Goal request already processed."),
            "request_id": next(iter(request_ids)),
        })
        replay_queue.put_nowait(
            {"type": "turn_complete", "stop_reason": "end_turn", "request_id": next(iter(request_ids))}
        )

    async def event_generator():
        private_queue = asyncio.Queue() if reply_id else None
        pump = asyncio.create_task(_pump_goal_reply(session, str(reply_id), private_queue)) if reply_id else None
        revocation_watch = (
            asyncio.create_task(_forward_goal_revocation(queue, private_queue))
            if private_queue is not None and read_context is not None else None
        )
        try:
            while True:
                try:
                    event = await asyncio.wait_for(
                        (replay_queue if replay_queue is not None else
                         private_queue if private_queue is not None else queue).get(),
                        timeout=EVENT_IDLE_TIMEOUT_SECONDS,
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

                if event is PHONE_STREAM_REVOKED or (read_context and read_context.revoked):
                    return
                event.setdefault("session_id", session_id)
                if _foreign_completion(event, request_ids, tagged):
                    continue
                if _foreign_error(event, request_ids) or _foreign_output(event, request_ids, tagged):
                    continue

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
            tasks = [task for task in (pump, revocation_watch) if task is not None]
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            session.unsubscribe(queue)

    headers = {}
    if accepted.get("goal_request_id"):
        headers["X-Kollab-Goal-Request"] = str(accepted["goal_request_id"])
    if reply_id:
        headers["X-Kollab-Goal-Reply"] = str(reply_id)
    return EventSourceResponse(event_generator(), headers=headers)


@router.get("/{session_id}/events")
async def stream_events(session_id: str, request: Request = None):
    """Follow a session's event stream without submitting a turn.

    A client that reloads mid-turn loses the POST /message stream, but the
    daemon keeps working - and if it is blocked on a permission prompt, the
    answer never produces visible output. This lets a reconnecting client pick
    the turn back up. It does not end at turn_complete: the daemon starts a
    queued turn right after the last one, and a stream that closed there would
    drop that turn's first events (already queued behind turn_complete) and
    miss the rest until the client reconnected. It ends when the client leaves,
    after EVENT_IDLE_TIMEOUT_SECONDS of silence or when the daemon closes; the
    finally below releases the subscription.
    """
    registry = get_session_registry()
    session = registry.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if not session.alive:
        raise HTTPException(status_code=409, detail="Session daemon is not running")

    queue = _subscribe(session, _request_read_context(request))

    async def event_generator():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=EVENT_IDLE_TIMEOUT_SECONDS
                    )
                except asyncio.TimeoutError:
                    return

                if event is PHONE_STREAM_REVOKED:
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
    goal_reply_id: Optional[str] = None


def _assistant_command_type(command: Dict[str, Any]) -> str:
    return str(command.get("type") or "").strip().lower()


def _assistant_message_content(command: Dict[str, Any]) -> MessageContent:
    """Extract text/image parts from an assistant-ui add-message command."""
    message = command.get("message")
    if isinstance(message, str):
        return message
    if not isinstance(message, dict):
        content = command.get("content")
        return content if isinstance(content, str) else ""

    parts = message.get("parts", [])
    if isinstance(parts, list):
        normalized_parts: List[Dict[str, Any]] = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text" and isinstance(part.get("text"), str):
                normalized_parts.append({"type": "text", "text": part["text"]})
            elif part.get("type") == "image" and isinstance(part.get("image"), str):
                normalized_parts.append({"type": "image", "image": part["image"]})
        if any(part.get("type") == "image" for part in normalized_parts):
            return normalized_parts
        if normalized_parts:
            return "\n".join(part["text"] for part in normalized_parts)
    content = message.get("content")
    if isinstance(content, str):
        return content
    return ""


def _save_sent_media(content: MessageContent, session_id: str) -> List[Dict[str, str]]:
    """Store a sent message's image parts; return their refs in part order.

    Empty for a text-only message or when this engine keeps no local
    conversation storage (a remote daemon writes its history elsewhere).
    A decode failure drops that image with a log line rather than failing
    the send: the turn itself is unaffected (#1088).
    """
    if not isinstance(content, list) or not contains_image_content(content):
        return []
    from kollabor_config.config_utils import get_project_data_dir

    from .. import media_store

    conversations_dir = get_project_data_dir() / "conversations"
    saved: List[Dict[str, str]] = []
    for part in content:
        if not isinstance(part, dict) or part.get("type") != "image":
            continue
        image = part.get("image")
        if not isinstance(image, str):
            continue
        try:
            record = media_store.save(conversations_dir, session_id, image)
        except media_store.MediaError as error:
            # Keep one index slot per attempted part: the history overlay
            # counts every image part, and a dropped slot would shift every
            # later row's refs. The tombstone binds nothing and stays out
            # of listings (#1124 review P2).
            mime = image.split(";", 1)[0].removeprefix("data:")
            media_store.record_failed(
                conversations_dir, session_id, mime or "application/octet-stream"
            )
            logger.warning("sent image not stored: %s", error)
            continue
        saved.append({"ref": record.ref, "mime": record.mime})
    return saved


def _assistant_message_text(command: Dict[str, Any]) -> str:
    """Render an assistant-ui add-message command for validation/display."""
    return content_to_text(_assistant_message_content(command))


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


def _assistant_permission_identity(messages: List[Dict[str, Any]], call_id: str) -> tuple[str, str]:
    """Find the tool id and owner token on the exact permission call in client state."""
    for message in messages:
        content = message.get("content")
        if message.get("role") != "assistant" or not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict) or part.get("toolCallId") != call_id:
                continue
            if part.get("toolName") != "request_permission":
                continue
            args = part.get("args")
            if not isinstance(args, dict):
                continue
            tool_id = args.get("tool_id")
            value = args.get("permission_id")
            if not isinstance(value, str) or not value:
                details = args.get("details")
                value = details.get("permission_id") if isinstance(details, dict) else None
            if isinstance(tool_id, str) and tool_id and isinstance(value, str) and value:
                return tool_id, value
    return "", ""


async def _assistant_next_event(queue, controller, timeout=None):
    """Wait for either a daemon event or assistant-stream cancellation.

    Returns None once the stream is cancelled, without starting another queue
    read. If cancellation and a queue read finish together, preserve the event;
    the next call observes cancellation. Returns ``{}`` when ``timeout``
    seconds pass with neither. A revoked stream marker remains terminal.

    A closed stream (another chat opened, the tab closed, a phone dropped)
    leaves the daemon's turn running; Stop cancels it through
    ``POST /{session_id}/cancel``.
    """
    if controller.cancelled_event.is_set():
        return None

    event_task = asyncio.create_task(queue.get())
    cancel_task = asyncio.create_task(controller.cancelled_event.wait())
    try:
        await asyncio.wait(
            {event_task, cancel_task},
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if event_task.done():
            cancel_task.cancel()
            event = event_task.result()
            return None if event is PHONE_STREAM_REVOKED else event
        if cancel_task.done():
            event_task.cancel()
            return None
        return {}
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


def _permission_result_from_event(event: Dict[str, Any]) -> Dict[str, Any]:
    """Preserve ACP vendor metadata while keeping native defaults stable."""
    event_type = str(event.get("type") or "")
    result: Dict[str, Any] = {
        "decision": "approve" if event_type == "permission_granted" else "deny",
    }
    if "scope" in event or not event.get("vendor_saved"):
        result["scope"] = event.get("scope", "once")
    for key in ("option_id", "vendor_saved", "warning", "permission_id"):
        if key in event:
            result[key] = event[key]
    return result


def _assistant_permission_id(event: Dict[str, Any]) -> Optional[str]:
    """Read the unique permission prompt identity when the daemon provides it."""
    value = event.get("permission_id")
    if value is None and isinstance(event.get("details"), dict):
        value = event["details"].get("permission_id")
    return str(value) if value is not None and str(value) else None


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


def _assistant_set_state_value(controller: Any, key: str, value: Any) -> None:
    """Set one root state field across assistant-stream state versions."""
    state = controller.state
    if state is None:
        controller.state = {key: value}
        return
    try:
        state[key] = value
    except (KeyError, TypeError, AttributeError):
        controller.state = {key: value}


def _assistant_copy_messages(state: Any) -> List[Dict[str, Any]]:
    """Copy the JSON message state before the StateProxy starts patching it."""
    if not isinstance(state, dict) or not isinstance(state.get("messages"), list):
        return []
    try:
        messages = json.loads(json.dumps(state["messages"]))
    except (TypeError, ValueError):
        return []
    for message in messages:
        if (
            isinstance(message, dict)
            and message.get("role") == "user"
            and contains_image_content(message.get("content"))
        ):
            message["content"] = content_to_text(message["content"])
    return messages


def _assistant_continued_tool_parts(
    messages: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Copies of the tool calls on the assistant message a continuation extends."""
    for message in reversed(messages):
        if message.get("role") != "assistant":
            continue
        content = message.get("content")
        return [
            copy.deepcopy(part)
            for part in (content if isinstance(content, list) else [])
            if isinstance(part, dict) and part.get("type") == "tool-call"
        ]
    return []


def _assistant_update_message_state(
    messages: List[Dict[str, Any]],
    session_id: str,
    submitted_contents: List[MessageContent],
    parts: List[Dict[str, Any]],
    status: Optional[Dict[str, str]],
    submitted_media: Optional[List[List[Dict[str, str]]]] = None,
    submitted_request_ids: Optional[List[str]] = None,
    submitted_row_ids: Optional[List[Optional[str]]] = None,
) -> List[Dict[str, Any]]:
    """Persist the completed or interrupted turn in assistant-ui state.

    ``parts`` are the turn's text, reasoning and tool-call parts in arrival
    order, so the message reads chronologically like its reloaded history.
    ``status=None`` leaves the message status out so the client derives it
    (running while the request is open); mid-turn publishes use that so a
    cancelled or crashed run cannot leave a message spinning forever.
    """
    if not submitted_contents:
        for message in reversed(messages):
            if message.get("role") != "assistant":
                continue
            content = message.get("content")
            existing_parts = (
                list(content)
                if isinstance(content, list)
                else ([{"type": "text", "text": content}] if content else [])
            )
            # A tool call this run knows by id (adopted from this message, e.g.
            # a gated tool whose prompt the user just answered) replaces the
            # stored part so its result lands in place; the rest append in order.
            stored_tools = {
                part.get("toolCallId"): index
                for index, part in enumerate(existing_parts)
                if isinstance(part, dict) and part.get("type") == "tool-call"
            }
            for part in parts:
                index = (
                    stored_tools.get(part.get("toolCallId"))
                    if part.get("type") == "tool-call"
                    else None
                )
                if index is None:
                    existing_parts.append(part)
                else:
                    existing_parts[index] = part
            message["content"] = existing_parts
            if status is None:
                message.pop("status", None)
            else:
                message["status"] = status
            return messages

    # Row L + i after an L-row state, the id the web client gives its pending
    # copy of message i and tracks while the send is in flight (#1356). A
    # message the page drew before it sent it (it waited behind a running
    # turn) names the row it has on screen, and keeps it (#1102): a row under
    # a new id is taken down and built again. The name must be one of this
    # session's send rows and free.
    first_row = len(messages)
    taken = {message.get("id") for message in messages}
    named_row = re.compile(rf"user-{re.escape(session_id)}-\d+")
    for index, submitted_content in enumerate(submitted_contents):
        row_id = submitted_row_ids[index] if submitted_row_ids and index < len(submitted_row_ids) else None
        if not isinstance(row_id, str) or row_id in taken or named_row.fullmatch(row_id) is None:
            row_id = f"user-{session_id}-{first_row + index}"
        taken.add(row_id)
        row: Dict[str, Any] = {
            "id": row_id,
            "role": "user",
            # Keep data URLs out of assistant-ui state/history. The image
            # was already handed to the daemon; the visible token is the
            # durable client-side representation for this slice.
            "content": content_to_text(submitted_content),
        }
        # The stored copies of this turn's sent images (#1088): the browser
        # renders thumbnails from these refs instead of the raw tokens.
        if submitted_media and index < len(submitted_media) and submitted_media[index]:
            row["media"] = submitted_media[index]
        # The send this row is: its saved copy names the same id (#1359).
        if submitted_request_ids and index < len(submitted_request_ids):
            row["metadata"] = {"custom": {"requestId": submitted_request_ids[index]}}
        messages.append(row)

    assistant_message: Dict[str, Any] = {
        "id": f"assistant-{session_id}-{len(messages)}",
        "role": "assistant",
        "content": list(parts) if parts else "",
    }
    if status is not None:
        assistant_message["status"] = status
    messages.append(assistant_message)
    return messages


def _assistant_turn_message(
    session_id: str,
    messages: List[Dict[str, Any]],
    turn: Dict[str, Any],
) -> Dict[str, Any]:
    """Make one assistant-ui row for one completed or in-flight daemon turn."""
    message: Dict[str, Any] = {
        "id": str(
            turn.get("message_id")
            or f"assistant-{session_id}-{len(messages)}"
        ),
        "role": "assistant",
        "content": copy.deepcopy(turn.get("parts") or []),
    }
    if not message["content"]:
        message["content"] = ""
    if turn.get("status") is not None:
        message["status"] = copy.deepcopy(turn["status"])
    return message


def _assistant_align_streamed_ids(
    messages: List[Dict[str, Any]], history: List[Dict[str, Any]]
) -> bool:
    """Record this turn's history positions without replacing its row ids.

    Only a strict one-to-one match between the trailing stored rows and the
    provisional rows justifies a source-index mapping, and the complete role/content
    alignment must be unique across refreshed history. Anything else (a
    refresh that missed the turn, ambiguous duplicate content, a content
    mismatch, or an unexpected role) keeps the provisional ids without a
    synchronized claim, for the next full reload.

    Role order alone is not evidence: a stale [user, assistant] pair looks
    identical to this turn's rows by role. Every paired row must also match
    on content: user rows by exact text; assistant rows by exact text, or by a
    strict suffix when the streamed row carries tool-call cards. A stored
    calls row may sit between pairs only when its call ids all appear in the
    streamed row's call cards, proving it was rendered inside this turn.

    Returns whether the mapping happened. ``True`` means every provisional
    row now carries this snapshot's ``historySourceIndex`` metadata, so the
    caller may claim incremental sync against exactly this snapshot while
    keeping the row ids stable for the viewport. ``False`` means the snapshot
    could not be reconciled (it missed the turn, or rows the page already shows
    collide with it): the caller must claim nothing and let the page take its
    next full load."""

    def _text(content: Any) -> str:
        """Comparable plain text of a content field (string or parts)."""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for part in content:
                if (
                    isinstance(part, dict)
                    and part.get("type") in ("text", "reasoning")
                    and isinstance(part.get("text"), str)
                ):
                    parts.append(part["text"])
            return "".join(parts)
        return ""

    def _call_ids(row: Dict[str, Any]) -> set[str]:
        """Tool call ids recorded on a stored row (metadata-borne)."""
        ids: set[str] = set()
        metadata = row.get("metadata")
        if not isinstance(metadata, dict):
            return ids
        stack: list[Any] = [metadata]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                for key, value in node.items():
                    if key in ("tool_calls", "xml_tool_calls", "acp_updates"):
                        stack.append(value)
                    elif key == "id" and isinstance(value, str) and value:
                        ids.add(value)
            elif isinstance(node, list):
                stack.extend(node)
        return ids

    def _streamed_call_ids(row: Dict[str, Any]) -> set[str]:
        """Call-card ids the streamed assistant row rendered."""
        content = row.get("content")
        if not isinstance(content, list):
            return set()
        return {
            str(part.get("toolCallId"))
            for part in content
            if isinstance(part, dict)
            and part.get("type") == "tool-call"
            and part.get("toolCallId")
        }

    def _rendered_inside(stored_row: Dict[str, Any], streamed_row: Dict[str, Any]) -> bool:
        """A stored calls row the streamed row's call cards account for.

        Stored history keeps a tool round as one assistant row carrying its
        text next to metadata-borne call ids; the streamed turn renders the
        same round inside its single row. Call ids are per-turn identifiers,
        so a full id subset is the identity proof; the walk only reaches
        this check for rows not yet paired, and it never consumes a
        streamed row, so a stale row can extend the search but cannot
        claim one."""
        if stored_row.get("role") != "assistant":
            return False
        stored_ids = _call_ids(stored_row)
        if not stored_ids:
            return False
        streamed_ids = _streamed_call_ids(streamed_row)
        return bool(streamed_ids) and stored_ids.issubset(streamed_ids)

    def _streamed_request_id(row: Dict[str, Any]) -> str:
        """The request id of the send a streamed user row is (#1359)."""
        metadata = row.get("metadata")
        custom = metadata.get("custom") if isinstance(metadata, dict) else None
        value = custom.get("requestId") if isinstance(custom, dict) else None
        return value if isinstance(value, str) else ""

    def _saved_request_ids(row: Dict[str, Any]) -> set[str]:
        """The request ids of the sends a saved user row holds (#1359)."""
        metadata = row.get("metadata")
        if not isinstance(metadata, dict):
            return set()
        records = metadata.get("operator_inputs")
        ids = {
            record["request_id"]
            for record in (records if isinstance(records, list) else [])
            if isinstance(record, dict) and isinstance(record.get("request_id"), str) and record["request_id"]
        }
        if isinstance(metadata.get("request_id"), str) and metadata["request_id"]:
            ids.add(metadata["request_id"])
        return ids

    def _pairs_by_content(stored_row: Dict[str, Any], streamed_row: Dict[str, Any]) -> bool:
        """Content identity for a role-matched pair."""
        if stored_row.get("role") == "user":
            # A saved row that names its sends pairs by id: the daemon saves
            # sends that arrive together as one row, behind any agent HUD, and
            # another sender's identical words are not this send.
            request_id = _streamed_request_id(streamed_row)
            saved_ids = _saved_request_ids(stored_row)
            if request_id and saved_ids:
                return request_id in saved_ids
            return _text(stored_row.get("content")) == _text(streamed_row.get("content"))
        stored_text = _text(stored_row.get("content"))
        if not stored_text:
            return False
        streamed_text = _text(streamed_row.get("content"))
        if streamed_text == stored_text:
            return True
        # A bare tail match is not identity: a stale stored answer can be
        # the tail of an unrelated new answer ("42" vs "Answer: 42"), and
        # pairing it would rename this turn onto the stale rows. Only a
        # merged tool round legitimately renders the stored text as a
        # strict tail, and that turn's streamed row carries its own call
        # cards — require that evidence for any strict-suffix match.
        return (
            bool(_streamed_call_ids(streamed_row))
            and streamed_text.endswith(stored_text)
        )

    provisional: List[int] = []
    shown: set[int] = set()
    for index, message in enumerate(messages):
        role = message.get("role")
        message_id = message.get("id")
        if role not in ("user", "assistant") or not isinstance(message_id, str):
            continue
        if message_id == "live-turn" or message_id.startswith("live-turn:"):
            # The page's copy of a turn it was only watching when it sent:
            # a display row, never one of this turn's (#1359).
            continue
        metadata = message.get("metadata")
        custom = metadata.get("custom") if isinstance(metadata, dict) else None
        if isinstance(custom, dict) and "compactionArchiveOrder" in custom:
            # These rows retain their display IDs but belong to a superseded snapshot.
            continue
        source_id = custom.get("historySourceId") if isinstance(custom, dict) else None
        if isinstance(source_id, str) and re.fullmatch(r"history-\d+", source_id) is None:
            # Derived display rows (error, Hub note, etc.) share the position
            # of their source. They cannot prove the canonical row was shown.
            continue
        source_index = custom.get("historySourceIndex") if isinstance(custom, dict) else None
        if isinstance(source_index, int) and not isinstance(source_index, bool):
            if (
                0 <= source_index < len(history)
                and history[source_index].get("role") == role
                and (
                    _pairs_by_content(history[source_index], message)
                    # A stored row of tool calls alone has no words to
                    # compare: the row shown for it draws its calls.
                    or (
                        not _text(history[source_index].get("content"))
                        and _rendered_inside(history[source_index], message)
                    )
                )
            ):
                shown.add(source_index)
                continue
            if source_id is not None:
                # An aliased row's display ID can name an obsolete position.
                return False
            if isinstance(custom, dict):
                custom.pop("historySourceIndex", None)
        if message_id.startswith("history-"):
            # Synthetic rows (``history-<n>-error``) are state-only; they
            # never carry a stored counterpart and are left untouched.
            history_id = re.fullmatch(r"history-(\d+)", message_id)
            if history_id is not None:
                shown.add(int(history_id.group(1)))
            continue
        provisional.append(index)
    if not provisional:
        return True
    mega_row: Dict[str, Any] = next(
        (messages[i] for i in provisional if messages[i].get("role") == "assistant"),
        {},
    )
    # The sends this turn's user rows are. When each names one, a saved user
    # row naming only other sends is another client's message the daemon
    # steered into the running turn: not one of these rows, so the walk passes
    # it and the page adds it from history (#1359).
    own_requests = [
        _streamed_request_id(messages[i]) for i in provisional if messages[i].get("role") == "user"
    ]

    def _foreign_user(stored_row: Dict[str, Any]) -> bool:
        if stored_row.get("role") != "user" or not own_requests or not all(own_requests):
            return False
        saved_ids = _saved_request_ids(stored_row)
        return bool(saved_ids) and not saved_ids & set(own_requests)

    stored: List[int] = []
    cursor = len(provisional) - 1
    for index in range(len(history) - 1, -1, -1):
        if cursor < 0:
            break
        stored_row = history[index]
        role = stored_row.get("role")
        if role == "system":
            # Hub echoes land behind the turn's rows; they render as nothing.
            continue
        if role == "tool":
            # Tool results render onto their call's card (matched by
            # tool_call_id), never as rows of their own: a streamed turn has
            # no row to pair them with. Skipping them keeps the user and
            # assistant pairing on the stored absolute indexes.
            continue
        if role not in ("user", "assistant") or index in shown:
            return False
        if _foreign_user(stored_row):
            continue
        streamed_row = messages[provisional[cursor]]
        if role == streamed_row.get("role") and _pairs_by_content(
            stored_row, streamed_row
        ):
            stored.append(index)
            cursor -= 1
            continue
        if _rendered_inside(stored_row, mega_row):
            # A turn that ends on its final text pairs the text row first;
            # its calls rows (text plus metadata-borne ids) then prove
            # themselves against this turn's streamed call cards and are
            # left for the merge to add rather than paired. A turn that
            # ends ON the tool batch pairs its calls row instead, so the
            # pairing attempt must come first.
            continue
        # Role order alone proves nothing: an unrelated stale pair matches
        # this turn's shape by role. Without content identity the snapshot
        # cannot be reconciled against what the page shows.
        return False
    if cursor >= 0:
        # History ran out before every provisional row paired: the refresh
        # does not cover this turn. Keep the provisional ids.
        return False
    stored.reverse()

    # A successful nearest-tail match is not enough if the same complete
    # role/content sequence appears more than once in the stored history.
    # Count possible full alignments, including earlier duplicate rows that
    # the greedy pass skipped, and fail closed unless the mapping is unique.
    selected = dict(zip(provisional, stored))
    alignment_counts: Dict[tuple[int, int], int] = {}

    def _count_prefix_alignments(before: int, cursor: int) -> int:
        if cursor < 0:
            return 1
        key = (before, cursor)
        if key in alignment_counts:
            return alignment_counts[key]

        for history_index in range(before, -1, -1):
            stored_row = history[history_index]
            role = stored_row.get("role")
            if role in ("system", "tool"):
                continue
            if role not in ("user", "assistant") or history_index in shown:
                alignment_counts[key] = 0
                return 0
            if _foreign_user(stored_row):
                continue

            message_index = provisional[cursor]
            streamed_row = messages[message_index]
            rendered_inside = _rendered_inside(stored_row, mega_row)
            if (
                role == streamed_row.get("role")
                and _pairs_by_content(stored_row, streamed_row)
            ):
                if rendered_inside and selected.get(message_index) != history_index:
                    continue
                matched = _count_prefix_alignments(history_index - 1, cursor - 1)
                if matched >= 2:
                    alignment_counts[key] = 2
                    return 2
                skipped = _count_prefix_alignments(history_index - 1, cursor)
                alignment_counts[key] = min(2, matched + skipped)
                return alignment_counts[key]
            if rendered_inside:
                continue
            alignment_counts[key] = 0
            return 0

        alignment_counts[key] = 0
        return 0

    final_message_index = provisional[-1]
    final_streamed_row = messages[final_message_index]
    complete_alignments = 0
    for history_index, stored_row in enumerate(history):
        if history_index in shown:
            continue
        if (
            stored_row.get("role") != final_streamed_row.get("role")
            or not _pairs_by_content(stored_row, final_streamed_row)
        ):
            continue
        if (
            _rendered_inside(stored_row, mega_row)
            and selected.get(final_message_index) != history_index
        ):
            continue
        complete_alignments += _count_prefix_alignments(
            history_index - 1, len(provisional) - 2
        )
        if complete_alignments > 1:
            return False
    if complete_alignments != 1:
        return False

    for message_index, history_index in zip(provisional, stored):
        message = messages[message_index]
        metadata = message.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
            message["metadata"] = metadata
        custom = metadata.get("custom")
        if not isinstance(custom, dict):
            custom = {}
            metadata["custom"] = custom
        custom["historySourceIndex"] = history_index

    return True


def _assistant_set_messages(controller: Any, messages: List[Dict[str, Any]]) -> None:
    """Publish message state while retaining the rest of the client state."""
    state = controller.state
    if state is None:
        controller.state = {"messages": messages}
    else:
        state["messages"] = messages


def _assistant_close_shown_prompt(
    messages: List[Dict[str, Any]],
    call_id: str,
    result: Dict[str, Any],
    permission_id: str | None,
) -> None:
    """Put the answer on a prompt card an earlier message shows.

    The message is replaced, not edited in place, so the next progress publish
    sees it changed and sends it.
    """
    for index, message in enumerate(messages):
        content = message.get("content")
        if message.get("role") != "assistant" or not isinstance(content, list):
            continue
        for at, part in enumerate(content):
            if not isinstance(part, dict) or part.get("toolCallId") != call_id:
                continue
            args = part.get("args")
            current_id = args.get("permission_id") if isinstance(args, dict) else None
            if not current_id and isinstance(args, dict):
                details = args.get("details")
                current_id = details.get("permission_id") if isinstance(details, dict) else None
            if permission_id and current_id != permission_id:
                continue
            if not permission_id and current_id:
                continue
            if isinstance(part, dict):
                parts = list(content)
                parts[at] = {
                    **part,
                    "result": result,
                    "isError": result["decision"] == "deny",
                }
                messages[index] = {**message, "content": parts}
                return


def _now_ms() -> int:
    return int(time.time() * 1000)


def _tool_completed_ms(started: int, event: Dict[str, Any]) -> int:
    """End of a tool call: the daemon's measured run time, else receipt time.

    A missing or zero ``execution_time`` (seconds) must not render as "0ms".
    """
    elapsed = event.get("execution_time")
    if isinstance(elapsed, (int, float)) and elapsed > 0:
        return started + int(elapsed * 1000)
    return max(started, _now_ms())


def _assistant_harness_tool_part(
    update: Dict[str, Any], part: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Project one merged ACP card without treating its title as a native tool."""
    if part is None:
        part = {"type": "tool-call", "toolCallId": str(update["toolCallId"])}
        # A reconnect may first observe a finished card. Its arrival time
        # cannot establish a duration for work that happened before it.
        if update.get("status") not in {"completed", "failed"}:
            part["timing"] = {"startedAt": _now_ms()}
    result = part.get("result")
    metadata = result.get("metadata") if isinstance(result, dict) else None
    previous = metadata.get("acp") if isinstance(metadata, dict) else None
    if not isinstance(previous, dict):
        previous = {}
    payload = {**previous, **{key: value for key, value in update.items() if value is not None}}
    status = str(payload.get("status") or "pending")
    preliminary = status not in {"completed", "failed"}
    tool_input = payload.get("rawInput")
    if tool_input is None:
        tool_input = {}
    output = payload.get("rawOutput")
    if output is None:
        output = payload.get("content") or ""
    part.update({
        "toolName": "harness_tool",
        "args": tool_input,
        "argsText": json.dumps(tool_input, ensure_ascii=False),
        "result": {"success": status != "failed", "output": output, "error": "",
                   "metadata": {"acp": copy.deepcopy(payload)}},
        "isError": status == "failed",
        "isPreliminary": preliminary,
    })
    timing = part.get("timing")
    if timing is None:
        return part
    if preliminary:
        timing.pop("completedAt", None)
    elif timing.get("completedAt") is None:
        timing["completedAt"] = _tool_completed_ms(timing["startedAt"], update)
    return part


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
        "cancelled": "stop",
        "canceled": "stop",
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
        finish_fields: Dict[str, Any] = {
            "finishReason": _assistant_finish_reason(usage.get("stopReason")),
            "usage": {
                "inputTokens": int(usage.get("inputTokens", 0) or 0),
                "outputTokens": int(usage.get("outputTokens", 0) or 0),
            },
        }
        if completion.get("error"):
            # Structured run-status error record: class/code/provider/
            # retryable/cause from the daemon event, display message along
            # for the UI (#720). Older clients ignore the extra key.
            finish_fields["error"] = completion["error"]
        yield _assistant_protocol_chunk("message-finish", **finish_fields)


@router.post("/{session_id}/assistant")
async def assistant_transport(
    session_id: str, body: AssistantRequest, request: Request = None
):
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

    read_context = _request_read_context(request)

    try:
        from assistant_stream import RunController, create_run
        from assistant_stream.serialization import AssistantTransportResponse
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail="assistant-stream is required for the assistant transport endpoint",
        ) from exc

    completion: Dict[str, Any] = {"completed": False}
    handover_delivery: Dict[str, Any] = {
        "enabled": False,
        "token": None,
        "sent": asyncio.Event(),
    }

    async def run_callback(controller: RunController):
        state_blob = body.state if isinstance(body.state, dict) else {}
        handover_token = state_blob.get(_ASSISTANT_HANDOVER_STATE_KEY)
        handover_token_from_request = handover_token is not None
        handover_record: Optional[Dict[str, Any]] = None
        handover_claim_id: Optional[str] = None
        handover_error = ""
        clear_handover_state_on_reject = False
        shared_queue: Optional[asyncio.Queue] = None
        queue_from_ledger = False
        if handover_token is not None:
            (
                handover_record,
                handover_claim_id,
                claim_error,
                retained_queue,
                retained_context,
            ) = _assistant_claim_handover(session, handover_token)
            if claim_error:
                handover_error = claim_error
                clear_handover_state_on_reject = (
                    "capability is invalid or expired" in claim_error.lower()
                    or claim_error.lower().startswith("permission handover expired")
                )
                handover_record = None
                handover_claim_id = None
            elif retained_context is not read_context:
                _assistant_release_handover(
                    session, handover_token, handover_claim_id, handover_record
                )
                handover_error = (
                    "Permission handover authorization context changed; "
                    "accepted turns were not resumed."
                )
                clear_handover_state_on_reject = True
                handover_record = None
                handover_claim_id = None
            elif retained_queue is None:
                _assistant_release_handover(
                    session, handover_token, handover_claim_id, handover_record
                )
                handover_error = (
                    "Permission handover event subscription is unavailable; "
                    "accepted turns were not resumed."
                )
                clear_handover_state_on_reject = True
                handover_record = None
                handover_claim_id = None
            else:
                shared_queue = retained_queue
                queue_from_ledger = True
        subscribed_here = False
        if shared_queue is None and not handover_error:
            shared_queue = _subscribe(session, read_context)
            subscribed_here = True
        queue = shared_queue
        preserve_handover_queue = False
        goal_pump = None
        revocation_watch = None
        tool_controllers: Dict[str, Any] = {}
        permission_controllers: Dict[tuple[str, str], Any] = {}
        permission_tool_ids: Dict[str, str] = {}
        permission_decisions: Dict[str, Dict[str, Any]] = {}
        permission_resolution_pending: Dict[str, Dict[str, Any]] = {}
        permission_resolution_errors: Dict[str, str] = {}
        permission_resolution_deadline: Optional[float] = None
        state_messages = _assistant_copy_messages(body.state)
        persist_message_state = isinstance(body.state, dict) and isinstance(
            body.state.get("messages"), list
        )
        submitted_contents: List[MessageContent] = []
        # Per-submission stored-image refs (#1088), parallel to
        # submitted_contents: saved at send time so the bytes outlive the turn.
        submitted_media: List[List[Dict[str, str]]] = []
        # The request id of each submission, parallel to submitted_contents (#1359).
        submitted_request_ids: List[str] = []
        # The request ID the daemon says owns each submitted user row.
        submitted_turn_request_ids: List[str] = []
        submitted_row_ids: List[Optional[str]] = []  # the row each send names, if any (#1102)
        resumed_request_order: List[str] = []
        resumed_rows: Dict[str, Dict[str, Any]] = {}
        resumed_prompt_request_ids: set[str] = set()
        resumed_prompt_tools: Dict[str, set[str]] = {}
        resumed_prompt_permission_ids: Dict[str, set[str]] = {}
        resumed_permission_request_ids: Dict[str, set[str]] = {}
        request_ids: set[str] = set()
        admitted_turn_request_ids: set[str] = set()
        possibly_accepted_request_ids: set[str] = set()
        untagged_request_ids: set[str] = set()
        completed_request_ids: set[str] = set()
        completed_assistant_turns: List[Dict[str, Any]] = []
        current_turn_request_ids: set[str] = set()
        answered_prompts: set[tuple[str, str]] = set()
        assistant_tool_parts: Dict[str, Dict[str, Any]] = {}
        # Text, reasoning and tool-call parts in arrival order.
        assistant_parts: List[Dict[str, Any]] = []
        saw_action = False
        # What the client holds; progress publishes only send what differs.
        sent_messages = list(state_messages)
        last_publish = float("-inf")
        # Streamed text the throttle has not published yet.
        stream_held = False
        # Why the daemon says the turn failed; the turn still ends normally.
        turn_error = ""
        # Structured fields from the error event (error_class/code/...).
        turn_error_fields: dict = {}
        run_error: Optional[Dict[str, Any]] = None
        admission_error = ""
        correlation_deadline: Optional[float] = None
        usage_totals = {"inputTokens": 0, "outputTokens": 0, "toolCalls": 0}
        final_stop_reason = "end_turn"
        handover_drop_baseline = int(getattr(queue, "dropped_count", 0) or 0)
        event_loss_seen = False
        event_loss_count = 0
        event_loss_message = ""
        event_loss_terminal = False
        event_loss_permission = False

        if handover_record is not None:
            resumed_request_order = [
                str(request_id)
                for request_id in handover_record.get("request_ids") or ()
                if request_id is not None and str(request_id)
            ]
            request_ids.update(resumed_request_order)
            admitted_turn_request_ids.update(resumed_request_order)
            possibly_accepted_request_ids.update(
                str(request_id)
                for request_id in handover_record.get("possibly_accepted_request_ids") or ()
                if request_id is not None and str(request_id)
            )
            admission_error = str(handover_record.get("admission_error") or "")
            if admission_error:
                completion["error"] = {"message": admission_error}
            submitted_turn_request_ids.extend(resumed_request_order)
            completed_request_ids.update(
                str(request_id)
                for request_id in handover_record.get("completed_request_ids") or ()
                if request_id is not None
            )
            untagged_request_ids.update(
                str(request_id)
                for request_id in handover_record.get("untagged_request_ids") or ()
                if request_id is not None
            )
            completed_assistant_turns.extend(
                copy.deepcopy(handover_record.get("completed_turns") or [])
            )
            resumed_rows = {
                str(row["request_id"]): copy.deepcopy(row)
                for row in handover_record.get("rows") or []
                if isinstance(row, dict)
                and row.get("request_id") is not None
                and row.get("row_id") is not None
            }
            resumed_prompt_tools = {
                str(request_id): {
                    str(tool_id)
                    for tool_id in tool_ids or ()
                    if tool_id is not None and str(tool_id)
                }
                for request_id, tool_ids in (
                    handover_record.get("prompt_tools") or {}
                ).items()
            }
            resumed_prompt_request_ids = set(resumed_prompt_tools)
            resumed_prompt_permission_ids = {
                str(tool_id): {
                    str(permission_id)
                    for permission_id in permission_ids or ()
                    if permission_id is not None and str(permission_id)
                }
                for tool_id, permission_ids in (
                    handover_record.get("prompt_permission_ids") or {}
                ).items()
            }
            resumed_permission_request_ids = {
                str(permission_id): {
                    str(request_id)
                    for request_id in request_ids or ()
                    if request_id is not None and str(request_id)
                }
                for permission_id, request_ids in (
                    handover_record.get("permission_request_ids") or {}
                ).items()
            }
            permission_resolution_errors = {
                str(tool_id): str(message)
                for tool_id, message in (
                    handover_record.get("permission_resolution_errors") or {}
                ).items()
            }
            for tool_ids in resumed_prompt_tools.values():
                for tool_id in tool_ids:
                    permission_ids = resumed_prompt_permission_ids.get(tool_id) or {""}
                    for permission_id in permission_ids:
                        call_id = (
                            f"permission_{tool_id}_{permission_id}"
                            if permission_id
                            else f"permission_{tool_id}"
                        )
                        permission_tool_ids[call_id] = tool_id
            permission_decisions = copy.deepcopy(
                handover_record.get("permission_decisions") or {}
            )
            assistant_parts = copy.deepcopy(
                handover_record.get("assistant_parts") or []
            )
            assistant_tool_parts = {
                str(part.get("toolCallId")): part
                for part in assistant_parts
                if isinstance(part, dict)
                and part.get("type") == "tool-call"
                and part.get("toolCallId")
            }
            answered_prompts.update(
                (str(answer[0]), str(answer[1]))
                for answer in handover_record.get("answered_prompts") or ()
                if isinstance(answer, (list, tuple))
                and len(answer) == 2
                and answer[0] is not None
                and answer[1] is not None
            )
            current_turn_request_ids.update(
                str(request_id)
                for request_id in handover_record.get("current_turn_request_ids") or ()
                if request_id is not None
            )
            turn_error = str(handover_record.get("turn_error") or "")
            turn_error_fields = copy.deepcopy(
                handover_record.get("turn_error_fields") or {}
            )
            saved_messages = handover_record.get("messages")
            if isinstance(saved_messages, list):
                state_messages = copy.deepcopy(saved_messages)
                sent_messages = list(state_messages)
                if persist_message_state:
                    _assistant_set_messages(controller, state_messages)
            saved_usage = handover_record.get("usage_totals") or {}
            for key in usage_totals:
                usage_totals[key] = int(saved_usage.get(key, 0) or 0)
            handover_drop_baseline = int(
                handover_record.get(
                    "queue_dropped_count", handover_drop_baseline
                )
                or 0
            )
            event_loss_seen = bool(handover_record.get("event_loss_seen"))
            event_loss_count = int(handover_record.get("event_loss_count", 0) or 0)
            event_loss_message = str(
                handover_record.get("event_loss_message") or ""
            )
            event_loss_terminal = bool(handover_record.get("event_loss_terminal"))
            event_loss_permission = bool(
                handover_record.get("event_loss_permission")
            )
            if event_loss_seen and admitted_turn_request_ids - completed_request_ids:
                correlation_deadline = (
                    _assistant_correlation_monotonic()
                    + EVENT_IDLE_TIMEOUT_SECONDS
                )
            if (
                len(resumed_request_order) > 1
                and untagged_request_ids - completed_request_ids
            ):
                correlation_deadline = (
                    _assistant_correlation_monotonic()
                    + EVENT_IDLE_TIMEOUT_SECONDS
                )
            if possibly_accepted_request_ids - completed_request_ids:
                # An earlier admission response may have been lost. Keep the
                # resumed receive loop bounded even when a later accepted turn
                # completes and the uncertain request never emits anything.
                correlation_deadline = (
                    _assistant_correlation_monotonic()
                    + EVENT_IDLE_TIMEOUT_SECONDS
                )

        def reject_handover(message: str) -> None:
            nonlocal handover_record, handover_claim_id
            controller.add_error(message)
            completion["error"] = {"message": message}
            completion["usage"] = {
                **usage_totals,
                "stopReason": "error",
            }
            completion["completed"] = True
            _assistant_set_usage(controller, completion["usage"])
            if handover_token and handover_claim_id:
                _assistant_release_handover(
                    session, handover_token, handover_claim_id, handover_record
                )
                handover_claim_id = None
            if clear_handover_state_on_reject:
                # Invalid, expired, or otherwise non-resumable capabilities
                # must not poison every later request copied from this state.
                _assistant_set_state_value(
                    controller, _ASSISTANT_HANDOVER_STATE_KEY, None
                )
            handover_record = None

        async def wait_for_handover_capability_delivery() -> bool:
            """Wait boundedly for the ASGI send callback before admitting work."""
            if (
                handover_token_from_request
                or not handover_token
                or not handover_delivery["enabled"]
            ):
                return True
            handover_delivery["token"] = handover_token
            state_manager = getattr(controller, "_state_manager", None)
            flush = getattr(state_manager, "flush", None)
            if callable(flush):
                flush()

            deadline = time.monotonic() + EVENT_IDLE_TIMEOUT_SECONDS
            while True:
                if (read_context and read_context.revoked) or getattr(
                    shared_queue, "revoked", False
                ):
                    return False
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    message = (
                        "Cannot submit assistant messages because the handover "
                        "capability ASGI send timed out. No messages were submitted. "
                        "Retry the request."
                    )
                    controller.add_error(message)
                    completion["error"] = {"message": message}
                    completion["usage"] = {
                        **usage_totals,
                        "stopReason": "error",
                    }
                    completion["completed"] = True
                    _assistant_set_state_value(
                        controller, _ASSISTANT_HANDOVER_STATE_KEY, None
                    )
                    _assistant_set_usage(controller, completion["usage"])
                    return False
                try:
                    await asyncio.wait_for(
                        handover_delivery["sent"].wait(),
                        timeout=min(remaining, 0.05),
                    )
                except asyncio.TimeoutError:
                    continue
                return not (
                    (read_context and read_context.revoked)
                    or getattr(shared_queue, "revoked", False)
                )

        def publish_progress() -> None:
            """Publish the in-flight turn so its parts reach the browser.

            The web client renders only ``state.messages``, so without this a
            turn shows nothing until it ends. Rebuilt from the untouched base
            each time (the final publishes below still mutate it in place);
            ops carry per-message sets, not the whole history.
            """
            nonlocal sent_messages, last_publish, stream_held
            last_publish = time.monotonic()
            stream_held = False
            if not persist_message_state:
                return
            progress = build_messages_for_turn()
            published = controller.state["messages"]
            for index, message in enumerate(progress):
                if index >= len(sent_messages):
                    published.append(message)
                elif message != sent_messages[index]:
                    published[index] = message
            sent_messages = progress

        def stable_user_row_id(proposed: Any) -> str:
            """Reserve each user row ID in admission order before rows move."""
            taken = {
                message.get("id") for message in state_messages
            } | set(submitted_row_ids)
            row_pattern = re.compile(rf"user-{re.escape(session_id)}-\d+")
            if (
                isinstance(proposed, str)
                and row_pattern.fullmatch(proposed)
                and proposed not in taken
            ):
                return proposed
            ordinal = len(submitted_row_ids)
            suffix = len(state_messages) + ordinal
            candidate = f"user-{session_id}-{suffix}"
            while candidate in taken:
                suffix += 1
                candidate = f"user-{session_id}-{suffix}"
            return candidate

        def assistant_row_id(request_ids_for_turn: set[str]) -> str:
            if queue_from_ledger:
                indexes = [
                    resumed_request_order.index(request_id)
                    for request_id in request_ids_for_turn
                    if request_id in resumed_request_order
                ]
                ordinal = min(indexes) if indexes else len(resumed_request_order)
                return f"assistant-{session_id}-resume-{ordinal}"
            if len(submitted_contents) == 1:
                # Keep the established row id for ordinary one-message runs.
                # Multi-send runs need ids independent of progress-list length
                # so each streamed turn keeps its row through reordering.
                return f"assistant-{session_id}-{len(state_messages) + 1}"
            indexes = [
                index
                for index, request_id in enumerate(submitted_turn_request_ids)
                if request_id in request_ids_for_turn
            ]
            if not indexes:
                indexes = [
                    index
                    for index, request_id in enumerate(submitted_turn_request_ids)
                    if request_id not in completed_request_ids
                ]
            ordinal = min(indexes) if indexes else len(submitted_contents)
            return f"assistant-{session_id}-turn-{len(state_messages) + ordinal}"

        def build_resumed_messages_for_turn(
            status: Optional[Dict[str, str]] = None,
            request_ids_for_turn: Optional[set[str]] = None,
            parts_override: Optional[List[Dict[str, Any]]] = None,
            message_id_override: Optional[str] = None,
        ) -> List[Dict[str, Any]]:
            """Render completed and active turns beside their original user rows."""
            current = [dict(message) for message in state_messages]
            if submitted_contents:
                present_row_ids = {
                    row.get("id") for row in current if row.get("role") == "user"
                }
                present_request_ids = {
                    (row.get("metadata") or {}).get("custom", {}).get("requestId")
                    for row in current
                    if row.get("role") == "user"
                }
                missing_indexes = [
                    index
                    for index, row_id in enumerate(submitted_row_ids)
                    if row_id not in present_row_ids
                    and submitted_request_ids[index] not in present_request_ids
                ]
                if missing_indexes:
                    _assistant_update_message_state(
                        current,
                        session_id,
                        [submitted_contents[index] for index in missing_indexes],
                        [],
                        None,
                        [submitted_media[index] for index in missing_indexes],
                        [submitted_request_ids[index] for index in missing_indexes],
                        [submitted_row_ids[index] for index in missing_indexes],
                    )
                    current.pop()  # discard the helper's single reply placeholder

            def render_turn(
                turn_ids: set[str],
                parts: List[Dict[str, Any]],
                turn_status: Optional[Dict[str, str]],
                message_id: Optional[str],
            ) -> None:
                nonlocal current
                if not turn_ids or (not parts and turn_status is None):
                    return
                prompt_ids = turn_ids & resumed_prompt_request_ids
                prompt_identities: set[tuple[str, str]] = set()
                for request_id in prompt_ids:
                    for tool_id in resumed_prompt_tools.get(request_id, set()):
                        permission_ids = {
                            permission_id
                            for permission_id, owner_request_ids
                            in resumed_permission_request_ids.items()
                            if request_id in owner_request_ids
                            and permission_id
                            in resumed_prompt_permission_ids.get(tool_id, set())
                        }
                        if permission_ids:
                            prompt_identities.update(
                                (tool_id, permission_id)
                                for permission_id in permission_ids
                            )
                        else:
                            prompt_identities.add((tool_id, ""))
                prompt_call_ids = {
                    f"permission_{tool_id}_{permission_id}"
                    if permission_id
                    else f"permission_{tool_id}"
                    for tool_id, permission_id in prompt_identities
                }
                prompt_index = next(
                    (
                        index
                        for index in range(len(current) - 1, -1, -1)
                        if current[index].get("role") == "assistant"
                        and any(
                            isinstance(part, dict)
                            and part.get("toolCallId") in prompt_call_ids
                            for part in (
                                current[index].get("content")
                                if isinstance(current[index].get("content"), list)
                                else []
                            )
                        )
                    ),
                    None,
                )
                if prompt_ids and prompt_index is not None:
                    message = current.pop(prompt_index)
                    content = message.get("content")
                    merged = (
                        list(content)
                        if isinstance(content, list)
                        else ([{"type": "text", "text": content}] if content else [])
                    )
                    stored_tools = {
                        part.get("toolCallId"): index
                        for index, part in enumerate(merged)
                        if isinstance(part, dict)
                        and part.get("type") == "tool-call"
                    }
                    for part in parts:
                        index = (
                            stored_tools.get(part.get("toolCallId"))
                            if isinstance(part, dict) and part.get("type") == "tool-call"
                            else None
                        )
                        if index is None:
                            merged.append(copy.deepcopy(part))
                        else:
                            merged[index] = copy.deepcopy(part)
                    message["content"] = merged or ""
                    if turn_status is None:
                        message.pop("status", None)
                    else:
                        message["status"] = copy.deepcopy(turn_status)
                    target = len(current)
                    for index, row in enumerate(current):
                        if row.get("role") == "user" and any(
                            (resumed_rows.get(request_id) or {}).get("row_id")
                            == row.get("id")
                            for request_id in turn_ids
                        ):
                            target = max(target if target != len(current) else 0, index + 1)
                    if target:
                        current.insert(target, message)
                    else:
                        current.insert(min(prompt_index, len(current)), message)
                    return

                indexes = [
                    resumed_request_order.index(request_id)
                    for request_id in turn_ids
                    if request_id in resumed_request_order
                ]
                ordinal = min(indexes) if indexes else len(resumed_request_order)
                resolved_message_id = message_id or (
                    message_id_override
                    if request_ids_for_turn is not None
                    else None
                ) or f"assistant-{session_id}-resume-{ordinal}"
                assistant_message = _assistant_turn_message(
                    session_id,
                    current,
                    {
                        "parts": parts,
                        "status": turn_status,
                        "message_id": resolved_message_id,
                    },
                )
                user_indexes = []
                for request_id in turn_ids:
                    row_info = resumed_rows.get(request_id) or {}
                    row_id = row_info.get("row_id")
                    index = next(
                        (
                            i for i, row in enumerate(current)
                            if row.get("role") == "user" and row.get("id") == row_id
                        ),
                        None,
                    )
                    if index is None:
                        client_request_id = row_info.get("client_request_id")
                        index = next(
                            (
                                i for i, row in enumerate(current)
                                if row.get("role") == "user"
                                and (row.get("metadata") or {}).get("custom", {}).get(
                                    "requestId"
                                ) == client_request_id
                            ),
                            None,
                        )
                    if index is not None:
                        user_indexes.append(index)
                target = max(user_indexes) + 1 if user_indexes else len(current)
                existing_index = next(
                    (
                        index for index, row in enumerate(current)
                        if row.get("id") == resolved_message_id
                    ),
                    None,
                )
                if existing_index is not None:
                    current.pop(existing_index)
                    if existing_index < target:
                        target -= 1
                current.insert(target, assistant_message)

            ordered_turns = sorted(
                completed_assistant_turns,
                key=lambda turn: min(
                    (
                        resumed_request_order.index(request_id)
                        for request_id in turn.get("request_ids") or ()
                        if request_id in resumed_request_order
                    ),
                    default=len(resumed_request_order),
                ),
            )
            for turn in ordered_turns:
                render_turn(
                    set(turn.get("request_ids") or ()),
                    copy.deepcopy(turn.get("parts") or []),
                    copy.deepcopy(turn.get("status")),
                    turn.get("message_id"),
                )

            active_ids = set(
                request_ids_for_turn
                if request_ids_for_turn is not None
                else current_turn_request_ids - completed_request_ids
            )
            active_parts = copy.deepcopy(
                assistant_parts if parts_override is None else parts_override
            )
            render_turn(active_ids, active_parts, status, message_id_override)
            return current

        def build_messages_for_turn(
            status: Optional[Dict[str, str]] = None,
        ) -> List[Dict[str, Any]]:
            """Build the current stream state with one assistant row per turn."""
            if queue_from_ledger:
                return build_resumed_messages_for_turn(status)
            if not submitted_contents:
                if resumed_request_order:
                    return build_resumed_messages_for_turn(status)
                return _assistant_update_message_state(
                    [dict(message) for message in state_messages],
                    session_id,
                    submitted_contents,
                    copy.deepcopy(assistant_parts),
                    status,
                    copy.deepcopy(submitted_media),
                    list(submitted_request_ids),
                    list(submitted_row_ids),
                )
            current = [dict(message) for message in state_messages]
            grouped_submissions: set[int] = set()

            def append_user_rows(indexes: List[int]) -> None:
                if not indexes:
                    return
                _assistant_update_message_state(
                    current,
                    session_id,
                    [submitted_contents[index] for index in indexes],
                    [],
                    None,
                    [submitted_media[index] for index in indexes],
                    [submitted_request_ids[index] for index in indexes],
                    [submitted_row_ids[index] for index in indexes],
                )
                current.pop()  # discard the helper's single reply placeholder

            ordered_turns = sorted(
                completed_assistant_turns,
                key=lambda turn: min(
                    (
                        index
                        for index, request_id in enumerate(submitted_turn_request_ids)
                        if request_id in set(turn.get("request_ids") or ())
                    ),
                    default=len(submitted_contents),
                ),
            )
            for turn in ordered_turns:
                turn_ids = set(turn.get("request_ids") or ())
                indexes = [
                    index
                    for index, request_id in enumerate(submitted_turn_request_ids)
                    if request_id in turn_ids and index not in grouped_submissions
                ]
                append_user_rows(indexes)
                grouped_submissions.update(indexes)
                current.append(_assistant_turn_message(session_id, current, turn))

            active_ids = current_turn_request_ids - completed_request_ids
            active_indexes = [
                index
                for index, request_id in enumerate(submitted_turn_request_ids)
                if request_id in active_ids and index not in grouped_submissions
            ]
            if (assistant_parts or status is not None) and not active_indexes:
                pending_indexes = [
                    index
                    for index in range(len(submitted_contents))
                    if index not in grouped_submissions
                ]
                if len(pending_indexes) == 1:
                    active_indexes = pending_indexes
            append_user_rows(active_indexes)
            grouped_submissions.update(active_indexes)
            if assistant_parts or status is not None:
                current.append(
                    _assistant_turn_message(
                        session_id,
                        current,
                        {
                            "parts": assistant_parts,
                            "status": status,
                            "message_id": assistant_row_id(active_ids),
                        },
                    )
                )

            append_user_rows(
                [
                    index
                    for index in range(len(submitted_contents))
                    if index not in grouped_submissions
                ]
            )
            return current

        def handover_snapshot() -> Dict[str, Any]:
            request_order = list(
                dict.fromkeys(resumed_request_order + submitted_turn_request_ids)
            )
            rows = copy.deepcopy(resumed_rows)
            if not queue_from_ledger:
                for index, turn_request_id in enumerate(submitted_turn_request_ids):
                    if index >= len(submitted_request_ids) or index >= len(submitted_row_ids):
                        continue
                    rows[turn_request_id] = {
                        "request_id": turn_request_id,
                        "client_request_id": submitted_request_ids[index],
                        "row_id": submitted_row_ids[index],
                    }
            prompt_tools = {
                request_id: sorted(tool_ids)
                for request_id, tool_ids in resumed_prompt_tools.items()
                if tool_ids
            }
            return {
                "request_ids": request_order,
                "completed_request_ids": sorted(completed_request_ids),
                "possibly_accepted_request_ids": sorted(possibly_accepted_request_ids),
                "admission_error": admission_error,
                "untagged_request_ids": sorted(untagged_request_ids),
                "completed_turns": copy.deepcopy(completed_assistant_turns),
                "rows": list(rows.values()),
                "prompt_tools": prompt_tools,
                "prompt_permission_ids": {
                    tool_id: sorted(permission_ids)
                    for tool_id, permission_ids in resumed_prompt_permission_ids.items()
                },
                "permission_request_ids": {
                    permission_id: sorted(request_ids)
                    for permission_id, request_ids in resumed_permission_request_ids.items()
                },
                "permission_decisions": copy.deepcopy(permission_decisions),
                "permission_resolution_errors": dict(permission_resolution_errors),
                "answered_prompts": sorted(answered_prompts),
                "assistant_parts": copy.deepcopy(assistant_parts),
                "current_turn_request_ids": sorted(current_turn_request_ids),
                "turn_error": turn_error,
                "turn_error_fields": copy.deepcopy(turn_error_fields),
                "usage_totals": dict(usage_totals),
                "queue_dropped_count": handover_drop_baseline,
                "event_loss_seen": event_loss_seen,
                "event_loss_count": event_loss_count,
                "event_loss_message": event_loss_message,
                "event_loss_terminal": event_loss_terminal,
                "event_loss_permission": event_loss_permission,
                "messages": build_messages_for_turn(),
            }

        def store_handover_snapshot() -> None:
            nonlocal handover_record
            if not handover_token or not handover_claim_id or shared_queue is None:
                return
            handover_record = handover_snapshot()
            _assistant_update_handover(
                session,
                handover_token,
                handover_claim_id,
                handover_record,
                shared_queue,
                read_context,
            )

        def fail_correlation(message: str) -> None:
            """Close a partially accepted run without guessing turn ownership."""
            nonlocal preserve_handover_queue
            previous_error = completion.get("error") or {}
            already_reported_gap = (
                event_loss_seen
                and previous_error.get("code") == "assistant_event_stream_gap"
            )
            completion["error"] = {
                "message": message,
                **({"code": "assistant_event_stream_gap"} if event_loss_seen else {}),
            }
            completion["usage"] = {
                **usage_totals,
                "stopReason": "error",
            }
            completion["completed"] = True
            if not already_reported_gap:
                controller.add_error(message)
            if persist_message_state:
                status = (
                    {"type": "incomplete", "reason": "error", "error": message}
                    if assistant_parts
                    else None
                )
                _assistant_set_messages(
                    controller, build_messages_for_turn(status)
                )
                controller.state["synced"] = None
            _assistant_set_usage(controller, completion["usage"])
            preserve_handover_queue = bool(handover_token and handover_claim_id)
            store_handover_snapshot()

        def correlation_failure_message(reason: str) -> str:
            """Name accepted messages whose untagged events cannot be assigned."""
            possibly_pending = sorted(
                possibly_accepted_request_ids - completed_request_ids
            )
            if possibly_pending:
                labels = ", ".join(possibly_pending)
                admission_context = f"{admission_error} " if admission_error else ""
                return (
                    f"{admission_context}Cannot safely correlate the completion for "
                    f"possibly accepted message(s) {labels}: {reason}. These sends "
                    "may still be running. No messages were resubmitted."
                )
            missing = sorted(untagged_request_ids - completed_request_ids)
            candidates = ", ".join(missing) or ", ".join(sorted(untagged_request_ids))
            pending = sorted(admitted_turn_request_ids - completed_request_ids)
            pending_names = ", ".join(pending) or candidates
            admission_context = f"{admission_error} " if admission_error else ""
            return (
                f"{admission_context}Cannot safely correlate the completion for accepted message(s) "
                f"{candidates}: the daemon did not echo their request IDs and "
                f"{reason}. These sends were admitted; accepted message(s) "
                f"{pending_names} may still be running. No messages were "
                "resubmitted."
            )

        def permission_resolution_error_message(*, all_terminal: bool) -> str:
            prompt_labels = {
                str(pending.get("permission_id") or pending.get("tool_id") or key)
                for key, pending in permission_resolution_pending.items()
            }
            prompt_labels.update(
                str(key).removeprefix("unidentified:")
                for key in permission_resolution_errors
            )
            labels = ", ".join(sorted(prompt_labels)) or "the pending prompt"
            details = "; ".join(permission_resolution_errors.values())
            if not details:
                details = "no matching permission outcome event was observed"
            if all_terminal:
                accepted = ", ".join(sorted(admitted_turn_request_ids)) or "unknown"
                activity = (
                    f"Accepted message(s) {accepted} reached their terminal events."
                )
            else:
                pending = sorted(
                    admitted_turn_request_ids - completed_request_ids
                )
                accepted = ", ".join(pending or sorted(admitted_turn_request_ids))
                activity = (
                    f"Accepted message(s) {accepted or 'unknown'} may still be running."
                )
            active_prompt_keys = (
                set(permission_resolution_pending) | set(permission_resolution_errors)
            )
            attempted_decision = any(
                pending.get("attempted", True)
                for key, pending in permission_resolution_pending.items()
                if key in active_prompt_keys
            ) or any(
                decision.get("status") in {"attempted", "resolved"}
                for key, decision in permission_decisions.items()
                if key in active_prompt_keys
            )
            decision_outcome = (
                "The attempted decision may or may not have taken effect."
                if attempted_decision
                else "No permission response was submitted."
            )
            return (
                f"Permission resolution could not be confirmed for prompt(s) {labels}: "
                f"{details}. {decision_outcome} "
                f"{activity} No messages were resubmitted."
            )

        def record_event_loss() -> bool:
            """Mark one or more per-subscriber drops as an incomplete run."""
            nonlocal handover_drop_baseline, event_loss_seen, event_loss_count
            nonlocal event_loss_message, event_loss_terminal, event_loss_permission
            nonlocal correlation_deadline, preserve_handover_queue
            if (
                queue is None
                or not admitted_turn_request_ids
                or getattr(queue, "revoked", False)
            ):
                return False
            current_count = int(getattr(queue, "dropped_count", 0) or 0)
            delta = current_count - handover_drop_baseline
            if delta <= 0:
                return False
            event_loss_count += delta
            handover_drop_baseline = current_count
            event_loss_seen = True
            event_loss_terminal = event_loss_terminal or bool(
                getattr(queue, "dropped_terminal", False)
            )
            event_loss_permission = event_loss_permission or bool(
                getattr(queue, "dropped_permission", False)
            )
            accepted = sorted(admitted_turn_request_ids)
            pending = sorted(admitted_turn_request_ids - completed_request_ids)
            accepted_text = ", ".join(accepted) or "unknown"
            pending_text = ", ".join(pending) or accepted_text
            first_seq = getattr(queue, "first_dropped_seq", None)
            last_seq = getattr(queue, "last_dropped_seq", None)
            sequence_detail = (
                f" Daemon sequence range {first_seq}–{last_seq} was dropped."
                if first_seq is not None and last_seq is not None
                else ""
            )
            kinds = []
            if event_loss_terminal:
                kinds.append("including a terminal frame")
            if event_loss_permission:
                kinds.append("including a permission frame")
            kind_detail = f" ({', '.join(kinds)})" if kinds else ""
            event_loss_message = (
                f"Assistant event stream lost {event_loss_count} frame(s)"
                f"{kind_detail} for accepted message(s) {accepted_text}."
                f"{sequence_detail} Accepted message(s) {pending_text} may still be running. "
                "No messages were resubmitted."
            )
            completion["error"] = {
                "message": event_loss_message,
                "code": "assistant_event_stream_gap",
            }
            completion["usage"] = {**usage_totals, "stopReason": "error"}
            controller.add_error(event_loss_message)
            if isinstance(controller.state, dict):
                controller.state["error"] = event_loss_message
            else:
                try:
                    controller.state["error"] = event_loss_message
                except (KeyError, TypeError, AttributeError):
                    pass
            _assistant_set_usage(controller, completion["usage"])
            correlation_deadline = (
                _assistant_correlation_monotonic() + EVENT_IDLE_TIMEOUT_SECONDS
            )
            preserve_handover_queue = bool(handover_token and handover_claim_id)
            store_handover_snapshot()
            return True

        def command_message_label(command: Dict[str, Any], index: int) -> str:
            message = command.get("message")
            client_id = message.get("id") if isinstance(message, dict) else None
            if isinstance(client_id, str) and _CLIENT_REQUEST_ID.fullmatch(client_id):
                return client_id
            return f"command {index + 1}"

        def partial_admission_error(
            command_index: int,
            reason: str,
            *,
            attempted_request_id: Optional[str] = None,
            attempt_may_be_accepted: bool = False,
        ) -> str:
            accepted = ", ".join(submitted_request_ids) or "none"
            current = command_message_label(body.commands[command_index], command_index)
            if attempted_request_id is not None:
                current = attempted_request_id
            if attempt_may_be_accepted and add_message_count == 1:
                return (
                    f"Admission response for message {current} was lost; it may have "
                    f"been accepted. Admission error: {reason}. "
                    "No messages were resubmitted."
                )
            if attempt_may_be_accepted:
                attempted = f"Possibly accepted despite the admission error: {current}."
                future_start = command_index + 1
            elif attempted_request_id is not None:
                attempted = f"Rejected and not accepted: {current}."
                future_start = command_index + 1
            else:
                attempted = ""
                future_start = command_index
            unsent = [
                command_message_label(future, index)
                for index, future in enumerate(body.commands[future_start:], future_start)
                if _assistant_command_type(future) == "add-message"
            ]
            unsent_clause = (
                f"Not submitted: {', '.join(unsent)}. " if unsent else ""
            )
            attempted_clause = f"{attempted} " if attempted else ""
            return (
                "Partial assistant batch admission. "
                f"Acknowledged as accepted: {accepted}. "
                f"{attempted_clause}{unsent_clause}"
                f"Admission error: {reason}. No messages were resubmitted."
            )

        def record_possibly_accepted_submission(
            request_id: str,
            message_content: MessageContent,
            command: Dict[str, Any],
        ) -> None:
            """Track a send whose admission response was lost by exact request ID."""
            nonlocal admission_error, correlation_deadline
            nonlocal handover_record, handover_token, handover_claim_id
            if handover_token is None:
                retention_reason = ""
                if shared_queue is None:
                    retention_reason = "the event subscription is unavailable"
                else:
                    reserved_token, reserved_claim = _assistant_reserve_handover(
                        session, shared_queue, read_context
                    )
                    if reserved_token is None:
                        retention_reason = (
                            "all "
                            f"{_ASSISTANT_HANDOVER_MAX_ENTRIES} handover capacity "
                            "slots are occupied"
                        )
                    else:
                        handover_token = reserved_token
                        handover_claim_id = reserved_claim
                        handover_record = {}
                        _assistant_set_state_value(
                            controller,
                            _ASSISTANT_HANDOVER_STATE_KEY,
                            handover_token,
                        )
                if retention_reason:
                    retention_error = (
                        "Could not retain this event subscription because "
                        f"{retention_reason}. If this response ends before the "
                        f"terminal event for message {request_id} is observed, the "
                        "message may still be running and late events cannot be "
                        "guaranteed to be available on reconnect. No messages were "
                        "resubmitted."
                    )
                    admission_error = f"{admission_error} {retention_error}".strip()
            possibly_accepted_request_ids.add(request_id)
            request_ids.add(request_id)
            admitted_turn_request_ids.add(request_id)
            submitted_turn_request_ids.append(request_id)
            submitted_contents.append(message_content)
            submitted_media.append(_save_sent_media(message_content, session_id))
            submitted_request_ids.append(request_id)
            row_id = stable_user_row_id(command.get("rowId"))
            submitted_row_ids.append(row_id)
            if queue_from_ledger:
                if request_id not in resumed_request_order:
                    resumed_request_order.append(request_id)
                resumed_rows[request_id] = {
                    "request_id": request_id,
                    "client_request_id": request_id,
                    "row_id": row_id,
                }
            correlation_deadline = (
                _assistant_correlation_monotonic()
                + EVENT_IDLE_TIMEOUT_SECONDS
            )
            store_handover_snapshot()

        def add_streamed_text(part_type: str, text: str) -> None:
            """Grow the trailing text/reasoning part, or start one, and publish.

            Throttled: text inside the interval is held until the next publish,
            or until the event wait below times out on a quiet stream.
            """
            nonlocal stream_held
            if not text:
                return
            if assistant_parts and assistant_parts[-1]["type"] == part_type:
                assistant_parts[-1]["text"] += text
            else:
                assistant_parts.append({"type": part_type, "text": text})
            if time.monotonic() - last_publish >= STREAM_PUBLISH_INTERVAL:
                publish_progress()
            else:
                stream_held = True

        def held_text_due() -> Optional[float]:
            """Seconds until held text should publish; None when none is held."""
            if not stream_held:
                return None
            return max(0.0, last_publish + STREAM_PUBLISH_INTERVAL - time.monotonic())

        def has_unresolved_handover_permission() -> bool:
            """Block new input while an accepted permission prompt still owns the turn."""
            pending_request_ids = admitted_turn_request_ids - completed_request_ids
            for request_id in resumed_prompt_request_ids & pending_request_ids:
                for tool_id in resumed_prompt_tools.get(request_id, set()):
                    permission_ids = {
                        permission_id
                        for permission_id, owner_request_ids
                        in resumed_permission_request_ids.items()
                        if request_id in owner_request_ids
                        and permission_id
                        in resumed_prompt_permission_ids.get(tool_id, set())
                    }
                    identities = (
                        {(tool_id, permission_id) for permission_id in permission_ids}
                        if permission_ids
                        else {(tool_id, "")}
                    )
                    if any(identity not in answered_prompts for identity in identities):
                        return True
            return bool(permission_resolution_pending or permission_resolution_errors)

        try:
            if handover_error:
                reject_handover(handover_error)
                return
            if (read_context and read_context.revoked) or getattr(
                shared_queue, "revoked", False
            ):
                return
            new_event_loss = record_event_loss()
            if event_loss_seen and not new_event_loss and event_loss_message:
                completion["error"] = {
                    "message": event_loss_message,
                    "code": "assistant_event_stream_gap",
                }
                completion["usage"] = {**usage_totals, "stopReason": "error"}
                controller.add_error(event_loss_message)
                _assistant_set_usage(controller, completion["usage"])
            has_add_message = any(
                _assistant_command_type(command) == "add-message"
                for command in body.commands
            )
            add_message_count = sum(
                _assistant_command_type(command) == "add-message"
                for command in body.commands
            )
            if handover_record is not None and has_add_message:
                if event_loss_seen:
                    pending_ids = sorted(
                        admitted_turn_request_ids - completed_request_ids
                    )
                    pending_label = ", ".join(pending_ids) or "accepted messages"
                    reject_handover(
                        "Cannot admit new messages after an assistant event-stream "
                        f"gap. Accepted message(s) {pending_label} may still be "
                        "running; no resubmission or completion attribution is safe. "
                        "Their retained event stream remains owned by this capability."
                    )
                    return
                if has_unresolved_handover_permission():
                    reject_handover(
                        "Permission handover cannot admit new messages while an "
                        "accepted permission prompt remains unresolved; the accepted "
                        "sends were left owned by this capability."
                    )
                    return
            if handover_token is None and add_message_count > 0:
                if shared_queue is None:
                    message = (
                        "Cannot submit assistant messages because the event "
                        "subscription is unavailable. No messages were submitted."
                    )
                    controller.add_error(message)
                    completion["error"] = {"message": message}
                    completion["usage"] = {**usage_totals, "stopReason": "error"}
                    completion["completed"] = True
                    _assistant_set_usage(controller, completion["usage"])
                    return
                handover_token, handover_claim_id = _assistant_reserve_handover(
                    session, shared_queue, read_context
                )
                if handover_token is None:
                    unsent = [
                        command_message_label(command, index)
                        for index, command in enumerate(body.commands)
                        if _assistant_command_type(command) == "add-message"
                    ]
                    unsent_clause = (
                        f"Not submitted: {', '.join(unsent)}. " if unsent else ""
                    )
                    message = (
                        "Cannot submit assistant messages: the session already "
                        f"has {_ASSISTANT_HANDOVER_MAX_ENTRIES} active or expired "
                        "assistant handover records; handover capacity is full. "
                        f"{unsent_clause}No messages were submitted. Retry after an active handover "
                        "completes or an expired record is released."
                    )
                    controller.add_error(message)
                    completion["error"] = {"message": message}
                    completion["usage"] = {**usage_totals, "stopReason": "error"}
                    completion["completed"] = True
                    _assistant_set_usage(controller, completion["usage"])
                    return
                handover_record = {}
                _assistant_set_state_value(
                    controller, _ASSISTANT_HANDOVER_STATE_KEY, handover_token
                )
                if not await wait_for_handover_capability_delivery():
                    return
                if (read_context and read_context.revoked) or getattr(
                    shared_queue, "revoked", False
                ):
                    return
            if len(body.commands) > 1 and any(
                _assistant_command_type(command) == "add-message"
                and _is_goal_message(_assistant_message_content(command))
                for command in body.commands
            ):
                controller.add_error("Send a goal command on its own.")
                return
            for command_index, command in enumerate(body.commands):
                command_type = _assistant_command_type(command)
                if command_type == "add-message":
                    message_content = _assistant_message_content(command)
                    if not content_to_text(message_content).strip():
                        admission_error = "add-message command has no text"
                        if submitted_contents:
                            admission_error = partial_admission_error(
                                command_index, admission_error
                            )
                        controller.add_error(admission_error)
                        completion["error"] = {"message": admission_error}
                        break
                    message = command.get("message")
                    client_id = message.get("id") if isinstance(message, dict) else None
                    # The page knows its send by the id it made, so that id
                    # follows the send into saved history (#1359); an image goal
                    # also replays by it. Anything unsafe gets a fresh id.
                    request_id = (client_id if isinstance(client_id, str)
                                  and _CLIENT_REQUEST_ID.fullmatch(client_id) else uuid.uuid4().hex)
                    private_goal = _is_goal_message(message_content)
                    kwargs = {"request_id": request_id}
                    if private_goal:
                        uid = body.state.get("conversationUid") if isinstance(body.state, dict) else None
                        kwargs.update(private_goal_reply=True, goal_conversation_uid=uid)
                        if body.goal_reply_id is not None:
                            kwargs["private_goal_reply_id"] = body.goal_reply_id
                    if (read_context and read_context.revoked) or getattr(
                        shared_queue, "revoked", False
                    ):
                        return
                    try:
                        accepted = await session.send_message(message_content, **kwargs)
                    except asyncio.CancelledError:
                        if read_context and read_context.revoked:
                            raise
                        admission_error = partial_admission_error(
                            command_index,
                            "admission wait was cancelled",
                            attempted_request_id=request_id,
                            attempt_may_be_accepted=True,
                        )
                        record_possibly_accepted_submission(
                            request_id, message_content, command
                        )
                        completion["error"] = {"message": admission_error}
                        raise
                    except Exception as exc:
                        if read_context and read_context.revoked:
                            # Revocation wins over uncertain admission too: do
                            # not copy private content into a handover snapshot,
                            # media store, or response after access was removed.
                            return
                        admission_error = str(exc) or type(exc).__name__
                        admission_error = partial_admission_error(
                            command_index,
                            admission_error,
                            attempted_request_id=request_id,
                            attempt_may_be_accepted=True,
                        )
                        record_possibly_accepted_submission(
                            request_id, message_content, command
                        )
                        controller.add_error(admission_error)
                        completion["error"] = {"message": admission_error}
                        break
                    if read_context and read_context.revoked:
                        # Revocation wins before any admission receipt or
                        # private goal receipt is copied into client state.
                        return
                    if not accepted.get("accepted"):
                        admission_error = str(
                            accepted.get("message") or accepted.get("reason", "message rejected")
                        )
                        if submitted_contents:
                            admission_error = partial_admission_error(
                                command_index,
                                admission_error,
                                attempted_request_id=request_id,
                            )
                        controller.add_error(admission_error)
                        completion["error"] = {"message": admission_error}
                        break
                    saw_action = True
                    replayed = private_goal and accepted.get("completed") is True and accepted.get("replayed") is True
                    if replayed:
                        replay_id = str(accepted.get("request_id") or request_id)
                        queue = asyncio.Queue()
                        queue.put_nowait({
                            "type": "token", "text": str(accepted.get("message") or "Goal request already processed."),
                            "request_id": replay_id,
                        })
                        queue.put_nowait({"type": "turn_complete", "stop_reason": "end_turn", "request_id": replay_id})
                        turn_request_id = replay_id
                    elif private_goal:
                        reply_id = accepted.get("goal_reply_id")
                        if not reply_id:
                            controller.add_error("daemon did not provide a private goal reply")
                            return
                        request_ids.add(str(reply_id))
                        turn_request_id = str(reply_id)
                        queue = asyncio.Queue()
                        goal_pump = asyncio.create_task(_pump_goal_reply(session, str(reply_id), queue))
                        if read_context is not None:
                            revocation_watch = asyncio.create_task(_forward_goal_revocation(shared_queue, queue))
                    if accepted.get("goal_request_id") and isinstance(body.state, dict):
                        controller.state["goalSubmission"] = {
                            "requestId": str(accepted["goal_request_id"]),
                            "clientRequestId": client_id,
                        }
                    if isinstance(body.state, dict) and body.state.get("error"):
                        # Cancellation belongs to its previous turn. A new
                        # admitted input must not keep that error as its status.
                        controller.state["error"] = ""
                    if accepted.get("request_id"):
                        accepted_request_id = str(accepted["request_id"])
                        request_ids.add(accepted_request_id)
                        if not private_goal and not replayed:
                            turn_request_id = accepted_request_id
                    else:
                        accepted_request_id = request_id
                        request_ids.add(accepted_request_id)
                        if not private_goal and not replayed:
                            turn_request_id = accepted_request_id
                            untagged_request_ids.add(accepted_request_id)
                    admitted_turn_request_ids.add(turn_request_id)
                    submitted_turn_request_ids.append(turn_request_id)
                    submitted_contents.append(message_content)
                    submitted_media.append(_save_sent_media(message_content, session_id))
                    submitted_request_ids.append(request_id)
                    row_id = stable_user_row_id(command.get("rowId"))
                    submitted_row_ids.append(row_id)
                    if queue_from_ledger:
                        if turn_request_id not in resumed_request_order:
                            resumed_request_order.append(turn_request_id)
                        resumed_rows[turn_request_id] = {
                            "request_id": turn_request_id,
                            "client_request_id": request_id,
                            "row_id": row_id,
                        }
                    store_handover_snapshot()
                    if read_context and read_context.revoked:
                        return
                    if len(submitted_contents) > 1 and untagged_request_ids:
                        # Keep admitting the whole command batch. Request IDs
                        # were still sent with each message, so exact IDs on
                        # later events can recover ownership; if they cannot,
                        # the bounded correlation failure below reports the
                        # accepted messages without guessing or retrying.
                        correlation_deadline = (
                            _assistant_correlation_monotonic()
                            + EVENT_IDLE_TIMEOUT_SECONDS
                        )
                elif command_type == "add-tool-result":
                    call_id, result, decision, scope = _assistant_tool_result(command)
                    if not call_id:
                        controller.add_error(
                            "add-tool-result command has no toolCallId"
                        )
                        return
                    state_tool_id, state_permission_id = _assistant_permission_identity(
                        state_messages, call_id
                    )
                    result_tool_id = (
                        result.get("tool_id") if isinstance(result, dict) else None
                    )
                    result_permission_id = (
                        result.get("permission_id") if isinstance(result, dict) else None
                    )
                    if (
                        state_permission_id
                        and result_permission_id
                        and state_permission_id != result_permission_id
                    ):
                        message = (
                            "Permission response owner does not match the displayed prompt."
                        )
                        if handover_record is not None:
                            reject_handover(message)
                        else:
                            controller.add_error(message)
                        return
                    source_tool_id = (
                        state_tool_id
                        or (result_tool_id if isinstance(result_tool_id, str) else None)
                        or permission_tool_ids.get(call_id)
                    )
                    if not source_tool_id:
                        message = (
                            "Permission response is missing its tool owner; refresh the chat "
                            "before answering."
                        )
                        if handover_record is not None:
                            reject_handover(message)
                        else:
                            controller.add_error(message)
                        return
                    if not decision:
                        decision = "approve" if result is True else "deny"
                    if decision not in ("approve", "deny"):
                        controller.add_error(
                            "permission decision must be approve or deny"
                        )
                        return
                    option_id = result.get("option_id") if isinstance(result, dict) else None
                    supplied_permission_id = (
                        str(result.get("permission_id"))
                        if isinstance(result, dict)
                        and result.get("permission_id") is not None
                        and str(result.get("permission_id"))
                        else None
                    )
                    permission_id = supplied_permission_id or state_permission_id or None
                    known_permission_ids = resumed_prompt_permission_ids.get(source_tool_id, set())
                    if handover_record is not None:
                        known_prompt_tools = {
                            tool_id
                            for tool_ids in resumed_prompt_tools.values()
                            for tool_id in tool_ids
                        }
                        if source_tool_id not in known_prompt_tools:
                            reject_handover(
                                "Permission result does not match an accepted pending "
                                "prompt; no permission was resolved."
                            )
                            return
                        if permission_id is not None:
                            if permission_id not in known_permission_ids:
                                reject_handover(
                                    "Permission result did not match a server-recorded "
                                    "permission prompt; no permission was resolved."
                                )
                                return
                        elif len(known_permission_ids) == 1:
                            permission_id = next(iter(known_permission_ids))
                        elif len(known_permission_ids) > 1:
                            reject_handover(
                                "Permission result is ambiguous because the tool ID has "
                                "multiple pending prompts; include the matching permission_id."
                            )
                            return
                    decision_record = {
                        "decision": decision,
                        "scope": scope,
                        "option_id": str(option_id) if option_id is not None else None,
                        "permission_id": permission_id,
                    }
                    decision_key = permission_id or f"unidentified:{source_tool_id}"
                    previous_decision = permission_decisions.get(decision_key)
                    if handover_record is not None:
                        if previous_decision is not None and any(
                            previous_decision.get(key) != value
                            for key, value in decision_record.items()
                        ):
                            reject_handover(
                                "Permission handover was already attempted with a "
                                "different decision; the conflicting replay was rejected."
                            )
                            return
                        if previous_decision is None:
                            permission_decisions[decision_key] = {
                                **decision_record,
                                "status": "attempted",
                            }
                            store_handover_snapshot()
                    if not isinstance(permission_id, str) or not permission_id:
                        message = (
                            "Permission response was not submitted because the accepted "
                            f"prompt for tool_id '{source_tool_id}' has no unique "
                            "server-recorded permission_id."
                        )
                        if handover_record is None:
                            controller.add_error(message)
                            completion["error"] = {"message": message}
                            completion["usage"] = {
                                **usage_totals,
                                "stopReason": "error",
                            }
                            completion["completed"] = True
                            _assistant_set_usage(controller, completion["usage"])
                            _assistant_set_state_value(controller, "error", message)
                            return
                        if previous_decision is None:
                            permission_decisions[decision_key] = {
                                **decision_record,
                                "status": "not_submitted",
                            }
                        permission_resolution_errors[decision_key] = message
                        pending_request_ids = sorted(
                            request_id
                            for request_id, tool_ids in resumed_prompt_tools.items()
                            if source_tool_id in tool_ids
                        )
                        permission_resolution_pending[decision_key] = {
                            "tool_id": source_tool_id,
                            "permission_id": None,
                            "decision": decision,
                            "request_ids": pending_request_ids,
                            "attempted": False,
                        }
                        permission_resolution_deadline = (
                            _assistant_correlation_monotonic()
                            + EVENT_IDLE_TIMEOUT_SECONDS
                        )
                        store_handover_snapshot()
                        saw_action = True
                        continue
                    if previous_decision is not None and previous_decision.get("status") == "resolved":
                        resolved = True
                    else:
                        try:
                            permission_kwargs = {"permission_id": permission_id}
                            if option_id is not None:
                                permission_kwargs["option_id"] = str(option_id)
                            resolved = await session.resolve_permission(
                                source_tool_id,
                                decision,
                                scope,
                                **permission_kwargs,
                            )
                        except Exception:
                            # The daemon may have consumed the decision before
                            # this route lost its connection. The capability
                            # retains the attempted value for an idempotent retry.
                            store_handover_snapshot()
                            raise
                        if resolved and handover_record is not None:
                            permission_decisions[decision_key] = {
                                **decision_record,
                                "status": "resolved",
                            }
                            permission_resolution_errors.pop(decision_key, None)
                            store_handover_snapshot()
                    saw_action = True
                    if not resolved:
                        if handover_record is not None:
                            pending_request_ids = sorted(
                                resumed_permission_request_ids.get(
                                    permission_id, set()
                                )
                            )
                            permission_resolution_pending[decision_key] = {
                                "tool_id": source_tool_id,
                                "permission_id": permission_id,
                                "decision": decision,
                                "request_ids": pending_request_ids,
                                "attempted": True,
                            }
                            permission_resolution_deadline = (
                                _assistant_correlation_monotonic()
                                + EVENT_IDLE_TIMEOUT_SECONDS
                            )
                            if (
                                permission_id is None
                                and decision_key not in permission_resolution_errors
                            ):
                                permission_resolution_errors[decision_key] = (
                                    "Permission resolution could not be confirmed because "
                                    "the accepted prompt has no unique permission_id."
                                )
                            store_handover_snapshot()
                            continue
                        pending_permission_ids = sorted(
                            admitted_turn_request_ids - completed_request_ids
                        )
                        pending_detail = (
                            f"Accepted message(s) {', '.join(pending_permission_ids)} "
                            "may still be running."
                            if pending_permission_ids
                            else "The accepted turn may still be running."
                        )
                        message = (
                            "Permission resolution could not be confirmed for "
                            f"tool_id '{source_tool_id}'; the decision may or may "
                            f"not have taken effect. {pending_detail} "
                            "No messages were resubmitted."
                        )
                        controller.add_error(message)
                        completion["error"] = {"message": message}
                        completion["usage"] = {
                            **usage_totals,
                            "stopReason": "error",
                        }
                        completion["completed"] = True
                        _assistant_set_usage(controller, completion["usage"])
                        _assistant_set_state_value(controller, "error", message)
                        return
                    answered_prompts.add((source_tool_id, permission_id))
                    store_handover_snapshot()
                else:
                    controller.add_error(
                        f"Unsupported assistant command: {command_type or 'unknown'}"
                    )
                    return

            if not saw_action and not submitted_contents:
                if not admission_error:
                    admission_error = "assistant request has no commands"
                    controller.add_error(admission_error)
                if completion.get("error") is None:
                    completion["error"] = {"message": admission_error}
                if completion.get("error") is not None:
                    completion["usage"] = {
                        **usage_totals,
                        "stopReason": "error",
                    }
                    completion["completed"] = True
                    _assistant_set_usage(controller, completion["usage"])
                return

            if not submitted_contents:
                # Answering a permission prompt continues the last assistant
                # message. Its tool calls started in the previous run, so adopt
                # them to finish those rows (result, duration) in place.
                for part in _assistant_continued_tool_parts(state_messages):
                    if part.get("toolCallId"):
                        assistant_tool_parts[part["toolCallId"]] = part
                        assistant_parts.append(part)
            elif persist_message_state:
                # The page drops its pending copy of a send at the first state
                # it receives, and clearing the last turn's error above is one.
                # A daemon busy with another turn stays quiet long after it
                # admits the send, so show the send's row now (#1359). The
                # reply's row follows with its first output: until then the
                # page keeps the placeholder it already draws, and the first
                # words still publish at once (the throttle has not started).
                admitted = _assistant_update_message_state(
                    [dict(message) for message in state_messages],
                    session_id,
                    submitted_contents,
                    [],
                    None,
                    copy.deepcopy(submitted_media),
                    list(submitted_request_ids),
                    list(submitted_row_ids),
                )[:-1]
                controller.state["messages"].extend(admitted[len(sent_messages):])
                sent_messages = admitted

            while True:
                if (read_context and read_context.revoked) or getattr(
                    queue, "revoked", False
                ):
                    return
                record_event_loss()
                event_timeout = held_text_due()
                if correlation_deadline is not None:
                    deadline_remaining = max(
                        0.0,
                        correlation_deadline - _assistant_correlation_monotonic(),
                    )
                    event_timeout = (
                        deadline_remaining
                        if event_timeout is None
                        else min(event_timeout, deadline_remaining)
                    )
                if permission_resolution_deadline is not None:
                    permission_remaining = max(
                        0.0,
                        permission_resolution_deadline
                        - _assistant_correlation_monotonic(),
                    )
                    event_timeout = (
                        permission_remaining
                        if event_timeout is None
                        else min(event_timeout, permission_remaining)
                    )
                event = await _assistant_next_event(
                    queue, controller, timeout=event_timeout
                )
                if event is None or (read_context and read_context.revoked):
                    return
                if getattr(queue, "revoked", False):
                    return
                record_event_loss()
                if not event:
                    if (
                        permission_resolution_deadline is not None
                        and _assistant_correlation_monotonic()
                        >= permission_resolution_deadline
                        and permission_resolution_pending
                    ):
                        fail_correlation(
                            permission_resolution_error_message(all_terminal=False)
                        )
                        return
                    if (
                        correlation_deadline is not None
                        and _assistant_correlation_monotonic()
                        >= correlation_deadline
                    ):
                        fail_correlation(
                            (
                                f"{event_loss_message} No safely identified terminal "
                                "event arrived after the stream gap; accepted messages "
                                "may still be running. No messages were resubmitted."
                                if event_loss_seen
                                else correlation_failure_message(
                                    "no safely identified terminal event arrived before "
                                    f"the {EVENT_IDLE_TIMEOUT_SECONDS:g}s correlation deadline"
                                )
                            )
                        )
                        return
                    # The daemon went quiet after streaming (e.g. before
                    # turn_complete): publish the text the throttle held.
                    publish_progress()
                    continue
                event_type = str(event.get("type") or "")
                named_event_ids = _event_request_ids(event)
                correlated_pending_ids = named_event_ids & (
                    admitted_turn_request_ids - completed_request_ids
                )
                correlated_activity = event_type in (
                    _TURN_OUTPUT | _PERMISSION_ANSWERS
                    | {"error", "turn_start", "turn_complete"}
                )
                if (
                    correlation_deadline is not None
                    and correlated_pending_ids
                    and correlated_activity
                ):
                    # Reset only for safely correlated activity. A busy but
                    # unrelated daemon turn must not hold this run open.
                    correlation_deadline = (
                        _assistant_correlation_monotonic()
                        + EVENT_IDLE_TIMEOUT_SECONDS
                    )
                if permission_resolution_pending and correlated_pending_ids and correlated_activity:
                    permission_resolution_deadline = (
                        _assistant_correlation_monotonic()
                        + EVENT_IDLE_TIMEOUT_SECONDS
                    )
                multiple_untracked = (
                    len(submitted_contents) > 1
                    and bool(untagged_request_ids - completed_request_ids)
                )
                if multiple_untracked and not named_event_ids:
                    if event_type in {"turn_complete", "error"}:
                        fail_correlation(
                            correlation_failure_message(
                                f"the daemon emitted an unnamed {event_type} event"
                            )
                        )
                        return
                    if event_type in _TURN_OUTPUT:
                        # Never put unowned shared-stream output into one of
                        # this request's assistant rows.
                        continue
                event_turn_ids = named_event_ids & admitted_turn_request_ids
                if event_turn_ids and event_type in _TURN_OUTPUT | {"error"}:
                    current_turn_request_ids.clear()
                    current_turn_request_ids.update(event_turn_ids)
                elif (
                    event_type in _TURN_OUTPUT | {"error"}
                    and not named_event_ids
                    and len(admitted_turn_request_ids - completed_request_ids) == 1
                    and not multiple_untracked
                ):
                    current_turn_request_ids = (
                        admitted_turn_request_ids - completed_request_ids
                    )
                tagged = bool(request_ids) and not (
                    untagged_request_ids - completed_request_ids
                )
                if _foreign_completion(event, request_ids, tagged):
                    continue
                if _foreign_error(event, request_ids) or _foreign_output(event, request_ids, tagged):
                    continue

                if event_type == "token":
                    token = str(event.get("text") or "")
                    controller.append_text(token)
                    add_streamed_text("text", token)
                elif event_type == "response":
                    # The daemon's clean text for this reply: streamed tokens
                    # carried the raw XML tool tags, which never render as
                    # text. Drop the trailing streamed text part and put the
                    # clean text in its place, before the tool cards arrive.
                    clean_text = str(event.get("text") or "")
                    while assistant_parts and assistant_parts[-1]["type"] == "text":
                        assistant_parts.pop()
                    if clean_text:
                        assistant_parts.append({"type": "text", "text": clean_text})
                    publish_progress()
                elif event_type == "thinking":
                    thinking = str(event.get("text") or "")
                    controller.append_reasoning(thinking)
                    add_streamed_text("reasoning", thinking)
                elif event_type == "acp_update":
                    update = event.get("update") or {}
                    if (
                        not isinstance(update, dict)
                        or update.get("sessionUpdate")
                        not in {"tool_call", "tool_call_update"}
                    ):
                        continue
                    tool_id = str(update.get("toolCallId") or "")
                    if not tool_id:
                        continue
                    tool_part = assistant_tool_parts.get(tool_id)
                    if tool_part is None:
                        tool_part = _assistant_harness_tool_part(update)
                        assistant_tool_parts[tool_id] = tool_part
                        assistant_parts.append(tool_part)
                        tool_controller = await controller.add_tool_call("harness_tool", tool_id)
                        tool_controllers[tool_id] = tool_controller
                        tool_controller.append_args_text(tool_part["argsText"])
                    else:
                        _assistant_harness_tool_part(update, tool_part)
                    if not tool_part["isPreliminary"]:
                        tool_controller = tool_controllers.pop(tool_id, None)
                        if tool_controller is not None:
                            _assistant_set_tool_result(tool_controller, tool_part["result"])
                    publish_progress()
                elif event_type == "tool_start":
                    if event.get("acp_passthrough"):
                        continue
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
                        "timing": {"startedAt": _now_ms()},
                    }
                    assistant_tool_parts[tool_id] = tool_part
                    assistant_parts.append(tool_part)
                    tool_controller.append_args_text(tool_part["argsText"])
                    publish_progress()
                elif event_type == "permission_request":
                    source_tool_id = str(event.get("tool_id") or "")
                    if not source_tool_id:
                        continue
                    permission_id = _assistant_permission_id(event)
                    details = event.get("details") if isinstance(event.get("details"), dict) else {}
                    prompt_permission_ids = resumed_prompt_permission_ids.setdefault(
                        source_tool_id, set()
                    )
                    if permission_id is not None:
                        prompt_permission_ids.add(permission_id)
                    prompt_request_ids = (
                        event_turn_ids
                        or current_turn_request_ids
                        & (admitted_turn_request_ids - completed_request_ids)
                    )
                    if not prompt_request_ids:
                        pending_turn_ids = (
                            admitted_turn_request_ids - completed_request_ids
                        )
                        if len(pending_turn_ids) == 1:
                            prompt_request_ids = pending_turn_ids
                    if not prompt_request_ids and len(
                        admitted_turn_request_ids - completed_request_ids
                    ) > 1:
                        fail_correlation(
                            correlation_failure_message(
                                "the daemon emitted a permission request without a "
                                "safe turn identity"
                            )
                        )
                        return
                    if permission_id is not None:
                        resumed_permission_request_ids.setdefault(
                            permission_id, set()
                        ).update(prompt_request_ids)
                    for request_id in prompt_request_ids:
                        resumed_prompt_tools.setdefault(request_id, set()).add(
                            source_tool_id
                        )
                        resumed_prompt_request_ids.add(request_id)
                    permission_call_id = (
                        f"permission_{source_tool_id}_{permission_id}"
                        if permission_id
                        else f"permission_{source_tool_id}"
                    )
                    permission_tool_ids[permission_call_id] = source_tool_id
                    permission_controller = await controller.add_tool_call(
                        "request_permission", permission_call_id
                    )
                    permission_controllers[(source_tool_id, str(permission_id or ""))] = permission_controller
                    permission_args = {
                        "tool_id": source_tool_id,
                        **(
                            {"request_id": event["request_id"]}
                            if isinstance(event.get("request_id"), str)
                            and event["request_id"]
                            else {}
                        ),
                        **({"permission_id": permission_id} if permission_id else {}),
                        "tool_name": event.get("tool_name"),
                        "risk_level": event.get("risk_level"),
                        "risk_reason": event.get("risk_reason"),
                        "input": event.get("input") or {},
                        "details": details,
                        "acp_options": details.get("acp_options", []),
                    }
                    permission_part = {
                        "type": "tool-call",
                        "toolCallId": permission_call_id,
                        "toolName": "request_permission",
                        "args": permission_args,
                    }
                    permission_part["argsText"] = json.dumps(
                        permission_part["args"], ensure_ascii=False
                    )
                    assistant_tool_parts[permission_call_id] = permission_part
                    assistant_parts.append(permission_part)
                    permission_controller.append_args_text(permission_part["argsText"])
                    if persist_message_state:
                        _assistant_set_messages(
                            controller,
                            build_messages_for_turn(
                                {"type": "requires-action", "reason": "tool-calls"}
                            ),
                        )
                    if handover_token and handover_claim_id:
                        preserve_handover_queue = True
                        store_handover_snapshot()
                    # Return this run after exposing the prompt.  The daemon is
                    # blocked until a later add-tool-result request answers it;
                    # keeping this stream open would deadlock that follow-up.
                    return
                elif event_type == "tool_result":
                    if event.get("acp_passthrough"):
                        continue
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
                        started = (tool_part.get("timing") or {}).get("startedAt")
                        if started is not None:
                            tool_part["timing"] = {
                                "startedAt": started,
                                "completedAt": _tool_completed_ms(started, event),
                            }
                    if tool_controller is not None:
                        _assistant_set_tool_result(tool_controller, result)
                    elif tool_part is None:
                        controller.add_tool_result(tool_id, result)
                    # else: adopted from the prior run; this run's wire stream
                    # never opened that call, so the state publish carries it.
                    publish_progress()
                elif event_type in ("permission_granted", "permission_denied", "permission_cancelled"):
                    source_tool_id = str(event.get("tool_id") or "")
                    event_permission_id = _assistant_permission_id(event)
                    pending_resolution = None
                    pending_resolution_key = None
                    if event_permission_id is not None:
                        pending_resolution = permission_resolution_pending.get(
                            event_permission_id
                        )
                        if pending_resolution is not None:
                            pending_resolution_key = event_permission_id
                    if pending_resolution is None:
                        for key, candidate in permission_resolution_pending.items():
                            if candidate.get("tool_id") == source_tool_id:
                                pending_resolution = candidate
                                pending_resolution_key = key
                                break
                    if pending_resolution is not None:
                        expected_permission_id = pending_resolution.get("permission_id")
                        owner_request_ids = set(
                            pending_resolution.get("request_ids") or ()
                        )
                        event_request_ids = _event_request_ids(event)
                        if (
                            source_tool_id != pending_resolution.get("tool_id")
                            or expected_permission_id is None
                            or event_permission_id != expected_permission_id
                            or (
                                owner_request_ids
                                and event_request_ids
                                and not owner_request_ids.intersection(event_request_ids)
                            )
                        ):
                            # Tool IDs can be reused. An outcome without the
                            # exact server-issued prompt ID cannot settle this
                            # attempted permission response.
                            continue
                        expected_decision = str(
                            pending_resolution.get("decision") or ""
                        )
                        observed_decision = (
                            "approve"
                            if event_type == "permission_granted"
                            else "deny"
                            if event_type == "permission_denied"
                            else "cancelled"
                        )
                        if observed_decision == expected_decision:
                            previous = permission_decisions.get(
                                pending_resolution_key or ""
                            )
                            if previous is not None:
                                permission_decisions[pending_resolution_key] = {
                                    **previous,
                                    "status": "resolved",
                                }
                            permission_resolution_errors.pop(
                                pending_resolution_key or "", None
                            )
                            answered_prompts.add(
                                (source_tool_id, event_permission_id)
                            )
                            _assistant_set_state_value(controller, "error", "")
                        else:
                            permission_resolution_errors[pending_resolution_key or ""] = (
                                "Observed permission outcome "
                                f"'{observed_decision}' for tool_id '{source_tool_id}' "
                                f"did not confirm attempted '{expected_decision}'."
                            )
                        permission_resolution_pending.pop(
                            pending_resolution_key, None
                        )
                        if not permission_resolution_pending:
                            permission_resolution_deadline = None
                        store_handover_snapshot()
                    permission_id = event_permission_id
                    permission_result = _permission_result_from_event(event)
                    permission_call_id = (
                        f"permission_{source_tool_id}_{permission_id}"
                        if permission_id else f"permission_{source_tool_id}"
                    )
                    permission_part = assistant_tool_parts.get(
                        permission_call_id
                    )
                    part_args = permission_part.get("args") if isinstance(permission_part, dict) else None
                    part_permission_id = part_args.get("permission_id") if isinstance(part_args, dict) else None
                    if permission_part is not None and permission_id == part_permission_id:
                        permission_part.update(
                            {
                                "result": permission_result,
                                "isError": permission_result["decision"] == "deny",
                            }
                        )
                    else:
                        # A card an earlier run showed, answered by this run's
                        # message (a typed yes or no answers a parked card).
                        _assistant_close_shown_prompt(
                            state_messages,
                            permission_call_id,
                            permission_result,
                            permission_id if isinstance(permission_id, str) else None,
                        )
                    permission_key = (source_tool_id, str(permission_id or ""))
                    permission_controller = permission_controllers.pop(permission_key, None)
                    if permission_controller is not None:
                        _assistant_set_tool_result(
                            permission_controller,
                            permission_result,
                        )
                    publish_progress()
                    if (
                        event.get("parked")
                        and event_type != "permission_granted"
                        and permission_key in answered_prompts
                        and not submitted_contents
                    ):
                        # A prompt its turn left open, answered no: no turn
                        # follows it (an Allow goes on with one).
                        _assistant_update_message_state(
                            state_messages,
                            session_id,
                            submitted_contents,
                            assistant_parts,
                            {"type": "complete", "reason": "stop"},
                            submitted_media,
                            submitted_request_ids,
                            submitted_row_ids,
                        )
                        if persist_message_state:
                            _assistant_set_messages(controller, state_messages)
                        return
                elif event_type == "error":
                    turn_error = str(event.get("message") or "") or "engine error"
                    # Structured classification rides the event untouched so
                    # clients classify by code/class, never message text
                    # (#549, #720).
                    turn_error_fields = {
                        key: event[key]
                        for key in ("error_class", "code", "provider", "retryable", "cause")
                        if event.get(key) is not None
                    }
                    # The run-status surface must carry the structured fields
                    # even when no message state persists — controller.add_error
                    # is display text only (#720).
                    completion["error"] = {
                        "message": turn_error, **turn_error_fields,
                    }
                    if not persist_message_state:
                        # No message state to carry it: fail the run instead.
                        controller.add_error(turn_error)
                elif event_type == "turn_complete":
                    if admitted_turn_request_ids:
                        # A turn may answer one queued send or several sends
                        # that the daemon batched together. Retire only the
                        # ids this completion actually names.
                        named = _event_request_ids(event)
                        completed_now = named & admitted_turn_request_ids
                        if not named:
                            pending_untagged = (
                                untagged_request_ids - completed_request_ids
                            )
                            if len(pending_untagged) == 1:
                                completed_now = pending_untagged
                        completed_now -= completed_request_ids
                        if not completed_now:
                            continue
                        completed_request_ids.update(completed_now)
                        pending_correlation_ids = (
                            (untagged_request_ids | possibly_accepted_request_ids)
                            - completed_request_ids
                        )
                        pending_gap_ids = (
                            admitted_turn_request_ids - completed_request_ids
                            if event_loss_seen
                            else set()
                        )
                        if not pending_correlation_ids and not pending_gap_ids:
                            correlation_deadline = None
                        turn_status = (
                            {
                                "type": "incomplete", "reason": "error",
                                "error": turn_error, **turn_error_fields,
                            }
                            if turn_error
                            else {"type": "complete", "reason": "stop"}
                        )
                        completed_assistant_turns.append(
                            {
                                "parts": copy.deepcopy(assistant_parts),
                                "status": turn_status,
                                "request_ids": sorted(completed_now),
                                "message_id": assistant_row_id(completed_now),
                            }
                        )
                        usage_totals["inputTokens"] += int(
                            event.get("input_tokens", 0) or 0
                        )
                        usage_totals["outputTokens"] += int(
                            event.get("output_tokens", 0) or 0
                        )
                        usage_totals["toolCalls"] += int(
                            event.get("tool_calls", 0) or 0
                        )
                        if turn_error and run_error is None:
                            run_error = {
                                "message": turn_error, **turn_error_fields,
                            }
                            completion["error"] = run_error
                        final_stop_reason = (
                            "error" if run_error or completion.get("error")
                            else str(event.get("stop_reason") or "end_turn")
                        )
                        assistant_parts.clear()
                        current_turn_request_ids.clear()
                        assistant_tool_parts.clear()
                        tool_controllers.clear()
                        permission_controllers.clear()
                        permission_tool_ids.clear()
                        answered_prompts.clear()
                        turn_error = ""
                        turn_error_fields = {}

                        if not admitted_turn_request_ids.issubset(
                            completed_request_ids
                        ):
                            publish_progress()
                            continue

                        # The last completion closes the response. Keep the
                        # usage total and all turn rows in admission order.
                        permission_error = ""
                        if permission_resolution_pending or permission_resolution_errors:
                            permission_error = permission_resolution_error_message(
                                all_terminal=True
                            )
                            completion["error"] = {"message": permission_error}
                            final_stop_reason = "error"
                            controller.add_error(permission_error)
                            _assistant_set_state_value(
                                controller, "error", permission_error
                            )
                        completion["usage"] = {
                            **usage_totals,
                            "stopReason": final_stop_reason,
                        }
                        completion["completed"] = True
                        preserve_handover_queue = False
                        mark_turn_incomplete = bool(
                            event_loss_seen
                            or permission_error
                            or (
                                admission_error
                                and (
                                    not queue_from_ledger
                                    or possibly_accepted_request_ids
                                    - completed_request_ids
                                )
                            )
                        )
                        if mark_turn_incomplete and completed_assistant_turns:
                            order = resumed_request_order + submitted_turn_request_ids
                            latest_turn = max(
                                completed_assistant_turns,
                                key=lambda turn: min(
                                    (
                                        index
                                        for index, request_id in enumerate(order)
                                        if request_id in set(turn.get("request_ids") or ())
                                    ),
                                    default=len(order),
                                ),
                            )
                            latest_turn["status"] = {
                                "type": "incomplete",
                                "reason": "error",
                                "error": (
                                    admission_error
                                    or event_loss_message
                                    or permission_error
                                ),
                            }
                        final_messages = build_messages_for_turn()
                    else:
                        # Permission answers can continue a turn without a new
                        # user send; preserve their existing single-row update.
                        completion["usage"] = {
                            "inputTokens": int(event.get("input_tokens", 0) or 0),
                            "outputTokens": int(event.get("output_tokens", 0) or 0),
                            "toolCalls": int(event.get("tool_calls", 0) or 0),
                            "stopReason": "error"
                            if turn_error
                            else event.get("stop_reason", "end_turn"),
                        }
                        completion["completed"] = True
                        final_messages = _assistant_update_message_state(
                            state_messages,
                            session_id,
                            submitted_contents,
                            assistant_parts,
                            (
                                {
                                    "type": "incomplete", "reason": "error",
                                    "error": turn_error, **turn_error_fields,
                                }
                                if turn_error
                                else {"type": "complete", "reason": "stop"}
                            ),
                            submitted_media,
                            submitted_request_ids,
                            submitted_row_ids,
                        )
                    if persist_message_state:
                        if goal_pump is not None:
                            # Other goal commands may have reached history in
                            # between this page's request and its result. A
                            # total history count is not a prefix of the rows
                            # this stream rendered. Ask the page to reload the
                            # canonical history once it is idle instead. The
                            # daemon is not asked for history: the full reload
                            # supersedes any count this stream could set.
                            _assistant_set_messages(controller, final_messages)
                            controller.state["synced"] = None
                            if event.get("history_recorded"):
                                controller.state["goalHistoryReloadRequired"] = True
                        else:
                            # Publish the finished turn first: the page must
                            # not wait on history-refresh RPCs (up to 2, ~3s
                            # worst case) to see the completed status — that
                            # spun the completion spinner on a slow daemon.
                            # Provisional ids are fine for this publish; the
                            # refresh below records source indexes and
                            # republishes the aligned rows before any synced
                            # claim (#666: never claim synced for rows the page
                            # has not been shown). The snapshot only decides
                            # the sync claim; alignment mutates these published
                            # rows in place (#675).
                            _assistant_set_messages(controller, final_messages)
                            refreshed = None
                            for _attempt in (0, 1):
                                try:
                                    refreshed = await session.refresh_history(
                                        stale_raises=True
                                    )
                                except Exception as exc:
                                    logger.debug(
                                        "history refresh for %s failed: %s",
                                        session_id, exc,
                                    )
                                    refreshed = None
                                if refreshed:
                                    break
                            aligned = False
                            if refreshed:
                                aligned = _assistant_align_streamed_ids(
                                    final_messages, refreshed
                                )
                            if aligned:
                                # Each streamed row carries its source index
                                # in exactly this snapshot, so the page can
                                # recognize canonical rows without replacing
                                # their ids. Republish the metadata before the
                                # watermark is claimed (#767/#666).
                                _assistant_set_messages(
                                    controller, final_messages
                                )
                                controller.state["synced"] = {
                                    "history": len(refreshed),
                                    "messages": len(final_messages),
                                }
                            else:
                                # No snapshot this stream reconciled against
                                # (both refreshes failed, or the mirror the
                                # retry returned missed the turn). Claim
                                # nothing: claiming it would let the page
                                # merge canonical rows alongside the
                                # provisional ones and redraw the turn until
                                # a full reload. The page takes its full load.
                                controller.state["synced"] = None
                    _assistant_set_usage(controller, completion["usage"])
                    return
                elif event_type == "daemon_closed":
                    controller.add_error("session daemon exited")
                    return
        except Exception as exc:
            logger.exception("assistant transport failed for %s", session_id)
            controller.add_error(str(exc))
        finally:
            tasks = [task for task in (goal_pump, revocation_watch) if task is not None]
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            pending_handover_ids = (
                admitted_turn_request_ids - completed_request_ids
            )
            handover_revoked = bool(
                (read_context and read_context.revoked)
                or getattr(shared_queue, "revoked", False)
            )
            keep_handover = bool(
                handover_token
                and handover_claim_id
                and not handover_revoked
                and (preserve_handover_queue or pending_handover_ids)
            )
            if handover_token and handover_claim_id:
                if keep_handover:
                    store_handover_snapshot()
                    _assistant_release_handover(
                        session,
                        handover_token,
                        handover_claim_id,
                        handover_record,
                    )
                    preserve_handover_queue = True
                else:
                    _assistant_finish_handover(
                        session, handover_token, handover_claim_id
                    )
                    handover_claim_id = None
                    _assistant_set_state_value(
                        controller, _ASSISTANT_HANDOVER_STATE_KEY, None
                    )
            if subscribed_here and shared_queue is not None and not preserve_handover_queue:
                session.unsubscribe(shared_queue)

    stream = create_run(run_callback, state=body.state)
    protocol_stream = _assistant_protocol_stream(stream, completion)

    class _OwnedAssistantTransportResponse(AssistantTransportResponse):
        async def stream_response(self, send):
            handover_delivery["enabled"] = True

            async def send_with_handover_tracking(message):
                await send(message)
                token = handover_delivery.get("token")
                if token and message.get("type") == "http.response.body":
                    payload = message.get("body", b"")
                    wire = (
                        payload
                        if isinstance(payload, bytes)
                        else str(payload).encode("utf-8")
                    )
                    if str(token).encode("ascii") in wire:
                        handover_delivery["sent"].set()

            try:
                await super().stream_response(send_with_handover_tracking)
            finally:
                # Cancellation can arrive during downstream send, outside the
                # generators. Close every owned layer explicitly so create_run
                # releases its callback/subscription without stopping the daemon.
                with anyio.CancelScope(shield=True):
                    async with AsyncExitStack() as closing:
                        closing.push_async_callback(stream.aclose)
                        closing.push_async_callback(protocol_stream.aclose)
                        closing.push_async_callback(self.body_iterator.aclose)

    response = _OwnedAssistantTransportResponse(protocol_stream)
    return response


class CancelRequest(BaseModel):
    request_id: str = Field(min_length=1, strict=True)


@router.post("/{session_id}/cancel")
async def cancel_turn(session_id: str):
    registry = get_session_registry()
    session = registry.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    session.cancel_turn()
    return {"ok": True, "session_id": session_id}


@router.post("/{session_id}/cancel-owned")
async def cancel_owned_turn(session_id: str, body: CancelRequest):
    registry = get_session_registry()
    session = registry.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    result = await session.cancel_owned_turn(body.request_id)
    return {**result, "ok": True, "session_id": session_id}
