"""Heartbeat inventory exposes the engine clock and observation window."""

import time
from types import SimpleNamespace

from kollabor_engine.routes.plugins import PluginEventHub, list_plugins
from kollabor_plugins import registry


def test_inventory_exposes_engine_time_and_heartbeat_observation_start(monkeypatch):
    entries = [{"id": "hello", "state": "ok", "services": [{"name": "worker"}]}]
    monkeypatch.setattr(registry, "plugin_entries", lambda: entries)
    before = time.time()
    hub = PluginEventHub()
    after_hub = time.time()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        plugin_events=hub, plugin_dispatcher=SimpleNamespace(errors={})
    )))
    response = list_plugins(request)
    after_response = time.time()
    assert before <= response["server_time"] <= after_response
    service = response["plugins"][0]["services"][0]
    assert before <= service["heartbeat_monitor_since"] <= after_hub
    assert service["heartbeat"] is None
