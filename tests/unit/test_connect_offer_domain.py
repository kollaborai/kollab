"""L6: a window that cannot name the network's domain still gets a code, and a
fresh device can join a self-hosted relay from the guided form."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from kollabor.state.handlers import register_state_handlers
from plugins.hub.plugin import HubPlugin

OFFER = {"status": "offered", "offer_id": "a" * 32, "expires_at": "123", "code": "ABCD-EFGH"}


def _daemon(origin="https://relay.example.test"):
    seen = []

    async def create(domain):
        seen.append(domain)
        return dict(OFFER)

    plugin = HubPlugin.__new__(HubPlugin)
    plugin._identity = SimpleNamespace(agent_id="a")
    plugin._rpc_server = object()
    plugin._relay_commands = SimpleNamespace(client=SimpleNamespace(state=SimpleNamespace(origin=origin)))
    plugin._relay_agent = SimpleNamespace(create_enrollment_offer=create)
    plugin._connect_via_owner = lambda op, args: _none()
    plugin._start_relay_agent = lambda: _none()
    return plugin, seen


async def _none():
    return False, None


@pytest.mark.asyncio
async def test_an_empty_domain_means_the_daemons_own_network():
    plugin, seen = _daemon()
    result = await plugin._run_connect_enrollment_offer("")
    assert result["status"] == "offered" and seen == ["relay.example.test"]
    plugin, seen = _daemon(origin="")
    await plugin._run_connect_enrollment_offer("")
    assert seen == ["kollabor.ai"]
    plugin, seen = _daemon()
    await plugin._run_connect_enrollment_offer("other.example.test")
    assert seen == ["other.example.test"]


@pytest.mark.asyncio
async def test_the_state_handler_accepts_an_empty_domain():
    calls = []

    class State:
        async def hub_enrollment_offer(self, domain):
            calls.append(domain)
            return dict(OFFER)

    rpc = SimpleNamespace(methods={})
    rpc.register = lambda name, fn, *a, **k: rpc.methods.__setitem__(name, fn)
    register_state_handlers(rpc, State())
    out = await rpc.methods["state.hub_enrollment_offer"]({"domain": ""})
    assert out.get("status") == "offered" and calls == [""]
    bad = await rpc.methods["state.hub_enrollment_offer"]({"domain": "x\x00"})
    assert bad == {"error": "invalid connect offer request"}


@pytest.mark.asyncio
async def test_the_join_form_says_how_to_use_another_relay():
    from plugins.altview.connect_altview import ConnectAltView

    from .test_connect_attached_and_waiting import _FakeRenderer

    view = ConnectAltView(domain="kollabor.ai")
    renderer = _FakeRenderer((100, 30))
    await view.on_enter(renderer)
    await view.render_frame(0.0)
    assert "another relay" in renderer.text() and "tab" in renderer.text()


@pytest.mark.asyncio
async def test_the_guided_join_starts_on_the_known_relay_domain():
    from unittest.mock import AsyncMock

    plugin, _seen = _daemon(origin="https://relay.example.test")
    plugin._open_connect_altview = AsyncMock(return_value="")
    await plugin._guided_join()
    plugin._open_connect_altview.assert_awaited_once_with("relay.example.test")
