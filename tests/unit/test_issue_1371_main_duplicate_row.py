"""The ACP Send Now path reuses the staged input row for its sender ID."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from kollabor.llm.acp_bridge import ACPProfile, ACPRunner
from kollabor.llm.acp_orchestrator import ACPOrchestrator
from kollabor.state.chat_stream import ChatStreamService
from kollabor_tui.display_tap import DisplayTap


class _QueueRuntime:
    async def input_scope(self, conversation_uid):
        return ("scope", conversation_uid, "principal")

    async def add_input(self, params):
        return {"accepted": True, "id": params["id"], "state": "queued"}


class _Bus:
    def __init__(self, llm_service):
        self.tap = DisplayTap()
        self.tap.conversation_source = lambda: "conversation-a"
        self.services = {
            "display_tap": self.tap,
            "web_message_queue_runtime": _QueueRuntime(),
            "llm_service": llm_service,
        }

    def get_service(self, name):
        return self.services.get(name)

    def register_service(self, name, service):
        self.services[name] = service


class _State:
    def __init__(self):
        self._llm_service = SimpleNamespace(
            conversation_manager=None,
            conversation_history=[],
            message_display_service=None,
        )
        self._event_bus = _Bus(self._llm_service)
        self.snapshot_rows = []

    def get_conversation_uid(self):
        return "conversation-a"

    def chat_stream_snapshot(self):
        return {"rows": list(self.snapshot_rows), "turns": [], "inputs": []}


class _ACPDaemon:
    async def ext_method(self, _method, _params):
        return None


@pytest.mark.asyncio
async def test_steered_sender_id_keeps_one_user_row_in_the_chat_stream():
    state = _State()
    stream = ChatStreamService(state)
    stream.attach_display_tap()
    await stream.add_input({"id": "active-A", "text": "start", "attachments": []})
    state._event_bus.tap.publish(
        {"type": "turn_start", "request_id": "active-A", "turn_id": 4}
    )
    await stream.add_input({"id": "client-B", "text": "continue", "attachments": []})

    runner = ACPRunner(ACPProfile("issue-1371", ("unused",)))
    runner.session_id = "session-A"
    runner.generation = 7
    runner._active_generation = 7
    runner._active_turn_id = 4
    runner._active_context = SimpleNamespace(
        request_id="active-A",
        turn_id=4,
        thread_id="thread-A",
        origin={"operator": True},
    )
    runner.initialize_metadata = {"steering": {"supported": True}}
    runner._connection = _ACPDaemon()

    orchestrator = object.__new__(ACPOrchestrator)
    orchestrator.event_bus = state._event_bus
    orchestrator._runner = runner
    orchestrator._parents = {}

    result = await orchestrator.steer(
        "continue",
        origin={"operator": True},
        thread_id="thread-A",
        request_id="client-B",
    )

    snapshot = stream.subscribe({"after": None, "conversation_uid": None})[
        "events"
    ][0]["data"]
    sender_rows = [row for row in snapshot["rows"] if row["id"] == "client-B"]

    assert result["joined_request_id"] == "active-A"
    assert result["joined_turn_id"] == 4
    assert len(sender_rows) == 1
    assert sender_rows[0]["parts"] == [{"type": "text", "text": "continue"}]
