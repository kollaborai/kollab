"""Real owned daemons must retire their SDK when the creating process disappears.

The backend is an isolated deterministic SDK agent. This qualifies lifetime
ownership, including blocked startup, without calling an account/provider.
"""

import asyncio
import contextlib
import json
import os
import signal
import sys
import time
from pathlib import Path

import psutil
import pytest

ROOT = Path(__file__).resolve().parents[2]

SDK_AGENT = r'''
import asyncio, json, os
from pathlib import Path
import acp, psutil
from acp.schema import AgentCapabilities, CloseSessionResponse, InitializeResponse, NewSessionResponse

def record(phase):
    Path("owned-sdk-identity.json").write_text(json.dumps({
        "pid": os.getpid(), "create_time": psutil.Process().create_time(),
        "workspace": str(Path.cwd()), "phase": phase,
    }))

record("spawned")

class Agent:
    async def initialize(self, protocol_version, **kwargs):
        record("initialize")
        if os.environ.get("OWNED_ACP_START") == "blocked":
            await asyncio.Event().wait()
        return InitializeResponse(protocolVersion=1, agentCapabilities=AgentCapabilities(
            sessionCapabilities={"close": {}}))

    async def new_session(self, cwd, mcp_servers=None, **kwargs):
        record("ready")
        return NewSessionResponse(sessionId="owned-lifetime-sdk")

    async def close_session(self, session_id, **kwargs):
        record("close")
        return CloseSessionResponse()

asyncio.run(acp.run_agent(Agent(), use_unstable_protocol=True))
'''

OWNER = r'''
import asyncio, json, os, sys
from pathlib import Path
import psutil
from kollabor_engine.session import EngineSession

def identity(pid, workspace):
    process = psutil.Process(pid)
    return {"pid": pid, "create_time": process.create_time(), "workspace": str(workspace)}

async def main():
    root = Path(sys.argv[1])
    phase = sys.argv[2]
    workspace = root / "owned"
    session = EngineSession("owner-lifetime-proof", "owned-" + phase,
                            workspace=str(workspace), solo=True, stop_on_owner_exit=True)
    initialization = asyncio.create_task(session.initialize())
    if phase == "ready":
        await asyncio.wait_for(initialization, 45)
    else:
        async with asyncio.timeout(45):
            while True:
                path = workspace / "owned-sdk-identity.json"
                if session.daemon is not None and path.exists():
                    try:
                        if json.loads(path.read_text())["phase"] == "initialize":
                            break
                    except (ValueError, KeyError):
                        pass
                if initialization.done():
                    await initialization
                    raise AssertionError("blocked SDK unexpectedly became ready")
                await asyncio.sleep(0.05)

    report = {"phase": phase, "owner": identity(os.getpid(), root),
              "daemon": identity(session.daemon.pid, workspace),
              "sdk": json.loads((workspace / "owned-sdk-identity.json").read_text())}
    def save_report():
        (root / "owner-report.json").write_text(json.dumps(report, indent=2))
    save_report()
    if phase == "ready":
        sibling_workspace = root / "detached"
        sibling = EngineSession("independent-lifetime-proof", "owned-ready",
                                workspace=str(sibling_workspace), solo=True)
        sibling_initialization = asyncio.create_task(sibling.initialize())
        async with asyncio.timeout(45):
            while sibling.daemon is None:
                if sibling_initialization.done():
                    await sibling_initialization
                await asyncio.sleep(0.05)
        report["detached_daemon"] = identity(sibling.daemon.pid, sibling_workspace)
        save_report()
        await asyncio.wait_for(sibling_initialization, 45)
        report["detached_sdk"] = json.loads((sibling_workspace / "owned-sdk-identity.json").read_text())
        save_report()
        # Opening and closing an attached view must not transfer lifetime policy.
        view = await EngineSession.adopt({
            "session_id": sibling.session_id, "identity": sibling.daemon.identity,
            "workspace": str(sibling_workspace), "daemon_pid": sibling.daemon.pid,
        })
        assert view.stop_on_owner_exit is False and view.external
        await view.shutdown()
        assert sibling.alive
        report["adopted_view_detached_without_stopping_owner"] = True

    save_report()
    print(json.dumps(report), flush=True)
    await asyncio.to_thread(sys.stdin.buffer.read, 1)
    # Model a client dropping the owning stdio process without running finally.
    # Normal process exit, no signals and no cleanup RPC substitute.
    os._exit(0)

asyncio.run(main())
'''


def same_birth_alive(identity):
    try:
        process = psutil.Process(identity["pid"])
        return (
            process.is_running()
            and process.create_time() == identity["create_time"]
            and process.status() != psutil.STATUS_ZOMBIE
        )
    except psutil.NoSuchProcess:
        return False


def read_sdk_identity(workspace):
    with contextlib.suppress(FileNotFoundError, ValueError):
        return json.loads((workspace / "owned-sdk-identity.json").read_text())
    return None


async def wait_dead(*identities):
    async with asyncio.timeout(20):
        while any(same_birth_alive(identity) for identity in identities):
            await asyncio.sleep(0.05)


async def cleanup_test_daemon(identity, root):
    """Ordinary SIGTERM only, to the exact birth and private workspace we own."""
    if not same_birth_alive(identity):
        return
    process = psutil.Process(identity["pid"])
    workspace = Path(identity["workspace"])
    assert workspace.parent == root
    assert Path(process.cwd()).resolve() == workspace.resolve()
    assert "--detached" in process.cmdline()
    assert process.create_time() == identity["create_time"]
    process.send_signal(signal.SIGTERM)
    await wait_dead(identity)


def _test_spawned_process(process, root, adapter):
    """This test's stray: a daemon running in one of its folders, or an SDK
    running its adapter script. Both live under this run's tmp_path, so a
    concurrent run of the same test is never touched."""
    try:
        cmdline = process.cmdline()
        if not cmdline:
            return False
        if str(adapter) in cmdline:
            return True
        if "--detached" not in cmdline:
            return False
        return os.path.realpath(process.cwd()).startswith(os.path.realpath(root) + os.sep)
    except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied):
        return False


async def stop_stray_test_processes(root, adapter):
    """Final net: stop every daemon/SDK this test spawned, pass or fail.

    The detached sibling has no owner watchdog, and the identity-based
    cleanup above only knows processes the owner managed to report. An
    owner that dies before writing its report (crash, interrupted run)
    otherwise leaks its daemons for good.
    """
    strays = [
        process
        for process in psutil.process_iter(["pid"])
        if _test_spawned_process(process, root, adapter)
    ]
    for process in strays:
        with contextlib.suppress(psutil.NoSuchProcess):
            process.terminate()
    _, alive = psutil.wait_procs(strays, timeout=10) if strays else ([], [])
    for process in alive:
        with contextlib.suppress(psutil.NoSuchProcess):
            process.kill()
    if alive:
        _, alive = psutil.wait_procs(alive, timeout=10)
    assert not alive, f"test processes survived teardown: {[p.pid for p in alive]}"


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["ready", "blocked"])
async def test_owner_drop_retires_daemon_and_sdk_without_affecting_detached_agent(tmp_path, phase):
    root = tmp_path
    root.chmod(0o700)
    home = root / "home"
    directory = home / ".kollab"
    directory.mkdir(parents=True, mode=0o700)
    for name in ("owned", "detached"):
        (root / name).mkdir(mode=0o700)
    adapter = root / "owned_sdk_agent.py"
    adapter.write_text(SDK_AGENT)
    adapter.chmod(0o600)
    profiles = {
        "owned-" + state: {"provider": "acp", "command": [sys.executable, str(adapter)],
                          "env": {"OWNED_ACP_START": state}}
        for state in ("ready", "blocked")
    }
    config = {"kollabor": {"llm": {"default_profile": "owned-ready", "active_profile": "owned-ready",
                                   "profiles": profiles}},
              "plugins": {"hub": {"enabled": True}, "mcp": {"enabled": False}}}
    config_path = directory / "config.json"
    config_path.write_text(json.dumps(config))
    config_path.chmod(0o600)
    bootstrap = root / "owner.py"
    bootstrap.write_text(OWNER)
    bootstrap.chmod(0o600)
    env = {"HOME": str(home), "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
           "PYTHONPATH": str(ROOT), "KOLLAB_NO_KEYRING": "1", "TERM": "dumb"}
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-u", str(bootstrap), str(root), phase, cwd=root, env=env,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    owner_identity = {"pid": process.pid, "create_time": psutil.Process(process.pid).create_time()}
    stderr_task = asyncio.create_task(process.stderr.read())
    report = {}
    observation = {"backend": "isolated deterministic SDK, no provider call", "phase": phase}
    try:
        line = await asyncio.wait_for(process.stdout.readline(), 100)
        if not line:
            await asyncio.wait_for(process.wait(), 5)
            raise AssertionError((await stderr_task).decode())
        report = json.loads(line)
        assert same_birth_alive(report["daemon"]) and same_birth_alive(report["sdk"])
        if phase == "ready":
            assert report["adopted_view_detached_without_stopping_owner"]
            assert same_birth_alive(report["detached_daemon"]) and same_birth_alive(report["detached_sdk"])
        started = time.monotonic()
        process.stdin.write(b"x")
        await process.stdin.drain()
        assert await asyncio.wait_for(process.wait(), 5) == 0
        await wait_dead(report["daemon"], report["sdk"])
        observation["owned_same_births_exited_without_signals"] = True
        observation["owner_drop_cleanup_seconds"] = time.monotonic() - started
        if phase == "ready":
            assert same_birth_alive(report["detached_daemon"]) and same_birth_alive(report["detached_sdk"])
            observation["independent_daemon_and_sdk_preserved"] = True
        # A blocked initialize must not restart/publish an SDK after cleanup.
        await asyncio.sleep(1.2)
        assert read_sdk_identity(root / "owned")["create_time"] == report["sdk"]["create_time"]
        assert not same_birth_alive(report["sdk"])
    finally:
        problems: list[str] = []
        try:
            if process.returncode is None:
                # This is the exact process created here; ordinary termination only.
                if same_birth_alive(owner_identity):
                    process.terminate()
                await asyncio.wait_for(process.wait(), 10)
            stderr = await asyncio.wait_for(stderr_task, 10)
            (root / "owner-stderr.txt").write_bytes(stderr)
        except BaseException as error:
            problems.append(f"owner shutdown: {type(error).__name__}: {error}")
        # Retain all identity/evidence files, even when the qualification fails.
        if not report and (root / "owner-report.json").exists():
            report = json.loads((root / "owner-report.json").read_text())
        if "detached_daemon" in report and "detached_sdk" not in report:
            sdk = read_sdk_identity(root / "detached")
            if sdk is not None:
                report["detached_sdk"] = sdk
        try:
            for key in ("daemon", "detached_daemon"):
                if key in report:
                    await cleanup_test_daemon(report[key], root)
            for key in ("sdk", "detached_sdk"):
                if key in report:
                    await wait_dead(report[key])
        except BaseException as error:
            # A failed precise cleanup must not skip the sweep below.
            problems.append(f"identity cleanup: {type(error).__name__}: {error}")
        try:
            # Last net, pass or fail: never leave a daemon or SDK behind,
            # even when the owner died before reporting any identity.
            await stop_stray_test_processes(root, adapter)
        except BaseException as error:
            problems.append(f"stray sweep: {type(error).__name__}: {error}")
        observation["identities"] = report
        observation["remaining_same_births_after_owned_teardown"] = [
            key for key, value in report.items()
            if isinstance(value, dict) and "pid" in value and same_birth_alive(value)
        ]
        observation["teardown_problems"] = problems
        (root / "lifetime-observation.json").write_text(json.dumps(observation, indent=2))
        if problems:
            raise AssertionError("teardown problems: " + "; ".join(problems))
