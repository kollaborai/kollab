"""Restart waits for the same stop grace the wrapper uses (30 s default), not a separate 10 s (#1028)."""

import asyncio
from types import SimpleNamespace

import pytest
from kollabor_engine.routes import plugins as routes


@pytest.mark.parametrize("service", [{}, {"stop_timeout_s": 10**1000}])
def test_restart_waits_for_the_default_stop_grace(tmp_path, monkeypatch, service):
    import kollabor_plugins.services as services

    clock = [0.0]
    real_sleep = asyncio.sleep

    async def fake_sleep(seconds):
        clock[0] += seconds
        await real_sleep(0)

    monkeypatch.setattr(routes.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(routes.asyncio, "sleep", fake_sleep)
    # The old process takes 20 s to exit: inside the 30 s grace, past a 10 s + 5 s budget.
    monkeypatch.setattr(services, "_recorded_process_alive", lambda _pid: clock[0] < 20)
    started = []
    monkeypatch.setattr(services, "control_service", lambda *args, **kwargs: started.append(args))

    controls = routes.PluginServiceControls(routes.PluginEventHub())
    context = SimpleNamespace(id="hello", manifest={"services": {"worker": service}}, data=tmp_path)
    asyncio.run(controls._finish_restart(context, "worker"))
    assert started == [("hello", "worker", "start")]


@pytest.mark.parametrize("value", [10**1000, 86401])
def test_inventory_drops_an_unbounded_heartbeat(tmp_path, value):
    """An old approved manifest skips validate_manifest; inventory still applies the manifest bound."""
    from kollabor_plugins.registry import _service_entries

    manifest = {"services": {"worker": {"command": ["/bin/true"], "heartbeat_s": value}}}
    (entry,) = _service_entries(tmp_path, "hello", manifest)
    assert entry["heartbeat_s"] is None


def test_restart_gives_up_exactly_at_the_shared_grace_plus_buffer(tmp_path, monkeypatch):
    """Pins the budget: stop_timeout() + 5 s, so a shortened default fails this test."""
    import kollabor_plugins.packages as packages
    import kollabor_plugins.services as services

    clock = [0.0]
    real_sleep = asyncio.sleep

    async def fake_sleep(seconds):
        clock[0] += seconds
        await real_sleep(0)

    monkeypatch.setattr(routes.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(routes.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(services, "_recorded_process_alive", lambda _pid: True)
    started = []
    monkeypatch.setattr(services, "control_service", lambda *args, **kwargs: started.append(args))
    grace = packages.stop_timeout({})
    controls = routes.PluginServiceControls(routes.PluginEventHub())
    context = SimpleNamespace(id="hello", manifest={"services": {"worker": {}}}, data=tmp_path)
    asyncio.run(controls._finish_restart(context, "worker"))
    assert grace == 30
    assert started == []
    assert grace + 5 <= clock[0] < grace + 5 + 0.5
