"""A stalled ACP runner close cannot hold the session transition forever (#602)."""
import asyncio
from types import SimpleNamespace

import pytest

from kollabor.llm import acp_orchestrator as mod
from kollabor.state.local import LocalStateService
from kollabor_ai.profile_manager import LLMProfile
from tests.unit.test_acp_orchestrator import Bus, FakeRunner
from tests.unit.test_loadout_manager import _FakeProfileManager, _MemoryConfig


@pytest.fixture(autouse=True)
def isolated_bindings(tmp_path, monkeypatch):
    monkeypatch.setattr("kollabor_config.config_utils.get_project_data_dir", lambda: tmp_path / "data")


def _world(monkeypatch):
    """An orchestrator whose first runner never finishes closing until released."""
    created: list = []
    start_entered = asyncio.Event()
    close_entered = asyncio.Event()
    release_close = asyncio.Event()

    class Runner(FakeRunner):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.stalls = not created
            self.late_close_finished = False
            created.append(self)

        async def start(self, binding=None):
            if not self.stalls:
                return await super().start(binding)
            start_entered.set()
            await asyncio.Event().wait()

        async def close(self):
            if not self.stalls:
                return await super().close()
            close_entered.set()
            await release_close.wait()
            self.closed = True
            self.late_close_finished = True

    monkeypatch.setattr(mod, "ACPRunner", Runner)
    monkeypatch.setattr(mod, "publish_semantic", lambda *_a, **_k: None)
    # The bound under test; absent on a build that never bounds teardown.
    monkeypatch.setattr(mod, "RUNNER_CLOSE_TIMEOUT", 0.05, raising=False)
    stalled = LLMProfile(name="stalled-acp", provider="acp", command=("fixture",))
    other = LLMProfile(name="other-acp", provider="acp", command=("fixture",))
    native = LLMProfile(name="native-profile", provider="openai", model="fixture")
    config = _MemoryConfig({})
    pm = _FakeProfileManager([stalled, other, native], config=config)
    pm.set_active_profile("stalled-acp")
    bus = Bus()
    bus.services["llm_service"] = SimpleNamespace(config=config, session_stats={})
    orch = mod.ACPOrchestrator(bus)
    bus.services["acp_orchestrator"] = orch
    state = LocalStateService(bus.services["llm_service"], pm, event_bus=bus)
    return SimpleNamespace(
        created=created, start_entered=start_entered, close_entered=close_entered,
        release_close=release_close, pm=pm, orch=orch, state=state, stalled=stalled, other=other,
    )


@pytest.mark.asyncio
async def test_cancelled_startup_with_never_returning_close_releases_the_transition(monkeypatch):
    w = _world(monkeypatch)
    startup = asyncio.create_task(w.orch.start(w.stalled))
    await asyncio.wait_for(w.start_entered.wait(), timeout=5)
    startup.cancel()
    await asyncio.wait_for(w.close_entered.wait(), timeout=5)
    old = w.created[0]
    try:
        # Cancellation still reaches the caller once teardown is bounded.
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(startup, timeout=5)
        assert not w.orch.transitioning
        assert not old.late_close_finished  # the close really is still stalled

        # Unrelated work is no longer refused as "in progress".
        snapshot = await asyncio.wait_for(
            w.state.set_active_profile("native-profile"), timeout=5)
        assert snapshot is not None
        assert w.pm.get_active_profile().name == "native-profile"

        # A successor ACP runner installs while the old close is still stalled.
        await asyncio.wait_for(w.orch.start(w.other), timeout=5)
        successor = w.orch.runner
        assert successor is not old and w.orch.active and not successor.closed

        # The old teardown finishing late cannot touch the successor.
        w.release_close.set()
        await asyncio.sleep(0.05)
        assert old.late_close_finished
        assert w.orch.runner is successor and w.orch.active and not successor.closed
        result = await w.orch.submit("hello", source="rpc")
        assert result["text"] == "hello"
    finally:
        w.release_close.set()
        await w.orch.shutdown()
