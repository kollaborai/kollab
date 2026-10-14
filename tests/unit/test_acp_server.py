"""Inbound session ownership, SDK notification shapes and cleanup boundaries."""

import asyncio
from copy import deepcopy
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
    assert start["toolCallId"] != "native-tool" and start["title"] == "terminal"
    assert start["kind"] == "execute" and start["rawInput"] == {"command": "echo native"}
    assert result["sessionUpdate"] == "tool_call_update" and result["status"] == "failed"
    assert result["toolCallId"] == start["toolCallId"]
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


@pytest.mark.asyncio
@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("permission_first", [False, True])
async def test_repeated_live_source_ids_are_unique_and_permission_replies_keep_source_owner(
    engine_factory, tmp_path, wrapped, permission_first,
):
    agent = KollabACPAgent()
    agent.on_connect(sdk_connection())
    await agent.initialize(1)
    session_id = (await agent.new_session(str(tmp_path))).sessionId
    engine = engine_factory.instances[-1]
    produced = []
    source_events = []
    permission_replies = []
    permission_done = asyncio.Event()

    async def select(**kwargs):
        return RequestPermissionResponse.model_validate({"outcome": {
            "outcome": "selected", "optionId": kwargs["options"][0].optionId}})

    agent.connection.request_permission.side_effect = select

    async def produce(request_id):
        for index in range(2):
            permission_done.clear()
            start = {"type": "tool_start", "tool_id": "terminal_0",
                     "tool_name": "terminal", "tool_type": "terminal", "input": {"command": str(index)}}
            permission = {"type": "permission_request", "tool_id": "terminal_0", "tool_name": "terminal"}
            if wrapped:
                # The compatibility display event must not allocate a second invocation.
                engine.events.put_nowait({"request_id": request_id, **start, "acp_passthrough": True})
                start = {"type": "acp_update", "update": {"sessionUpdate": "tool_call",
                    "toolCallId": "terminal_0", "title": "Vendor tool", "kind": "execute", "status": "pending"}}
                permission["details"] = {"source": "acp", "acp_options": [
                    {"optionId": "opaque-yes", "name": "Allow", "kind": "allow_once"},
                    {"optionId": "opaque-no", "name": "Deny", "kind": "reject_once"}]}
            initial = [permission, start] if permission_first else [start, permission]
            for event in initial:
                row = {"request_id": request_id, **event}
                source_events.append((row, deepcopy(row)))
                engine.events.put_nowait(row)
            await asyncio.wait_for(permission_done.wait(), 1)
            if wrapped:
                progress = {"type": "acp_update", "update": {"sessionUpdate": "tool_call_update",
                    "toolCallId": "terminal_0", "status": "in_progress", "rawOutput": 0}}
                result = {"type": "acp_update", "update": {"sessionUpdate": "tool_call_update",
                    "toolCallId": "terminal_0", "status": "completed", "rawOutput": str(index)}}
                engine.events.put_nowait({"request_id": request_id, **progress})
            else:
                result = {"type": "tool_result", "tool_id": "terminal_0", "success": True, "output": str(index)}
            engine.events.put_nowait({"request_id": request_id, **result})
        engine.events.put_nowait({"request_id": request_id, "type": "turn_complete", "stop_reason": "end_turn"})

    async def rpc(name, params):
        if name == "state.acp_submit":
            produced.append(asyncio.create_task(produce(params["request_id"])))
            return {"accepted": True}
        if name == "permission.respond":
            permission_replies.append(params)
            permission_done.set()
        return {}

    engine.daemon.rpc.call.side_effect = rpc
    for _ in range(2):
        assert (await agent.prompt(session_id, [TextContentBlock(type="text", text="next")])).stopReason == "end_turn"
    await asyncio.gather(*produced)
    starts = [row for row in agent.connection.updates if row["sessionUpdate"] == "tool_call"]
    results = [row for row in agent.connection.updates if row.get("status") == "completed"]
    start_ids = [row["toolCallId"] for row in starts]
    assert len(start_ids) == len(set(start_ids)) == 4
    assert [row["toolCallId"] for row in results] == start_ids
    permission_ids = [call.kwargs["tool_call"].toolCallId
                      for call in agent.connection.request_permission.call_args_list]
    assert permission_ids == start_ids
    if wrapped:
        assert [row["toolCallId"] for row in agent.connection.updates
                if row.get("status") == "in_progress"] == start_ids
        assert all(row.get("rawOutput") == 0 for row in agent.connection.updates if row.get("status") == "in_progress")
    assert permission_replies == [{"tool_id": "terminal_0", **(
        {"option_id": "opaque-yes"} if wrapped else {"control": "APPROVE_ONCE"})}] * 4
    assert all(row == original for row, original in source_events)
    await agent.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("wrapped", [False, True])
async def test_replay_tool_ids_are_unique_stable_and_disjoint_from_new_work_after_restart(
    engine_factory, tmp_path, monkeypatch, wrapped,
):
    agent = KollabACPAgent()
    agent.on_connect(sdk_connection())
    await agent.initialize(1)
    session_id = (await agent.new_session(str(tmp_path))).sessionId
    history = []
    for turn in range(2):
        metadata = {"tool_calls": [{"id": source_id, "name": "terminal", "type": "terminal",
            "input": {"command": str(turn)}, "result": str(turn), "status": "completed"}
            for source_id in ["terminal_0", "terminal_1"]]}
        if wrapped:
            metadata = {"provider": "acp", "acp_updates": [
                {"kind": kind, "payload": {"sessionUpdate": kind, "toolCallId": source_id,
                    **({"title": str(turn), "kind": "execute", "status": "pending"}
                       if kind == "tool_call" else {"status": "completed", "rawOutput": str(turn)})}}
                for source_id in ["terminal_0", "terminal_1"] for kind in ["tool_call", "tool_call_update"]]}
        history.append({"role": "assistant", "content": "", "metadata": metadata,
                        "timestamp": f"2026-10-14T04:00:0{turn}"})
    original_history = deepcopy(history)
    engine_factory.instances[-1].refresh_history.return_value = history
    await agent.load_session(str(tmp_path), session_id)
    first = deepcopy(agent.connection.updates)
    starts = [row["toolCallId"] for row in first if row["sessionUpdate"] == "tool_call"]
    results = [row["toolCallId"] for row in first if row["sessionUpdate"] == "tool_call_update"]
    assert len(starts) == len(set(starts)) == 4
    assert starts == results
    agent.connection.updates.clear()
    await agent.load_session(str(tmp_path), session_id)
    assert agent.connection.updates == first
    await agent.close_session(session_id)

    restored = KollabACPAgent()
    restored.on_connect(sdk_connection())
    await restored.initialize(1)
    original_create = restored._create_session

    async def create(*args, **kwargs):
        session = await original_create(*args, **kwargs)
        session.engine.refresh_history.return_value = history
        return session

    monkeypatch.setattr(restored, "_create_session", create)
    await restored.resume_session(str(tmp_path), session_id)
    assert restored.connection.updates == []
    await restored.load_session(str(tmp_path), session_id)
    assert restored.connection.updates == first
    engine = engine_factory.instances[-1]

    async def rpc(name, params):
        if name == "state.acp_submit":
            source = {"type": "tool_start", "tool_id": "terminal_0", "tool_name": "terminal"}
            if wrapped:
                source = {"type": "acp_update", "update": {"sessionUpdate": "tool_call", "toolCallId": "terminal_0",
                    "title": "New backend tool", "kind": "execute", "status": "in_progress"}}
            engine.events.put_nowait({"request_id": params["request_id"], **source})
            engine.events.put_nowait({"request_id": params["request_id"], "type": "turn_complete",
                                     "stop_reason": "end_turn"})
            return {"accepted": True}
        return {}

    engine.daemon.rpc.call.side_effect = rpc
    for _ in range(2):
        await restored.prompt(session_id, [TextContentBlock(type="text", text="new tool")])
    new_ids = [row["toolCallId"] for row in restored.connection.updates[len(first):]
               if row["sessionUpdate"] == "tool_call"]
    assert len(set(new_ids)) == 2 and not set(new_ids).intersection(starts)
    assert history == original_history
    await restored.shutdown()
