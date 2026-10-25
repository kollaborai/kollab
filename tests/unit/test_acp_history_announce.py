"""A history row that lands mid-turn reaches watchers before turn_complete (#266)."""

import asyncio
import queue
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from kollabor.llm import acp_orchestrator as mod
from kollabor_ai.profile_manager import LLMProfile
from kollabor_tui.display_tap import DisplayTap


class Bus:
    def __init__(self):
        self.services = {}

    def get_service(self, name):
        return self.services.get(name)

    def register_service(self, name, service):
        self.services[name] = service


class BusyRunner:
    """Admits into a future the test resolves, so a turn can stay open."""

    def __init__(self, profile, update_callback=None, permission_decider=None,
                 permission_bridge=None, lifecycle_callback=None):
        self.profile = profile
        self.update_callback = update_callback
        self.is_processing = False
        self.session_id = "s1"
        self.generation = 1
        self.binding = None
        self.admissions = {}

    async def start(self, binding=None):
        pass

    def admit(self, text, *, request_id, source, thread_id=None, origin=None):
        future = asyncio.get_running_loop().create_future()
        self.admissions[request_id] = (text, future)
        return mod.ACPAdmission(request_id, source, thread_id, 1, 1, text, future, origin or {})

    async def cancel(self):
        pass

    def cancel_queued(self, request_id):
        self.admissions.pop(request_id, None)

    async def close(self):
        pass


def drain(events):
    result = []
    while True:
        try:
            result.append(events.get_nowait())
        except queue.Empty:
            return result


@pytest.mark.asyncio
async def test_midturn_history_row_announces_before_turn_complete(tmp_path, monkeypatch):
    monkeypatch.setattr("kollabor_config.config_utils.get_project_data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(mod, "ACPRunner", BusyRunner)
    tap = DisplayTap()
    events = tap.subscribe("observer")
    bus = Bus()
    bus.services["display_tap"] = tap
    llm = SimpleNamespace(
        conversation_manager=MagicMock(),
        conversation_logger=None,
        conversation_history=[],
        message_display_service=MagicMock(),
    )
    bus.services["llm_service"] = llm
    orchestrator = mod.ACPOrchestrator(bus)
    await orchestrator.start(LLMProfile(name="fake", provider="acp", command=("fake",)))
    try:
        # A turn is already running: started, never finished.
        first = orchestrator.admit("first", request_id="turn-1", source="rpc")
        orchestrator.runner.is_processing = True
        drain(events)

        # A peer's message admitted mid-turn lands its user row now.
        orchestrator.admit("peer hello", request_id="peer-1", source="agent")
        await asyncio.sleep(0)

        observed = drain(events)
        kinds = [event.get("type") for event in observed]
        assert "hub_message" in kinds
        assert "turn_complete" not in kinds
        assert llm.conversation_manager.add_message.call_count == 2
        assert not first.future.done()

        # The turn still ends with its own terminal event, after the announce.
        for request_id, (_, future) in orchestrator.runner.admissions.items():
            if not future.done():
                future.set_result(mod.ACPResult(
                    1, "s1", "done", "end_turn", (), {"type": "turn_end"},
                    1, request_id, "rpc", None,
                ))
        await asyncio.wait_for(first.future, 2)
        await asyncio.wait_for(asyncio.gather(*orchestrator._completion_tasks), 2)
        later = drain(events)
        assert any(event.get("type") == "turn_complete" for event in later)
    finally:
        for _, (_, future) in getattr(orchestrator.runner, "admissions", {}).items():
            if not future.done():
                future.set_result(mod.ACPResult(
                    1, "s1", "", "cancelled", (), {"type": "turn_end"},
                    1, "cleanup", "rpc", None,
                ))
        await asyncio.wait_for(asyncio.gather(
            *orchestrator._completion_tasks, return_exceptions=True
        ), 2)
        await orchestrator.close()


@pytest.mark.asyncio
async def test_opening_row_and_human_turn_stay_quiet(tmp_path, monkeypatch):
    monkeypatch.setattr("kollabor_config.config_utils.get_project_data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(mod, "ACPRunner", BusyRunner)
    tap = DisplayTap()
    events = tap.subscribe("observer")
    bus = Bus()
    bus.services["display_tap"] = tap
    llm = SimpleNamespace(
        conversation_manager=MagicMock(),
        conversation_logger=None,
        conversation_history=[],
        message_display_service=MagicMock(),
    )
    bus.services["llm_service"] = llm
    orchestrator = mod.ACPOrchestrator(bus)
    await orchestrator.start(LLMProfile(name="fake", provider="acp", command=("fake",)))
    try:
        orchestrator.admit("human turn", request_id="human-1", source="rpc")
        await asyncio.sleep(0)
        assert "hub_message" not in [event.get("type") for event in drain(events)]
    finally:
        for _, (_, future) in getattr(orchestrator.runner, "admissions", {}).items():
            if not future.done():
                future.set_result(mod.ACPResult(
                    1, "s1", "", "cancelled", (), {"type": "turn_end"},
                    1, "cleanup", "rpc", None,
                ))
        await asyncio.wait_for(asyncio.gather(
            *orchestrator._completion_tasks, return_exceptions=True
        ), 2)
        await orchestrator.close()
