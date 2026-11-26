"""Hub admissions retain their ids and structured notes in the chat stream."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kollabor.llm.llm_coordinator import LLMService
from kollabor.state.chat_stream import ChatStreamLog, ChatStreamService
from kollabor_agent.runtime import AgentRuntime
from kollabor_events.chat_stream import sequence_problems
from kollabor_tui.display_tap import DisplayTap
from plugins.hub.models import HubMessage, MessageScope
from plugins.hub.plugin import HubPlugin


def _script_stream(hub_events):
    """Project captured admission events into the shared row/lifecycle contract."""
    log = ChatStreamLog(
        "conversation-a",
        snapshot_provider=lambda: {"rows": [], "turns": [], "inputs": []},
    )
    snapshot = log.read(after=None)[0]
    for index, event in enumerate(hub_events):
        note = event["hub_note"]
        assert note["type"] == "hub-note"
        row_id = event["message_id"]
        admission = event["hub_admission"]
        is_input = admission == "input"
        row = {
            "id": row_id,
            "seq": 0,
            "kind": "input" if is_input else "note",
            "role": "user" if is_input else "system",
            "turn_id": None,
            "status": "queued" if is_input else "done",
            "parts": [note],
            "created_at": "2026-11-25T12:00:00Z",
        }
        log.publish("row.upsert", row)
        if not is_input:
            continue

        turn_id = f"hub-turn-{index}"
        log.publish("input.state", {"id": row_id, "state": "queued", "turn_id": None})
        log.publish(
            "turn.state",
            {"turn_id": turn_id, "state": "running", "input_ids": [row_id]},
        )
        log.publish(
            "input.state", {"id": row_id, "state": "started", "turn_id": turn_id}
        )
        log.publish(
            "turn.state",
            {"turn_id": turn_id, "state": "ended", "input_ids": [row_id]},
        )
        log.publish(
            "input.state",
            {
                "id": row_id,
                "state": "ended",
                "turn_id": turn_id,
                "outcome": "completed",
            },
        )

    replay = log.read(after=0, conversation_uid="conversation-a")
    return [snapshot, *replay]


def _assert_valid_stream(hub_events, *, row_kind, message_id):
    stream = _script_stream(hub_events)
    assert sequence_problems(stream) == []
    rows = [
        event["data"]
        for event in stream
        if event["type"] == "row.upsert" and event["data"]["id"] == message_id
    ]
    assert rows
    assert rows[-1]["kind"] == row_kind
    assert rows[-1]["id"] == message_id
    assert rows[-1]["parts"][0]["type"] == "hub-note"
    assert all(
        "<agent_hud>" not in part.get("text", "")
        for row in rows
        for part in row["parts"]
        if part.get("type") == "text"
    )


class _EventBus:
    def __init__(self, services):
        self.services = services
        self.emit_with_hooks = AsyncMock()

    def get_service(self, name):
        return self.services.get(name)


class _LLM:
    def __init__(self):
        self.conversation_history = []
        self.current_parent_uuid = "parent-1"
        self.is_processing = False
        self.conversation_logger = SimpleNamespace(log_system_message=AsyncMock())
        self.hud_calls = []
        self.submit_human_input = AsyncMock(return_value={"status": "queued"})

    def queue_agent_hud(self, **kwargs):
        self.hud_calls.append(kwargs)

    def drain_pending_agent_hud(self):
        return "<agent_hud>\n[hub:peer->koordinator]\n+ raw content\n</agent_hud>"


def _plugin(*, llm, tap, acp=None, arrival_wake="none"):
    services = {"llm_service": llm, "display_tap": tap}
    if acp is not None:
        services["acp_orchestrator"] = acp
    bus = _EventBus(services)
    plugin = HubPlugin(event_bus=bus)
    plugin._identity = AgentRuntime(
        name="koordinator", identity="koordinator", agent_id="koordinator-id"
    )
    plugin.config = SimpleNamespace(
        get=lambda key, default=None: (
            arrival_wake if key == "plugins.hub.arrival_wake" else default
        )
    )
    plugin._task_ledger = None
    plugin._presence = MagicMock()
    return plugin


def _hub_events(tap):
    return [event for event in tap._history if event.get("type") == "hub_message"]


@pytest.mark.asyncio
async def test_native_hub_admission_stays_a_note_until_a_turn_starts():
    tap = DisplayTap()
    llm = _LLM()
    plugin = _plugin(llm=llm, tap=tap)
    message = HubMessage(
        id="native-wake-1",
        from_agent="peer-id",
        from_identity="peer",
        to="koordinator",
        scope=MessageScope.DIRECT.value,
        content="Please run the focused tests.",
    )

    await plugin._on_message_received(message)

    events = _hub_events(tap)
    assert len(events) == 1
    assert events[0]["hub_admission"] == "note"
    _assert_valid_stream(events, row_kind="note", message_id=message.id)


@pytest.mark.asyncio
async def test_real_hub_message_projects_one_row_into_chat_stream():
    tap = DisplayTap()
    tap.conversation_source = lambda: "conversation-a"
    llm = _LLM()
    plugin = _plugin(llm=llm, tap=tap)
    plugin._display_tap = tap
    state = SimpleNamespace(
        _event_bus=plugin.event_bus,
        _llm_service=llm,
        get_conversation_uid=lambda: "conversation-a",
        chat_stream_snapshot=lambda: {"rows": [], "turns": [], "inputs": []},
    )
    stream = ChatStreamService(state)
    stream.subscribe({"after": None, "conversation_uid": None})
    message = HubMessage(
        id="one-live-hub-row",
        from_agent="peer-id",
        from_identity="peer",
        to="koordinator",
        scope=MessageScope.DIRECT.value,
        content="Please inspect the current change.",
    )

    await plugin._on_message_received(message)

    rows = list(stream._rows["conversation-a"].values())
    assert [row["id"] for row in rows] == [message.id]
    assert rows[0]["kind"] == "note"
    assert rows[0]["parts"][0]["type"] == "hub-note"
    assert message.id not in stream._inputs.get("conversation-a", {})
    persisted_row = llm.conversation_logger.log_system_message.await_args.kwargs[
        "chat_stream_row"
    ]
    assert persisted_row["id"] == message.id
    assert persisted_row["kind"] == "note"
    assert persisted_row["parts"] == rows[0]["parts"]


@pytest.mark.asyncio
async def test_buffered_native_wake_has_no_input_lifecycle_before_turn_start():
    tap = DisplayTap()
    tap.conversation_source = lambda: "conversation-a"
    llm = _LLM()
    plugin = _plugin(llm=llm, tap=tap)
    plugin._decide_hub_wake = lambda *args, **kwargs: SimpleNamespace(
        should_trigger=True, mode="buffer", reason="native turn is busy"
    )
    plugin._display_tap = tap
    state = SimpleNamespace(
        _event_bus=plugin.event_bus,
        _llm_service=llm,
        get_conversation_uid=lambda: "conversation-a",
        chat_stream_snapshot=lambda: {"rows": [], "turns": [], "inputs": []},
    )
    stream = ChatStreamService(state)
    stream.subscribe({"after": None, "conversation_uid": None})
    message = HubMessage(
        id="buffered-native-wake",
        from_agent="peer-id",
        from_identity="peer",
        to="koordinator",
        scope=MessageScope.DIRECT.value,
        content="Please hold this for the active turn.",
    )

    await plugin._on_message_received(message)

    events = _hub_events(tap)
    assert len(events) == 1
    assert events[0]["hub_admission"] == "note"
    assert stream._rows["conversation-a"][message.id]["kind"] == "note"
    assert message.id not in stream._inputs.get("conversation-a", {})
    assert not any(
        event.get("type") == "input.state"
        and event.get("data", {}).get("id") == message.id
        for event in stream._log("conversation-a").read(
            after=0, conversation_uid="conversation-a"
        )
    )


@pytest.mark.asyncio
async def test_acp_admission_passes_hub_id_and_thread_into_steering():
    acp = SimpleNamespace(steer=AsyncMock(return_value=True))
    llm = object.__new__(LLMService)
    llm._manual_relay_origin_authorized = lambda _origin: True
    hub_note = {
        "type": "hub-note",
        "from": "peer",
        "to": "koordinator",
        "text": "Please inspect this change.",
        "observed": False,
        "thread_id": "thread-2",
    }

    result = await llm._admit_acp(
        acp,
        {
            "message": hub_note["text"],
            "request_id": "acp-steered-hub-1",
            "thread_id": "thread-2",
            "hub_message_id": "acp-steered-hub-1",
            "hub_note": hub_note,
        },
        "hub_message",
    )

    assert result == {"status": "injected", "request_id": "acp-steered-hub-1"}
    kwargs = acp.steer.await_args.kwargs
    assert kwargs["request_id"] == "acp-steered-hub-1"
    assert kwargs["thread_id"] == "thread-2"
    assert kwargs["origin"]["hub_message_id"] == "acp-steered-hub-1"
    assert kwargs["origin"]["hub_note"] == hub_note


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message, observed, presence",
    [
        (
            HubMessage(
                id="native-observed-1",
                from_agent="peer-id",
                from_identity="peer",
                to="another-agent",
                scope=MessageScope.DIRECT.value,
                content="A useful note for the team.",
            ),
            True,
            None,
        ),
        (
            HubMessage(
                id="native-presence-1",
                from_agent="peer-id",
                from_identity="peer",
                to="koordinator",
                scope=MessageScope.DIRECT.value,
                content="The peer became available.",
                metadata={"lifecycle_event": "arrival"},
            ),
            False,
            "online",
        ),
    ],
)
async def test_native_no_turn_admissions_are_note_rows(message, observed, presence):
    tap = DisplayTap()
    llm = _LLM()
    plugin = _plugin(llm=llm, tap=tap, arrival_wake="none")

    await plugin._on_message_received(message)

    events = _hub_events(tap)
    assert len(events) == 1
    _assert_valid_stream(events, row_kind="note", message_id=message.id)
    note = events[0]["hub_note"]
    assert note["observed"] is observed
    assert note.get("presence") == presence


@pytest.mark.asyncio
async def test_acp_peer_turn_carries_its_structured_note_through_admission():
    tap = DisplayTap()
    llm = _LLM()
    acp = SimpleNamespace(
        active=True, observe_notification=MagicMock(return_value=True)
    )
    plugin = _plugin(llm=llm, tap=tap, acp=acp)
    plugin._agent_dispatch_mode = lambda _llm: "acp"
    message = HubMessage(
        id="acp-peer-1",
        from_agent="peer-id",
        from_identity="peer",
        to="koordinator",
        scope=MessageScope.DIRECT.value,
        content="Please inspect this change.",
        thread_id="thread-1",
    )

    await plugin._on_message_received(message)

    llm.submit_human_input.assert_awaited_once()
    kwargs = llm.submit_human_input.await_args.kwargs
    assert kwargs["request_id"] == message.id
    note = kwargs["origin_context"]["hub_note"]
    assert note["thread_id"] == "thread-1"
    _assert_valid_stream(
        [{"message_id": message.id, "hub_note": note, "hub_admission": "input"}],
        row_kind="input",
        message_id=message.id,
    )


@pytest.mark.asyncio
async def test_acp_operator_turn_carries_its_structured_note_through_admission():
    tap = DisplayTap()
    llm = _LLM()
    acp = SimpleNamespace(
        active=True, observe_notification=MagicMock(return_value=True)
    )
    plugin = _plugin(llm=llm, tap=tap, acp=acp)
    plugin._agent_dispatch_mode = lambda _llm: "acp"
    message = HubMessage(
        id="acp-operator-1",
        from_agent="human",
        from_identity="Marco",
        to="koordinator",
        scope=MessageScope.DIRECT.value,
        content="Please run the final check.",
        metadata={"operator_message": True},
    )

    await plugin._on_message_received(message)

    llm.submit_human_input.assert_awaited_once()
    kwargs = llm.submit_human_input.await_args.kwargs
    assert kwargs["request_id"] == message.id
    assert kwargs["origin_context"]["hub_note"]["from"] == "Marco"
    _assert_valid_stream(
        [
            {
                "message_id": message.id,
                "hub_note": kwargs["origin_context"]["hub_note"],
                "hub_admission": "input",
            }
        ],
        row_kind="input",
        message_id=message.id,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        HubMessage(
            id="acp-human-observed-1",
            from_agent="human",
            from_identity="Marco",
            to="koordinator",
            scope=MessageScope.DIRECT.value,
            content="Thanks.",
            metadata={"operator_message": True},
        ),
        HubMessage(
            id="acp-direct-observed-1",
            from_agent="peer-id",
            from_identity="peer",
            to="koordinator",
            scope=MessageScope.DIRECT.value,
            content="Thanks.",
        ),
    ],
)
async def test_acp_non_waking_human_and_direct_messages_are_note_rows(message):
    tap = DisplayTap()
    llm = _LLM()
    acp = SimpleNamespace(
        active=True, observe_notification=MagicMock(return_value=True)
    )
    plugin = _plugin(llm=llm, tap=tap, acp=acp)
    plugin._agent_dispatch_mode = lambda _llm: "acp"
    plugin._decide_hub_wake = lambda *args, **kwargs: SimpleNamespace(
        should_trigger=False, mode="observe", reason="scripted no-wake"
    )

    await plugin._on_message_received(message)

    events = _hub_events(tap)
    assert len(events) == 1
    _assert_valid_stream(events, row_kind="note", message_id=message.id)
    llm.submit_human_input.assert_not_awaited()


def test_acp_observation_flush_promotes_the_message_id_to_an_input_row():
    events = []
    tap = DisplayTap(on_event=events.append)
    llm = SimpleNamespace(
        conversation_manager=None,
        conversation_logger=None,
        conversation_history=[],
        message_display_service=None,
    )
    bus = _EventBus({"llm_service": llm, "display_tap": tap})
    from kollabor.llm.acp_orchestrator import ACPOrchestrator

    orchestrator = ACPOrchestrator(bus)
    orchestrator._runner = SimpleNamespace(is_processing=False)
    orchestrator.admit = lambda text, **kwargs: SimpleNamespace(
        request_id=kwargs["request_id"]
    )
    message = HubMessage(
        id="acp-observation-1",
        from_agent="peer-id",
        from_identity="peer",
        to="*",
        scope=MessageScope.BROADCAST.value,
        content="Notice this update.",
        thread_id="thread-observe",
    )

    assert orchestrator.observe_notification(message) is True

    hub_events = [event for event in events if event.get("type") == "hub_message"]
    assert [event.get("hub_admission") for event in hub_events] == ["note", "input"]
    assert all(event["message_id"] == message.id for event in hub_events)
    _assert_valid_stream(hub_events, row_kind="input", message_id=message.id)
