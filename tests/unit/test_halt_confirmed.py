"""L2: halt, resume and revoke acts need `confirmed: true`, checked on the server side."""

from __future__ import annotations

import asyncio

import pytest

from kollabor.panels import get_panel
from plugins.hub import halt_runtime as hr
from plugins.hub import network_halt as nh
from plugins.hub.relay_agent import RelayAgentBridge
from plugins.hub.relay_state import RelayError

from .test_panels_connect_halt import MEMBERS, Ctx, snap

ACTS = ["halt", "propose_resume", "confirm_resume", "resume_local", "confirm_revoke"]


@pytest.mark.parametrize("action", ACTS)
def test_the_core_check_refuses_an_unconfirmed_act(action):
    with pytest.raises(nh.HaltError) as err:
        nh.require_confirmed(action, {})
    assert "confirm" in str(err.value).lower()
    for bad in (None, {"confirmed": "true"}, {"confirmed": 1}, {"confirmed": False}):
        with pytest.raises(nh.HaltError):
            nh.require_confirmed(action, bad)
    nh.require_confirmed(action, {"confirmed": True})
    nh.require_confirmed("count", {})  # not a halt, resume or revoke
    nh.require_confirmed("confirm_machine", {})


@pytest.mark.parametrize("action", ACTS)
def test_the_daemon_refuses_an_unconfirmed_act(action):
    bridge = object.__new__(RelayAgentBridge)
    with pytest.raises(RelayError) as err:
        asyncio.run(bridge.hub_halt_act(action, {}))
    assert "confirm" in str(err.value).lower()


@pytest.mark.parametrize("action", ACTS)
def test_the_halt_listener_refuses_an_unconfirmed_act(action):
    link = hr.HaltLink.__new__(hr.HaltLink)
    with pytest.raises(RelayError) as err:
        asyncio.run(link.hub_halt_act(action, {}))
    assert "confirm" in str(err.value).lower()


@pytest.mark.asyncio
async def test_the_panel_refuses_without_it_and_passes_it_on_with_it():
    panel = get_panel("connect-halt")
    ctx = Ctx(snap(active=[{"hash": "h" * 64, "reason": "r", "machine": "b" * 64, "issued_at": 1}]), MEMBERS)
    out = await panel.act(ctx, "propose_resume", {})
    assert out["ok"] is False and "confirm" in out["message"].lower() and ctx.acts == []
    out = await panel.act(ctx, "propose_resume", {"confirmed": True})
    assert out["ok"] is True and ctx.acts == [("propose_resume", {"confirmed": True})]
    new = get_panel("connect-halt-new")
    ctx2 = Ctx(snap(), MEMBERS)
    out = await new.act(ctx2, "halt", {"changes": {"reason": "x"}})
    assert out["ok"] is False and ctx2.acts == []
    out = await new.act(ctx2, "halt", {"changes": {"reason": "x"}, "confirmed": True})
    assert out["ok"] is True and ctx2.acts[0][1]["confirmed"] is True
