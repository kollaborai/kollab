"""Exercise structured goal input across the real daemon command boundary."""

import asyncio
import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from kollabor.commands.executor import SlashCommandExecutor
from kollabor.commands.parser import SlashCommandParser
from kollabor.commands.registry import SlashCommandRegistry
from kollabor.commands.system_commands.handlers.goal import GoalCommandHandler
from kollabor.goals.service import GoalService
from kollabor.state.goal_store import GoalStore
from kollabor.state.local import LocalStateService
from kollabor_events.models import EventType


@pytest.fixture
def boundary(tmp_path):
    store = GoalStore(tmp_path / "goals.db")
    service = GoalService(store, daemon_id="test")
    profile = SimpleNamespace(name="native", provider="openai")
    profile.get_provider = lambda: profile.provider
    manager = SimpleNamespace(get_active_profile=lambda: profile)
    tasks, events, history = [], [], []
    identity = {"uid": "conversation-a"}
    class Bus:
        def __init__(self):
            self.services = {}
            self.hooks = []
            self.switch_on = None
            self.block_on = None
            self.reached = asyncio.Event()
            self.release = asyncio.Event()
        def get_service(self, name):
            return self.services.get(name)
        async def emit_with_hooks(self, event_type, data, source):
            self.hooks.append((event_type, data))
            if event_type == self.switch_on:
                identity["uid"] = "conversation-b"
            if event_type == self.block_on:
                self.reached.set()
                await self.release.wait()
            await asyncio.sleep(0)
            return data
    bus = Bus()
    llm = SimpleNamespace(profile_manager=manager, is_processing=False,
        supports_image_input=lambda: True, submit_human_input=AsyncMock(),
        _add_conversation_message=lambda *args, **kw: history.append((args, kw)))
    def start(coro, *, name=None):
        task = asyncio.create_task(coro, name=name)
        tasks.append(task)
        return task
    llm.create_background_task = start
    state = LocalStateService(llm, manager, event_bus=bus)
    state.get_conversation_uid = lambda: identity["uid"]
    registry = SlashCommandRegistry()
    handler = GoalCommandHandler(registry, bus)
    handler.register_commands()
    driver = SimpleNamespace(schedule=Mock())
    bus.services.update(goal_service=service, llm_service=llm, state_service=state,
        goal_driver=driver, slash_parser=SlashCommandParser(),
        command_registry=registry, command_executor=SlashCommandExecutor(registry),
        display_tap=SimpleNamespace(publish=lambda event: events.append(event)))
    yield SimpleNamespace(store=store, service=service, profile=profile, state=state,
        llm=llm, tasks=tasks, events=events, history=history, bus=bus, identity=identity)
    store.close()


def message(text, raw=b"image-A", kind="image"):
    url = "data:image/png;base64," + base64.b64encode(raw).decode()
    image = {"type": "image", "image": url} if kind == "image" else {"type": "image_url", "image_url": {"url": url}}
    return [{"type": "text", "text": text}, image]


async def finish(boundary):
    if boundary.tasks:
        await asyncio.gather(*boundary.tasks, return_exceptions=True)
        boundary.tasks.clear()
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_overlapping_requests_keep_own_images_and_recover_loser(boundary):
    first = await boundary.state.send_message(message("/goal inspect A", b"A"))
    second = await boundary.state.send_message(message("/goal inspect B", b"B"))
    assert first["accepted"] and not second["accepted"]
    pending = boundary.service.pending_attachment_requests("conversation-a")
    assert len(pending) == 2
    await finish(boundary)
    goal = boundary.store.get_active("conversation-a")
    assert goal is not None, (boundary.events, boundary.history, boundary.bus.hooks)
    assert goal.objective == "inspect A"
    assert boundary.service.load_attachment(goal.attachment_refs[0]) == b"A"
    remaining = boundary.service.pending_attachment_requests("conversation-a")
    assert len(remaining) == 1 and "inspect B" in remaining[0]["command_preview"]
    assert remaining[0]["request_id"] in second["reason"]
    boundary.llm.submit_human_input.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_image_request_does_not_attach_to_later_text_goal(boundary):
    assert (await boundary.state.send_message(message("/goal --invalid inspect", b"private")))["accepted"]
    await finish(boundary)
    retained = boundary.service.pending_attachment_requests("conversation-a")
    assert len(retained) == 1
    assert (await boundary.state.send_message("/goal write a poem"))["accepted"]
    await finish(boundary)
    goal = boundary.store.get_active("conversation-a")
    assert goal.objective == "write a poem" and goal.attachment_refs == []
    assert boundary.service.pending_attachment_requests("conversation-a") == retained


@pytest.mark.asyncio
async def test_changed_conversation_before_dispatch_cannot_move_images(boundary):
    assert (await boundary.state.send_message(message("/goal secret A", b"A")))["accepted"]
    boundary.identity["uid"] = "conversation-b"
    await finish(boundary)
    assert boundary.store.get_active("conversation-a") is None
    assert boundary.store.get_active("conversation-b") is None
    assert boundary.service.pending_attachment_requests("conversation-a")
    assert not boundary.service.pending_attachment_requests("conversation-b")
    assert "secret A" not in repr(boundary.events) + repr(boundary.history) + repr(boundary.bus.hooks)


@pytest.mark.asyncio
async def test_changed_conversation_after_handler_suppresses_sensitive_output(boundary):
    boundary.service.stage_attachment_request("conversation-a", "/goal private-preview", [{"ref": "secret-ref"}])
    boundary.bus.switch_on = EventType.SLASH_COMMAND_COMPLETE
    assert (await boundary.state.send_message("/goal requests"))["accepted"]
    await finish(boundary)
    combined = repr(boundary.events) + repr(boundary.history) + repr(boundary.bus.hooks)
    assert "private-preview" not in combined and "secret-ref" not in combined
    assert boundary.history == []
    assert not any(event.get("type") in ("error", "turn_complete") for event in boundary.events)


@pytest.mark.asyncio
async def test_goal_hook_output_keeps_a_safe_attached_terminal_notice(boundary):
    boundary.service.stage_attachment_request("conversation-a", "/goal private-preview", [{"ref": "secret-ref"}])
    assert (await boundary.state.send_message("/goal requests"))["accepted"]
    await finish(boundary)
    notices = [data["message"] for kind, data in boundary.bus.hooks
               if kind == EventType.COMMAND_OUTPUT_DISPLAY]
    assert notices == ["Goal command completed; see the requesting client for details."]
    assert "private-preview" not in repr(notices) and "secret-ref" not in repr(notices)


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["/goal", "/goal clear", "/goal\tshow", "/goals\nresume"])
async def test_control_with_image_is_refused_and_retained(boundary, text):
    existing = boundary.service.create_goal("conversation-a", "existing", "/repo")
    response = await boundary.state.send_message(message(text))
    assert not response["accepted"] and "/goal retry" in response["reason"]
    assert boundary.store.get(existing.goal_id).status == "active"
    assert len(boundary.service.pending_attachment_requests("conversation-a")) == 1
    boundary.llm.submit_human_input.assert_not_awaited()


@pytest.mark.asyncio
async def test_image_url_and_authored_text_exclude_placeholder(boundary):
    response = await boundary.state.send_message(message("/goal inspect", b"url-image", kind="image_url"))
    assert response["accepted"]
    await finish(boundary)
    goal = boundary.store.get_active("conversation-a")
    assert goal is not None, (boundary.events, boundary.history, boundary.bus.hooks)
    assert goal.objective == "inspect"
    assert boundary.service.load_attachment(goal.attachment_refs[0]) == b"url-image"


@pytest.mark.asyncio
@pytest.mark.parametrize("text_type,image_type", [("TEXT", "IMAGE"), (" text ", " image ")])
async def test_goal_parts_use_normalizer_case_rules(boundary, text_type, image_type):
    content = message("/goal inspect case", b"case-image")
    content[0]["type"] = text_type
    content[1]["type"] = image_type
    result = await boundary.state.send_message(content)
    assert result["accepted"]
    await finish(boundary)
    goal = boundary.store.get_active("conversation-a")
    assert goal.objective == "inspect case"
    assert boundary.service.load_attachment(goal.attachment_refs[0]) == b"case-image"
    boundary.llm.submit_human_input.assert_not_awaited()


@pytest.mark.asyncio
async def test_busy_image_goal_is_preserved_and_not_sent_to_chat(boundary):
    boundary.llm.is_processing = True
    response = await boundary.state.send_message(message("/goal inspect busy"))
    assert not response["accepted"] and "/goal retry" in response["reason"]
    assert boundary.service.pending_attachment_requests("conversation-a")
    assert boundary.tasks == []
    boundary.llm.submit_human_input.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_promotion_refuses_entire_set_without_leaking_bytes(boundary, caplog):
    body = message("/goal inspect", b"VALID")
    body.append({"type": "image", "image": "data:image/png;base64,PRIVATE-invalid"})
    response = await boundary.state.send_message(body)
    assert not response["accepted"]
    assert not boundary.service.pending_attachment_requests("conversation-a")
    assert boundary.store.get_active("conversation-a") is None
    assert "PRIVATE-invalid" not in caplog.text + repr(response) + repr(boundary.events)


@pytest.mark.asyncio
async def test_cancelled_before_dispatch_remains_recoverable(boundary):
    response = await boundary.state.send_message(message("/goal inspect cancelled"))
    assert response["accepted"]
    boundary.tasks[0].cancel()
    await finish(boundary)
    pending = boundary.service.pending_attachment_requests("conversation-a")
    assert len(pending) == 1 and "cancelled" in pending[0]["command_preview"]
    boundary.store.close()
    boundary.store = GoalStore(boundary.store.db_path)
    assert boundary.store.pending_attachment_requests("conversation-a")[0]["request_id"] == pending[0]["request_id"]


@pytest.mark.asyncio
async def test_cancelled_after_commit_returns_same_receipt_on_retry(boundary):
    boundary.bus.block_on = EventType.SLASH_COMMAND_COMPLETE
    response = await boundary.state.send_message(message("/goal inspect committed"))
    assert response["accepted"]
    request_id = boundary.service.pending_attachment_requests("conversation-a")[0]["request_id"]
    await asyncio.wait_for(boundary.bus.reached.wait(), 2)
    goal = boundary.store.get_active("conversation-a")
    assert goal is not None
    boundary.tasks[0].cancel()
    await finish(boundary)
    boundary.bus.block_on = None
    boundary.profile.provider = "acp"
    receipt = await boundary.state.send_message(f"/goal retry {request_id}")
    assert receipt["accepted"]
    await finish(boundary)
    assert any("already created" in str(event.get("text", "")) for event in boundary.events)
    assert boundary.store.get_active("conversation-a").goal_id == goal.goal_id






@pytest.mark.asyncio
async def test_overlapping_structured_goals_cross_real_attach_rpc(boundary, monkeypatch, caplog):
    import contextlib
    import json
    import tempfile
    from pathlib import Path
    from kollabor.state.handlers import register_state_handlers
    from kollabor_config import local_ipc
    from kollabor_rpc import RpcServer
    from kollabor_tui.display_tap import DisplayTap
    from plugins.hub.messenger import AgentSocketServer

    caplog.set_level("DEBUG")
    with tempfile.TemporaryDirectory(prefix="goal-rpc-") as socket_dir:
        monkeypatch.setattr("plugins.hub.messenger.get_socket_dir", lambda: Path(socket_dir))
        rpc = RpcServer()
        register_state_handlers(rpc, boundary.state)
        tap = DisplayTap(history_size=10)
        boundary.bus.services["display_tap"] = tap
        server = AgentSocketServer(agent_id="goal-rpc", on_message=None, socket_name="goal")
        server._display_tap = tap
        server._rpc_server = rpc
        reader, writer = await local_ipc.connect(await server.start())

        async def send(frame):
            writer.write((json.dumps(frame) + "\n").encode())
            await writer.drain()

        async def reply(request_id):
            while True:
                frame = json.loads((await asyncio.wait_for(reader.readline(), 3)).decode())
                if frame.get("action") == "rpc_reply" and frame.get("request_id") == request_id:
                    return frame["result"]

        try:
            await send({"action": "attach", "mode": "interactive", "client_id": "engine"})
            boundary.bus.block_on = EventType.SLASH_COMMAND_DETECTED
            await send({"action": "rpc_request", "request_id": "first", "method": "state.send_message", "params": {"message": message("/goal inspect A", b"wire-A"), "private_goal_reply": True, "goal_conversation_uid": "conversation-a"}})
            assert (await reply("first"))["accepted"]
            await asyncio.wait_for(boundary.bus.reached.wait(), 2)
            await send({"action": "rpc_request", "request_id": "second", "method": "state.send_message", "params": {"message": message("/goal inspect B", b"wire-B"), "private_goal_reply": True, "goal_conversation_uid": "conversation-a"}})
            refused = await reply("second")
            assert not refused["accepted"] and "/goal retry" in refused["reason"]
            boundary.bus.release.set()
            await finish(boundary)
            goal = boundary.store.get_active("conversation-a")
            assert goal.objective == "inspect A"
            assert boundary.service.load_attachment(goal.attachment_refs[0]) == b"wire-A"
            assert len(boundary.service.pending_attachment_requests("conversation-a")) == 1
            assert base64.b64encode(b"wire-A").decode() not in caplog.text + repr(tap.get_snapshot())
            assert goal.attachment_refs[0]["ref"] not in caplog.text + repr(tap.get_snapshot())
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            await server.stop()


@pytest.mark.asyncio
async def test_private_overlapping_goal_replies_do_not_enter_shared_tap(boundary):
    boundary.service.stage_attachment_request("conversation-a", "/goal private-preview-B", [{"ref": "private-ref-B"}])
    reached, release = asyncio.Event(), asyncio.Event()
    emit = boundary.bus.emit_with_hooks
    seen = 0

    async def hold_first_detection(event_type, data, source):
        nonlocal seen
        if event_type == EventType.SLASH_COMMAND_DETECTED:
            seen += 1
            if seen == 1:
                reached.set()
                await release.wait()
        return await emit(event_type, data, source)

    boundary.bus.emit_with_hooks = hold_first_detection
    first = await boundary.state.send_message("/goal inspect A", private_goal_reply=True, goal_conversation_uid="conversation-a")
    await asyncio.wait_for(reached.wait(), 2)
    second = await boundary.state.send_message("/goal pending", private_goal_reply=True, goal_conversation_uid="conversation-a")
    await asyncio.wait_for(boundary.tasks[1], 2)
    mailbox = boundary.state._goal_reply_mailbox
    assert mailbox.poll(first["goal_reply_id"], "conversation-a")["status"] == "pending"
    assert "private-preview-B" in mailbox.poll(second["goal_reply_id"], "conversation-a")["message"]
    assert "private-preview-B" not in repr(boundary.events) + repr(boundary.bus.hooks)
    assert not any(event.get("type") in ("token", "turn_complete") for event in boundary.events)
    release.set()
    await finish(boundary)
    assert "inspect A" in mailbox.poll(first["goal_reply_id"], "conversation-a")["message"]
    assert all(kwargs.get("publish_user_event") is False for (args, kwargs) in boundary.history if args[0] == "user")


@pytest.mark.asyncio
async def test_private_goal_conversation_change_returns_safe_terminal_reply(boundary):
    boundary.bus.block_on = EventType.SLASH_COMMAND_DETECTED
    result = await boundary.state.send_message("/goal secret A", private_goal_reply=True, goal_conversation_uid="conversation-a")
    await asyncio.wait_for(boundary.bus.reached.wait(), 2)
    boundary.identity["uid"] = "conversation-b"
    boundary.bus.release.set()
    await finish(boundary)
    reply = boundary.state._goal_reply_mailbox.poll(result["goal_reply_id"], "conversation-b")
    assert reply["status"] == "complete" and not reply["success"]
    assert "secret A" not in repr(reply) + repr(boundary.events) + repr(boundary.bus.hooks)


@pytest.mark.asyncio
async def test_private_goal_reply_crosses_real_daemon_rpc(boundary):
    from kollabor.state.handlers import register_state_handlers
    from kollabor_rpc import RpcServer

    server = RpcServer()
    register_state_handlers(server, boundary.state)
    async def call(request_id, method, params):
        return await server.handle_wire({"action": "rpc_request", "request_id": request_id, "method": method, "params": params})

    capability = await call("cap", "state.goal_reply_capabilities", {})
    assert capability["result"]["private_goal_reply"] is True
    sent = await call("send", "state.send_message", {"message": "/goal show", "private_goal_reply": True, "goal_conversation_uid": "conversation-a"})
    reply_id = sent["result"]["goal_reply_id"]
    await finish(boundary)
    reply = await call("poll", "state.poll_goal_reply", {"reply_id": reply_id})
    assert reply["result"]["status"] == "complete" and "no active goal" in reply["result"]["message"]
    assert not any(event.get("type") in ("token", "turn_complete") for event in boundary.events)
    boundary.identity["uid"] = "conversation-b"
    foreign = await call("poll-other", "state.poll_goal_reply", {"reply_id": reply_id})
    assert "no active goal" not in repr(foreign)


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["/goal show", message("/GOALS\tpending")])
async def test_old_rpc_client_cannot_broadcast_an_unowned_goal_reply(boundary, content):
    from kollabor.state.handlers import register_state_handlers
    from kollabor_rpc import RpcServer

    server = RpcServer()
    register_state_handlers(server, boundary.state)
    response = await server.handle_wire({
        "action": "rpc_request", "request_id": "old-client",
        "method": "state.send_message", "params": {"message": content},
    })
    assert not response["result"]["accepted"]
    assert "update" in response["result"]["reason"]
    assert not boundary.events and not boundary.tasks
    assert not boundary.service.pending_attachment_requests("conversation-a")


@pytest.mark.asyncio
async def test_private_goal_interleaving_through_local_ipc_and_both_web_transports(boundary, monkeypatch):
    import contextlib
    import json
    import tempfile
    from pathlib import Path
    from kollabor.state.handlers import register_state_handlers
    from kollabor.state.remote import RemoteStateService
    from kollabor_config import local_ipc
    from kollabor_engine.routes import messages
    from kollabor_engine.session import EngineSession
    from kollabor_rpc import RpcClient, RpcServer
    from kollabor_tui.display_tap import DisplayTap
    from plugins.hub.messenger import AgentSocketServer

    boundary.service.stage_attachment_request("conversation-a", "/goal private-B", [{"ref": "secret-ref-B"}])
    reached, release = asyncio.Event(), asyncio.Event()
    emit = boundary.bus.emit_with_hooks
    seen = 0
    async def hold_first_detection(event_type, data, source):
        nonlocal seen
        if event_type == EventType.SLASH_COMMAND_DETECTED:
            seen += 1
            if seen == 1:
                reached.set()
                await release.wait()
        return await emit(event_type, data, source)
    boundary.bus.emit_with_hooks = hold_first_detection

    with tempfile.TemporaryDirectory(prefix="goal-reply-rpc-") as socket_dir:
        monkeypatch.setattr("plugins.hub.messenger.get_socket_dir", lambda: Path(socket_dir))
        rpc = RpcServer()
        register_state_handlers(rpc, boundary.state)
        tap = DisplayTap(history_size=50)
        boundary.bus.services["display_tap"] = tap
        server = AgentSocketServer(agent_id="goal-reply", on_message=None, socket_name="goal")
        server._display_tap, server._rpc_server = tap, rpc
        reader, writer = await local_ipc.connect(await server.start())
        client = RpcClient(writer)
        seen_events = []
        writer.write((json.dumps({"action": "attach", "mode": "interactive", "client_id": "engine"}) + "\n").encode())
        await writer.drain()
        async def read():
            while data := await reader.readline():
                frame = json.loads(data.decode())
                if frame.get("action") == "rpc_reply":
                    client.on_reply(frame)
                else:
                    seen_events.append(frame)
        reader_task = asyncio.create_task(read())
        class Session:
            session_id = "goal-session"
            alive = True
            send_message = EngineSession.send_message
            poll_goal_reply = EngineSession.poll_goal_reply
            def __init__(self):
                self.state = RemoteStateService(client)
                self.daemon = SimpleNamespace(rpc=client)
                self.subscribers = []
            def subscribe(self):
                queue = asyncio.Queue()
                self.subscribers.append(queue)
                return queue
            def unsubscribe(self, queue):
                self.subscribers.remove(queue)
            async def refresh_history(self):
                return []
        session = Session()
        monkeypatch.setattr(messages, "get_session_registry", lambda: SimpleNamespace(get=lambda _id: session))
        async def collect(response):
            return [part async for part in response.body_iterator]
        try:
            legacy = await messages.send_message(session.session_id, messages.MessageRequest(content="/goal inspect A", conversation_uid="conversation-a"))
            first = asyncio.create_task(collect(legacy))
            await asyncio.wait_for(reached.wait(), 2)
            for queue in session.subscribers:
                queue.put_nowait({"type": "token", "text": "foreign ordinary turn"})
                queue.put_nowait({"type": "turn_complete"})
            assistant = await messages.assistant_transport(session.session_id, messages.AssistantRequest(
                commands=[{"type": "add-message", "content": "/goal pending"}], state={"messages": [], "conversationUid": "conversation-a"}))
            second = await asyncio.wait_for(collect(assistant), 4)
            assert "private-B" in repr(second) and "foreign ordinary turn" not in repr(second)
            assert not first.done()
            release.set()
            first_result = await asyncio.wait_for(first, 4)
            assert "inspect A" in repr(first_result) and "private-B" not in repr(first_result)
            assert "private-B" not in repr(tap.get_snapshot()) + repr(seen_events)
            assert not session.subscribers
            # Both HTTP adapters bind the goal to the conversation shown by
            # the caller, before image staging or command execution.
            boundary.identity["uid"] = "conversation-b"
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as stale:
                await messages.send_message(session.session_id, messages.MessageRequest(
                    content="/goal pending", conversation_uid="conversation-a"))
            assert stale.value.status_code == 400 and "refresh" in str(stale.value.detail)
            stale_assistant = await messages.assistant_transport(session.session_id, messages.AssistantRequest(
                commands=[{"type": "add-message", "content": "/goal pending"}],
                state={"messages": [], "conversationUid": "conversation-a"}))
            stale_wire = await asyncio.wait_for(collect(stale_assistant), 4)
            assert "refresh" in repr(stale_wire) and "private-B" not in repr(stale_wire)
        finally:
            release.set()
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            reader_task.cancel()
            await asyncio.gather(reader_task, return_exceptions=True)
            await server.stop()


@pytest.mark.asyncio
async def test_goal_cancel_before_send_fences_admission(boundary):
    from kollabor.state.handlers import register_state_handlers
    from kollabor_rpc import RpcServer

    server = RpcServer()
    register_state_handlers(server, boundary.state)
    async def call(request_id, method, params):
        return await server.handle_wire({"action": "rpc_request", "request_id": request_id, "method": method, "params": params})
    reply_id = "goal-" + "a" * 32
    cancelled = await call("cancel", "state.cancel_goal_reply", {"reply_id": reply_id})
    assert cancelled["result"]["cancelled"] and cancelled["result"]["queued"]
    sent = await call("send", "state.send_message", {"message": message("/goal inspect"), "private_goal_reply": True, "goal_conversation_uid": "conversation-a", "private_goal_reply_id": reply_id})
    assert not sent["result"]["accepted"] and "cancelled" in sent["result"]["reason"]
    assert boundary.service.pending_attachment_requests("conversation-a") == []
    assert boundary.tasks == []


@pytest.mark.asyncio
async def test_cancel_private_goal_does_not_cancel_unrelated_model_turn(boundary):
    from kollabor.state.handlers import register_state_handlers
    from kollabor_rpc import RpcServer

    boundary.llm.is_processing = True
    boundary.llm.cancel_current_request = Mock()
    boundary.bus.block_on = EventType.SLASH_COMMAND_DETECTED
    server = RpcServer()
    register_state_handlers(server, boundary.state)
    async def call(request_id, method, params):
        return await server.handle_wire({"action": "rpc_request", "request_id": request_id, "method": method, "params": params})
    reply_id = "goal-" + "b" * 32
    sent = await call("send", "state.send_message", {"message": "/goal show", "private_goal_reply": True, "goal_conversation_uid": "conversation-a", "private_goal_reply_id": reply_id})
    assert sent["result"]["accepted"]
    await asyncio.wait_for(boundary.bus.reached.wait(), 2)
    cancelled = await call("cancel", "state.cancel_goal_reply", {"reply_id": reply_id})
    assert cancelled["result"]["cancelled"]
    await finish(boundary)
    reply = await call("poll", "state.poll_goal_reply", {"reply_id": reply_id})
    assert reply["result"]["status"] == "complete" and "cancelled" in reply["result"]["message"]
    boundary.llm.cancel_current_request.assert_not_called()
    assert not any(event.get("type") in ("token", "turn_complete") for event in boundary.events)


@pytest.mark.asyncio
async def test_private_goal_refuses_a_changed_or_unknown_conversation_before_staging(boundary):
    content = message('/goal inspect private', b'private-image')
    old = await boundary.state.send_message(content, private_goal_reply=True, goal_conversation_uid='conversation-b')
    missing = await boundary.state.send_message(content, private_goal_reply=True)
    assert not old['accepted'] and not missing['accepted']
    assert 'refresh' in old['reason'] and 'refresh' in missing['reason']
    assert not boundary.service.pending_attachment_requests('conversation-a') and not boundary.tasks


@pytest.mark.asyncio
async def test_conversation_snapshot_refuses_tail_across_identical_histories(boundary):
    boundary.llm.conversation_history = [SimpleNamespace(role="user", content="same text", timestamp="", metadata={}, thinking=None)]
    first = await boundary.state.get_conversation()
    assert first.metadata['conversation_uid'] == 'conversation-a'
    boundary.identity['uid'] = 'conversation-b'
    second = await boundary.state.get_conversation(since=1, anchor=first.anchor,
                                                    expected_conversation_uid='conversation-a')
    assert second.metadata['conversation_uid'] == 'conversation-b'
    assert second.since is None
