from types import SimpleNamespace

import pytest

from kollabor.application import TerminalLLMChat


class FakeRunner:
    instances = []

    def __init__(self, event_bus, *, identity, hub_plugin):
        self.event_bus = event_bus
        self.identity = identity
        self.hub_plugin = hub_plugin
        self.profile = None
        self.started = 0
        self.closed = 0
        self.__class__.instances.append(self)

    async def start(self, profile):
        self.profile = profile
        self.started += 1

    async def close(self):
        self.closed += 1


@pytest.mark.asyncio
async def test_application_starts_one_acp_runner_for_selected_profile(monkeypatch):
    import kollabor.llm.acp_orchestrator as module

    FakeRunner.instances.clear()
    monkeypatch.setattr(module, "ACPOrchestrator", FakeRunner)
    profile = SimpleNamespace(name="claude-code", provider="acp")
    hub = object()
    event_bus = SimpleNamespace(get_service=lambda name: hub if name == "hub_plugin" else None)
    app = TerminalLLMChat.__new__(TerminalLLMChat)
    app._attach_to = None
    app.acp_orchestrator = None
    app.profile_manager = SimpleNamespace(get_active_profile=lambda: profile)
    app.event_bus = event_bus
    app.args = SimpleNamespace(as_identity="daemon-proof")

    await app._initialize_acp_orchestrator()
    await app._initialize_acp_orchestrator()

    assert len(FakeRunner.instances) == 1
    runner = FakeRunner.instances[0]
    assert runner.started == 1
    assert runner.identity == "daemon-proof"
    assert runner.hub_plugin is hub
    assert runner.profile is profile

    await app.acp_orchestrator.close()
    assert runner.closed == 1


@pytest.mark.asyncio
async def test_application_does_not_publish_runner_after_start_failure(monkeypatch):
    import kollabor.llm.acp_orchestrator as module

    class FailingRunner(FakeRunner):
        async def start(self, profile):
            raise RuntimeError("adapter unavailable")

    monkeypatch.setattr(module, "ACPOrchestrator", FailingRunner)
    app = TerminalLLMChat.__new__(TerminalLLMChat)
    app._attach_to = None
    app.acp_orchestrator = None
    app.profile_manager = SimpleNamespace(
        get_active_profile=lambda: SimpleNamespace(name="claude-code", provider="acp")
    )
    app.event_bus = SimpleNamespace(get_service=lambda _name: None)
    app.args = SimpleNamespace(as_identity="daemon-proof")

    with pytest.raises(RuntimeError, match="adapter unavailable"):
        await app._initialize_acp_orchestrator()
    assert app.acp_orchestrator is None
