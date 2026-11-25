"""Hub admissions retain their ids and structured notes in the chat stream."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kollabor_events.chat_stream import sequence_problems
from kollabor.state.chat_stream import ChatStreamLog
from kollabor_tui.display_tap import DisplayTap
from kollabor_agent.runtime import AgentRuntime
from plugins.hub.models import HubMessage, MessageScope
from plugins.hub.plugin import HubPlugin


def _script_stream(hub_events):
    """Project captured admission events into the shared row/lifecycle contract."""
    log = ChatStreamLog(
        "conversation-a",
        snapshot_provider=lambda: {"rows": [], "turns": [], "inputs": []},
    )
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

    snapshot = log.read(after=None)[0]
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
async def test_native_turn_start_is_an_input_row_under_the_hub_message_id():
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
    _assert_valid_stream(events, row_kind="input", message_id=message.id)


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
