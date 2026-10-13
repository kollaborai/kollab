import asyncio
from types import SimpleNamespace

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
    def __init__(self, profile, update_callback=None):
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
