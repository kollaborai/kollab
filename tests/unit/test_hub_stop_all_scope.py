"""Issue #669: /hub stop all must only sweep agents the caller started.

The mesh-wide stop is operator-only (kollab --hub stop all). An agent
session may stop the agents it started itself; peers launched by other
lanes or the operator are immune, and the command is not advertised to
callers with nothing of their own to stop.
"""

import asyncio
import time
from types import SimpleNamespace

from plugins.hub.plugin import HubPlugin
from plugins.hub.startup_messages import (
    HUB_STARTUP_TIPS,
    choose_startup_tip,
)


def _peer(identity, pid, started_at=None):
    return SimpleNamespace(
        identity=identity,
        pid=pid,
        socket_path=f"/tmp/{identity}.sock",
        agent_id=f"agent-{identity}",
        started_at=started_at,
    )


def _session(pid, start_time, alive=True):
    """Fake orchestrator AgentSession: pid + start_time + liveness."""
    return SimpleNamespace(pid=pid, start_time=start_time, is_alive=alive)


def _orch(agents):
    """Fake agent_orchestrator service: {name: session}. None = absent."""
    if agents is None:
        return None
    return SimpleNamespace(
        orchestrator=SimpleNamespace(get_all_agents=lambda: dict(agents))
    )


def test_stop_all_only_sweeps_agents_the_caller_started(monkeypatch):
    async def run_test():
        plugin = HubPlugin()
        now = time.time()
        plugin._presence = SimpleNamespace(
            scan_all_presence=lambda: [
                _peer("helper", 111, started_at=now - 5),
                _peer("ruby", 222, started_at=now - 3),
            ]
        )
        plugin._identity = SimpleNamespace(agent_id="me", identity="citrine")
        monkeypatch.setattr(
            plugin,
            "_get_orchestrator",
            lambda: _orch({"helper": _session(111, now - 7)}),
        )

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
            scan_all_presence=lambda: [_peer("ruby", 222, started_at=time.time())]
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


def test_stop_all_ignores_stale_row_sharing_caller_pid(monkeypatch):
    """Duplicate-PID probe: a stale row from another session that shares the
    caller's tracked PID must not be swept with the caller's own agent."""

    async def run_test():
        now = time.time()
        plugin = HubPlugin()
        plugin._presence = SimpleNamespace(
            scan_all_presence=lambda: [
                # the caller's own agent: live socket, booted right after spawn
                _peer("helper", 4242, started_at=now - 5),
                # another session's stale row (dead socket, still in the
                # presence grace window) that reused the same PID number
                _peer("ghost-lane", 4242, started_at=now - 86400),
            ]
        )
        plugin._identity = SimpleNamespace(agent_id="me", identity="citrine")
        monkeypatch.setattr(
            plugin,
            "_get_orchestrator",
            lambda: _orch({"helper": _session(4242, now - 7, alive=True)}),
        )

        stopped = []

        async def fake_stop(agent, reason=""):
            stopped.append(agent.identity)
            return "stopped"

        monkeypatch.setattr(plugin, "_stop_peer_agent", fake_stop)

        result = await plugin._handle_stop_command("all")

        assert stopped == ["helper"], f"stopped {stopped}, expected only caller's"
        assert "ghost-lane" in result
        assert "not started by you" in result

    asyncio.run(run_test())


def test_stop_all_never_kills_by_reused_pid_of_dead_session(monkeypatch):
    """A tracked session whose process already exited must not make its old
    PID a kill token: the live process now holding that PID is immune."""

    async def run_test():
        now = time.time()
        plugin = HubPlugin()
        plugin._presence = SimpleNamespace(
            scan_all_presence=lambda: [
                # another lane's live process that recycled the dead session's PID
                _peer("new-owner", 777, started_at=now - 10),
            ]
        )
        plugin._identity = SimpleNamespace(agent_id="me", identity="citrine")
        monkeypatch.setattr(
            plugin,
            "_get_orchestrator",
            lambda: _orch({"helper": _session(777, now - 3600, alive=False)}),
        )

        stopped = []

        async def fake_stop(agent, reason=""):
            stopped.append(agent.identity)
            return "stopped"

        monkeypatch.setattr(plugin, "_stop_peer_agent", fake_stop)

        result = await plugin._handle_stop_command("all")

        assert stopped == [], f"stopped {stopped}: killed by reused PID"
        assert "new-owner" in result
        assert "no agents started by you" in result

    asyncio.run(run_test())
