"""Hub stop lifecycle tests."""

import asyncio
from types import SimpleNamespace

from plugins.hub.plugin import HubPlugin


class FakePresence:
    def __init__(self):
        self.cleaned = []
        self.agents = []

    def cleanup_agent(self, agent):
        self.cleaned.append(agent.identity)

    def scan_all_presence(self):
        return list(self.agents)


def test_stop_peer_waits_after_shutdown_ack_then_sigterms(monkeypatch):
    async def run_test():
        plugin = HubPlugin()
        plugin._presence = FakePresence()
        agent = SimpleNamespace(
            identity="lapis",
            pid=1234,
            socket_path="/tmp/lapis.sock",
            agent_id="agent-lapis",
        )

        async def signal_shutdown(socket_path, reason="", timeout=3.0):
            return True

        wait_results = iter([False, True])

        async def wait_for_exit(agent, timeout=5.0):
            return next(wait_results)

        kills = []

        monkeypatch.setattr(
            "plugins.hub.plugin.AgentMessenger.signal_shutdown",
            signal_shutdown,
        )
        monkeypatch.setattr(plugin, "_wait_for_agent_exit", wait_for_exit)
        monkeypatch.setattr(plugin, "_force_kill_agent", lambda agent: kills.append(agent.pid) or True)

        result = await plugin._stop_peer_agent(agent, reason="test")

        assert result == "killed (SIGTERM after graceful timeout)"
        assert kills == [1234]
        assert plugin._presence.cleaned == ["lapis"]

    asyncio.run(run_test())


def test_stop_peer_does_not_cleanup_if_pid_survives_sigterm(monkeypatch):
    async def run_test():
        plugin = HubPlugin()
        plugin._presence = FakePresence()
        agent = SimpleNamespace(
            identity="sapphire",
            pid=5678,
            socket_path="/tmp/sapphire.sock",
            agent_id="agent-sapphire",
        )

        async def signal_shutdown(socket_path, reason="", timeout=3.0):
            return True

        async def wait_for_exit(agent, timeout=5.0):
            return False

        monkeypatch.setattr(
            "plugins.hub.plugin.AgentMessenger.signal_shutdown",
            signal_shutdown,
        )
        monkeypatch.setattr(plugin, "_wait_for_agent_exit", wait_for_exit)
        monkeypatch.setattr(plugin, "_force_kill_agent", lambda agent: True)

        result = await plugin._stop_peer_agent(agent, reason="test")

        assert result == "stop timed out (pid still alive)"
        assert plugin._presence.cleaned == []

    asyncio.run(run_test())


def test_stop_all_stops_peers_concurrently():
    async def run_test():
        plugin = HubPlugin()
        plugin._presence = FakePresence()
        plugin._identity = SimpleNamespace(
            agent_id="agent-koordinator",
            identity="koordinator",
        )
        plugin._presence.agents = [
            SimpleNamespace(identity="lapis", agent_id="agent-lapis"),
            SimpleNamespace(identity="sapphire", agent_id="agent-sapphire"),
        ]

        async def stop_peer(agent, reason):
            await asyncio.sleep(0.1)
            return "stopped"

        plugin._stop_peer_agent = stop_peer

        start = asyncio.get_running_loop().time()
        result = await plugin._handle_stop_command("all")
        elapsed = asyncio.get_running_loop().time() - start

        assert elapsed < 0.18
        assert "lapis: stopped" in result
        assert "sapphire: stopped" in result

    asyncio.run(run_test())
