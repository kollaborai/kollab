import asyncio
import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from kollabor.llm import acp_orchestrator as mod
from kollabor_ai.profile_manager import LLMProfile
from kollabor_ai.conversation_logger import KollaborConversationLogger
from kollabor_ai.conversation_manager import ConversationManager
from kollabor_events.data_models import ConversationMessage
from kollabor.state.local import LocalStateService
from kollabor_tui.display_tap import DisplayTap
from plugins.hub.models import HubMessage


@pytest.fixture(autouse=True)
def isolated_bindings(tmp_path, monkeypatch):
    monkeypatch.setattr("kollabor_config.config_utils.get_project_data_dir", lambda: tmp_path / "data")


@pytest.mark.asyncio
async def test_peer_observations_keep_hub_history_without_echoing_as_human_input(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "ACPRunner", FakeRunner)
    configuration = SimpleNamespace(
        _conversations_dir=tmp_path / "conversations",
        get=lambda key, default=None: False if key == "kollabor.llm.save_conversations" else default,
    )
    conversation_logger = KollaborConversationLogger(configuration._conversations_dir)
    manager = ConversationManager(configuration, conversation_logger)
    display = MagicMock()
    llm = SimpleNamespace(
        conversation_manager=manager, conversation_logger=conversation_logger,
        conversation_history=[], message_display_service=display,
    )
    bus = Bus()
    bus.services["llm_service"] = llm
    orchestrator = mod.ACPOrchestrator(bus)
    await orchestrator.start(LLMProfile(name="fake", provider="acp", command=("fake",)))
    try:
        # Replies and broadcasts that arrive during a turn share one bounded
        # model prompt. Each was already rendered by Hub on arrival.
        orchestrator.runner.is_processing = True
        for index in range(22):
            orchestrator.observe_notification(HubMessage(
                action="message", from_agent=f"peer-id-{index}",
                from_identity=f"peer-{index}", to="receiver" if index % 2 else "*",
                content=f"PEER_NOTE_{index}\n\nDetails {index}",
                thread_id=f"thread-{index}", reply_to=f"request-{index}" if index % 2 else "",
            ))
        orchestrator.runner.is_processing = False
        orchestrator._flush_observations()
        await asyncio.gather(*orchestrator._completion_tasks)

        snapshot = await LocalStateService(llm, None).get_conversation()
        inputs = [message for message in snapshot.messages if message.role == "user"]
        # One row per observation, written as each arrived, so a watching page
        # shows it live (#266).
        assert len(inputs) == 22
        assert all(row.content.count("[hub channel:") == 1 for row in inputs)
        assert "PEER_NOTE_0\n" in inputs[0].content
        assert "PEER_NOTE_21" in inputs[21].content
        # The model still gets one bounded batch once the turn ends.
        context = next(
            update["text"]
            for update in orchestrator.runner._updates
            if str(update["request_id"]).startswith("observations-")
        )
        assert context.startswith("<agent_hud>\n") and context.endswith("\n</agent_hud>")
        assert "2 older observations omitted." in context
        assert "PEER_NOTE_0\n" not in context and "PEER_NOTE_1\n" not in context
        assert context.count("[hub channel:") == 20
        assert "[hub:peer-2->]" in context
        assert "[hub:peer-21->receiver]" in context
        assert "[thread:thread-21] [reply-to:request-21]" in context
        assert context.count("PEER_NOTE_21") == 1
        display.display_user_message.assert_not_called()

        # Human prose that resembles routing text must still display verbatim.
        human = "Reply from sapphire [thread:example]\nThis is text I typed."
        admission = orchestrator.admit(human, request_id="human-control", source="rpc")
        await admission.future
        await asyncio.gather(*orchestrator._completion_tasks)
        display.display_user_message.assert_called_once_with(human)

        restored = ConversationManager(configuration)
        assert restored.load_session(conversation_logger.session_id)
        restored_inputs = [message["content"] for message in restored.messages if message["role"] == "user"]
        assert restored_inputs == [row.content for row in inputs] + [human]
    finally:
        await orchestrator.close()


@pytest.mark.asyncio
async def test_busy_observation_is_written_on_arrival_and_sent_once(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(mod, "ACPRunner", FakeRunner)
    semantic = []

    def spy_publish(bus, name, *args, **fields):
        semantic.append((name, fields))

    monkeypatch.setattr(mod, "publish_semantic", spy_publish)
    configuration = SimpleNamespace(
        _conversations_dir=tmp_path / "conversations",
        get=lambda key, default=None: (
            False if key == "kollabor.llm.save_conversations" else default
        ),
    )
    conversation_logger = KollaborConversationLogger(configuration._conversations_dir)
    manager = ConversationManager(configuration, conversation_logger)
    llm = SimpleNamespace(
        conversation_manager=manager,
        conversation_logger=conversation_logger,
        conversation_history=[],
        message_display_service=MagicMock(),
    )
    bus = Bus()
    bus.services["llm_service"] = llm
    orchestrator = mod.ACPOrchestrator(bus)
    await orchestrator.start(LLMProfile(name="fake", provider="acp", command=("fake",)))
    try:
        sent = []
        real_admit = orchestrator.runner.admit

        def record_admit(text, **kwargs):
            sent.append((text, kwargs))
            return real_admit(text, **kwargs)

        monkeypatch.setattr(orchestrator.runner, "admit", record_admit)
        orchestrator.runner.is_processing = True
        orchestrator.observe_notification(
            HubMessage(
                action="message",
                from_agent="peer-id-1",
                from_identity="peer-1",
                to="*",
                content="BUSY_NOTE\n\nDetails",
                thread_id=None,
                reply_to="",
            )
        )
        arrival_rows = list(orchestrator._observation_rows)
        rows = [m for m in llm.conversation_history if m.role == "user"]
        assert len(arrival_rows) == 1
        assert len(rows) == 1
        assert rows[0].metadata["source"] == "observation"
        hub_events = [fields for name, fields in semantic if name == "hub_message"]
        assert len(hub_events) == 1
        assert hub_events[0]["input_source"] == "observation"
        assert sent == []

        orchestrator.runner.is_processing = False
        orchestrator._flush_observations()
        await asyncio.gather(*orchestrator._completion_tasks)

        assert len(sent) == 1
        text, kwargs = sent[0]
        assert "BUSY_NOTE" in text
        assert kwargs["request_id"] == arrival_rows[0]
        rows = [m for m in llm.conversation_history if m.role == "user"]
        assert len(rows) == 1
    finally:
        await orchestrator.close()


@pytest.mark.asyncio
async def test_real_runner_start_failure_keeps_actionable_admission_error(tmp_path, monkeypatch):
    monkeypatch.setenv("KOLLAB_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr("kollabor_config.config_utils.get_project_data_dir", lambda: tmp_path / "data")
    bus = Bus()
    orchestrator = mod.ACPOrchestrator(bus)
    missing = str(tmp_path / "missing-adapter")
    profile = LLMProfile(name="missing-proof", provider="acp", command=(missing,), cwd=str(tmp_path))
    with pytest.raises(mod.ACPError, match="adapter executable not found"):
        await orchestrator.start(profile)
    assert missing in orchestrator.startup_error
    assert orchestrator.active is False
    with pytest.raises(mod.ACPError, match="missing-adapter"):
        orchestrator.admit("cannot run", request_id="owned", source="rpc")


@pytest.mark.asyncio
async def test_provider_refusal_keeps_one_correlated_terminal_and_rpc_code(monkeypatch):
    class RefusingRunner(FakeRunner):
        def admit(self, text, *, request_id, source, thread_id=None, origin=None):
            future = asyncio.get_running_loop().create_future()
            future.set_exception(mod.ACPError("claude-code: You've hit your weekly limit", code=-32603))
            return mod.ACPAdmission(request_id, source, thread_id, 1, 1, text, future, origin or {})
    monkeypatch.setattr(mod, "ACPRunner", RefusingRunner)
    bus = Bus()
    orchestrator = mod.ACPOrchestrator(bus)
    await orchestrator.start(LLMProfile(name="claude-code", provider="acp", command=("owned",)))
    admission = orchestrator.admit("request", request_id="quota-request", source="agent", thread_id="quota-thread")
    with pytest.raises(mod.ACPError):
        await admission.future
    await asyncio.gather(*orchestrator._completion_tasks)
    events = bus.services["display_tap"].events
    terminals = [event for event in events if event.get("type") == "turn_complete"]
    frames = [event for event in events if event.get("type") == "turn_end"]
    assert len(terminals) == len(frames) == 1
    assert terminals[0]["request_id"] == frames[0]["request_id"] == "quota-request"
    assert terminals[0]["thread_id"] == frames[0]["thread_id"] == "quota-thread"
    assert terminals[0]["code"] == frames[0]["code"] == -32603
    assert "weekly limit" in terminals[0]["error"]
    await orchestrator.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("stop_reason, tool_status", [("end_turn", "completed"), ("cancelled", "in_progress")])
async def test_detached_history_snapshot_and_jsonl_restore_keep_typed_messages_and_merged_cards(tmp_path, stop_reason, tool_status):
    configuration = SimpleNamespace(
        _conversations_dir=tmp_path / "conversations",
        get=lambda key, default=None: False if key == "kollabor.llm.save_conversations" else default,
    )
    conversation_logger = KollaborConversationLogger(configuration._conversations_dir)
    manager = ConversationManager(configuration, conversation_logger)
    history = []
    llm = SimpleNamespace(conversation_manager=manager, conversation_logger=conversation_logger, conversation_history=history)
    bus = Bus()
    bus.services["llm_service"] = llm
    orchestrator = mod.ACPOrchestrator(bus)
    orchestrator._profile_name = "owned-harness"
    state = LocalStateService(llm, None)
    try:
        orchestrator._record_user("owned-request", "Update the owned fixture", "rpc", None, track_completion=True)
        await asyncio.gather(*orchestrator._history_tasks)
        diff = {"type": "diff", "path": "owned_fixture.txt", "oldText": "old", "newText": "new"}
        updates = (
            {"kind": "text", "text": "Checking the fixture.\n"},
            {"kind": "agent_thought_chunk", "payload": {"content": {"type": "text", "text": "Owned reasoning."}}},
            {"kind": "tool_call", "payload": {
                "toolCallId": "owned-card", "title": "Original title", "kind": "edit", "status": "in_progress",
                "rawInput": {"path": "owned_fixture.txt"},
            }},
            {"kind": "tool_call_update", "payload": {
                "toolCallId": "owned-card", "title": "Updated title", "status": tool_status,
                "rawInput": None, "rawOutput": 0, "content": [diff],
            }},
            {"kind": "text", "text": "Turn ended."},
        )
        result = mod.ACPResult(1, "owned-session", "Turn ended.", stop_reason, updates, {}, 1, "owned-request", "rpc")
        await orchestrator._record_result(result)
        assert history is not manager.messages and all(isinstance(message, ConversationMessage) for message in history)
        live = await state.get_conversation()
        assert [message.role for message in live.messages] == ["user", "assistant"]
        assert live.messages[0].content == "Update the owned fixture"
        assert live.messages[1].content == "Checking the fixture.\nTurn ended."
        assert all(message.timestamp for message in live.messages)
        assert live.messages[1].thinking == "Owned reasoning."
        metadata = live.messages[1].metadata
        assert metadata["stop_reason"] == stop_reason
        assert len(metadata["tool_calls"]) == 1
        card = metadata["tool_calls"][0]
        assert card["name"] == "Updated title" and card["input"] == {"path": "owned_fixture.txt"}
        assert card["result"] == 0 and card["status"] == tool_status
        assert card["metadata"]["acp"]["kind"] == "edit"
        assert card["metadata"]["acp"]["content"] == [diff]

        restored_manager = ConversationManager(configuration)
        assert restored_manager.load_session(conversation_logger.session_id)
        assistant = next(message for message in restored_manager.messages if message["role"] == "assistant")
        assert set(assistant["metadata"]) == {"provider", "acp_updates", "tool_calls", "stop_reason"}
        assert assistant["metadata"]["provider"] == "acp"
        assert assistant["metadata"]["stop_reason"] == stop_reason
        assert assistant["metadata"]["tool_calls"] == metadata["tool_calls"]
        assert assistant["metadata"]["acp_updates"] == list(updates)
        restored_history = [ConversationMessage(role=message["role"], content=message["content"], metadata=message["metadata"])
                            for message in restored_manager.messages]
        restored = await LocalStateService(SimpleNamespace(conversation_history=restored_history,
                                                          conversation_manager=restored_manager), None).get_conversation()
        assert [message.role for message in restored.messages] == ["user", "assistant"]
        assert restored.messages[1].content == live.messages[1].content
        assert restored.messages[1].metadata["tool_calls"] == metadata["tool_calls"]
    finally:
        await orchestrator.close()


INTERRUPTED_AGENT = r"""
import asyncio, acp
from pathlib import Path
from acp.schema import (
    AgentMessageChunk, InitializeResponse, NewSessionResponse,
    PromptResponse, TextContentBlock, ToolCallStart, ToolCallProgress,
)
class Agent:
    def on_connect(self, connection):
        self.connection = connection
        self.stopped = asyncio.Event()
    async def initialize(self, protocol_version, **kwargs):
        return InitializeResponse(protocolVersion=1)
    async def new_session(self, cwd, **kwargs):
        return NewSessionResponse(sessionId="owned-interrupted-session")
    async def prompt(self, session_id, prompt, **kwargs):
        text = "\n".join(getattr(block, "text", "") for block in prompt)
        if "next turn" in text:
            await self.connection.session_update(session_id, AgentMessageChunk(
                sessionUpdate="agent_message_chunk",
                content=TextContentBlock(type="text", text="Replacement turn only."),
            ))
            await self.connection.session_update(session_id, ToolCallStart(
                sessionUpdate="tool_call", toolCallId="reused-card", title="Replacement fixture",
                kind="read", status="pending", rawInput={"path": "next.txt"},
            ))
            return PromptResponse(stopReason="end_turn")
        self.stopped.clear()
        await self.connection.session_update(session_id, AgentMessageChunk(
            sessionUpdate="agent_message_chunk",
            content=TextContentBlock(type="text", text="Observed before interruption.\n"),
        ))
        await self.connection.session_update(session_id, ToolCallStart(
            sessionUpdate="tool_call", toolCallId="reused-card", title="Original fixture",
            kind="read", status="pending", rawInput={"path": "stopped.txt"},
        ))
        await self.connection.session_update(session_id, ToolCallProgress(
            sessionUpdate="tool_call_update", toolCallId="reused-card",
            title="Waiting fixture", status="in_progress",
        ))
        if "refusal" in text:
            while not Path("release-refusal").exists():
                await asyncio.sleep(0.01)
            raise acp.RequestError(-32001, "Owned provider refusal")
        await self.stopped.wait()
        return PromptResponse(stopReason="cancelled")
    async def cancel(self, session_id, **kwargs):
        # These updates arrive after the admission future has been rejected.
        await asyncio.sleep(0.05)
        await self.connection.session_update(session_id, ToolCallProgress(
            sessionUpdate="tool_call_update", toolCallId="reused-card",
            title="Last reported fixture", status="in_progress",
            content=[{"type": "content", "content": {"type": "text", "text": "Work remains pending."}}],
        ))
        self.stopped.set()
asyncio.run(acp.run_agent(Agent()))
"""


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["cancel", "forced-cancel", "refusal"])
async def test_real_sdk_interrupted_turn_survives_snapshot_and_reload_without_replacement_updates(tmp_path, monkeypatch, scenario):
    monkeypatch.setenv("KOLLAB_CONFIG_DIR", str(tmp_path / "config"))
    configuration = SimpleNamespace(_conversations_dir=tmp_path / "conversations", get=lambda key, default=None: default)
    conversation_logger = KollaborConversationLogger(configuration._conversations_dir)
    manager = ConversationManager(configuration, conversation_logger)
    llm = SimpleNamespace(conversation_manager=manager, conversation_logger=conversation_logger, conversation_history=[])
    bus = Bus()
    bus.services["llm_service"] = llm
    orchestrator = mod.ACPOrchestrator(bus)
    (tmp_path / "home").mkdir()
    profile = LLMProfile(name="owned-interrupted", provider="acp", command=(sys.executable, "-c", INTERRUPTED_AGENT),
                         cwd=str(tmp_path), env={"HOME": str(tmp_path / "home")})
    runner = None
    try:
        await orchestrator.start(profile)
        runner = orchestrator.runner
        admission = orchestrator.admit(scenario, request_id="stopped-request", source="rpc", thread_id="stopped-thread")
        async with asyncio.timeout(5):
            while not any(event.get("type") == "acp_update" and event.get("update", {}).get("title") == "Waiting fixture"
                          for event in bus.services["display_tap"].events):
                await asyncio.sleep(0.01)
        stopped_generation = runner.generation
        queued = orchestrator.admit("must not run", request_id="queued-cancel", source="rpc")
        assert await orchestrator.cancel_request("queued-cancel") == {"cancelled": True, "queued": True}
        with pytest.raises(asyncio.CancelledError):
            await queued.future
        # Reuse the tool id in a queued turn while cancellation is settling.
        replacement = orchestrator.admit("next turn", request_id="next-request", source="rpc")
        if scenario != "refusal":
            if scenario == "forced-cancel":
                runner.cancel_timeout = 0.01
            assert orchestrator.prepare_cancel(session_id=runner.session_id, generation=stopped_generation)
            await asyncio.wait_for(orchestrator.cancel(session_id=runner.session_id, generation=stopped_generation), 15)
        else:
            (tmp_path / "release-refusal").touch()
        with pytest.raises(mod.ACPError):
            await asyncio.wait_for(admission.future, 5)
        await asyncio.wait_for(replacement.future, 10)
        await asyncio.gather(*tuple(orchestrator._completion_tasks))
        expected_reason = "error" if scenario == "refusal" else "cancelled"
        snapshot = json.loads(json.dumps((await LocalStateService(llm, None).get_conversation()).to_dict()))
        assistants = [message for message in snapshot["messages"] if message["role"] == "assistant"]
        assert len(assistants) == 2
        assert not any(message["metadata"].get("request_id") == "queued-cancel" for message in assistants)
        stopped = next(message for message in assistants if message["metadata"]["request_id"] == "stopped-request")
        next_turn = next(message for message in assistants if message["metadata"]["request_id"] == "next-request")
        assert stopped["content"] == "Observed before interruption.\n"
        assert stopped["metadata"]["stop_reason"] == expected_reason
        card = stopped["metadata"]["tool_calls"][0]
        assert card["input"] == {"path": "stopped.txt"} and card["status"] == "in_progress"
        assert card["metadata"]["acp"]["kind"] == "read"
        assert card["name"] == ("Last reported fixture" if scenario == "cancel" else "Waiting fixture")
        if scenario == "cancel":
            assert card["metadata"]["acp"]["content"][0]["content"]["text"] == "Work remains pending."
        assert next_turn["content"] == "Replacement turn only."
        assert next_turn["metadata"]["stop_reason"] == "end_turn"
        assert next_turn["metadata"]["tool_calls"][0]["status"] == "pending"
        assert all(update["request_id"] == "stopped-request" for update in stopped["metadata"]["acp_updates"])
        terminal_events = [event for event in bus.services["display_tap"].events
                           if event.get("request_id") == "stopped-request" and event.get("type") in {"turn_complete", "turn_end"}]
        assert len(terminal_events) == 2
        assert all(event["generation"] == stopped_generation for event in terminal_events)
        assert all(event["thread_id"] == "stopped-thread" for event in terminal_events)
        if scenario == "refusal":
            assert all(event["code"] == -32001 for event in terminal_events)
        assert not orchestrator._observed_turns

        restored_manager = ConversationManager(configuration)
        assert restored_manager.load_session(conversation_logger.session_id)
        restored_history = [ConversationMessage(role=message["role"], content=message["content"], metadata=message["metadata"])
                            for message in restored_manager.messages]
        restored = await LocalStateService(SimpleNamespace(conversation_manager=restored_manager,
                                                          conversation_history=restored_history), None).get_conversation()
        restored_assistants = [message for message in restored.messages if message.role == "assistant"]
        assert len(restored_assistants) == 2
        restored_stopped = next(message for message in restored_assistants if message.metadata["stop_reason"] == expected_reason)
        assert restored_stopped.content == stopped["content"]
        assert restored_stopped.metadata["tool_calls"] == stopped["metadata"]["tool_calls"]
        assert restored_stopped.metadata["acp_updates"] == stopped["metadata"]["acp_updates"]
    finally:
        await orchestrator.close()
        if runner is not None:
            assert not runner._live_owned_processes()


@pytest.mark.asyncio
async def test_history_error_after_assistant_write_does_not_retry_or_duplicate_terminal(tmp_path, monkeypatch):
    configuration = SimpleNamespace(_conversations_dir=tmp_path / "conversations", get=lambda key, default=None: default)
    conversation_logger = KollaborConversationLogger(configuration._conversations_dir)
    manager = ConversationManager(configuration, conversation_logger)
    llm = SimpleNamespace(conversation_manager=manager, conversation_logger=conversation_logger, conversation_history=[])
    bus = Bus()
    bus.services["llm_service"] = llm
    orchestrator = mod.ACPOrchestrator(bus)
    original_write = conversation_logger.log_assistant_message
    writes = 0

    async def fail_after_write(*args, **kwargs):
        nonlocal writes
        writes += 1
        await original_write(*args, **kwargs)
        raise OSError("Owned history write failed after append")

    monkeypatch.setattr(conversation_logger, "log_assistant_message", fail_after_write)
    profile = LLMProfile(name="owned-write", provider="acp", command=(sys.executable, "-c", INTERRUPTED_AGENT), cwd=str(tmp_path))
    runner = None
    try:
        await orchestrator.start(profile)
        runner = orchestrator.runner
        admission = orchestrator.admit("next turn", request_id="write-request", source="rpc")
        completion_tasks = tuple(orchestrator._completion_tasks)
        await asyncio.wait_for(admission.future, 5)
        completions = await asyncio.gather(*completion_tasks)
        assert len(completions) == writes == 1
        assert completions[0]["stop_reason"] == "error"
        snapshot = await LocalStateService(llm, None).get_conversation()
        assert len([message for message in snapshot.messages if message.role == "assistant"]) == 1
        restored = ConversationManager(configuration)
        assert restored.load_session(conversation_logger.session_id)
        assert len([message for message in restored.messages if message["role"] == "assistant"]) == 1
        events = bus.services["display_tap"].events
        assert len([event for event in events if event.get("type") == "turn_complete"]) == 1
        assert len([event for event in events if event.get("type") == "turn_end"]) == 1
        assert not orchestrator._observed_turns
    finally:
        await orchestrator.close()
        if runner is not None:
            assert not runner._live_owned_processes()


class Tap:
    def __init__(self):
        self.events = []

    def publish(self, event):
        self.events.append(event)

    def publish_semantic(self, event):
        self.events.append(event)


class Hub:
    def __init__(self):
        self.rotations = 0
        self._identity = None
        self._presence = None
        self._socket_server = type("Socket", (), {"socket_path": "/tmp/hub.sock"})()

    def rotate_acp_hub_binding(self):
        self.rotations += 1
        return (f"token-{self.rotations}", self.rotations)


class Bus:
    def __init__(self):
        self.services = {"display_tap": Tap()}

    def get_service(self, name):
        return self.services.get(name)

    def register_service(self, name, service):
        self.services[name] = service


class FakeRunner:
    def __init__(self, profile, update_callback=None, permission_decider=None, permission_bridge=None, lifecycle_callback=None):
        self.profile = profile
        self.update_callback = update_callback
        self.lifecycle_callback = lifecycle_callback
        self.started = False
        self.closed = False
        self._updates = []
        self.session_id = "s1"
        self.generation = 1
        self.binding = None
        self.state = "stopped"
        self.resume_mode = "fresh"

    async def start(self, binding=None):
        if self.lifecycle_callback is not None:
            await self.lifecycle_callback("starting", self)
        self.started = True
        self.state = "ready"
        if self.lifecycle_callback is not None:
            await self.lifecycle_callback("ready", self)

    def admit(self, text, *, request_id, source, thread_id=None, origin=None):
        future = asyncio.get_running_loop().create_future()
        update = {"kind": "text", "text": text, "request_id": request_id}
        self._updates.append(update)
        if self.update_callback:
            self.update_callback(update)
        future.set_result(
            mod.ACPResult(
                1, "s1", text, "end_turn", tuple(self._updates),
                {"type": "turn_end"}, 1, request_id, source, thread_id,
            )
        )
        return mod.ACPAdmission(request_id, source, thread_id, 1, 1, text, future, origin or {})

    async def prompt(self, text):
        return (await self.admit(text, request_id="legacy", source="rpc").future)

    async def cancel(self):
        pass

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_orchestrator_owns_runner_and_publishes_semantic(monkeypatch):
    monkeypatch.setattr(mod, "ACPRunner", FakeRunner)
    monkeypatch.setattr(
        mod, "publish_semantic",
        lambda bus, event_type, **fields: bus.get_service("display_tap").publish_semantic(
            {"type": event_type, **fields}
        ),
    )
    bus = Bus()
    hub = Hub()
    orchestrator = mod.ACPOrchestrator(bus, identity="lapis", hub_plugin=hub)
    profile = LLMProfile(name="fake", provider="acp", command=("fake",))
    await orchestrator.start(profile)
    result = await orchestrator.submit("hello", source="rpc")
    assert result["text"] == "hello"
    await asyncio.sleep(0)
    assert bus.services["acp_orchestrator"] is orchestrator
    assert any(event.get("type") == "turn_complete" for event in bus.services["display_tap"].events)
    assert any(
        event.get("type") == "token" and event.get("text") == "hello"
        for event in bus.services["display_tap"].events
    )
    assert any(event.get("type") == "turn_end" and event.get("hidden") for event in bus.services["display_tap"].events)
    await orchestrator.close()
    assert not orchestrator.active
    assert hub.rotations == 2


@pytest.mark.asyncio
async def test_runner_update_publishes_contextual_metadata_to_real_display_tap():
    tap = DisplayTap()
    bus = Bus()
    bus.services["display_tap"] = tap
    orchestrator = mod.ACPOrchestrator(bus)

    orchestrator._on_runner_update({
        "kind": "text",
        "text": "chunk",
        "request_id": "req-1",
        "turn_id": 7,
        "source": "hub",
        "thread_id": "thread-1",
    })

    event = tap.get_snapshot()[-1]
    assert event["type"] == "token"
    assert event["text"] == "chunk"
    assert event["request_id"] == "req-1"
    assert event["turn_id"] == 7
    assert event["input_source"] == "hub"
    assert event["thread_id"] == "thread-1"


@pytest.mark.asyncio
async def test_selected_profile_has_one_runner_fifo_and_cleanup(monkeypatch):
    monkeypatch.setattr(mod, "ACPRunner", FakeRunner)
    monkeypatch.setattr(
        mod,
        "publish_semantic",
        lambda *_args, **_fields: None,
    )
    bus = Bus()
    hub = Hub()
    profile_manager = SimpleNamespace(
        get_active_profile=lambda: LLMProfile(
            name="claude-code", provider="acp", command=("fake",)
        )
    )
    orchestrator = mod.ACPOrchestrator(bus, identity="daemon-1", hub_plugin=hub)
    await orchestrator.start(profile_manager.get_active_profile())
    first = orchestrator.admit("first", request_id="req-1", source="cli")
    second = orchestrator.admit("second", request_id="req-2", source="cli")
    assert [first.request_id, second.request_id] == ["req-1", "req-2"]
    assert orchestrator.runner is not None
    assert orchestrator.runner.profile.name == "claude-code"
    await asyncio.gather(first.future, second.future)
    await orchestrator.close()
    assert hub.rotations == 2
    assert orchestrator.runner is None


@pytest.mark.asyncio
async def test_restart_start_failure_closes_replacement_and_clears_state(monkeypatch):
    created = []

    class RestartRunner(FakeRunner):
        def __init__(self, profile, update_callback=None, permission_decider=None, permission_bridge=None, lifecycle_callback=None):
            super().__init__(profile, update_callback, permission_bridge=permission_bridge, lifecycle_callback=lifecycle_callback)
            self.start_count = 0
            created.append(self)

        async def start(self, binding=None):
            self.start_count += 1
            await super().start(binding=binding)
            if len(created) > 1:
                raise RuntimeError("replacement child failed")

    monkeypatch.setattr(mod, "ACPRunner", RestartRunner)
    bus = Bus()
    hub = Hub()
    orchestrator = mod.ACPOrchestrator(bus, identity="daemon-1", hub_plugin=hub)
    profile = LLMProfile(name="claude-code", provider="acp", command=("fake",))
    await orchestrator.start(profile)
    old = orchestrator.runner
    assert old is created[0]

    with pytest.raises(RuntimeError, match="replacement child failed"):
        await orchestrator.start(profile)

    assert old.closed is True
    assert created[1].closed is True
    assert orchestrator.runner is None
    assert orchestrator.active is False
    assert orchestrator._binding is None
