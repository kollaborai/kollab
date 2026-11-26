"""Resume and branch session changes reach existing chat stream clients."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from kollabor.state.goal_replies import GoalReplyMailbox
from kollabor.state.handlers import register_state_handlers
from kollabor.state.local import LocalStateService
from kollabor.state.web_message_queue_runtime import register_web_message_queue_runtime
from kollabor_ai.conversation_logger import KollaborConversationLogger
from kollabor_ai.conversation_manager import ConversationManager
from kollabor_events import EventBus
from kollabor_events.data_models import ConversationMessage
from kollabor_rpc import RpcCallerContext, RpcServer
from kollabor_tui.display_tap import DisplayTap


def _manager_config(conversations_dir):
    return SimpleNamespace(
        _conversations_dir=conversations_dir,
        get=lambda _key, default=None: default,
    )


def _state_with_saved_session(tmp_path, *, saved_message_count=1):
    logger = KollaborConversationLogger(tmp_path)
    logger.reset_session("current-before")
    manager = ConversationManager(_manager_config(tmp_path), logger)
    saved_at = datetime(2026, 11, 26, tzinfo=timezone.utc).isoformat()
    saved_messages = [
        {
            "uuid": f"saved-message-{index}",
            "role": "user",
            "content": (
                "saved session transcript"
                if index == 0
                else f"saved page row {index}"
            ),
            "timestamp": datetime(2026, 11, 26, 0, 0, index, tzinfo=timezone.utc).isoformat(),
            "parent_uuid": None,
            "metadata": {"request_id": f"saved-session-input-{index}"},
            "session_id": "saved-session",
        }
        for index in range(saved_message_count)
    ]
    manager.current_session_id = "saved-session"
    manager.conversation_metadata = {
        "conversation_uid": "conversation-saved",
        "started_at": saved_at,
        "message_count": len(saved_messages),
        "turn_count": 1,
        "topics": [],
        "model_used": None,
    }
    manager.messages = saved_messages
    manager.message_index = {message["uuid"]: message for message in saved_messages}
    manager.context_window = saved_messages
    assert manager.save_session("saved-session")

    old_message = {
        "uuid": "old-message",
        "role": "user",
        "content": "old session transcript",
        "timestamp": saved_at,
        "parent_uuid": None,
        "metadata": {"request_id": "old-session-input"},
        "session_id": "current-before",
    }
    manager.current_session_id = "current-before"
    manager.conversation_metadata = {
        "conversation_uid": "conversation-before",
        "started_at": saved_at,
        "message_count": 1,
        "turn_count": 1,
        "topics": [],
        "model_used": None,
    }
    manager.messages = [old_message]
    manager.message_index = {old_message["uuid"]: old_message}
    manager.context_window = [old_message]
    logger.reset_session("current-before")
    llm = SimpleNamespace(
        conversation_manager=manager,
        conversation_logger=logger,
        conversation_history=[
            ConversationMessage(
                role="user",
                content="old session transcript",
                metadata={"request_id": "old-session-input"},
            )
        ],
        session_stats={"messages": 1},
    )
    bus = EventBus()
    tap = DisplayTap()
    bus.register_service("display_tap", tap)
    state = LocalStateService(llm, None, event_bus=bus)
    tap.conversation_source = state.get_conversation_uid
    return state, tap


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["resume", "branch"])
async def test_successful_session_switch_notifies_two_existing_clients(
    tmp_path, operation
):
    state, tap = _state_with_saved_session(tmp_path)
    queues = [tap.subscribe("client-one"), tap.subscribe("client-two")]
    old_uid = state.get_conversation_uid()

    if operation == "resume":
        result = await state.resume_conversation("saved-session")
    else:
        result = await state.branch_conversation("saved-session", 0)

    new_uid = state.get_conversation_uid()
    assert result["message_count"] == 1
    assert new_uid != old_uid
    for client_queue in queues:
        frame = client_queue.get_nowait()
        assert frame["type"] == "chat_stream"
        event = frame["event"]
        assert event["type"] == "replace"
        assert event["conversation_uid"] == new_uid
        texts = [
            part.get("text")
            for row in event["data"]["rows"]
            for part in row["parts"]
            if part.get("type") == "text"
        ]
        assert "saved session transcript" in texts
        assert "old session transcript" not in texts


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["resume", "branch"])
async def test_failed_session_switch_does_not_notify_clients(tmp_path, operation):
    state, tap = _state_with_saved_session(tmp_path)
    queues = [tap.subscribe("client-one"), tap.subscribe("client-two")]
    old_uid = state.get_conversation_uid()

    with pytest.raises(ValueError):
        if operation == "resume":
            await state.resume_conversation("missing-session")
        else:
            await state.branch_conversation("saved-session", 99)

    assert state.get_conversation_uid() == old_uid
    assert all(client_queue.empty() for client_queue in queues)


@pytest.mark.asyncio
async def test_resume_reused_uid_discards_stale_stream_state_and_rotates_generation(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("kollabor.state.chat_stream.REPLAY_WINDOW", 2)
    state, tap = _state_with_saved_session(tmp_path, saved_message_count=4)
    queues = [tap.subscribe("client-one"), tap.subscribe("client-two")]

    await state.resume_conversation("saved-session")
    uid = state.get_conversation_uid()
    for client_queue in queues:
        assert client_queue.get_nowait()["event"]["type"] == "replace"

    stream = state.chat_stream
    old_log = stream._log(uid)
    old_generation = old_log.generation
    expected_page = stream.row_pager(uid=uid).read(before=None, limit=1)
    assert len(expected_page["rows"]) == 1
    assert expected_page["older"] is not None
    expected_older_page = stream.row_pager(uid=uid).read(
        before=expected_page["older"], limit=1
    )

    stale_turn_id = "turn-from-later-visit"
    stale_input_id = "input-from-later-visit"
    stream._set_input_state(
        uid, stale_input_id, "started", turn_id=stale_turn_id
    )
    stream._add_turn_input(uid, stale_turn_id, stale_input_id)
    stream._input_turns[(uid, stale_input_id)] = stale_turn_id
    stream._set_turn_state(
        uid, stale_turn_id, "running", input_id=stale_input_id
    )
    stream._active_turns[uid] = stale_turn_id
    stream._upsert(
        uid,
        {
            "id": stale_input_id,
            "seq": 0,
            "kind": "input",
            "role": "user",
            "turn_id": stale_turn_id,
            "status": "started",
            "parts": [{"type": "text", "text": "stale later input"}],
            "created_at": "2026-11-27T00:00:00Z",
        },
    )
    stream._upsert(
        uid,
        {
            "id": "later-note",
            "seq": 0,
            "kind": "note",
            "role": "system",
            "turn_id": stale_turn_id,
            "status": "done",
            "parts": [{"type": "text", "text": "stale later note"}],
            "created_at": "2026-11-27T00:00:00Z",
        },
    )
    for client_queue in queues:
        while not client_queue.empty():
            client_queue.get_nowait()

    stale_snapshot = stream._snapshot(uid)
    assert any(row["id"] == "later-note" for row in stale_snapshot["rows"])
    assert any(item["id"] == stale_input_id for item in stale_snapshot["inputs"])
    assert any(turn["turn_id"] == stale_turn_id for turn in stale_snapshot["turns"])

    await state.resume_conversation("saved-session")

    frames = [client_queue.get_nowait() for client_queue in queues]
    assert all(frame["event"]["type"] == "replace" for frame in frames)
    replacement = frames[0]["event"]
    assert replacement["conversation_uid"] == uid
    assert replacement["generation"] != old_generation
    assert all(frame["event"]["generation"] == replacement["generation"] for frame in frames)
    replacement_rows = replacement["data"]["rows"]
    assert all(row["id"] != "later-note" for row in replacement_rows)
    assert all(row["id"] != stale_input_id for row in replacement_rows)
    assert all(
        item["id"] != stale_input_id for item in stream._snapshot(uid)["inputs"]
    )
    assert all(
        turn["turn_id"] != stale_turn_id for turn in stream._snapshot(uid)["turns"]
    )

    current_page = stream.row_pager(uid=uid).read(before=None, limit=1)
    assert [row["id"] for row in current_page["rows"]] == [
        row["id"] for row in expected_page["rows"]
    ]
    assert current_page["older"] == expected_page["older"]
    current_older_page = stream.row_pager(uid=uid).read(
        before=current_page["older"], limit=1
    )
    assert [row["id"] for row in current_older_page["rows"]] == [
        row["id"] for row in expected_older_page["rows"]
    ]


@pytest.mark.asyncio
async def test_resume_reused_uid_preserves_queue_ledger_and_goal_owner(tmp_path):
    state, _tap = _state_with_saved_session(tmp_path)
    state._llm_service.is_processing = True
    server = RpcServer()
    register_state_handlers(server, state)
    runtime = register_web_message_queue_runtime(server, state)
    uid = state.get_conversation_uid()
    reply_id = "goal-" + "d" * 32
    input_id = "queued-goal-input"
    terminal_input_id = "cancelled-before-reset"

    async def call(method, params, *, principal=501):
        response = await server.handle_wire(
            {
                "action": "rpc_request",
                "request_id": f"{method}-{principal}-{input_id}",
                "method": method,
                "params": params,
            },
            caller_context=RpcCallerContext.from_unix_peer(principal),
        )
        assert response["error"] is None
        return response["result"]

    release_reply = asyncio.Event()

    async def pending_private_reply():
        await release_reply.wait()
        return {"success": True, "message": "owner-only result"}

    pending_task = asyncio.create_task(pending_private_reply())
    try:
        await state.resume_conversation("saved-session")
        uid = state.get_conversation_uid()
        queued = await call(
            "state.chat_stream.add_input",
            {
                "id": input_id,
                "text": "/goal show",
                "attachments": [],
                "goal_reply_id": reply_id,
            },
        )
        assert queued == {"id": input_id, "state": "queued"}
        scope = state.chat_stream._input_scopes[(uid, input_id)]
        mailbox = GoalReplyMailbox()
        mailbox.register(uid, pending_task, reply_id)
        state._goal_reply_mailbox = mailbox

        terminal = await call(
            "state.chat_stream.add_input",
            {
                "id": terminal_input_id,
                "text": "ordinary queued input",
                "attachments": [],
            },
        )
        assert terminal == {"id": terminal_input_id, "state": "queued"}
        cancelled = await call(
            "state.chat_stream.cancel_queued_input", {"id": terminal_input_id}
        )
        assert cancelled == {"found": True, "cancelled": True}
        assert await call(
            "state.poll_goal_reply", {"reply_id": reply_id}
        ) == {"status": "pending"}
        assert await call(
            "state.poll_goal_reply", {"reply_id": reply_id}, principal=502
        ) == {"status": "missing", "message": "goal reply expired or is unavailable"}

        await state.resume_conversation("saved-session")

        # A same-UID restore invalidates later transcript rows but leaves the
        # accepted queue entry, retry binding, and reply principal intact.
        snapshot = state.chat_stream._snapshot(uid)
        queued_row = next(row for row in snapshot["rows"] if row["id"] == input_id)
        assert queued_row["status"] == "queued"
        assert any(
            item["id"] == input_id and item["state"] == "queued"
            for item in snapshot["inputs"]
        )
        assert all(row["id"] != terminal_input_id for row in snapshot["rows"])
        assert (
            state.chat_stream._admission_inputs[uid][terminal_input_id]["outcome"]
            == "cancelled"
        )
        assert state.chat_stream._input_scopes[(uid, input_id)] == scope
        assert await call(
            "state.chat_stream.add_input",
            {
                "id": input_id,
                "text": "/goal show",
                "attachments": [],
                "goal_reply_id": reply_id,
            },
        ) == {"id": input_id, "state": "queued"}
        assert await call(
            "state.chat_stream.add_input",
            {
                "id": terminal_input_id,
                "text": "ordinary queued input",
                "attachments": [],
            },
        ) == {"id": terminal_input_id, "state": "ended"}
        assert await call(
            "state.chat_stream.add_input",
            {
                "id": input_id,
                "text": "/goal show",
                "attachments": [],
                "goal_reply_id": reply_id,
            },
            principal=502,
        ) == {"error": "input was not admitted"}
        assert await call(
            "state.poll_goal_reply", {"reply_id": reply_id}
        ) == {"status": "pending"}
        assert await call(
            "state.poll_goal_reply", {"reply_id": reply_id}, principal=502
        ) == {"status": "missing", "message": "goal reply expired or is unavailable"}

        turn_id = state.chat_stream.begin_ordered_command(
            input_id, uid, scope[0]
        )
        assert turn_id is not None
    finally:
        release_reply.set()
        if not pending_task.done():
            pending_task.cancel()
        await asyncio.gather(pending_task, return_exceptions=True)
        await runtime.close()


@pytest.mark.asyncio
async def test_resume_reused_uid_restores_completed_input_over_stale_started_projection(
    tmp_path,
):
    state, _tap = _state_with_saved_session(tmp_path)
    await state.resume_conversation("saved-session")
    uid = state.get_conversation_uid()
    stream = state.chat_stream
    input_id = "saved-session-input-0"

    # Simulate a later visit that reused the stable request ID and left a
    # nonterminal lifecycle projection in the daemon cache.
    stream._input_scopes[(uid, input_id)] = ("later-queue", uid, "uid:501")
    stream._input_text[(uid, input_id)] = "later visit"
    stream._input_attachments[(uid, input_id)] = []
    stream._input_goal_reply_ids[(uid, input_id)] = None
    stream._set_input_state(uid, input_id, "started", turn_id="later-turn")

    await state.resume_conversation("saved-session")

    snapshot_input = next(
        item
        for item in stream._snapshot(uid)["inputs"]
        if item["id"] == input_id
    )
    assert snapshot_input["state"] == "ended"
    assert snapshot_input.get("outcome") == "completed"
    assert snapshot_input.get("turn_id") != "later-turn"
    assert stream._admission_inputs[uid][input_id]["state"] == "ended"
