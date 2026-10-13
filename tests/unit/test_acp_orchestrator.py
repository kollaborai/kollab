import pytest

from kollabor.llm import acp_orchestrator as mod
from kollabor_ai.profile_manager import LLMProfile


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
    def __init__(self, profile):
        self.profile = profile
        self.started = False
        self.closed = False
        self._updates = []

    async def start(self):
        self.started = True

    async def prompt(self, text):
        import asyncio
        await asyncio.sleep(0.03)
        self._updates.append({"kind": "text", "text": text})
        return mod.ACPResult(1, "s1", text, "end_turn", tuple(self._updates), {"type": "turn_end"}, 1)

    async def cancel(self):
        pass

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_orchestrator_owns_runner_and_publishes_semantic(monkeypatch):
    monkeypatch.setattr(mod, "ACPRunner", FakeRunner)
    bus = Bus()
    hub = Hub()
    orchestrator = mod.ACPOrchestrator(bus, identity="lapis", hub_plugin=hub)
    profile = LLMProfile(name="fake", provider="acp", command=("fake",))
    await orchestrator.start(profile)
    result = await orchestrator.submit("hello", source="rpc")
    assert result["text"] == "hello"
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
