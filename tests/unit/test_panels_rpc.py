"""get_panel / panel_action across the RPC boundary: daemon handlers <-> RemoteStateService."""

import asyncio
from types import SimpleNamespace

import pytest

from kollabor.panels import PanelError
from kollabor.state.handlers import register_state_handlers
from kollabor.state.local import LocalStateService
from kollabor.state.remote import RemoteStateService


class _Config:
    def get(self, path, default=None):
        return default

    def set(self, path, value):
        pass

    def save_key(self, *args, **kwargs):
        return True


class _Rpc:
    """Routes RpcClient.call straight to the daemon's registered handlers."""

    def __init__(self, handlers):
        self.handlers = handlers
        self.last_timeout = None

    async def call(self, method, params, timeout=None):
        self.last_timeout = timeout
        return await self.handlers[method](params)


@pytest.fixture
def wire(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    service = LocalStateService.__new__(LocalStateService)
    service._llm_service = SimpleNamespace(config=_Config())
    service._event_bus = None
    handlers = {}
    register_state_handlers(
        SimpleNamespace(register=lambda *a, **k: handlers.__setitem__(a[0], a[1])),
        service,
    )
    rpc = _Rpc(handlers)
    return RemoteStateService(rpc), rpc, handlers


def run(coro):
    return asyncio.run(coro)


def test_describe_round_trips(wire):
    remote, _, _ = wire
    view = run(remote.get_panel("config", {}))
    assert (view["panel"], view["kind"]) == ("config", "form")


def test_unknown_panel_keeps_its_404(wire):
    remote, _, _ = wire
    with pytest.raises(PanelError) as err:
        run(remote.get_panel("nope"))
    assert err.value.status == 404
    with pytest.raises(PanelError) as err:
        run(remote.panel_action("nope", "save", {}))
    assert err.value.status == 404


def test_action_errors_and_status_survive_the_wire(wire):
    remote, _, _ = wire
    refused = run(
        remote.panel_action(
            "config", "save", {"changes": {"no.such": 1}, "target": "global"}
        )
    )
    assert refused["ok"] is False and refused["errors"] == {
        "no.such": "unknown setting"
    }
    with pytest.raises(PanelError) as err:
        run(remote.panel_action("config", "save", {"changes": {}, "target": "x"}))
    assert err.value.status == 400
    with pytest.raises(PanelError) as err:
        run(remote.panel_action("config", "explode", {}))
    assert err.value.status == 404


def test_panel_action_outlasts_the_default_timeout(wire):
    remote, rpc, _ = wire
    run(remote.panel_action("config", "save", {"changes": {}, "target": "global"}))
    assert rpc.last_timeout >= 40.0
    run(remote.get_panel("config"))
    assert rpc.last_timeout == RemoteStateService.DEFAULT_TIMEOUT


def test_handlers_reject_malformed_requests(wire):
    _, _, handlers = wire
    bad = run(handlers["state.get_panel"]({"name": 5}))
    assert bad == {"error": "invalid panel request", "status": 400}
    bad = run(
        handlers["state.panel_action"]({"name": "config", "action": "save", "x": 1})
    )
    assert bad["status"] == 400
