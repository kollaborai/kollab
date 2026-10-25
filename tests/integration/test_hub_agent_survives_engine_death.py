"""Integration: hub agents must survive the death of the process that spawned them.

P0-c incident chain, 2026-10-16 20:46:
  1. The engine's own conversation ran ``pkill -f "--detached --as"`` -- that
     substring is in EVERY hub agent's command line on every machine, so the
     whole fleet died instantly (operator error class, guarded by docs).
  2. Worse, the orchestrator armed each spawned agent's parent watchdog with
     KOLLAB_PARENT_PID=<engine pid>, so even a *plain* engine restart
     self-shut every agent it had spawned within ~10s. This test pins the
     lifecycle fix: an agent spawned by a process that then dies must keep
     running (reconnect/independent lifecycle), not take its own exit.

Runs the real ``kollab --detached`` daemon with an isolated HOME, spawns it
through the orchestrator's real spawn path, kills the spawner, and asserts
the agent outlives it.
"""

import asyncio
import contextlib
import os
import secrets
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).parent.parent.parent))

REPO_ROOT = Path(__file__).parent.parent.parent


def _isolate_home(monkeypatch):
    home = Path("/tmp") / f"kollab-engsurv-home-{secrets.token_hex(6)}"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(home))
    return home


async def _wait_for_socket(home: Path, timeout: float = 60.0) -> Path:
    """Wait for the spawned daemon's hub socket to appear."""
    deadline = time.monotonic() + timeout
    socket_root = Path("/tmp/kollabor-hub")
    while time.monotonic() < deadline:
        for sock in socket_root.rglob("*.sock"):
            # Fresh socket file for this isolated HOME run
            if sock.stat().st_mtime > home.stat().st_mtime - 2:
                return sock
        await asyncio.sleep(0.5)
    raise TimeoutError("daemon hub socket never appeared")


async def _wait_until_gone(pid: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        await asyncio.sleep(0.25)
    return False


def _agents_under(home: Path) -> list[int]:
    """Kollab processes running with this test's HOME (the detached agent and its fork)."""
    out = subprocess.run(
        ["/bin/ps", "-E", "-axww", "-o", "pid=,command="], capture_output=True, text=True
    ).stdout
    return [
        int(line.split(None, 1)[0])
        for line in out.splitlines()
        if f"HOME={home}" in line.split() and "kollabor_cli_main" in line
    ]


async def _stop_agents(home: Path) -> None:
    """Stop every agent this run started, with its MCP servers.

    The agent is detached on purpose (it must outlive its spawner), so a failed
    assert or an interrupted run would otherwise leave it running for good. It
    leads its own session, so its process group takes the MCP servers with it.
    """
    pids = _agents_under(home)
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for pid in pids:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(pid, sig)
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(pid, sig)
        gone = [await _wait_until_gone(pid, 10) for pid in pids]
        if all(gone):
            return


@pytest.mark.asyncio
async def test_agent_survives_spawner_death(monkeypatch):
    """Spawn a detached agent, kill its parent, assert the agent survives."""
    home = _isolate_home(monkeypatch)

    # Long-lived fake parent that runs the real spawn path.
    parent_code = f"""
import asyncio, sys
sys.path.insert(0, {str(REPO_ROOT)!r})
sys.path.insert(0, {str(REPO_ROOT / 'plugins')!r})

from plugins.agent_orchestrator.orchestrator import AgentOrchestrator

async def main():
    orch = AgentOrchestrator(project_name="engsurv-test")
    ok = await orch.spawn(
        name="survivor",
        task="idling for the survival test; do nothing",
        wait=False,
        identity="survivor",
    )
    print("SPAWN_OK" if ok else "SPAWN_FAIL", flush=True)
    # Park forever; the test kills this pid to simulate the engine dying.
    await asyncio.Event().wait()

asyncio.run(main())
"""

    parent = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        parent_code,
        cwd=str(REPO_ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=dict(os.environ),
    )

    try:
        await _check_survival(parent, home)
    finally:
        if parent.returncode is None:
            parent.kill()
        await _stop_agents(home)


async def _check_survival(parent, home: Path) -> None:
    # Parent must report a successful spawn before we proceed.
    try:
        line = await asyncio.wait_for(parent.stdout.readline(), 90)
        assert b"SPAWN_OK" in line, f"spawn failed: {line!r}"
    except AssertionError:
        parent.kill()
        raise

    # Wait for the agent process to exist: poll for the detached daemon's
    # hub socket (it registers presence on boot).
    sock = await _wait_for_socket(home, timeout=90)
    assert sock.exists()

    # Find the agent pid via its presence record dir (isolated HOME).
    # Project dir keys on the spawn cwd (REPO_ROOT), not the isolated HOME.
    presence_dir = home / ".kollab" / "projects"
    agent_pid = None
    deadline = time.monotonic() + 30
    while agent_pid is None and time.monotonic() < deadline:
        for pfile in presence_dir.rglob("hub/presence/*.json"):
            try:
                import json

                data = json.loads(pfile.read_text())
                if data.get("identity") == "survivor":
                    agent_pid = int(data.get("pid", 0)) or None
            except Exception:
                pass
        if agent_pid is None:
            await asyncio.sleep(0.5)
    assert agent_pid, "could not find spawned agent pid in presence records"

    # Give the agent a moment to fully boot, then KILL THE PARENT
    # (simulating the engine process dying -- plain death, no pkill).
    await asyncio.sleep(3)
    parent.kill()
    await parent.wait()

    # The old buggy behavior: child notices parent death within ~10s and
    # shuts itself down. The fixed behavior: child keeps running.
    await asyncio.sleep(12)
    try:
        os.kill(agent_pid, 0)
    except ProcessLookupError:
        pytest.fail(
            "agent died after its spawner was killed -- parent-watchdog "
            "coupling is back (KOLLAB_PARENT_PID must not be set for hub agents)"
        )
