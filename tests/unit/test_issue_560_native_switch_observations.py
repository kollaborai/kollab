"""Issue #560: a switch from an ACP profile to a native one answers buffered peer messages.

Peer broadcasts that arrive while the ACP harness is busy are written to the
conversation as they arrive and kept in the orchestrator until its turn ends.
Closing the harness for a native profile used to leave them there: the native
model had them in its history but nothing started a turn, and a later switch
back to ACP would offer them to a harness again.

All collaborators are in-process fakes: no adapter process is started.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio

from kollabor.llm import acp_orchestrator as mod
from kollabor.llm.llm_coordinator import LLMService
from kollabor.state.local import LocalStateService
from kollabor_ai.loadout_manager import Loadout, LoadoutManager
from kollabor_ai.profile_manager import LLMProfile
from kollabor_events import EventType
from plugins.hub.models import HubMessage


class _Runner:
    """Minimal ACP runner: starts, records what it is asked to run, closes."""

    def __init__(self, profile, **_kwargs):
        self.profile = profile
        self.session_id = "s1"
        self.generation = 1
        self.binding = None
        self.state = "ready"
        self.resume_mode = "fresh"
        self.is_processing = False
        self.admitted = []

    async def start(self, binding=None):
        pass

    def admit(self, text, *, request_id, source, thread_id=None, origin=None, **_kw):
        self.admitted.append(text)
        future = asyncio.get_running_loop().create_future()
        future.set_result(
            mod.ACPResult(
                1, "s1", text, "end_turn", (), {"type": "turn_end"}, 1,
                request_id, source, thread_id,
            )
        )
        return mod.ACPAdmission(
            request_id, source, thread_id, 1, 1, text, future, origin or {}
        )

    async def close(self):
        pass


class _Bus:
    def __init__(self):
        self.services = {}
        self.emitted = []

    def get_service(self, name):
        return self.services.get(name)

    def register_service(self, name, service):
        self.services[name] = service

    async def emit_with_hooks(self, event_type, data, source):
        self.emitted.append((event_type, data))
        return {}


class _ProfileManager:
    def __init__(self, profiles, active):
        self.profiles = {p.name: p for p in profiles}
        self.active = active

    def get_profile(self, name):
        return self.profiles.get(name)

    def get_active_profile(self):
        return self.profiles.get(self.active)

    def get_profile_names(self):
        return list(self.profiles)

    def set_active_profile(self, name, persist=True):
        if name not in self.profiles:
            return False
        self.active = name
        return True


ACP = LLMProfile(name="harness", provider="acp", command=("fake",))
NATIVE = LLMProfile(
    name="native", provider="openai", model="gpt-test", api_key="k",
    base_url="http://127.0.0.1:1/v1",
)


def _peer_note():
    return HubMessage(
        action="message", from_agent="peer-id", from_identity="peer",
        to="*", content="PEER_NOTE", thread_id=None, reply_to="",
    )


@pytest_asyncio.fixture
async def harness(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "kollabor_config.config_utils.get_project_data_dir", lambda: tmp_path / "data"
    )
    monkeypatch.setattr(mod, "ACPRunner", _Runner)
    monkeypatch.setattr(mod, "publish_semantic", lambda *a, **k: None)
    bus = _Bus()
    orchestrator = mod.ACPOrchestrator(bus)
    await orchestrator.start(ACP)
    yield bus, orchestrator
    await orchestrator.close()


def _buffer_peer_note(orchestrator):
    """A peer broadcast arrives while the harness is mid-turn: it is buffered."""
    orchestrator.runner.is_processing = True
    orchestrator.observe_notification(_peer_note())


def _continues(bus):
    return [d for t, d in bus.emitted if t == EventType.TRIGGER_LLM_CONTINUE]


def _llm_service(bus, manager, monkeypatch):
    service = LLMService.__new__(LLMService)
    service._provider_lock = asyncio.Lock()
    service.profile_manager = manager
    service.event_bus = bus
    service.api_service = Mock()
    service.api_service.reinitialize_provider = AsyncMock(return_value=True)
    service.conversation_logger = Mock()
    service._current_provider = SimpleNamespace(provider_name="openai", model="gpt-test")
    service._provider_registry = Mock()
    service._provider_registry.get_provider = AsyncMock(
        return_value=service._current_provider
    )
    monkeypatch.setattr(LLMService, "_announce_hub_model_switch", AsyncMock())
    return service


def _history_manager(bus):
    """A conversation manager stand-in: add_message appends a row and returns its uuid."""
    manager = SimpleNamespace(messages=[], current_session_id="session")

    def add_message(role, content, metadata=None):
        message_uuid = f"row-{len(manager.messages)}"
        manager.messages.append(
            {"uuid": message_uuid, "role": role, "content": content, "metadata": metadata or {}}
        )
        return message_uuid

    manager.add_message = add_message
    bus.register_service("llm_service", SimpleNamespace(conversation_manager=manager))
    return manager


def _script_writes(manager, monkeypatch, outcomes):
    """Each history write takes the next outcome, then appends normally.

    fails_before_append: the write raises and no row lands. lands_then_raises: the
    row is appended, then the write raises (an autosave failure).
    """
    real_add_message = manager.add_message
    pending = list(outcomes)

    def add_message(*args, **kwargs):
        outcome = pending.pop(0) if pending else "appends"
        if outcome == "fails_before_append":
            raise OSError(28, "No space left on device")
        message_uuid = real_add_message(*args, **kwargs)
        if outcome == "lands_then_raises":
            raise OSError(28, "No space left on device")
        return message_uuid

    monkeypatch.setattr(manager, "add_message", add_message)


@pytest.mark.asyncio
async def test_coordinator_native_switch_starts_one_turn_for_buffered_messages(
    harness, monkeypatch
):
    bus, orchestrator = harness
    _buffer_peer_note(orchestrator)
    manager = _ProfileManager([ACP, NATIVE], "harness")
    service = _llm_service(bus, manager, monkeypatch)

    assert await service.switch_profile("native", persist=False) is True

    assert manager.active == "native"
    assert len(_continues(bus)) == 1

    # Switching back must not offer the messages to a harness a second time.
    await orchestrator.start(ACP)
    orchestrator._flush_observations()
    assert orchestrator.runner.admitted == []


@pytest.mark.asyncio
async def test_state_rpc_native_switch_starts_one_turn_for_buffered_messages(harness):
    bus, orchestrator = harness
    _buffer_peer_note(orchestrator)
    manager = _ProfileManager([ACP, NATIVE], "harness")
    llm = SimpleNamespace(
        api_service=SimpleNamespace(reinitialize_provider=AsyncMock(return_value=True))
    )
    state = LocalStateService(llm_service=llm, profile_manager=manager, event_bus=bus)

    snapshot = await state.set_active_profile("native")

    assert snapshot.name == "native"
    assert len(_continues(bus)) == 1


@pytest.mark.asyncio
async def test_native_switch_without_buffered_messages_starts_no_turn(
    harness, monkeypatch
):
    bus, _orchestrator = harness
    manager = _ProfileManager([ACP, NATIVE], "harness")
    service = _llm_service(bus, manager, monkeypatch)

    assert await service.switch_profile("native", persist=False) is True

    assert _continues(bus) == []


@pytest.mark.asyncio
async def test_handover_does_not_start_a_turn_for_a_message_whose_row_still_fails(
    harness, monkeypatch, caplog
):
    """A buffered message whose history row still fails at handover stays pending; no turn starts."""
    bus, orchestrator = harness

    def failing_write(*_args, **_kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(orchestrator, "_record_user", failing_write)
    _buffer_peer_note(orchestrator)
    assert len(orchestrator._unwritten_observations) == 1

    await orchestrator.hand_over_to_native()

    assert _continues(bus) == []
    assert len(orchestrator._unwritten_observations) == 1
    assert "ACP observation history write failed again (OSError)" in caplog.text


@pytest.mark.asyncio
async def test_handover_shows_a_retried_row_whose_history_write_fails_in_background(
    harness, monkeypatch
):
    """The retry lands the row; its history write then fails in the background.

    The handover waits for that write, so the failure is shown in the chat (as #803 shows a
    failed turn row) before the native turn starts.
    """
    bus, orchestrator = harness
    manager = _history_manager(bus)
    real_add_message = manager.add_message
    failures = [OSError(28, "No space left on device")]

    def fails_before_the_row_lands(*args, **kwargs):
        if failures:
            raise failures.pop()
        return real_add_message(*args, **kwargs)

    monkeypatch.setattr(manager, "add_message", fails_before_the_row_lands)
    events, shown = [], []

    async def log_user_message(_text):
        await asyncio.sleep(0)
        events.append("write-failed")
        raise OSError(28, "No space left on device")

    llm = bus.get_service("llm_service")
    llm.conversation_logger = SimpleNamespace(log_user_message=log_user_message)
    llm.message_display_service = SimpleNamespace(display_system_message=shown.append)
    real_emit = bus.emit_with_hooks

    async def emit(event_type, data, source):
        if event_type == EventType.TRIGGER_LLM_CONTINUE:
            events.append("continue")
        return await real_emit(event_type, data, source)

    monkeypatch.setattr(bus, "emit_with_hooks", emit)
    _buffer_peer_note(orchestrator)
    assert manager.messages == []

    await orchestrator.hand_over_to_native()

    assert len(manager.messages) == 1
    assert shown == ["Conversation history not saved for this message."]
    assert events == ["write-failed", "continue"]


@pytest.mark.asyncio
async def test_background_observation_write_failure_is_shown_to_the_user(harness):
    """An observation row whose history write fails in the background is shown, not only logged."""
    bus, orchestrator = harness
    _history_manager(bus)
    llm = bus.get_service("llm_service")
    llm.conversation_logger = SimpleNamespace(
        log_user_message=AsyncMock(side_effect=OSError(28, "No space left on device"))
    )
    shown = []
    llm.message_display_service = SimpleNamespace(display_system_message=shown.append)

    orchestrator._record_user(
        "observations-background", "PEER_NOTE", "observation", None, track_completion=True
    )
    await asyncio.gather(*list(orchestrator._history_tasks), return_exceptions=True)

    assert shown == ["Conversation history not saved for this message."]


@pytest.mark.asyncio
async def test_handover_writes_a_queued_message_whose_arrival_row_failed(
    harness, monkeypatch
):
    """The retry queue holds (row id, entry) pairs; the handover writes each and starts the turn."""
    bus, orchestrator = harness
    manager = _history_manager(bus)
    real_add_message = manager.add_message
    failures = [OSError(28, "No space left on device")]

    def fails_before_the_row_lands(*args, **kwargs):
        if failures:
            raise failures.pop()
        return real_add_message(*args, **kwargs)

    monkeypatch.setattr(manager, "add_message", fails_before_the_row_lands)
    _buffer_peer_note(orchestrator)
    assert manager.messages == []
    assert len(orchestrator._unwritten_observations) == 1

    await orchestrator.hand_over_to_native()

    assert len(_continues(bus)) == 1
    assert sum("PEER_NOTE" in m["content"] for m in manager.messages) == 1
    assert len(orchestrator._unwritten_observations) == 0


@pytest.mark.asyncio
async def test_handover_keeps_the_first_row_when_its_failed_write_had_landed(
    harness, monkeypatch
):
    """A row that landed before its write raised is reused, not appended again (#581)."""
    bus, orchestrator = harness
    manager = _history_manager(bus)
    real_add_message = manager.add_message
    failures = [OSError(28, "No space left on device")]

    def lands_then_raises(*args, **kwargs):
        message_uuid = real_add_message(*args, **kwargs)
        if failures:
            raise failures.pop()
        return message_uuid

    monkeypatch.setattr(manager, "add_message", lands_then_raises)
    _buffer_peer_note(orchestrator)
    assert len(manager.messages) == 1
    assert len(orchestrator._unwritten_observations) == 1

    await orchestrator.hand_over_to_native()

    assert len(_continues(bus)) == 1
    assert sum("PEER_NOTE" in m["content"] for m in manager.messages) == 1
    assert len(orchestrator._unwritten_observations) == 0


@pytest.mark.asyncio
async def test_a_fallback_batch_that_landed_before_its_write_raised_is_not_written_again(
    harness, monkeypatch
):
    """The arrival and its handover retry fail before append; the fallback batch lands, then raises."""
    bus, orchestrator = harness
    manager = _history_manager(bus)
    _script_writes(
        manager, monkeypatch, ["fails_before_append", "fails_before_append", "lands_then_raises"]
    )
    _buffer_peer_note(orchestrator)

    await orchestrator.hand_over_to_native()
    await orchestrator.hand_over_to_native()  # a later retry must find nothing to write

    assert sum("PEER_NOTE" in m["content"] for m in manager.messages) == 1
    assert len(_continues(bus)) == 1
    assert len(orchestrator._unwritten_observations) == 0


@pytest.mark.asyncio
async def test_a_handover_retry_that_landed_before_its_write_raised_is_not_written_again(
    harness, monkeypatch
):
    """The arrival fails before append; the handover's retry lands, then raises."""
    bus, orchestrator = harness
    manager = _history_manager(bus)
    _script_writes(manager, monkeypatch, ["fails_before_append", "lands_then_raises"])
    _buffer_peer_note(orchestrator)

    await orchestrator.hand_over_to_native()

    assert sum("PEER_NOTE" in m["content"] for m in manager.messages) == 1
    assert len(_continues(bus)) == 1
    assert len(orchestrator._unwritten_observations) == 0


class _ProfilesWithUpdate(_ProfileManager):
    def update_profile(self, name, **_kwargs):
        return name in self.profiles


@pytest.mark.asyncio
async def test_loadout_direct_fallback_hands_buffered_messages_to_native(
    harness, monkeypatch
):
    """A loadout switch to native with no state service still starts the one answering turn."""
    bus, orchestrator = harness
    _buffer_peer_note(orchestrator)
    manager = _ProfilesWithUpdate([ACP, NATIVE], "harness")
    bus.register_service("acp_orchestrator", orchestrator)
    bus.register_service(
        "llm_service",
        SimpleNamespace(
            api_service=SimpleNamespace(reinitialize_provider=AsyncMock(return_value=True)),
            _load_native_tools=AsyncMock(),
        ),
    )
    monkeypatch.setattr(LoadoutManager, "_announce_model_switch", AsyncMock())
    loadout = Loadout(name="native", provider_profile="native", model="gpt-test")

    assert await LoadoutManager(manager).activate(loadout, event_bus=bus) == loadout

    assert manager.active == "native"
    assert len(_continues(bus)) == 1
