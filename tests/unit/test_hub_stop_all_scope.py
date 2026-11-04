"""Issue #669: /hub stop all must only sweep agents the caller started.

The mesh-wide stop is operator-only (kollab --hub stop all). An agent
session may stop the agents it started itself; peers launched by other
lanes or the operator are immune, and the command is not advertised to
callers with nothing of their own to stop.
"""

import asyncio
import signal
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from kollabor_agent.tool_definitions.hub import hub_stop
from plugins.agent_orchestrator.models import AgentSession
from plugins.agent_orchestrator.orchestrator import AgentOrchestrator
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
    """Fake orchestrator AgentSession: PID, Popen handle, and liveness."""

    class FakeProc:
        def __init__(self):
            self.pid = pid

        def poll(self):
            return None if alive else 0

    return SimpleNamespace(
        pid=pid,
        start_time=start_time,
        is_alive=alive,
        proc=FakeProc(),
        process_lock=threading.RLock(),
    )


def test_hub_stop_model_rules_describe_caller_owned_scope():
    assert "named local agent" in hub_stop.description
    assert "In the 'all' case" in hub_stop.description
    assert "immune" in hub_stop.parameters[0].description
    rendered = hub_stop.to_markdown()
    assert "only agents you started" in rendered
    assert "peers started by others" in rendered

    prompt = (
        Path(__file__).resolve().parents[2]
        / "bundles"
        / "agents"
        / "system"
        / "hub-collaboration.md"
    ).read_text()
    assert "target `all` stops only agents you started" in prompt
    assert "peers started by other agents or the operator are immune" in prompt


def _patch_process_create_time(monkeypatch, plugin):
    """Use the fake session's captured OS start time in scope tests."""
    monkeypatch.setattr(
        plugin, "_session_process_create_time", lambda session: session.start_time
    )


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
        _patch_process_create_time(monkeypatch, plugin)
        monkeypatch.setattr(
            plugin,
            "_get_orchestrator",
            lambda: _orch({"helper": _session(111, now - 7)}),
        )

        stopped = []

        async def fake_stop(agent, reason="", session=None):
            stopped.append(agent.identity)
            return "stopped"

        monkeypatch.setattr(plugin, "_stop_peer_agent", fake_stop)

        result = await plugin._handle_stop_command("all")

        assert stopped == ["helper"]
        assert "ruby" in result
        assert "not started by you" in result

    asyncio.run(run_test())


def test_stop_all_does_not_infer_detached_child_ownership_from_launcher_pid(
    monkeypatch,
):
    """A presence PID never inherits ownership from a different Popen PID."""

    async def run_test():
        now = time.time()
        plugin = HubPlugin()
        plugin._presence = SimpleNamespace(
            scan_all_presence=lambda: [_peer("helper", 222, started_at=now - 5)]
        )
        plugin._identity = SimpleNamespace(agent_id="me", identity="citrine")
        _patch_process_create_time(monkeypatch, plugin)
        # Models the old double-detach shape: Popen owns the wrapper PID, while
        # the presence record belongs to its detached child.
        monkeypatch.setattr(
            plugin,
            "_get_orchestrator",
            lambda: _orch({"helper": _session(111, now - 7, alive=True)}),
        )

        stopped = []

        async def fake_stop(agent, reason="", session=None):
            stopped.append(agent.identity)
            return "stopped"

        monkeypatch.setattr(plugin, "_stop_peer_agent", fake_stop)

        result = await plugin._handle_stop_command("all")

        assert stopped == []
        assert "helper" in result
        assert "no agents started by you" in result

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

        async def fake_stop(agent, reason="", session=None):
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
        _patch_process_create_time(monkeypatch, plugin)
        monkeypatch.setattr(
            plugin,
            "_get_orchestrator",
            lambda: _orch({"helper": _session(4242, now - 7, alive=True)}),
        )

        stopped = []

        async def fake_stop(agent, reason="", session=None):
            stopped.append(agent.identity)
            return "stopped"

        monkeypatch.setattr(plugin, "_stop_peer_agent", fake_stop)

        result = await plugin._handle_stop_command("all")

        assert stopped == ["helper"], f"stopped {stopped}, expected only caller's"
        assert "ghost-lane" in result
        assert "not started by you" in result

    asyncio.run(run_test())


def test_stop_all_rejects_recent_stale_presence_within_old_120_second_window(
    monkeypatch,
):
    """A stale row 90 seconds before this process started is not this session.

    The former symmetric 120-second timestamp window accepted this reused PID.
    """

    async def run_test():
        now = time.time()
        plugin = HubPlugin()
        session = _session(4242, now - 10)
        plugin._presence = SimpleNamespace(
            scan_all_presence=lambda: [
                _peer("previous-owner", 4242, started_at=now - 100)
            ],
            cleanup_agent=lambda agent: None,
        )
        plugin._identity = SimpleNamespace(agent_id="me", identity="citrine")
        _patch_process_create_time(monkeypatch, plugin)
        monkeypatch.setattr(
            plugin,
            "_get_orchestrator",
            lambda: _orch({"helper": session}),
        )

        stopped = []

        async def fake_stop(agent, reason="", session=None):
            stopped.append(agent.identity)
            return "stopped"

        monkeypatch.setattr(plugin, "_stop_peer_agent", fake_stop)
        result = await plugin._handle_stop_command("all")

        assert stopped == []
        assert "previous-owner" in result
        assert "no agents started by you" in result

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
        _patch_process_create_time(monkeypatch, plugin)
        monkeypatch.setattr(
            plugin,
            "_get_orchestrator",
            lambda: _orch({"helper": _session(777, now - 3600, alive=False)}),
        )

        stopped = []

        async def fake_stop(agent, reason="", session=None):
            stopped.append(agent.identity)
            return "stopped"

        monkeypatch.setattr(plugin, "_stop_peer_agent", fake_stop)

        result = await plugin._handle_stop_command("all")

        assert stopped == [], f"stopped {stopped}: killed by reused PID"
        assert "new-owner" in result
        assert "no agents started by you" in result

    asyncio.run(run_test())


def test_stop_all_never_signals_reused_pid_after_mid_stop_exit(monkeypatch):
    """The tracked session can exit during the async shutdown wait.

    Final escalation must recheck the child under its shared process lock and
    withhold raw-pid signals when the tracked child has exited.
    """

    async def run_test():
        now = time.time()
        plugin = HubPlugin()
        session = _session(4242, now - 7, alive=True)
        plugin._presence = SimpleNamespace(
            scan_all_presence=lambda: [_peer("helper", 4242, started_at=now - 5)],
            cleanup_agent=lambda agent: None,
        )
        plugin._identity = SimpleNamespace(agent_id="me", identity="citrine")
        _patch_process_create_time(monkeypatch, plugin)
        monkeypatch.setattr(
            plugin,
            "_get_orchestrator",
            lambda: _orch({"helper": session}),
        )

        async def exiting_shutdown(socket_path, reason="", timeout=3.0, **generation):
            # the tracked process dies during the awaited shutdown window;
            # graceful path "fails" so the sweep reaches its fallback
            assert generation == {
                "expected_agent_id": "agent-helper",
                "expected_pid": 4242,
                "expected_started_at": now - 5,
            }
            session.is_alive = False
            return False

        monkeypatch.setattr(
            "plugins.hub.plugin.AgentMessenger.signal_shutdown", exiting_shutdown
        )

        async def instant_wait(agent, timeout=5.0):
            return False

        monkeypatch.setattr(plugin, "_wait_for_agent_exit", instant_wait)

        kills = []

        def fake_kill(pid, sig):
            kills.append((pid, sig))
            return True

        monkeypatch.setattr("os.kill", fake_kill)

        result = await plugin._handle_stop_command("all")

        assert kills == [], f"signaled {kills} after the tracked process exited"
        assert "tracked-process guard" in result

    asyncio.run(run_test())


def test_stop_all_withholds_escalation_when_session_dies_after_guard(monkeypatch):
    """A pre-dispatch liveness check cannot authorize a later raw-PID signal."""

    async def run_test():
        now = time.time()
        plugin = HubPlugin()
        session = _session(4242, now - 7, alive=True)
        plugin._presence = SimpleNamespace(
            scan_all_presence=lambda: [_peer("helper", 4242, started_at=now - 5)],
            cleanup_agent=lambda agent: None,
        )
        plugin._identity = SimpleNamespace(agent_id="me", identity="citrine")
        _patch_process_create_time(monkeypatch, plugin)
        monkeypatch.setattr(
            plugin,
            "_get_orchestrator",
            lambda: _orch({"helper": session}),
        )
        # Force the advisory guard to pass; the tracked Popen reports the exit
        # when final escalation takes the session lock.
        monkeypatch.setattr(
            HubPlugin, "_signal_allowed", staticmethod(lambda session: True)
        )

        # The child is already reported as exited when the signal path checks.
        class ReapedProc:
            pid = 4242

            def poll(self):
                return 0

            def send_signal(self, sig):
                raise AssertionError("a reaped process handle must not signal")

        session.proc = ReapedProc()

        async def fail_shutdown(socket_path, reason="", timeout=3.0, **generation):
            assert generation == {
                "expected_agent_id": "agent-helper",
                "expected_pid": 4242,
                "expected_started_at": now - 5,
            }
            return False

        monkeypatch.setattr(
            "plugins.hub.plugin.AgentMessenger.signal_shutdown", fail_shutdown
        )

        async def instant_wait(agent, timeout=5.0):
            return False

        monkeypatch.setattr(plugin, "_wait_for_agent_exit", instant_wait)

        kills = []

        def fake_kill(pid, sig):
            kills.append((pid, sig))
            return True

        monkeypatch.setattr("os.kill", fake_kill)

        result = await plugin._handle_stop_command("all")

        assert kills == [], f"raw-signaled {kills} after the guard passed"
        assert "tracked-process guard" in result

    asyncio.run(run_test())


def test_signal_session_noops_when_child_already_reaped():
    """A tracked child already observed as exited is not signaled."""

    class FakeProc:
        returncode = 0

        def poll(self):
            return 0

        def send_signal(self, sig):
            raise AssertionError("send_signal must not run on a reaped child")

    session = SimpleNamespace(proc=FakeProc(), process_lock=threading.RLock())
    assert HubPlugin()._signal_session(session, 15) is False


def test_signal_session_sends_through_handle_while_child_unreaped():
    recorded = []

    class FakeProc:
        returncode = None

        def poll(self):
            return None

        def send_signal(self, sig):
            recorded.append(sig)

    session = SimpleNamespace(proc=FakeProc(), process_lock=threading.RLock())
    assert HubPlugin()._signal_session(session, 15) is True
    assert recorded == [15]


def test_signal_session_fails_closed_without_handle():
    plugin = HubPlugin()
    assert plugin._signal_session(SimpleNamespace(pid=4242), 15) is False
    assert plugin._signal_session(None, 15) is False


def test_signal_session_requires_shared_lifecycle_lock():
    class FakeProc:
        def poll(self):
            return None

        def send_signal(self, sig):
            raise AssertionError("must not signal without the shared lifecycle lock")

    assert HubPlugin()._signal_session(SimpleNamespace(proc=FakeProc()), 15) is False


def test_signal_session_rejects_handle_for_a_different_presence_pid():
    recorded = []

    class FakeProc:
        pid = 1111

        def poll(self):
            return None

        def send_signal(self, sig):
            recorded.append(sig)

    session = SimpleNamespace(pid=1111, proc=FakeProc(), process_lock=threading.RLock())

    assert HubPlugin()._signal_session(session, 15, expected_pid=2222) is False
    assert recorded == []


def test_escalation_rechecks_presence_generation_before_signaling(monkeypatch):
    recorded = []

    class FakeProc:
        pid = 1111

        def poll(self):
            return None

        def send_signal(self, sig):
            recorded.append(sig)

    plugin = HubPlugin()
    session = SimpleNamespace(pid=1111, proc=FakeProc(), process_lock=threading.RLock())
    agent = _peer("helper", 1111, started_at=100.0)
    monkeypatch.setattr(plugin, "_session_process_create_time", lambda session: 101.0)

    assert plugin._escalate_agent(agent, session, signal.SIGTERM) is False
    assert recorded == []


def test_hub_signal_serializes_with_orchestrator_child_reaping():
    wait_entered = threading.Event()
    release_wait = threading.Event()
    signal_entered = threading.Event()
    signal_lock_attempted = threading.Event()

    class ObservableLock:
        def __init__(self):
            self._lock = threading.RLock()

        def __enter__(self):
            if threading.current_thread().name == "signal-agent":
                signal_lock_attempted.set()
            self._lock.acquire()
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            self._lock.release()

    class BlockingProc:
        pid = 4242

        def __init__(self):
            self.returncode = None

        def poll(self):
            return self.returncode

        def terminate(self):
            pass

        def wait(self, timeout=None):
            wait_entered.set()
            if not release_wait.wait(timeout=2):
                raise TimeoutError("test did not release fake process wait")
            self.returncode = 0
            return 0

        def kill(self):
            self.returncode = -signal.SIGKILL

        def send_signal(self, sig):
            signal_entered.set()

    proc = BlockingProc()
    session = AgentSession(
        name="helper",
        full_name="project-helper",
        status="running",
        start_time=time.time(),
        proc=proc,
        pid=proc.pid,
        process_lock=ObservableLock(),
    )
    orchestrator = object.__new__(AgentOrchestrator)
    orchestrator.agents = {session.name: session}
    stop_results = []
    signal_results = []

    def reap_child():
        stop_results.append(orchestrator._kill_session(session.full_name))

    def signal_child():
        signal_results.append(HubPlugin()._signal_session(session, signal.SIGTERM))

    reaper = threading.Thread(target=reap_child)
    signaler = threading.Thread(target=signal_child, name="signal-agent")
    reaper.start()
    try:
        assert wait_entered.wait(timeout=1)
        signaler.start()
        assert signal_lock_attempted.wait(timeout=1)
        assert not signal_entered.wait(timeout=0.1)
    finally:
        release_wait.set()
        reaper.join(timeout=2)
        if signaler.ident is not None:
            signaler.join(timeout=2)

    assert not reaper.is_alive()
    assert not signaler.is_alive()
    assert stop_results == [True]
    assert signal_results == [False]
    assert not signal_entered.is_set()


def test_scan_all_presence_includes_home_shared_and_legacy_roots(tmp_path, monkeypatch):
    """Stop-all must see agents outside a spoofed HOME and current project."""
    import json
    import os

    from kollabor_agent.runtime import AgentRuntime
    from kollabor_config import config_utils
    from plugins.hub import presence

    actual_home = tmp_path / "actual-home"
    redirected_home = tmp_path / "redirected-home"
    shared_root = tmp_path / "kollabor-hub"
    current_presence = (
        redirected_home / ".kollab" / "projects" / "current" / "hub" / "presence"
    )
    shared_root.mkdir(mode=0o700)

    monkeypatch.setenv("HOME", str(redirected_home))
    monkeypatch.setattr(
        config_utils, "get_config_directory", lambda: redirected_home / ".kollab"
    )
    monkeypatch.setattr(presence, "get_presence_dir", lambda: current_presence)
    monkeypatch.setattr(presence, "SOCKET_DIR_ROOT", shared_root)
    monkeypatch.setattr(presence, "_system_home_directory", lambda: actual_home)

    def write_agent(directory, agent_id, last_heartbeat=1):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{agent_id}.json").write_text(
            json.dumps(
                {
                    "agent_id": agent_id,
                    "identity": agent_id,
                    "pid": os.getpid(),
                    "socket_path": str(shared_root / f"{agent_id}.sock"),
                    "last_heartbeat": last_heartbeat,
                }
            )
        )

    write_agent(current_presence, "caller")
    write_agent(
        actual_home / ".kollab" / "projects" / "home-project" / "hub" / "presence",
        "home-agent",
    )
    write_agent(actual_home / ".kollab" / "hub" / "presence", "legacy-agent")
    write_agent(
        redirected_home / ".kollab" / "projects" / "override" / "hub" / "presence",
        "override-agent",
    )
    write_agent(shared_root / "project" / "hub" / "presence", "shared-agent")
    # A duplicate session record must not cause stop-all to signal it twice.
    write_agent(shared_root / "duplicate" / "presence", "home-agent", last_heartbeat=2)

    manager = presence.PresenceManager(AgentRuntime(agent_id="caller"))
    agents = manager.scan_all_presence()

    agent_ids = [agent.agent_id for agent in agents]
    assert set(agent_ids) == {
        "home-agent",
        "legacy-agent",
        "override-agent",
        "shared-agent",
    }
    assert agent_ids.count("home-agent") == 1
    home_agent = next(agent for agent in agents if agent.agent_id == "home-agent")
    assert home_agent.last_heartbeat == 2


def test_system_home_directory_ignores_home_override(tmp_path, monkeypatch):
    """Resolve the OS account home even if a process overrides HOME."""
    import os

    import pytest

    if os.name == "nt":
        pytest.skip("pwd is unavailable on Windows")

    import pwd
    from types import SimpleNamespace

    from plugins.hub import presence

    actual_home = tmp_path / "actual-home"
    redirected_home = tmp_path / "redirected-home"
    monkeypatch.setenv("HOME", str(redirected_home))
    monkeypatch.setattr(
        pwd, "getpwuid", lambda _uid: SimpleNamespace(pw_dir=str(actual_home))
    )

    assert presence._system_home_directory() == actual_home
