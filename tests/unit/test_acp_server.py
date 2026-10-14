"""Inbound session ownership, SDK notification shapes and cleanup boundaries."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from acp.exceptions import RequestError
from acp.schema import RequestPermissionResponse, SessionNotification, TextContentBlock

from kollabor.acp_server import ClientSession, KollabACPAgent


@pytest.fixture
def engine_factory(monkeypatch):
    import kollabor_engine.session as module

    class Engine:
        instances = []

        def __init__(self, session_id, **kwargs):
            self.session_id = session_id
            self.workspace = kwargs.get("workspace")
            self.approval_mode = kwargs.get("approval_mode", "confirm_all")
            self.daemon = SimpleNamespace(rpc=SimpleNamespace(call=AsyncMock(return_value={})))
            self.state = SimpleNamespace(
                get_conversation=AsyncMock(return_value=SimpleNamespace(session_id=f"native-{session_id}")),
                resume_conversation=AsyncMock(return_value={}),
            )
            self.initialize = AsyncMock()
            self.shutdown = AsyncMock()
            self.refresh_history = AsyncMock(return_value=[])
            self.events = asyncio.Queue()
            self.subscribe = lambda: self.events
            self.unsubscribe = lambda events: None
            self.instances.append(self)

        @classmethod
        async def adopt(cls, found):
            return cls(found["session_id"], workspace=found["workspace"])

    monkeypatch.setattr(module, "EngineSession", Engine)
    return Engine


def sdk_connection():
    updates = []

    async def update(session_id, notification):
        parsed = SessionNotification.model_validate({"sessionId": session_id, "update": notification})
        updates.append(parsed.update.model_dump(by_alias=True, exclude_none=True))

    return SimpleNamespace(session_update=update, updates=updates,
                           request_permission=AsyncMock(return_value=RequestPermissionResponse.model_validate(
                               {"outcome": {"outcome": "selected", "optionId": "once"}})))


@pytest.mark.asyncio
async def test_native_live_tools_use_valid_sdk_notifications(engine_factory, tmp_path):
    agent = KollabACPAgent()
    agent.on_connect(sdk_connection())
    await agent.initialize(2)
    session_id = (await agent.new_session(str(tmp_path))).sessionId
    engine = engine_factory.instances[-1]

    async def rpc(name, params):
        if name == "state.acp_submit":
            request_id = params["request_id"]
            for event in [
                {"type": "turn_complete", "request_id": "mesh", "stop_reason": "end_turn"},
                {"type": "tool_start", "tool_id": "native-write", "tool_name": "file_write", "input": {"path": "owned.txt"}},
                {"type": "tool_result", "tool_id": "native-write", "success": True, "output": "saved",
                 "metadata": {"diffs": [{"path": "owned.txt", "old_text": "before", "new_text": "after"}]}},
                {"type": "token", "text": "owned answer"},
                {"type": "turn_complete", "stop_reason": "end_turn"},
            ]:
                engine.events.put_nowait({"request_id": request_id, **event})
            return {"accepted": True}
        return {}

    engine.daemon.rpc.call.side_effect = rpc
    result = await agent.prompt(session_id, [TextContentBlock(type="text", text="write")])
    assert result.stopReason == "end_turn"
    updates = agent.connection.updates
    assert [update["sessionUpdate"] for update in updates] == ["tool_call", "tool_call_update", "agent_message_chunk"]
    assert updates[1]["content"][1] == {"type": "diff", "path": "owned.txt", "oldText": "before", "newText": "after"}
    await agent.shutdown()


@pytest.mark.asyncio
async def test_native_replay_uses_same_sdk_tool_variants(engine_factory, tmp_path):
    agent = KollabACPAgent()
    agent.on_connect(sdk_connection())
    await agent.initialize(1)
    session_id = (await agent.new_session(str(tmp_path))).sessionId
    engine = engine_factory.instances[-1]
    engine.refresh_history.return_value = [{"role": "assistant", "content": "saved answer",
        "metadata": {"tool_calls": [{"id": "saved-tool", "name": "terminal", "type": "terminal",
                                     "input": {"command": "echo saved"}, "result": "saved", "status": "completed"}]}}]
    await agent.load_session(str(tmp_path), session_id)
    assert [update["sessionUpdate"] for update in agent.connection.updates] == ["tool_call", "tool_call_update", "agent_message_chunk"]
    await agent.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", ['{"command":"echo native"}', {"command": "echo native"}])
async def test_native_replay_joins_function_call_to_separate_error_and_diff(engine_factory, tmp_path, arguments):
    agent = KollabACPAgent()
    agent.on_connect(sdk_connection())
    await agent.initialize(1)
    session_id = (await agent.new_session(str(tmp_path))).sessionId
    engine_factory.instances[-1].refresh_history.return_value = [
        {"role": "assistant", "content": "", "metadata": {"tool_calls": [
            {"id": "native-tool", "type": "function", "function": {"name": "terminal", "arguments": arguments}}]}},
        {"role": "tool", "content": "tool failed", "metadata": {"tool_call_id": "native-tool", "is_error": True,
            "diffs": [{"path": "owned.txt", "old_text": "before", "new_text": "after"}]}},
        {"role": "assistant", "content": "saved answer"},
    ]
    await agent.load_session(str(tmp_path), session_id)
    start, result, answer = agent.connection.updates
    assert start["sessionUpdate"] == "tool_call"
    assert start["toolCallId"] == "native-tool" and start["title"] == "terminal"
    assert start["kind"] == "execute" and start["rawInput"] == {"command": "echo native"}
    assert result["sessionUpdate"] == "tool_call_update" and result["status"] == "failed"
    assert result["rawOutput"] == "tool failed"
    assert result["content"][1] == {"type": "diff", "path": "owned.txt", "oldText": "before", "newText": "after"}
    assert answer["content"]["text"] == "saved answer"
    await agent.shutdown()


@pytest.mark.asyncio
async def test_native_replay_cannot_join_reused_id_across_assistant_turns(engine_factory, tmp_path):
    agent = KollabACPAgent()
    agent.on_connect(sdk_connection())
    await agent.initialize(1)
    session_id = (await agent.new_session(str(tmp_path))).sessionId
    calls = lambda command: {"tool_calls": [{"id": "reused", "type": "function",
        "function": {"name": "terminal", "arguments": {"command": command}}}]}
    engine_factory.instances[-1].refresh_history.return_value = [
        {"role": "assistant", "content": "", "metadata": calls("first")},
        {"role": "assistant", "content": "interrupted before output"},
        {"role": "assistant", "content": "", "metadata": calls("second")},
        {"role": "tool", "content": "second output", "metadata": {"tool_call_id": "reused"}},
        {"role": "assistant", "content": "second answer"},
    ]
    await agent.load_session(str(tmp_path), session_id)
    updates = agent.connection.updates
    assert [entry["sessionUpdate"] for entry in updates] == [
        "tool_call", "agent_message_chunk", "tool_call", "tool_call_update", "agent_message_chunk"]
    assert updates[0]["rawInput"] == {"command": "first"}
    assert updates[2]["rawInput"] == {"command": "second"}
    assert updates[3]["rawOutput"] == "second output" and updates[3]["status"] == "completed"
    await agent.shutdown()


@pytest.mark.asyncio
async def test_failed_new_save_shuts_down_owned_engine(engine_factory, tmp_path, monkeypatch):
    agent = KollabACPAgent()
    await agent.initialize(1)
    error = OSError("owned disk failure")
    monkeypatch.setattr(agent.saved, "save", lambda *args: (_ for _ in ()).throw(error))
    with pytest.raises(OSError) as failure:
        await agent.new_session(str(tmp_path))
    assert failure.value is error
    engine_factory.instances[-1].shutdown.assert_awaited_once()
    assert agent.sessions == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["load_session", "resume_session"])
async def test_failed_restore_write_cleans_engine_without_temporary_handle(engine_factory, tmp_path, monkeypatch, operation):
    agent = KollabACPAgent()
    agent.on_connect(sdk_connection())
    await agent.initialize(1)
    agent.saved.save("stable", str(tmp_path), "saved-native")
    files_before = set(agent.saved.directory.iterdir())
    error = OSError("owned restoration disk failure")
    monkeypatch.setattr(agent.saved, "save", lambda *args: (_ for _ in ()).throw(error))
    with pytest.raises(OSError) as failure:
        await getattr(agent, operation)(str(tmp_path), "stable")
    assert failure.value is error
    assert set(agent.saved.directory.iterdir()) == files_before
    assert agent.saved.load("stable", str(tmp_path)) == "saved-native"
    engine_factory.instances[-1].shutdown.assert_awaited_once()
    assert agent.sessions == {} and agent.restoring == set()


@pytest.mark.asyncio
async def test_attached_close_detaches_despite_cancel_rpc_error(engine_factory, tmp_path):
    agent = KollabACPAgent(attach="lapis")
    engine = engine_factory("attached", workspace=str(tmp_path))
    session = ClientSession("client", engine, str(tmp_path), True, requests=["own-request"])
    agent.sessions[session.id] = session
    error = RuntimeError("owned RPC disconnect")
    engine.daemon.rpc.call.side_effect = error
    with pytest.raises(RuntimeError) as failure:
        await agent.close_session(session.id)
    assert failure.value is error
    engine.shutdown.assert_awaited_once()
    assert agent.sessions == {}


@pytest.mark.asyncio
async def test_close_keeps_first_write_error_while_cleanup_is_cancelled(engine_factory, tmp_path, monkeypatch):
    agent = KollabACPAgent()
    await agent.initialize(1)
    session_id = (await agent.new_session(str(tmp_path))).sessionId
    entered = asyncio.Event()
    release = asyncio.Event()

    async def shutdown():
        entered.set()
        await release.wait()

    engine_factory.instances[-1].shutdown.side_effect = shutdown
    error = OSError("first write failure")
    monkeypatch.setattr(agent.saved, "save", lambda *args: (_ for _ in ()).throw(error))
    closing = asyncio.create_task(agent.close_session(session_id))
    await asyncio.wait_for(entered.wait(), 1)
    closing.cancel()
    await asyncio.sleep(0)
    assert not closing.done()
    release.set()
    with pytest.raises(OSError) as failure:
        await closing
    assert failure.value is error
    assert agent.sessions == {}


@pytest.mark.asyncio
async def test_attach_selects_exact_folder_and_refuses_mode_change(engine_factory, tmp_path, monkeypatch):
    from kollabor_engine.hub_bridge import HubBridge
    workspace = tmp_path / "selected"
    workspace.mkdir()
    sibling = tmp_path / "sibling"
    sibling.mkdir()
    rows = [{"identity": "claude", "workspace": str(sibling), "session_id": "wrong-folder"},
            {"identity": "claude", "workspace": str(workspace), "session_id": "right-folder"},
            {"identity": "claude", "workspace": str(workspace), "session_id": "remote", "device": "remote"}]
    monkeypatch.setattr(HubBridge, "discover_sessions", lambda *args, **kwargs: rows)
    agent = KollabACPAgent(attach="claude")
    await agent.initialize(1)
    session_id = (await agent.new_session(str(workspace))).sessionId
    assert agent.sessions[session_id].engine.session_id == "right-folder"
    with pytest.raises(RequestError):
        await agent.set_session_mode(session_id, "trust_all")
    agent.sessions[session_id].engine.daemon.rpc.call.assert_not_awaited()
    await agent.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("attached", [False, True])
async def test_permission_scope_and_opaque_options(engine_factory, tmp_path, attached):
    agent = KollabACPAgent()
    agent.on_connect(sdk_connection())
    engine = engine_factory("engine", workspace=str(tmp_path))
    session = ClientSession("client", engine, str(tmp_path), attached)
    offered = [{"optionId": "vendor-once", "name": "Allow once", "kind": "allow_once"},
               {"optionId": "vendor-always", "name": "Always", "kind": "allow_always"},
               {"optionId": "vendor-deny", "name": "Deny", "kind": "reject_once"}]
    agent.connection.request_permission.return_value = RequestPermissionResponse.model_validate(
        {"outcome": {"outcome": "selected", "optionId": "vendor-once"}})
    await agent._permission(session, {"tool_id": "vendor", "details": {"acp_options": offered}})
    options = agent.connection.request_permission.call_args.kwargs["options"]
    assert ("allow_always" in {option.kind for option in options}) == (not attached)
    assert engine.daemon.rpc.call.call_args.args == ("permission.respond", {"tool_id": "vendor", "option_id": "vendor-once"})
