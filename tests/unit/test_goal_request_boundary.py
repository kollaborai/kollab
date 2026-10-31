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
            await send({"action": "rpc_request", "request_id": "first", "method": "state.send_message", "params": {"message": message("/goal inspect A", b"wire-A")}})
            assert (await reply("first"))["accepted"]
            await asyncio.wait_for(boundary.bus.reached.wait(), 2)
            await send({"action": "rpc_request", "request_id": "second", "method": "state.send_message", "params": {"message": message("/goal inspect B", b"wire-B")}})
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
