import asyncio
from types import SimpleNamespace

import pytest

from kollabor.llm import acp_orchestrator as mod
from kollabor_ai.profile_manager import LLMProfile
from kollabor_tui.display_tap import DisplayTap


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
    def __init__(self, profile, update_callback=None, permission_decider=None, permission_bridge=None):
        self.profile = profile
        self.update_callback = update_callback
        self.started = False
        self.closed = False
        self._updates = []

    async def start(self):
        self.started = True

    def admit(self, text, *, request_id, source, thread_id=None):
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
        return SimpleNamespace(request_id=request_id, future=future)

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
        def __init__(self, profile, update_callback=None, permission_decider=None, permission_bridge=None):
            super().__init__(profile, update_callback)
            self.start_count = 0
            created.append(self)

        async def start(self):
            self.start_count += 1
            self.started = True
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
