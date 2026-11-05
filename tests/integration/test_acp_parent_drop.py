"""Real owned daemons must retire their SDK when the creating process disappears.

The backend is an isolated deterministic SDK agent. This qualifies lifetime
ownership, including blocked startup, without calling an account/provider.
"""

import asyncio
import contextlib
import json
import math
import os
import sys
import time
from pathlib import Path

import psutil
import pytest

from tests.unit._owned_processes import asyncio_child_signal_is_reap_serialized

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

    async def record_daemon_started(started_session, started_workspace):
        daemon = started_session.daemon
        if daemon is None:
            raise AssertionError("EngineSession on_spawned callback ran without a daemon handle")
        identity_path = started_workspace / "owned-daemon-identity.json"
        temporary = identity_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(identity(daemon.pid, started_workspace)))
        os.replace(temporary, identity_path)

    async def record_owned_daemon(started_session):
        await record_daemon_started(started_session, workspace)

    initialization = asyncio.create_task(session.initialize(on_spawned=record_owned_daemon))
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

        async def record_detached_daemon(started_session):
            await record_daemon_started(started_session, sibling_workspace)

        sibling_initialization = asyncio.create_task(
            sibling.initialize(on_spawned=record_detached_daemon)
        )
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
    if not isinstance(identity, dict):
        return False
    pid = identity.get("pid")
    birth = identity.get("create_time")
    if (
        type(pid) is not int
        or pid <= 1
        or isinstance(birth, bool)
        or not isinstance(birth, (int, float))
        or not math.isfinite(birth)
    ):
        return False
    try:
        process = psutil.Process(pid)
        return (
            process.is_running()
            and process.create_time() == birth
            and process.status() != psutil.STATUS_ZOMBIE
        )
    except psutil.NoSuchProcess:
        return False
    except psutil.Error as exc:
        raise RuntimeError(f"could not verify test-owned process {pid}") from exc


def read_sdk_identity(workspace):
    with contextlib.suppress(FileNotFoundError, ValueError):
        return json.loads((workspace / "owned-sdk-identity.json").read_text())
    return None


async def wait_dead(*identities):
    async with asyncio.timeout(20):
        while any(same_birth_alive(identity) for identity in identities):
            await asyncio.sleep(0.05)


async def observe_test_owned_processes(root, report):
    """Observe recorded children without sending signals through recycled PIDs.

    Daemons are detached, so the test runner has no retained direct-child
    handle for them. Their only safe shutdown path is their own owner/watchdog.
    Owned children get a bounded 20-second exit observation; missing or stale
    receipts are reported. Survivors without safe signal authority are deferred
    to the ephemeral CI runner instead of being signalled by numeric PID.
    """
    problems = []
    deferred = []
    entries = (
        (root / "owned", "daemon", "owned-daemon-identity.json", True),
        (root / "owned", "sdk", "owned-sdk-identity.json", True),
        (root / "detached", "detached_daemon", "owned-daemon-identity.json", False),
        (root / "detached", "detached_sdk", "owned-sdk-identity.json", False),
    )
    for workspace, key, filename, should_exit in entries:
        path = workspace / filename
        if key.startswith("detached_") and report.get("phase") != "ready":
            try:
                path.stat()
            except FileNotFoundError:
                continue
        identity = report.get(key)
        if identity is None:
            try:
                identity = json.loads(path.read_text())
            except FileNotFoundError:
                problems.append(f"missing {key} identity receipt: {path}")
                continue
            except (OSError, ValueError) as error:
                problems.append(f"unreadable {key} identity receipt {path}: {error}")
                continue
        if (
            not isinstance(identity, dict)
            or type(identity.get("pid")) is not int
            or identity["pid"] <= 1
            or isinstance(identity.get("create_time"), bool)
            or not isinstance(identity.get("create_time"), (int, float))
            or not math.isfinite(identity["create_time"])
        ):
            problems.append(f"invalid {key} process identity; no signal attempted: {path}")
            continue
        if Path(str(identity.get("workspace", ""))).resolve() != workspace.resolve():
            problems.append(f"{key} identity escaped its test workspace; no signal attempted: {path}")
            continue

        if should_exit:
            try:
                await wait_dead(identity)
            except (asyncio.TimeoutError, RuntimeError) as error:
                problems.append(
                    f"{key} process {identity['pid']} may remain; no safe signal authority; "
                    f"deferred to ephemeral CI runner: {error}"
                )
        else:
            try:
                if same_birth_alive(identity):
                    deferred.append(
                        {
                            "identity": key,
                            "pid": identity["pid"],
                            "reason": (
                                "detached test child has no retained direct handle or verified watchdog; "
                                "deferred to ephemeral CI runner"
                            ),
                        }
                    )
            except RuntimeError as error:
                problems.append(
                    f"could not verify detached {key} process {identity['pid']}; "
                    f"no safe signal authority; deferred to ephemeral CI runner: {error}"
                )
    return problems, deferred


async def observe_test_owned_processes_after_owner_shutdown(root, report):
    """Recover owner evidence when possible, then always observe cleanup state."""
    problems = []
    if not isinstance(report, dict):
        problems.append("owner report was not a JSON object")
        report = {}

    if not report:
        try:
            owner_report = (root / "owner-report.json").read_text()
        except FileNotFoundError:
            pass
        except OSError as error:
            problems.append(f"unreadable owner report: {type(error).__name__}: {error}")
        else:
            try:
                recovered = json.loads(owner_report)
            except ValueError as error:
                problems.append(f"unreadable owner report: {type(error).__name__}: {error}")
            else:
                if isinstance(recovered, dict):
                    report = recovered
                else:
                    problems.append("owner report was not a JSON object")

    try:
        if "detached_daemon" in report and "detached_sdk" not in report:
            sdk = read_sdk_identity(root / "detached")
            if sdk is not None:
                report["detached_sdk"] = sdk
    except BaseException as error:
        problems.append(f"detached SDK identity observation: {type(error).__name__}: {error}")

    try:
        cleanup_problems, deferred = await observe_test_owned_processes(root, report)
        problems.extend(cleanup_problems)
    except BaseException as error:
        deferred = []
        problems.append(f"identity observation: {type(error).__name__}: {error}")
    return report, problems, deferred


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
            safe_signal = asyncio_child_signal_is_reap_serialized()
            if process.returncode is None:
                if safe_signal:
                    # Use the retained asyncio child owner, with no separate
                    # PID lookup. POSIX methods remain numeric signals, enabled
                    # only where Python's watcher serializes reaping on loop.
                    process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 10)
                except asyncio.TimeoutError as error:
                    if not safe_signal:
                        raise RuntimeError(
                            "asyncio child did not exit and this runtime has no safe signal path; "
                            "defer cleanup to the ephemeral CI runner"
                        ) from error
                    process.kill()
                    await asyncio.wait_for(process.wait(), 5)
            stderr = await asyncio.wait_for(stderr_task, 10)
            (root / "owner-stderr.txt").write_bytes(stderr)
        except BaseException as error:
            problems.append(f"owner shutdown: {type(error).__name__}: {error}")
        # Retain all identity/evidence files, even when the qualification fails.
        report, identity_problems, deferred = await observe_test_owned_processes_after_owner_shutdown(root, report)
        problems.extend(identity_problems)
        observation["identities"] = report
        observation["deferred_cleanup_no_safe_signal_authority"] = deferred
        remaining = []
        for key, value in report.items():
            if not isinstance(value, dict) or "pid" not in value:
                continue
            try:
                if same_birth_alive(value):
                    remaining.append(key)
            except RuntimeError as error:
                remaining.append(f"{key}: unverified ({error})")
        observation["remaining_same_births_after_owned_teardown"] = remaining
        observation["teardown_problems"] = problems
        (root / "lifetime-observation.json").write_text(json.dumps(observation, indent=2))
        if problems:
            raise AssertionError("teardown problems: " + "; ".join(problems))
