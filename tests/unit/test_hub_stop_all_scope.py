"""Issue #669: /hub stop all must only sweep agents the caller started.

The mesh-wide stop is operator-only (kollab --hub stop all). An agent
session may stop the agents it started itself; peers launched by other
lanes or the operator are immune, and the command is not advertised to
callers with nothing of their own to stop.
"""

import asyncio
from types import SimpleNamespace

from plugins.hub.plugin import HubPlugin
from plugins.hub.startup_messages import (
    HUB_STARTUP_TIPS,
    choose_startup_tip,
)


def _peer(identity, pid):
    return SimpleNamespace(
        identity=identity,
        pid=pid,
        socket_path=f"/tmp/{identity}.sock",
        agent_id=f"agent-{identity}",
    )


def _orch(agents):
    """Fake agent_orchestrator service: {name: session(pid=...)}. None = absent."""
    if agents is None:
        return None
    return SimpleNamespace(
        orchestrator=SimpleNamespace(
            get_all_agents=lambda: {
                name: SimpleNamespace(pid=pid) for name, pid in agents.items()
            }
        )
    )


def test_stop_all_only_sweeps_agents_the_caller_started(monkeypatch):
    async def run_test():
        plugin = HubPlugin()
        plugin._presence = SimpleNamespace(
            scan_all_presence=lambda: [_peer("helper", 111), _peer("ruby", 222)]
        )
        plugin._identity = SimpleNamespace(agent_id="me", identity="citrine")
        monkeypatch.setattr(plugin, "_get_orchestrator", lambda: _orch({"helper": 111}))

        stopped = []

        async def fake_stop(agent, reason=""):
            stopped.append(agent.identity)
            return "stopped"

        monkeypatch.setattr(plugin, "_stop_peer_agent", fake_stop)

        result = await plugin._handle_stop_command("all")

        assert stopped == ["helper"]
        assert "ruby" in result
        assert "not started by you" in result

    asyncio.run(run_test())


def test_stop_all_with_no_started_agents_stops_nothing(monkeypatch):
    async def run_test():
        plugin = HubPlugin()
        plugin._presence = SimpleNamespace(
            scan_all_presence=lambda: [_peer("ruby", 222)]
        )
        plugin._identity = SimpleNamespace(agent_id="me", identity="citrine")
        monkeypatch.setattr(plugin, "_get_orchestrator", lambda: None)

        stopped = []

        async def fake_stop(agent, reason=""):
            stopped.append(agent.identity)
            return "stopped"

        monkeypatch.setattr(plugin, "_stop_peer_agent", fake_stop)

        result = await plugin._handle_stop_command("all")

        assert stopped == []
        assert "ruby" in result
        assert "no agents started by you" in result

    asyncio.run(run_test())


def test_stop_all_advertisement_hidden_without_started_agents(monkeypatch):
    """The startup status only advertises stop all to callers that own agents."""

    async def run_test():
        plugin = HubPlugin()
        plugin._identity = SimpleNamespace(identity="citrine", session_log=None)
        displayed = []
        renderer = SimpleNamespace(
            message_coordinator=SimpleNamespace(
                display_message_sequence=lambda seq: displayed.extend(seq)
            )
        )
        monkeypatch.setattr(
            "plugins.hub.plugin.choose_startup_tip",
            lambda *a, **kw: "Use /hub status.",
        )

        monkeypatch.setattr(plugin, "_get_orchestrator", lambda: _orch({}))
        plugin._display_startup_status(renderer, "member", [])
        joined = " ".join(text for _, text, _ in displayed)
        assert "stop all" not in joined.lower()

        monkeypatch.setattr(plugin, "_get_orchestrator", lambda: _orch({"helper": 111}))
        plugin._display_startup_status(renderer, "member", [])
        joined = " ".join(text for _, text, _ in displayed)
        assert "stop all" in joined.lower()

    asyncio.run(run_test())


def test_stop_all_tips_excluded_for_callers_with_no_started_agents():
    for seed in range(len(HUB_STARTUP_TIPS)):
        tip = choose_startup_tip(seed=seed, include_stop_all=False)
        assert "stop all" not in tip.lower()

    assert any("stop all" in tip.lower() for tip in HUB_STARTUP_TIPS)
