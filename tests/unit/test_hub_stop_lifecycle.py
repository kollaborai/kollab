"""Hub stop lifecycle tests."""

import asyncio
import json
import os
import signal
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

from plugins.hub.messenger import AgentMessenger, AgentSocketServer
from plugins.hub.plugin import HubPlugin
from plugins.hub.presence import PresenceManager

SLEEPER = [sys.executable, "-c", "import time; time.sleep(60)"]
# a child that exits at once and is never reaped by its parent
FORK_ONE_AND_KEEP_IT = (
    "import os, time\n"
    "pid = os.fork()\n"
    "if pid == 0:\n"
    "    os._exit(0)\n"
    "print(pid, flush=True)\n"
    "time.sleep(60)\n"
)


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
        monkeypatch.setattr(
            plugin, "_force_kill_agent", lambda agent: kills.append(agent.pid) or True
        )

        result = await plugin._stop_peer_agent(agent, reason="test")

        assert result == "killed (SIGTERM after graceful timeout)"
        assert kills == [1234]
        assert plugin._presence.cleaned == ["lapis"]

    asyncio.run(run_test())


def test_stop_peer_escalates_to_sigkill_when_sigterm_fails(monkeypatch):
    """A wedged agent that survives graceful shutdown + SIGTERM must be
    SIGKILLed and cleaned up — this is what lets `stop all` reap a hung
    coordinator instead of leaving it squatting the slot."""

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

        # grace wait -> False, SIGTERM wait -> False, SIGKILL wait -> True
        wait_results = iter([False, False, True])

        async def wait_for_exit(agent, timeout=5.0):
            return next(wait_results)

        hard_kills = []

        monkeypatch.setattr(
            "plugins.hub.plugin.AgentMessenger.signal_shutdown",
            signal_shutdown,
        )
        monkeypatch.setattr(plugin, "_wait_for_agent_exit", wait_for_exit)
        monkeypatch.setattr(plugin, "_force_kill_agent", lambda agent: True)
        monkeypatch.setattr(
            plugin,
            "_hard_kill_agent",
            lambda agent: hard_kills.append(agent.pid) or True,
        )

        result = await plugin._stop_peer_agent(agent, reason="test")

        assert result == "killed (SIGKILL — was unresponsive)"
        assert hard_kills == [5678]
        assert plugin._presence.cleaned == ["sapphire"]

    asyncio.run(run_test())


def test_stop_peer_reports_failure_only_if_pid_survives_sigkill(monkeypatch):
    """If even SIGKILL doesn't land, report failure and do NOT cleanup."""

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
            return False  # never exits, even after SIGKILL

        monkeypatch.setattr(
            "plugins.hub.plugin.AgentMessenger.signal_shutdown",
            signal_shutdown,
        )
        monkeypatch.setattr(plugin, "_wait_for_agent_exit", wait_for_exit)
        monkeypatch.setattr(plugin, "_force_kill_agent", lambda agent: True)
        monkeypatch.setattr(plugin, "_hard_kill_agent", lambda agent: True)

        result = await plugin._stop_peer_agent(agent, reason="test")

        assert result == "stop timed out (pid survived SIGKILL)"
        assert plugin._presence.cleaned == []

    asyncio.run(run_test())


def test_caller_owned_stop_does_not_signal_or_unlink_replacement_generation(
    tmp_path, monkeypatch
):
    """A replacement listener rejects the old snapshot and stays usable."""

    async def run_test():
        monkeypatch.setattr("plugins.hub.presence.get_presence_dir", lambda: tmp_path)
        presence = PresenceManager(SimpleNamespace(agent_id="caller"))
        replacement_row = tmp_path / "agent-helper.json"
        shutdowns = []
        process = SimpleNamespace(returncode=None)
        process.pid = 12001

        class TrackedProcess:
            pid = process.pid

            def poll(self):
                return process.returncode

            def send_signal(self, sig):
                raise AssertionError(f"stale session received {sig}")

        class TrackedSession:
            pid = process.pid
            start_time = 10.0
            proc = TrackedProcess()
            process_lock = threading.RLock()

            @property
            def is_alive(self):
                return self.proc.poll() is None

        session = TrackedSession()

        async def replacement_status():
            return {
                "network": "replacement",
                "device": "laptop",
                "trust": "open",
                "agents": [],
            }

        server = AgentSocketServer(
            "agent-helper",
            lambda _message: None,
            on_shutdown=shutdowns.append,
            on_network_status=replacement_status,
            socket_name=f"stop-gen-{os.getpid()}-{time.time_ns()}",
        )
        server._identity_info = SimpleNamespace(
            agent_id="agent-helper", pid=12002, started_at=20.0
        )
        socket_path = str(await server.start())
        try:
            replacement_row.write_text(
                json.dumps(
                    {
                        # Identity and agent_id are reused, but this is a
                        # different process generation on the same socket.
                        "agent_id": "agent-helper",
                        "identity": "helper",
                        "pid": 12002,
                        "started_at": 20.0,
                        "socket_path": socket_path,
                    }
                )
            )
            selected = SimpleNamespace(
                identity="helper",
                agent_id="agent-helper",
                pid=12001,
                started_at=10.0,
                socket_path=socket_path,
            )
            plugin = HubPlugin()
            plugin._presence = presence
            # The snapshot matched when selected and immediately before its
            # socket call. The original child exits as the replacement
            # receives the request, modeling the async connect race.
            monkeypatch.setattr(plugin, "_presence_generation_matches", lambda *_: True)
            matches_generation = server._shutdown_target_matches

            def replacement_takes_over(message):
                process.returncode = 0
                return matches_generation(message)

            server._shutdown_target_matches = replacement_takes_over

            result = await plugin._stop_peer_agent(
                selected, reason="test stop all", session=session
            )

            assert "tracked-process guard" in result
            assert shutdowns == []
            assert json.loads(replacement_row.read_text())["pid"] == 12002
            assert server.socket_path.exists()
            assert await AgentMessenger.request_network_status(socket_path) == {
                "type": "network_status",
                "network": "replacement",
                "device": "laptop",
                "trust": "open",
                "agents": [],
            }
        finally:
            await server.stop()

    asyncio.run(run_test())


def test_generation_check_fails_closed_with_older_socket_server(monkeypatch):
    """An older receiver gets no shutdown action when it cannot prove scope."""

    async def run_test():
        requests = []

        class OldReceiver:
            async def readline(self):
                return b'{"type":"error","msg":"unknown shutdown_generation_check"}'

        class FakeWriter:
            def write(self, data):
                requests.append(json.loads(data.decode()))

            async def drain(self):
                return None

            def close(self):
                return None

            async def wait_closed(self):
                return None

        async def fake_open(*_args, **_kwargs):
            return OldReceiver(), FakeWriter()

        monkeypatch.setattr(AgentMessenger, "_open", staticmethod(fake_open))
        result = await AgentMessenger.signal_shutdown(
            "/fake/identity.sock",
            reason="test stop all",
            expected_agent_id="agent-helper",
            expected_pid=12001,
            expected_started_at=10.0,
        )

        assert result is False
        assert requests == [
            {
                "action": "shutdown_generation_check",
                "expected_agent_id": "agent-helper",
                "expected_pid": 12001,
                "expected_started_at": 10.0,
            }
        ]

    asyncio.run(run_test())


def test_generation_check_accepts_the_selected_socket_server_generation():
    """The current receiver accepts a matching generation on one connection."""

    async def run_test():
        shutdowns = []
        server = AgentSocketServer(
            "agent-helper",
            lambda _message: None,
            on_shutdown=shutdowns.append,
            socket_name=f"stop-gen-ok-{os.getpid()}-{time.time_ns()}",
        )
        server._identity_info = SimpleNamespace(
            agent_id="agent-helper", pid=12001, started_at=10.0
        )
        socket_path = str(await server.start())
        try:
            assert await AgentMessenger.signal_shutdown(
                socket_path,
                reason="test stop all",
                expected_agent_id="agent-helper",
                expected_pid=12001,
                expected_started_at=10.0,
            )
            assert shutdowns == ["test stop all"]
        finally:
            await server.stop()

    asyncio.run(run_test())


def test_agent_pid_alive_reads_an_exited_unreaped_child_as_dead():
    """The window that forked the daemon is its parent, so the exited daemon is
    a zombie and os.kill(pid, 0) still succeeds on it."""
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    try:
        deadline = time.monotonic() + 5
        while HubPlugin._agent_pid_alive(child.pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not HubPlugin._agent_pid_alive(child.pid)
    finally:
        child.wait()


def test_agent_pid_alive_reads_another_parents_zombie_as_dead():
    """`kollab --hub stop` from another shell: the daemon exited, but its parent,
    the attached window, has not reaped it yet."""
    parent = subprocess.Popen(
        [sys.executable, "-c", FORK_ONE_AND_KEEP_IT],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        zombie = int(parent.stdout.readline())
        deadline = time.monotonic() + 5
        while HubPlugin._agent_pid_alive(zombie) and time.monotonic() < deadline:
            time.sleep(0.05)
        os.kill(zombie, 0)  # still in the process table
        assert not HubPlugin._agent_pid_alive(zombie)
    finally:
        parent.kill()
        parent.wait()


def test_agent_pid_alive_is_true_for_pids_that_really_survive():
    child = subprocess.Popen(SLEEPER)
    try:
        assert HubPlugin._agent_pid_alive(child.pid)  # our child, still running
        assert HubPlugin._agent_pid_alive(os.getpid())  # not our child
        assert not HubPlugin._agent_pid_alive(0)
    finally:
        child.kill()
        child.wait()


def test_stop_peer_reports_stopped_when_the_forked_daemon_exits_unreaped(monkeypatch):
    """`/hub stop all` typed in the attached window: the daemon exits on the
    shutdown request but stays a zombie of that window. That is a stop, not
    "pid survived SIGKILL"."""

    async def run_test():
        child = subprocess.Popen(SLEEPER)
        try:
            plugin = HubPlugin()
            plugin._presence = FakePresence()
            agent = SimpleNamespace(
                identity="koordinator",
                pid=child.pid,
                socket_path="/tmp/koordinator.sock",
                agent_id="agent-koordinator",
            )

            async def signal_shutdown(socket_path, reason="", timeout=3.0):
                os.kill(child.pid, signal.SIGKILL)  # the daemon dies, nobody reaps it
                return True

            monkeypatch.setattr(
                "plugins.hub.plugin.AgentMessenger.signal_shutdown",
                signal_shutdown,
            )
            monkeypatch.setattr("plugins.hub.plugin.STOP_GRACE_SECONDS", 0.3)
            monkeypatch.setattr("plugins.hub.plugin.STOP_TERM_SECONDS", 0.2)
            monkeypatch.setattr("plugins.hub.plugin.STOP_KILL_SECONDS", 0.3)

            result = await plugin._stop_peer_agent(agent, reason="test")

            assert result == "stopped"
            assert plugin._presence.cleaned == ["koordinator"]
        finally:
            child.wait()

    asyncio.run(run_test())


def test_stop_all_stops_peers_concurrently():
    async def run_test():
        plugin = HubPlugin()
        plugin._presence = FakePresence()
        plugin._identity = SimpleNamespace(
            agent_id="agent-koordinator",
            identity="koordinator",
        )
        boot = time.time() - 1
        plugin._presence.agents = [
            SimpleNamespace(
                identity="lapis",
                agent_id="agent-lapis",
                pid=101,
                started_at=boot,
            ),
            SimpleNamespace(
                identity="sapphire",
                agent_id="agent-sapphire",
                pid=102,
                started_at=boot,
            ),
        ]

        class TrackedProc:
            def __init__(self, pid):
                self.pid = pid

            def poll(self):
                return None

        sessions = {
            identity: SimpleNamespace(
                pid=pid,
                start_time=boot - 0.2,
                proc=TrackedProc(pid),
                is_alive=True,
            )
            for identity, pid in (("lapis", 101), ("sapphire", 102))
        }
        # The synthetic Popen sessions represent the direct detached children
        # now started by AgentOrchestrator, and these PIDs are the presence PIDs.
        plugin._session_process_create_time = lambda session: session.start_time

        # stop all only sweeps agents the caller started (#669); ownership
        # is pid + liveness + process generation, not a bare PID match
        plugin.event_bus = SimpleNamespace(
            get_service=lambda name: SimpleNamespace(
                orchestrator=SimpleNamespace(get_all_agents=lambda: dict(sessions))
            )
        )

        async def stop_peer(agent, reason, session=None):
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
