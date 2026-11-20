import asyncio
import contextlib
import json
import os
import sys
from types import SimpleNamespace

import acp
import psutil
import pytest
from acp.schema import PermissionOption, ToolCallStart

from kollabor.llm import acp_bridge as bridge_mod
from kollabor.llm.acp_bridge import ACPError, ACPPermissionBridge, ACPProfile, ACPRunner
from tests.unit._owned_processes import (
    asyncio_child_signal_is_reap_serialized,
    wait_for_recorded_identities,
)


def test_live_owned_processes_do_not_scan_host_process_table(tmp_path):
    runner = ACPRunner(ACPProfile("owned", (sys.executable,), cwd=str(tmp_path)))
    owner = psutil.Process()
    runner._adapter_pid = owner.pid
    runner._process_started_at = owner.create_time()
    runner._process_username = ""
    runner._owned_processes[owner.pid] = owner

    assert runner._live_owned_processes() == [owner]


def test_refresh_process_tree_tracks_descendants_on_windows(tmp_path, monkeypatch):
    class Process:
        def __init__(self, pid, children=()):
            self.pid = pid
            self._children = children

        def is_running(self):
            return True

        def ppid(self):
            return 1

        def children(self, recursive=False):
            assert recursive is True
            return list(self._children)

        def create_time(self):
            return float(self.pid)

    child = Process(202)
    adapter = Process(101, (child,))
    runner = ACPRunner(ACPProfile("owned", (sys.executable,), cwd=str(tmp_path)))
    runner._adapter_pid = adapter.pid
    runner._owned_processes[adapter.pid] = adapter
    monkeypatch.setattr(bridge_mod.os, "name", "nt")

    runner._refresh_process_tree()

    assert runner._owned_processes[child.pid] is child


@pytest.mark.asyncio
async def test_start_refuses_when_recorded_process_group_survives(tmp_path, monkeypatch):
    runner = ACPRunner(
        ACPProfile("owned", (sys.executable,), cwd=str(tmp_path)),
        persist_profile_resolution=False,
    )
    runner._process_group_id = 4242
    monkeypatch.setattr(runner, "_owned_process_group_exists", lambda: True)
    monkeypatch.setattr(
        ACPProfile,
        "resolve",
        lambda _profile, **_kwargs: ((sys.executable,), {}, str(tmp_path)),
    )

    async def unexpected_spawn(*_args, **_kwargs):
        pytest.fail("a new adapter must not overwrite the surviving process-group record")

    monkeypatch.setattr(bridge_mod.asyncio, "create_subprocess_exec", unexpected_spawn)
    with pytest.raises(ACPError, match="previous ACP process group still requires cleanup"):
        await runner._start_process()


@pytest.mark.asyncio
async def test_terminate_waits_until_owned_process_group_is_empty_after_kill(tmp_path, monkeypatch):
    runner = ACPRunner(
        ACPProfile("owned", (sys.executable,), cwd=str(tmp_path)),
        persist_profile_resolution=False,
    )
    runner._process_group_id = 4242
    # Empty during the SIGTERM grace, one member right after SIGKILL, then empty.
    checks = iter((False, True, False))
    group_states = []
    observed = []
    monkeypatch.setattr(runner, "_live_owned_processes", lambda: [])
    monkeypatch.setattr(
        runner, "_signal_owned_process_group", lambda signum: observed.append(signum)
    )

    def group_exists():
        exists = next(checks)
        group_states.append(exists)
        return exists

    monkeypatch.setattr(runner, "_owned_process_group_exists", group_exists)

    await runner._terminate_process()

    assert observed[-1] == bridge_mod.signal.SIGKILL
    assert group_states == [False, True, False]
    assert runner._process_group_id is None


@pytest.mark.asyncio
async def test_group_anchor_keeps_group_identity_after_adapter_exits(tmp_path):
    runner = ACPRunner(
        ACPProfile("owned", (sys.executable, "-c", FAKE_ADAPTER), cwd=str(tmp_path)),
        persist_profile_resolution=False,
    )
    try:
        await runner._start_process()
        assert runner._process is not None
        # The adapter keeps the launcher's PID and leads its own group.
        assert runner._process_group_id == runner._process.pid
        runner._process.terminate()
        await runner._process.wait()
        # The anchor outlives the adapter, so the group id is still held.
        assert runner._group_anchor_alive()
    finally:
        await runner._terminate_process()
    assert runner._group_anchor is None


@pytest.mark.asyncio
async def test_process_tracker_leaves_cleanup_to_close_after_close_started(tmp_path, monkeypatch):
    runner = ACPRunner(
        ACPProfile("owned", (sys.executable,), cwd=str(tmp_path)),
        persist_profile_resolution=False,
    )
    runner._owned_processes[12345] = object()
    runner._process = SimpleNamespace(returncode=0)
    runner._closing = True
    signals = []
    monkeypatch.setattr(runner, "_refresh_process_tree", lambda: None)
    monkeypatch.setattr(
        runner, "_signal_owned_process_group", lambda signum: signals.append(signum)
    )

    await runner._track_process_tree()

    assert signals == []


@pytest.mark.asyncio
@pytest.mark.parametrize("message, login_guidance", [
    ("Authentication required", True),
    ("This client is no longer supported for Gemini Code Assist for individuals", False),
    ("You've hit your weekly limit", False),
])
async def test_startup_refusal_preserves_public_message_code_and_cleans_process(tmp_path, message, login_guidance):
    adapter = """
import asyncio
import acp
from acp.schema import InitializeResponse
class Agent:
    async def initialize(self, protocol_version, **kwargs):
        return InitializeResponse(protocolVersion=1)
    async def new_session(self, cwd, **kwargs):
        raise acp.RequestError(-32000, REFUSAL_MESSAGE)
    def on_connect(self, conn):
        pass
asyncio.run(acp.run_agent(Agent()))
""".replace("REFUSAL_MESSAGE", repr(message))
    runner = ACPRunner(ACPProfile("codex-cli", (sys.executable, "-c", adapter), cwd=str(tmp_path)))
    with pytest.raises(ACPError) as caught:
        await runner.start()
    assert caught.value.code == -32000
    assert message in str(caught.value)
    assert ("Run codex login" in str(caught.value)) is login_guidance
    assert runner._connection is None
    assert not runner._live_owned_processes()
    await runner.close()
    assert runner._queue.empty()
    assert not runner.is_processing


@pytest.mark.asyncio
@pytest.mark.parametrize("worker_state", ["never_started", "finished"])
async def test_close_without_queue_worker_releases_processing_and_is_repeatable(tmp_path, worker_state):
    runner = ACPRunner(ACPProfile("owned", (sys.executable,), cwd=str(tmp_path)))
    if worker_state == "finished":
        runner._worker = asyncio.create_task(asyncio.sleep(0))
        await runner._worker

    for _ in range(2):
        await runner.close()
        assert runner.state == "closed"
        assert runner._worker is None
        assert runner._queue.empty()
        assert not runner.is_processing


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["idle", "completed_turn", "concurrent_close"])
async def test_close_after_started_worker_releases_queue_and_is_repeatable(tmp_path, stage):
    runner = ACPRunner(ACPProfile("owned", (sys.executable, "-c", FAKE_ADAPTER), cwd=str(tmp_path)))
    try:
        await runner.start()
        worker = runner._worker
        if stage == "completed_turn":
            assert (await runner.prompt("finish this turn")).text == "hello "
        if stage == "concurrent_close":
            await asyncio.gather(runner.close(), runner.close())
        else:
            await runner.close()
        assert worker.done()
        assert runner._queue.empty()
        assert not runner.is_processing

        await runner.close()
        assert runner._queue.empty()
        assert not runner.is_processing
        assert runner._connection is None and runner._process is None
        assert not runner._live_owned_processes()
    finally:
        await runner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("release_before_deadline", [True, False])
async def test_close_settles_active_and_queued_owners_without_starting_more_turns(
    tmp_path, monkeypatch, release_before_deadline
):
    runner = ACPRunner(ACPProfile("owned", (sys.executable,), cwd=str(tmp_path)))
    entered = asyncio.Event()
    release = asyncio.Event()
    dispatched = []

    class Connection:
        async def close(self):
            pass

    async def run_prompt(admission, generation):
        dispatched.append(admission.request_id)
        entered.set()
        await release.wait()

    monkeypatch.setattr(runner, "_run_prompt", run_prompt)
    runner.session_id = "owned-session"
    runner._connection = Connection()
    runner._worker = asyncio.create_task(runner._drain_queue())
    worker = runner._worker
    first = runner.admit("active", request_id="active-request", source="hub", thread_id="active-thread")
    await asyncio.wait_for(entered.wait(), timeout=1)
    waiting = runner.admit("waiting", request_id="waiting-request", source="state_rpc", thread_id="waiting-thread")
    close = asyncio.create_task(runner.close())
    try:
        for admission in [first, waiting]:
            with pytest.raises(ACPError, match="runner closed"):
                await asyncio.wait_for(asyncio.shield(admission.future), timeout=1)
        late = runner.admit("during close", request_id="late-request", source="hub", thread_id="late-thread")
        if release_before_deadline:
            release.set()
        await asyncio.wait_for(close, timeout=4)
        with pytest.raises(ACPError, match="runner closed"):
            await asyncio.wait_for(asyncio.shield(late.future), timeout=1)
        assert worker.done()
        assert dispatched == ["active-request"]
        assert first.thread_id == "active-thread" and waiting.thread_id == "waiting-thread"
        assert runner._queue.empty()
        assert not runner.is_processing
    finally:
        release.set()
        await asyncio.gather(close, return_exceptions=True)
        await runner.close()


@pytest.mark.asyncio
async def test_close_settles_waiting_owners_when_consumer_is_held(tmp_path):
    runner = ACPRunner(ACPProfile("owned", (sys.executable,), cwd=str(tmp_path)))
    runner.session_id = "owned-session"
    runner._connection = SimpleNamespace(close=lambda: asyncio.sleep(0))
    runner._queue_release.clear()
    runner._worker = asyncio.create_task(runner._drain_queue())
    waiting = [runner.admit(f"waiting-{i}", request_id=f"r{i}", source="hub") for i in range(2)]
    await runner.close()
    for admission in waiting:
        with pytest.raises(ACPError, match="runner closed"):
            await admission.future
    assert runner._queue.empty()
    assert not runner.is_processing


@pytest.mark.asyncio
@pytest.mark.parametrize("code, rpc_message, streamed, expected", [
    (-32603, "Internal error", "You've hit your weekly limit · resets 5am (America/Phoenix)", "weekly limit"),
    (-32000, "Authentication required", "Failed to authenticate: OAuth session expired", "OAuth session expired"),
    (-32001, "Provider quota exhausted", "", "Provider quota exhausted"),
    (-32603, "Internal error", "Checking the limit of this function", "Internal error"),
])
async def test_provider_refusal_preserves_message_code_without_false_crash(
    tmp_path, code, rpc_message, streamed, expected
):
    runner = ACPRunner(ACPProfile.builtin("claude-code", cwd=str(tmp_path)))
    runner.session_id = "owned-session"
    runner.generation = 1
    runner._process = SimpleNamespace(returncode=None)
    runner.last_stderr = "[session/create] sessionId=irrelevant-session phase=register"

    class Connection:
        async def prompt(self, **kwargs):
            if streamed:
                runner._updates.append({"kind": "text", "text": streamed})
            raise acp.RequestError(code, rpc_message, {"credentials": "must-not-be-displayed"})

    runner._connection = Connection()
    future = asyncio.get_running_loop().create_future()
    admission = bridge_mod.ACPAdmission("request", "hub", "thread", 1, 1, "prompt", future)
    with pytest.raises(ACPError) as caught:
        await runner._run_prompt(admission, 1)
    assert caught.value.code == code
    assert expected in str(caught.value)
    assert "irrelevant-session" not in str(caught.value)
    assert "must-not-be-displayed" not in str(caught.value)
    assert "stopped mid-change" not in str(caught.value)
    assert runner._process.returncode is None
    if code == -32000:
        assert "Run claude" in str(caught.value)
    future.cancel()


@pytest.mark.asyncio
async def test_non_rpc_failure_warns_about_interrupted_work_only_after_process_exit(tmp_path):
    runner = ACPRunner(ACPProfile("owned", (sys.executable,), cwd=str(tmp_path)))
    runner.session_id = "s1"
    runner.generation = 1
    class Connection:
        async def prompt(self, **kwargs):
            raise ConnectionError("stream disconnected")
    runner._connection = Connection()
    for returncode in (None, 1):
        runner._process = SimpleNamespace(returncode=returncode)
        future = asyncio.get_running_loop().create_future()
        admission = bridge_mod.ACPAdmission("request", "rpc", None, 1, 1, "prompt", future)
        with pytest.raises(ACPError) as caught:
            await runner._run_prompt(admission, 1)
        assert ("stopped mid-change" in str(caught.value)) is (returncode is not None)
        future.cancel()

STUBBORN_ADAPTER = """
import asyncio
import os
import acp
from acp.schema import AgentMessageChunk, InitializeResponse, NewSessionResponse, PromptResponse, TextContentBlock

class FakeAgent:
    def __init__(self):
        self._conn = None
        self._run_file = os.environ["ACP_RUN_FILE"]
        try:
            self._run = int(open(self._run_file).read()) + 1
        except (FileNotFoundError, ValueError):
            self._run = 1
        with open(self._run_file, "w") as f:
            f.write(str(self._run))
    async def initialize(self, protocol_version, **kwargs):
        return InitializeResponse(protocolVersion=1)
    async def new_session(self, cwd, mcp_servers=None, **kwargs):
        return NewSessionResponse(sessionId=f"s{self._run}")
    async def prompt(self, session_id, prompt, **kwargs):
        if self._run == 1:
            async def late_update():
                await asyncio.sleep(0.2)
                await self._conn.session_update(session_id, AgentMessageChunk(
                    sessionUpdate="agent_message_chunk",
                    content=TextContentBlock(type="text", text="late-a "),
                ))
            asyncio.create_task(late_update())
            await asyncio.Future()
        await self._conn.session_update(session_id, AgentMessageChunk(
            sessionUpdate="agent_message_chunk",
            content=TextContentBlock(type="text", text="recovered "),
        ))
        return PromptResponse(stopReason="end_turn")
    async def cancel(self, session_id, **kwargs):
        return None
    def on_connect(self, conn):
        self._conn = conn

asyncio.run(acp.run_agent(FakeAgent()))
"""


COMPLIANT_ADAPTER = """
import asyncio
import acp
from acp.schema import AgentMessageChunk, InitializeResponse, NewSessionResponse, PromptResponse, TextContentBlock

class FakeAgent:
    def __init__(self):
        self._conn = None
        self._cancel = asyncio.Event()
    async def initialize(self, protocol_version, **kwargs):
        return InitializeResponse(protocolVersion=1)
    async def new_session(self, cwd, mcp_servers=None, **kwargs):
        return NewSessionResponse(sessionId="preserved")
    async def prompt(self, session_id, prompt, **kwargs):
        await self._cancel.wait()
        await self._conn.session_update(session_id, AgentMessageChunk(
            sessionUpdate="agent_message_chunk",
            content=TextContentBlock(type="text", text="drained "),
        ))
        return PromptResponse(stopReason="cancelled")
    async def cancel(self, session_id, **kwargs):
        self._cancel.set()
    def on_connect(self, conn):
        self._conn = conn

asyncio.run(acp.run_agent(FakeAgent()))
"""

FAKE_ADAPTER = """
import asyncio
import acp
from acp.schema import (
    AgentMessageChunk, InitializeResponse, NewSessionResponse,
    PermissionOption, PromptResponse, TextContentBlock, ToolCallStart,
)

class FakeAgent:
    def __init__(self):
        self._sessions = 0
    async def initialize(self, protocol_version, **kwargs):
        return InitializeResponse(protocolVersion=1)
    async def new_session(self, cwd, mcp_servers=None, **kwargs):
        self._sessions += 1
        return NewSessionResponse(sessionId=f's{self._sessions}')
    async def prompt(self, session_id, prompt, **kwargs):
        import asyncio
        await asyncio.sleep(0.05)
        await self._conn.session_update(session_id, AgentMessageChunk(
            sessionUpdate='agent_message_chunk',
            content=TextContentBlock(type='text', text='hello '),
        ))
        tool = ToolCallStart(
            sessionUpdate='tool_call', toolCallId='t1', title='ignored', kind='execute', status='completed',
        )
        await self._conn.session_update(session_id, tool)
        return PromptResponse(stopReason='end_turn')
    async def cancel(self, session_id, **kwargs):
        return None
    def on_connect(self, conn):
        self._conn = conn

asyncio.run(acp.run_agent(FakeAgent()))
"""


FAST_ORPHAN_ADAPTER = """
import asyncio
import json
import os
import signal
import time
import psutil
import acp
from acp.schema import InitializeResponse, NewSessionResponse, PromptResponse

def orphan():
    # Do not let the adapter return until the detached process has recorded
    # its identity: otherwise even a finally block can race its creation.
    read_fd, write_fd = os.pipe()
    if os.fork():
        os.close(write_fd)
        try:
            os.read(read_fd, 1)
        finally:
            os.close(read_fd)
        return
    # Detach by double fork. The orphan keeps the adapter's session unless
    # ACP_OWNED_CHILD_SETSID asks for the usual daemonizing setsid().
    if os.environ.get('ACP_OWNED_CHILD_SETSID') == '1':
        os.setsid()
    if os.fork():
        os._exit(0)
    signal.alarm(60)  # hard bound even if the test process is killed
    for fd in (0, 1, 2):
        try:
            os.close(fd)
        except OSError:
            pass
    path = os.environ['ACP_OWNED_CHILD_FILE']
    with open(path + '.tmp', 'w') as stream:
        json.dump({'pid': os.getpid(), 'birth': psutil.Process().create_time()}, stream)
    os.replace(path + '.tmp', path)
    os.write(write_fd, b'1')
    os.close(write_fd)
    try:
        owner = psutil.Process(int(os.environ['ACP_TEST_OWNER_PID']))
        birth = float(os.environ['ACP_TEST_OWNER_BIRTH'])
        while (owner.is_running() and owner.status() != psutil.STATUS_ZOMBIE
               and owner.create_time() == birth):
            time.sleep(0.1)
    except psutil.Error:
        pass

class Agent:
    async def initialize(self, protocol_version, **kwargs):
        return InitializeResponse(protocolVersion=1)
    async def new_session(self, cwd, **kwargs):
        return NewSessionResponse(sessionId='owned-orphan')
    async def prompt(self, session_id, prompt, **kwargs):
        if os.environ['ACP_OWNED_CHILD_MODE'] == 'prompt':
            orphan()
        return PromptResponse(stopReason='end_turn')
    def on_connect(self, conn):
        pass

signal.signal(signal.SIGCHLD, signal.SIG_IGN)
if os.environ['ACP_OWNED_CHILD_MODE'] == 'startup':
    orphan()
    os._exit(0)
asyncio.run(acp.run_agent(Agent()))
if os.environ['ACP_OWNED_CHILD_MODE'] == 'close':
    orphan()
"""


def _orphan_env(child_file, mode):
    return {
        "ACP_OWNED_CHILD_FILE": str(child_file),
        "ACP_OWNED_CHILD_MODE": mode,
        "ACP_TEST_OWNER_PID": str(os.getpid()),
        "ACP_TEST_OWNER_BIRTH": str(psutil.Process().create_time()),
    }


def _capture_test_subprocesses(monkeypatch, *, wait_for_exit=False):
    """Retain asyncio child owners independently of ACPRunner's tracking.

    POSIX ``Process.terminate`` and ``Process.kill`` still signal a numeric PID;
    these handles are not pidfds. Cleanup calls them only on the event loop that
    owns the transport, after checking its return code and without an intervening
    await, then waits on that same owner.
    """
    original_spawn = asyncio.create_subprocess_exec
    captured = []

    async def spawn(*args, **kwargs):
        process = await original_spawn(*args, **kwargs)
        if kwargs.get("process_group") == 0:
            # ACPRunner's POSIX group guard: it lives until close by design and
            # close owns it. Waiting on it would fail start() before the adapter
            # runs; killing it would make close refuse to signal the group.
            return process
        # Retain the child owner; do not resolve a detached PID as a fallback.
        captured.append(process)
        if wait_for_exit:
            # Model a root exiting before ACPRunner can inspect its tree.
            await asyncio.wait_for(process.wait(), timeout=3)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    return captured


async def _cleanup_test_subprocesses(captured):
    errors = []
    safe_signal = asyncio_child_signal_is_reap_serialized()
    for process in reversed(captured):
        try:
            if process.returncode is None and safe_signal:
                with contextlib.suppress(ProcessLookupError):
                    # POSIX methods still use the owner's numeric PID. This call
                    # is serialized with reaping only on supported runtimes.
                    process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=2)
            except asyncio.TimeoutError:
                if not safe_signal:
                    errors.append(
                        RuntimeError(
                            "asyncio child did not exit and this runtime has no safe signal path; "
                            "defer cleanup to the ephemeral CI runner"
                        )
                    )
                    continue
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await asyncio.wait_for(process.wait(), timeout=2)
        except Exception as exc:
            errors.append(exc)
    if errors:
        raise ExceptionGroup("test subprocess cleanup failed", errors)


def _wait_for_orphan_identity(child_file, timeout=5):
    if not child_file.exists():
        return "not-recorded"
    identity = json.loads(child_file.read_text())
    try:
        wait_for_recorded_identities(
            [(identity["pid"], identity["birth"])], timeout=timeout
        )
    except RuntimeError as exc:
        if "defer cleanup to the ephemeral CI runner" not in str(exc):
            raise
        return "deferred"
    return "exited"


def test_wait_for_orphan_identity_returns_exited_after_observation(tmp_path, monkeypatch):
    child_file = tmp_path / "child.json"
    child_file.write_text(json.dumps({"pid": 27123, "birth": 12.5}))
    observed = []

    def wait_for_identities(identities, timeout):
        observed.append((tuple(identities), timeout))

    monkeypatch.setattr(
        sys.modules[__name__], "wait_for_recorded_identities", wait_for_identities
    )

    assert _wait_for_orphan_identity(child_file, timeout=0.25) == "exited"
    assert observed == [(((27123, 12.5),), 0.25)]


def test_wait_for_orphan_identity_reports_bounded_deferral(tmp_path, monkeypatch):
    child_file = tmp_path / "early-child.json"
    child_file.write_text(json.dumps({"pid": 27124, "birth": 13.5}))

    def wait_for_identities(identities, timeout):
        assert tuple(identities) == ((27124, 13.5),)
        assert timeout == 5
        raise RuntimeError(
            "recorded processes remain; no PID/PGID fallback; "
            "defer cleanup to the ephemeral CI runner"
        )

    monkeypatch.setattr(
        sys.modules[__name__], "wait_for_recorded_identities", wait_for_identities
    )

    assert _wait_for_orphan_identity(child_file) == "deferred"


def test_wait_for_orphan_identity_propagates_unverifiable_identity(tmp_path, monkeypatch):
    child_file = tmp_path / "child.json"
    child_file.write_text(json.dumps({"pid": 27125, "birth": 14.5}))

    def wait_for_identities(_identities, timeout):
        assert timeout == 5
        raise RuntimeError("could not verify recorded process; no signal attempted")

    monkeypatch.setattr(
        sys.modules[__name__], "wait_for_recorded_identities", wait_for_identities
    )

    with pytest.raises(RuntimeError, match="could not verify recorded process"):
        _wait_for_orphan_identity(child_file)


def test_wait_for_orphan_identity_skips_missing_pidfile(tmp_path, monkeypatch):
    monkeypatch.setattr(
        sys.modules[__name__],
        "wait_for_recorded_identities",
        lambda *_args, **_kwargs: pytest.fail("must not inspect an absent pidfile"),
    )

    assert _wait_for_orphan_identity(tmp_path / "missing.json") == "not-recorded"


async def _cleanup_orphan_test(runner, captured, child_file):
    try:
        # The code under test can be broken, raise, or hang; it is not the
        # authority for the test's own process cleanup.
        with contextlib.suppress(Exception):
            await asyncio.wait_for(runner.close(), timeout=12)
    finally:
        # The detached fixture has its own owner-death check and alarm. We do
        # not signal its bare PID if the retained direct-child handle is gone.
        try:
            await _cleanup_test_subprocesses(captured)
        finally:
            outcome = _wait_for_orphan_identity(child_file)
    return outcome



@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX fork and setsid")
@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["prompt", "close"])
async def test_close_recovers_fast_detached_orphan_and_preserves_other_session_process(tmp_path, mode, monkeypatch):
    child_file = tmp_path / "child.json"
    runner = ACPRunner(ACPProfile("owned-orphan", (sys.executable, "-c", FAST_ORPHAN_ADAPTER),
                                  cwd=str(tmp_path), env=_orphan_env(child_file, mode)))
    captured = _capture_test_subprocesses(monkeypatch)
    outsider = None
    outsider_owner = None

    def child_identity():
        if not child_file.exists():
            return None
        identity = json.loads(child_file.read_text())
        with contextlib.suppress(psutil.NoSuchProcess):
            child = psutil.Process(identity["pid"])
            if child.create_time() == identity["birth"]:
                return child
        return None

    try:
        await runner.start()
        outsider = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "import time; time.sleep(60)", cwd=str(tmp_path),
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL, start_new_session=True,
        )
        outsider_owner = psutil.Process(outsider.pid)
        outsider_owner.create_time()
        await runner.prompt("create only the owned fixture")
        if mode == "prompt":
            for _ in range(200):
                if child_file.exists():
                    break
                await asyncio.sleep(0.005)
            assert child_file.exists()
            child = child_identity()
            assert child is not None
            assert os.getpgid(child.pid) == runner._process_group_id
        await runner.close()
        assert child_file.exists()
        child = child_identity()
        assert child is None or not child.is_running() or child.status() == psutil.STATUS_ZOMBIE
        assert outsider.returncode is None and outsider_owner.is_running()
        assert not runner._owned_processes and runner._adapter_pid is None
    finally:
        await _cleanup_orphan_test(runner, captured, child_file)


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX fork and setsid")
@pytest.mark.asyncio
async def test_setsid_orphan_lost_before_a_tree_sample_is_recovered(tmp_path):
    child_file = tmp_path / "child.json"
    runner = ACPRunner(ACPProfile("owned-orphan", (sys.executable, "-c", FAST_ORPHAN_ADAPTER), cwd=str(tmp_path), env={
        "ACP_OWNED_CHILD_FILE": str(child_file), "ACP_OWNED_CHILD_MODE": "prompt",
        "ACP_OWNED_CHILD_SETSID": "1",
    }))
    child = None
    try:
        await runner.start()
        await runner.prompt("create a setsid orphan")
        for _ in range(200):
            if child_file.exists() and child_file.read_text():
                break
            await asyncio.sleep(0.005)
        identity = json.loads(child_file.read_text())
        child = psutil.Process(identity["pid"])
        assert child.create_time() == identity["birth"]
        assert os.getsid(child.pid) != runner._adapter_pid
        await runner.close()
        assert not child.is_running() or child.status() == psutil.STATUS_ZOMBIE
        assert not runner._owned_processes and runner._adapter_pid is None
    finally:
        with contextlib.suppress(Exception):
            await runner.close()
        if child is not None:
            with contextlib.suppress(psutil.Error):
                child.kill()
                child.wait(timeout=2)


@pytest.mark.skipif(
    not hasattr(os, "fork") or not hasattr(os, "getsid"),
    reason="requires POSIX fork and sessions",
)
@pytest.mark.asyncio
async def test_orphan_is_recovered_where_os_getsid_is_missing(tmp_path, monkeypatch):
    child_file = tmp_path / "child.json"
    runner = ACPRunner(ACPProfile("owned-orphan", (sys.executable, "-c", FAST_ORPHAN_ADAPTER), cwd=str(tmp_path), env={
        "ACP_OWNED_CHILD_FILE": str(child_file), "ACP_OWNED_CHILD_MODE": "prompt",
        "ACP_OWNED_CHILD_SETSID": "1",
    }))
    child = None
    try:
        await runner.start()
        await runner.prompt("create an orphan")
        for _ in range(200):
            if child_file.exists() and child_file.read_text():
                break
            await asyncio.sleep(0.005)
        identity = json.loads(child_file.read_text())
        child = psutil.Process(identity["pid"])
        assert child.create_time() == identity["birth"]
        assert os.getsid(child.pid) != runner._adapter_pid
        monkeypatch.delattr(bridge_mod.os, "getsid")
        runner._recover_session_processes()
        assert child.pid in runner._owned_processes
        await runner.close()
        monkeypatch.undo()
        assert not child.is_running() or child.status() == psutil.STATUS_ZOMBIE
        assert not runner._owned_processes and runner._adapter_pid is None
    finally:
        with contextlib.suppress(Exception):
            await runner.close()
        if child is not None:
            with contextlib.suppress(psutil.Error):
                child.kill()
                child.wait(timeout=2)


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX fork")
@pytest.mark.asyncio
async def test_private_process_group_cleans_orphan_without_session_lookup(tmp_path, monkeypatch):
    """The anchored group cleans its members without session or marker lookup."""
    child_file = tmp_path / "child.json"
    runner = ACPRunner(ACPProfile("owned-orphan", (sys.executable, "-c", FAST_ORPHAN_ADAPTER), cwd=str(tmp_path), env={
        "ACP_OWNED_CHILD_FILE": str(child_file), "ACP_OWNED_CHILD_MODE": "prompt",
    }))
    child = None
    try:
        await runner.start()
        await runner.prompt("create an orphan")
        for _ in range(200):
            if child_file.exists() and child_file.read_text():
                break
            await asyncio.sleep(0.005)
        identity = json.loads(child_file.read_text())
        child = psutil.Process(identity["pid"])
        assert child.create_time() == identity["birth"]
        assert os.getpgid(child.pid) == runner._process_group_id
        runner._process_owner_token = None
        monkeypatch.delattr(bridge_mod.os, "getsid")
        await runner.close()
        assert not child.is_running() or child.status() == psutil.STATUS_ZOMBIE
        assert not runner._owned_processes and runner._adapter_pid is None
    finally:
        with contextlib.suppress(Exception):
            await runner.close()
        if child is not None:
            with contextlib.suppress(psutil.Error):
                child.kill()
                child.wait(timeout=2)


@pytest.mark.skipif(not hasattr(os, "getsid"), reason="requires POSIX sessions")
@pytest.mark.asyncio
async def test_session_lookup_ignores_session_whose_leader_pid_was_reused(tmp_path):
    """A reused adapter pid that leads another session must not be adopted."""
    stranger = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import time; time.sleep(60)", cwd=str(tmp_path),
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL, start_new_session=True,
    )
    try:
        assert os.getsid(stranger.pid) == stranger.pid
        runner = ACPRunner(ACPProfile("owned", (sys.executable,), cwd=str(tmp_path)))
        # The recorded adapter had this pid; the number now names the stranger.
        runner._adapter_pid = stranger.pid
        runner._process_started_at = 0
        runner._process_username = psutil.Process().username()
        runner._recover_session_processes()
        assert stranger.pid not in runner._owned_processes
        assert stranger.returncode is None
    finally:
        stranger.kill()
        await asyncio.wait_for(stranger.wait(), timeout=2)


@pytest.mark.asyncio
async def test_close_checks_only_recent_same_user_process_markers(tmp_path, monkeypatch):
    """#1044: marker lookup checks recent same-user candidates only."""
    reads: list[tuple[int, str, float]] = []
    original_environ = psutil.Process.environ

    def recording_environ(self):
        reads.append((self.pid, self.info["username"], self.info["create_time"]))
        return original_environ(self)

    monkeypatch.setattr(psutil.Process, "environ", recording_environ)
    runner = ACPRunner(ACPProfile("owned", (sys.executable, "-c", FAKE_ADAPTER), cwd=str(tmp_path)))
    outsider = None
    try:
        await runner.start()
        # A same-user process born after the adapter, in its own session.
        outsider = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "import time; time.sleep(60)", cwd=str(tmp_path),
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL, start_new_session=True,
        )
        assert (await runner.prompt("one turn")).text == "hello "
        process_started_at = runner._process_started_at
        process_username = runner._process_username
        assert process_started_at is not None and process_username is not None
        await runner.close()
        assert outsider.pid in [pid for pid, _, _ in reads]
        assert sum(pid == outsider.pid for pid, _, _ in reads) == 1
        assert all(
            username == process_username and birth >= process_started_at
            for _, username, birth in reads
        )
        assert outsider.returncode is None and psutil.Process(outsider.pid).is_running()
        assert not runner._live_owned_processes()
    finally:
        with contextlib.suppress(Exception):
            await runner.close()
        if outsider is not None and outsider.returncode is None:
            outsider.kill()
            await asyncio.wait_for(outsider.wait(), timeout=2)


@pytest.mark.skipif(not hasattr(os, "getsid"), reason="requires POSIX sessions")
@pytest.mark.asyncio
async def test_group_cleanup_without_owner_marker_never_reads_process_environments(tmp_path, monkeypatch):
    """#608: the process-group path does not inspect process environments."""
    reads: list[int] = []
    original_environ = psutil.Process.environ

    def recording_environ(self):
        reads.append(self.pid)
        return original_environ(self)

    monkeypatch.setattr(psutil.Process, "environ", recording_environ)
    runner = ACPRunner(ACPProfile("owned", (sys.executable, "-c", FAKE_ADAPTER), cwd=str(tmp_path)))
    outsider = None
    try:
        await runner.start()
        runner._process_owner_token = None
        outsider = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "import time; time.sleep(60)", cwd=str(tmp_path),
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL, start_new_session=True,
        )
        assert (await runner.prompt("one turn")).text == "hello "
        await runner.close()
        assert reads == []
        assert outsider.returncode is None
        assert not runner._live_owned_processes()
    finally:
        with contextlib.suppress(Exception):
            await runner.close()
        if outsider is not None and outsider.returncode is None:
            outsider.kill()
            await asyncio.wait_for(outsider.wait(), timeout=2)


SESSION_PROBE = (
    "import os\n"
    "open(os.environ['ACP_SID_FILE'], 'w').write(str(os.getsid(0)))\n"
)

GRACE_CHILD = (
    "import os, signal, time\n"
    "if os.fork() == 0:\n"
    "    null = os.open(os.devnull, os.O_RDWR)\n"
    "    for fd in (0, 1, 2):\n"
    "        os.dup2(null, fd)\n"
    "    def finish(*_):\n"
    "        time.sleep(1.5)\n"
    "        open(os.environ['ACP_GRACE_DONE'], 'w').write('done')\n"
    "        os._exit(0)\n"
    "    signal.signal(signal.SIGTERM, finish)\n"
    "    signal.alarm(30)\n"
    "    open(os.environ['ACP_GRACE_READY'], 'w').write('ready')\n"
    "    while True:\n"
    "        time.sleep(0.1)\n"
)


async def _wait_for_file(path, timeout=5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not (path.exists() and path.read_text()):
        if asyncio.get_running_loop().time() >= deadline:
            return False
        await asyncio.sleep(0.05)
    return True


@pytest.mark.skipif(not hasattr(os, "killpg"), reason="POSIX sessions only")
@pytest.mark.asyncio
async def test_adapter_runs_outside_the_parent_session(tmp_path):
    """Without a controlling terminal, an adapter tool that reads /dev/tty
    (ssh, sudo, git credentials) fails at once instead of stopping."""
    sid_file = tmp_path / "sid"
    runner = ACPRunner(
        ACPProfile("session", (sys.executable, "-c", SESSION_PROBE + FAKE_ADAPTER), cwd=str(tmp_path),
                   env={"ACP_SID_FILE": str(sid_file)}),
        persist_profile_resolution=False,
    )
    try:
        await runner._start_process()
        assert await _wait_for_file(sid_file)
        assert int(sid_file.read_text()) != os.getsid(0)
    finally:
        await runner._terminate_process()


@pytest.mark.skipif(not hasattr(os, "killpg"), reason="POSIX process groups only")
@pytest.mark.asyncio
async def test_close_lets_group_descendants_finish_their_sigterm_handler(tmp_path):
    """Close sends SIGTERM to the adapter's group and waits for its members,
    not only for the adapter, before SIGKILL."""
    ready, done = tmp_path / "ready", tmp_path / "done"
    runner = ACPRunner(
        ACPProfile("grace", (sys.executable, "-c", GRACE_CHILD + FAKE_ADAPTER), cwd=str(tmp_path),
                   env={"ACP_GRACE_READY": str(ready), "ACP_GRACE_DONE": str(done)}),
        persist_profile_resolution=False,
    )
    try:
        await runner._start_process()
        assert await _wait_for_file(ready)
    finally:
        await runner._terminate_process()
    assert done.exists()


@pytest.mark.skipif(not hasattr(os, "killpg"), reason="POSIX process groups only")
def test_group_signal_never_reaches_a_pgid_whose_anchor_is_gone(tmp_path, monkeypatch):
    """The anchor keeps the adapter's group id from being reused. Once it is
    gone, or none was recorded, the numeric id may belong to an unrelated
    group, so cleanup must never signal it again."""
    import signal
    import socket

    sent = []
    monkeypatch.setattr(os, "killpg", lambda pgid, signum: sent.append((pgid, signum)))
    profile = ACPProfile("anchor", (sys.executable, "-c", "pass"), cwd=str(tmp_path))

    held = ACPRunner(profile)
    ours, theirs = socket.socketpair()
    held._process_group_id = 4242
    held._group_anchor = ours
    try:
        assert held._signal_owned_process_group(signal.SIGTERM) is True
        theirs.close()  # the anchor exited, for example killed from outside
        assert held._signal_owned_process_group(signal.SIGKILL) is False
        assert held._signal_owned_process_group(signal.SIGTERM) is False
    finally:
        ours.close()
        theirs.close()

    unanchored = ACPRunner(profile)
    unanchored._process_group_id = 4343
    assert unanchored._signal_owned_process_group(signal.SIGTERM) is False

    assert sent == [(4242, signal.SIGTERM)]


@pytest.mark.skipif(not hasattr(os, "killpg"), reason="POSIX signals only")
@pytest.mark.asyncio
async def test_adapter_tree_keeps_default_sigpipe(tmp_path):
    """A pipeline under the adapter ends quietly on a closed pipe, as it does
    in a terminal; an inherited SIG_IGN makes `yes` report a broken pipe."""
    import shutil

    if shutil.which("yes") is None or shutil.which("head") is None:
        pytest.skip("needs yes and head")
    err, started = tmp_path / "yes.err", tmp_path / "started"
    script = f'yes 2>"{err}" | head -1 >/dev/null; : > "{started}"; exec "{sys.executable}" -c "$ACP_FAKE"'
    runner = ACPRunner(
        ACPProfile("pipe", ("/bin/sh", "-c", script), cwd=str(tmp_path), env={"ACP_FAKE": FAKE_ADAPTER}),
        persist_profile_resolution=False,
    )
    try:
        await runner._start_process()
        assert started.exists()
    finally:
        await runner._terminate_process()
    assert err.read_text() == ""


@pytest.mark.skipif(not hasattr(os, "killpg"), reason="POSIX process groups only")
def test_anchor_check_works_on_descriptors_above_1024(tmp_path):
    """select() cannot take a descriptor of 1024 or more; a live anchor there
    must still read as alive, or cleanup stops signaling the group."""
    import resource
    import socket

    target = 1500
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if hard != resource.RLIM_INFINITY and hard <= target:
        pytest.skip("cannot raise the descriptor limit")
    resource.setrlimit(resource.RLIMIT_NOFILE, (max(soft, target + 1), hard))
    ours, theirs = socket.socketpair()
    high = socket.socket(fileno=os.dup2(ours.fileno(), target))
    ours.close()
    try:
        runner = ACPRunner(ACPProfile("high", (sys.executable, "-c", "pass"), cwd=str(tmp_path)))
        runner._process_group_id = 4242
        runner._group_anchor = high
        assert runner._group_anchor_alive() is True
        theirs.close()
        assert runner._group_anchor_alive() is False
    finally:
        high.close()
        theirs.close()
        resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))


@pytest.mark.skipif(not hasattr(os, "killpg"), reason="POSIX process groups only")
def test_group_of_only_an_unreaped_dead_member_signals_without_error(tmp_path):
    """A member that exited but is not reaped yet stays in its group. macOS
    answers killpg on such a group with EPERM, Linux with success; with the
    anchor alive neither may raise, and the group is empty once it is reaped."""
    import signal
    import socket
    import subprocess
    import time

    member = subprocess.Popen([sys.executable, "-c", "pass"], process_group=0)
    deadline = time.monotonic() + 5
    while psutil.Process(member.pid).status() != psutil.STATUS_ZOMBIE:
        assert time.monotonic() < deadline
        time.sleep(0.02)
    ours, theirs = socket.socketpair()
    runner = ACPRunner(ACPProfile("zombie", (sys.executable, "-c", "pass"), cwd=str(tmp_path)))
    runner._process_group_id = member.pid
    runner._group_anchor = ours
    try:
        assert runner._signal_owned_process_group(signal.SIGTERM) in (True, False)
        assert runner._owned_process_group_exists() is True
        member.wait(timeout=5)
        assert runner._owned_process_group_exists() is False
        assert runner._signal_owned_process_group(signal.SIGKILL) is False
    finally:
        member.wait(timeout=5)
        ours.close()
        theirs.close()


@pytest.mark.skipif(not hasattr(os, "killpg"), reason="POSIX launcher only")
@pytest.mark.asyncio
async def test_launcher_that_never_execs_fails_within_the_startup_timeout(tmp_path, monkeypatch):
    """A launcher stuck before exec (stopped, or a hung filesystem) must not
    hold start() past the startup timeout."""
    import time

    monkeypatch.setattr(bridge_mod, "_POSIX_GROUP_LAUNCHER", "import time\ntime.sleep(60)\n")
    runner = ACPRunner(
        ACPProfile("stuck", (sys.executable, "-c", "pass"), cwd=str(tmp_path)),
        persist_profile_resolution=False,
        startup_timeout=1,
    )
    started = time.monotonic()
    try:
        with pytest.raises((TimeoutError, asyncio.TimeoutError)):
            await asyncio.wait_for(runner._start_process(), timeout=10)
        assert time.monotonic() - started < 5
    finally:
        await runner._terminate_process()


@pytest.mark.skipif(not hasattr(os, "killpg"), reason="POSIX launcher only")
@pytest.mark.asyncio
async def test_adapter_exec_failure_reports_the_os_error(tmp_path):
    """A failed exec surfaces the OS error, not a closed ACP connection."""
    bad = tmp_path / "not-a-program"
    bad.write_bytes(b"\x00\x01\x02 not an executable")
    bad.chmod(0o755)
    runner = ACPRunner(ACPProfile("bad", (str(bad),), cwd=str(tmp_path)), persist_profile_resolution=False)
    try:
        with pytest.raises(OSError, match="Exec format error"):
            await runner._start_process()
    finally:
        await runner._terminate_process()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX fork and setsid")
@pytest.mark.asyncio
async def test_startup_failure_cleans_same_group_orphan_after_root_exits(tmp_path, monkeypatch):
    child_file = tmp_path / "early-child.json"
    runner = ACPRunner(ACPProfile("owned-early-exit", (sys.executable, "-c", FAST_ORPHAN_ADAPTER),
                                  cwd=str(tmp_path), env=_orphan_env(child_file, "startup")))
    captured = _capture_test_subprocesses(monkeypatch, wait_for_exit=True)
    try:
        with pytest.raises(Exception):
            await runner.start()
        assert child_file.exists()
        identity = json.loads(child_file.read_text())
        with contextlib.suppress(psutil.NoSuchProcess):
            child = psutil.Process(identity["pid"])
            if child.create_time() == identity["birth"]:
                assert not child.is_running() or child.status() == psutil.STATUS_ZOMBIE
        assert runner._adapter_pid is None and not runner._owned_processes
    finally:
        await _cleanup_orphan_test(runner, captured, child_file)


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX fork and setsid")
@pytest.mark.asyncio
async def test_orphan_fixture_defers_cleanup_when_runner_close_raises(
    tmp_path, monkeypatch
):
    """The fixture must not signal a detached PID when close is broken."""
    instances = []
    captured_processes = []
    cleanup_outcomes = []
    original_init = ACPRunner.__init__
    original_close = ACPRunner.close
    original_cleanup = _cleanup_orphan_test

    def remember_runner(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        instances.append(self)

    async def broken_close(self):
        self._closing = True
        raise RuntimeError("injected close failure")

    async def observe_cleanup(runner, captured, child_file):
        try:
            outcome = await original_cleanup(runner, captured, child_file)
            cleanup_outcomes.append((child_file.name, outcome))
            return outcome
        finally:
            captured_processes.extend(captured)

    monkeypatch.setattr(ACPRunner, "__init__", remember_runner)
    monkeypatch.setattr(ACPRunner, "close", broken_close)
    monkeypatch.setattr(sys.modules[__name__], "_cleanup_orphan_test", observe_cleanup)
    try:
        with pytest.raises(RuntimeError, match="injected close failure"):
            await test_close_recovers_fast_detached_orphan_and_preserves_other_session_process(
                tmp_path, "prompt", monkeypatch
            )
        assert len(captured_processes) >= 2  # adapter and independently spawned outsider
        assert all(process.returncode is not None for process in captured_processes)
        assert cleanup_outcomes == [("child.json", "deferred")]
    finally:
        for runner in instances:
            await original_close(runner)


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX fork and setsid")
@pytest.mark.asyncio
async def test_startup_orphan_fixture_defers_when_product_cleanup_is_broken(
    tmp_path, monkeypatch
):
    cleanup_outcomes = []
    original_cleanup = _cleanup_orphan_test

    async def observe_cleanup(runner, captured, child_file):
        outcome = await original_cleanup(runner, captured, child_file)
        cleanup_outcomes.append((child_file.name, outcome))
        return outcome

    async def broken_terminate(self):
        pass

    monkeypatch.setattr(ACPRunner, "_terminate_process", broken_terminate)
    monkeypatch.setattr(sys.modules[__name__], "_cleanup_orphan_test", observe_cleanup)
    with pytest.raises(AssertionError):
        await test_startup_failure_cleans_same_group_orphan_after_root_exits(
            tmp_path, monkeypatch
        )
    assert cleanup_outcomes == [("early-child.json", "deferred")]



@pytest.mark.asyncio
async def test_profile_resolution_strips_environment_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    profile = ACPProfile("fake", (sys.executable, "-c", FAKE_ADAPTER), cwd=str(tmp_path))
    command, env, cwd = profile.resolve()
    assert command[0] == sys.executable
    assert "OPENAI_API_KEY" not in env
    assert cwd == str(tmp_path)
    profile = ACPProfile.from_dict("mcp", {
        "command": [sys.executable],
        "mcp_servers": [{"name": "hub", "command": "kollab", "args": ["--mcp-hub"]}],
    })
    assert profile.sdk_mcp_servers()[0].name == "hub"


@pytest.mark.asyncio
async def test_queued_turn_after_cancel_rebases_generation(tmp_path):
    profile = ACPProfile("fake", (sys.executable, "-c", FAKE_ADAPTER), cwd=str(tmp_path))
    runner = ACPRunner(profile)
    await runner.start()
    first = asyncio.create_task(runner.prompt("first"))
    await asyncio.sleep(0.01)
    second = asyncio.create_task(runner.prompt("second"))
    await runner.cancel()
    with pytest.raises(ACPError):
        await first
    result2 = await second
    assert result2.session_id == "s1"
    assert result2.generation == 3
    assert result2.text == "hello "
    await runner.close()


@pytest.mark.asyncio
async def test_queued_turn_keeps_first_generation_updates(tmp_path):
    profile = ACPProfile("fake", (sys.executable, "-c", FAKE_ADAPTER), cwd=str(tmp_path))
    runner = ACPRunner(profile)
    await runner.start()
    first = asyncio.create_task(runner.prompt("first"))
    await asyncio.sleep(0.01)
    second = asyncio.create_task(runner.prompt("second"))
    result1, result2 = await asyncio.gather(first, second)
    assert result1.text == "hello "
    assert result2.text == "hello "
    await runner.close()


@pytest.mark.asyncio
async def test_compliant_cancel_preserves_process_session_and_single_consumer(tmp_path):
    profile = ACPProfile("compliant", (sys.executable, "-c", COMPLIANT_ADAPTER), cwd=str(tmp_path))
    runner = ACPRunner(profile)
    await runner.start()
    process = runner._process
    worker = runner._worker
    first = asyncio.create_task(runner.prompt("first"))
    await asyncio.sleep(0.01)
    second = asyncio.create_task(runner.prompt("second"))
    await runner.cancel()
    with pytest.raises(ACPError):
        await first
    result = await second
    assert result.text == "drained "
    assert result.session_id == "preserved"
    assert result.generation == 2
    assert runner._process is process
    assert runner.session_id == "preserved"
    assert runner._worker is worker
    assert not runner._worker.done()
    await runner.close()


@pytest.mark.asyncio
async def test_ignored_cancel_kills_old_process_and_restarts_before_queue(tmp_path):
    run_file = tmp_path / "runs"
    profile = ACPProfile(
        "stubborn",
        (sys.executable, "-c", STUBBORN_ADAPTER),
        cwd=str(tmp_path),
        env={"ACP_RUN_FILE": str(run_file)},
    )
    runner = ACPRunner(profile)
    runner.cancel_timeout = 0.1
    await runner.start()
    old_process = runner._process
    first = asyncio.create_task(runner.prompt("first"))
    await asyncio.sleep(0.03)
    second = asyncio.create_task(runner.prompt("second"))
    await runner.cancel()
    with pytest.raises(ACPError):
        await first
    result = await asyncio.wait_for(second, 2)
    assert result.text == "recovered "
    assert runner._process is not old_process
    assert old_process.returncode is not None
    assert runner._worker is not None and not runner._worker.done()
    await runner.close()



@pytest.mark.asyncio
async def test_permission_cancel_while_decider_never_returns():
    started = asyncio.Event()

    async def decide(request):
        started.set()
        await asyncio.Future()

    bridge = ACPPermissionBridge(decide)
    bridge.bind("s1", 1)
    request = type("Request", (), {
        "session_id": "s1", "generation": 1,
        "options": ({"optionId": "allow", "kind": "allow_once"},),
    })()
    pending = asyncio.create_task(bridge.request(request))
    await started.wait()
    bridge.cancel()
    assert await asyncio.wait_for(pending, 1) is None


@pytest.mark.asyncio
async def test_permission_cancel_after_decider_await_fails_closed():
    started = asyncio.Event()
    release = asyncio.Event()

    async def decide(request):
        started.set()
        await release.wait()
        return "allow"

    bridge = ACPPermissionBridge(decide)
    bridge.bind("s1", 1)
    request = type("Request", (), {
        "session_id": "s1", "generation": 1,
        "options": ({"optionId": "allow", "kind": "allow_once"},),
    })()
    pending = asyncio.create_task(bridge.request(request))
    await started.wait()
    bridge.cancel()
    release.set()
    assert await pending is None


@pytest.mark.asyncio
async def test_runner_uses_sdk_agent_for_streams_answer_and_closes_process(tmp_path):
    profile = ACPProfile("fake", (sys.executable, "-c", FAKE_ADAPTER), cwd=str(tmp_path))
    runner = ACPRunner(profile)
    await runner.start()
    result = await runner.prompt("hello")
    assert result.session_id == "s1"
    assert result.text == "hello "
    assert result.generation == 1
    assert result.end_frame["type"] == "turn_end"
    assert any(update["kind"] == "tool_call" for update in result.updates)
    await runner.close()
    assert runner._process is None


@pytest.mark.asyncio
async def test_permission_bridge_fails_closed_without_decider():
    bridge = ACPPermissionBridge()
    bridge.bind("s1", 1)
    request = type("Request", (), {
        "session_id": "s1", "generation": 1,
        "options": ({"optionId": "allow", "kind": "allow_once"},),
    })()
    assert await bridge.request(request) is None


@pytest.mark.asyncio
async def test_permission_response_is_nested_and_option_bound(tmp_path):
    seen = []

    async def decide(request):
        seen.append(request)
        return "allow"

    runner = ACPRunner(ACPProfile("fake", (sys.executable,), cwd=str(tmp_path)), ACPPermissionBridge(decide))
    runner.session_id = "s1"
    runner.generation = 1
    runner._active_generation = 1
    runner.permission_bridge.bind("s1", 1)
    response = await runner._request_permission(
        "s1",
        ToolCallStart(sessionUpdate="tool_call", toolCallId="rpc-1", title="read", kind="read"),
        [PermissionOption(optionId="allow", name="Allow once", kind="allow_once")],
    )
    assert response.outcome.outcome == "selected"
    assert response.outcome.optionId == "allow"
    # Titles are display-only; operations come only from structured fields.
    assert seen[0].operation is None
    assert seen[0].workspace == str(tmp_path.resolve())
    assert seen[0].profile == "fake"



@pytest.mark.asyncio
async def test_permission_operation_uses_structured_command_paths_and_id_merge(tmp_path):
    seen = []

    async def decide(request):
        seen.append(request)
        return "allow"

    runner = ACPRunner(
        ACPProfile("fake", (sys.executable,), cwd=str(tmp_path)),
        ACPPermissionBridge(decide),
    )
    runner.session_id = "s1"
    runner._active_generation = 1
    runner.permission_bridge.bind("s1", 1)
    option = PermissionOption(optionId="allow", name="Allow once", kind="allow_once")

    def tool(payload):
        return type("Tool", (), {"model_dump": lambda self: payload})()

    await runner._request_permission(
        "s1", tool({"toolCallId": "exec", "kind": "execute", "rawInput": {"command": ["echo", "a b"]}}), [option]
    )
    await runner._request_permission(
        "s1", tool({"toolCallId": "chain", "kind": "execute", "rawInput": {"command": "echo a && echo b"}}), [option]
    )
    await runner._request_permission(
        "s1", tool({"toolCallId": "edit", "kind": "edit", "locations": [{"path": str(tmp_path / "x.py")}]}), [option]
    )
    await runner._request_permission(
        "s1", tool({"toolCallId": "outside", "kind": "edit", "locations": [{"path": "/outside"}]}), [option]
    )
    runner._updates.append({
        "kind": "tool_call",
        "payload": {"toolCallId": "merged", "kind": "execute", "rawInput": {"command": ["git", "status"]}},
    })
    await runner._request_permission("s1", tool({"toolCallId": "merged"}), [option])

    assert seen[0].operation == {"kind": "execute", "command": ["echo", "a b"]}
    assert seen[1].operation is None
    assert seen[2].operation == {
        "kind": "edit", "paths": [str((tmp_path / "x.py").resolve())]
    }
    assert seen[3].operation is None
    assert seen[4].operation == {"kind": "execute", "command": ["git", "status"]}
    assert seen[0].kind == "execute"
    assert seen[2].kind == "edit"


@pytest.mark.asyncio
async def test_permission_operation_keeps_kind_distinct_and_sparse_updates(tmp_path):
    seen = []

    async def decide(request):
        seen.append(request)
        return "allow"

    runner = ACPRunner(
        ACPProfile("fake", (sys.executable,), cwd=str(tmp_path)),
        ACPPermissionBridge(decide),
    )
    runner.session_id = "s1"
    runner._active_generation = 1
    runner.permission_bridge.bind("s1", 1)
    option = PermissionOption(optionId="allow", name="Allow once", kind="allow_once")

    def tool(payload):
        return type("Tool", (), {"model_dump": lambda self: payload})()

    path = str((tmp_path / "same").resolve())
    runner._updates.extend([
        {"kind": "tool_call", "payload": {
            "toolCallId": "sparse", "kind": "execute",
            "rawInput": {"command": ["echo", "ok"]}, "title": "original",
        }},
        {"kind": "tool_call_update", "payload": {
            "toolCallId": "sparse", "kind": None, "rawInput": None,
            "title": None,
        }},
    ])
    await runner._request_permission("s1", tool({"toolCallId": "sparse"}), [option])
    await runner._request_permission(
        "s1", tool({"toolCallId": "edit", "kind": "edit", "locations": [{"path": path}]}), [option]
    )
    await runner._request_permission(
        "s1", tool({"toolCallId": "delete", "kind": "delete", "locations": [{"path": path}]}), [option]
    )
    assert seen[0].operation == {"kind": "execute", "command": ["echo", "ok"]}
    assert seen[0].kind == "execute"
    assert seen[1].operation == {"kind": "edit", "paths": [path]}
    assert seen[2].operation == {"kind": "delete", "paths": [path]}
    assert seen[1].operation != seen[2].operation


@pytest.mark.asyncio
async def test_permission_bridge_honors_explicit_allow_always_option():
    async def decide(request):
        return "always"

    bridge = ACPPermissionBridge(decide)
    bridge.bind("s1", 1)
    request = type("Request", (), {
        "session_id": "s1", "generation": 1,
        "options": ({"optionId": "always", "kind": "allow_always"},),
    })()
    assert await bridge.request(request) == "always"


@pytest.mark.asyncio
async def test_permission_bridge_honors_explicit_reject_always_option():
    async def decide(request):
        return "reject"

    bridge = ACPPermissionBridge(decide)
    bridge.bind("s1", 1)
    request = type("Request", (), {
        "session_id": "s1", "generation": 1,
        "options": ({"optionId": "reject", "kind": "reject_always"},),
    })()
    assert await bridge.request(request) == "reject"


@pytest.mark.asyncio
async def test_permission_bridge_does_not_auto_select_always_kind():
    async def decide(request):
        return "allow"

    bridge = ACPPermissionBridge(decide)
    bridge.bind("s1", 1)
    request = type("Request", (), {
        "session_id": "s1", "generation": 1,
        "options": ({"optionId": "once", "kind": "allow_once"},),
    })()
    assert await bridge.request(request) is None



def test_missing_adapter_is_a_hard_gate(tmp_path):
    profile = ACPProfile("missing", ("definitely-not-an-acp-adapter",), cwd=str(tmp_path))
    with pytest.raises(ACPError):
        profile.resolve()

@pytest.mark.asyncio
async def test_admission_accepts_exactly_ten_waiting_turns(tmp_path):
    profile = ACPProfile("fake", (sys.executable, "-c", FAKE_ADAPTER), cwd=str(tmp_path))
    runner = ACPRunner(profile)
    await runner.start()
    runner._queue_release.clear()
    admissions = [
        runner.admit(f"m{i}", request_id=f"r{i}", source="state_rpc")
        for i in range(10)
    ]
    assert [item.request_id for item in admissions] == [f"r{i}" for i in range(10)]
    with pytest.raises(ACPError, match="full"):
        runner.admit("overflow", request_id="overflow", source="state_rpc")
    await runner.close()


@pytest.mark.asyncio
async def test_restart_failure_cleans_connection_process_and_stderr(tmp_path, monkeypatch):
    runner = ACPRunner(ACPProfile("fake", (sys.executable,), cwd=str(tmp_path)))

    class Connection:
        closed = False

        async def close(self):
            self.closed = True
            raise RuntimeError("close failed")

    class Process:
        pid = 4242
        returncode = None
        stdin = None

        async def wait(self):
            self.returncode = 0

    process = Process()
    connection = Connection()
    runner._connection = connection
    runner._process = process

    # This synthetic process is not owned by the test; do not discover host processes.
    monkeypatch.setattr(runner, "_live_owned_processes", lambda: [])
    runner._stderr_task = asyncio.create_task(asyncio.sleep(60))
    monkeypatch.setattr(bridge_mod.os, "killpg", lambda *_args: None)

    async def fail_start():
        raise RuntimeError("initialize failed")

    runner._start_process = fail_start
    with pytest.raises(RuntimeError, match="initialize failed"):
        await runner._restart_process()

    assert runner._connection is None
    assert runner._process is None
    assert runner._stderr_task is None
    assert process.returncode == 0
    assert connection.closed is True


def test_final_answer_uses_last_message_id_and_preserves_stream():
    updates = [
        {"kind": "text", "text": "working", "message_id": "commentary"},
        {"kind": "tool_call", "payload": {}},
        {"kind": "text", "text": "answer ", "message_id": "final"},
        {"kind": "text", "text": "now", "message_id": "final"},
    ]
    assert ACPRunner._select_answer(updates) == "answer now"
    assert updates[0]["message_id"] == "commentary"
    assert updates[-1]["message_id"] == "final"


def test_final_answer_without_ids_prefers_text_after_last_tool():
    updates = [
        {"kind": "text", "text": "before "},
        {"kind": "tool_call", "payload": {"x": 1}},
        {"kind": "thought", "payload": {"hidden": True}},
        {"kind": "text", "text": "after"},
    ]
    assert ACPRunner._select_answer(updates) == "after"


def test_final_answer_without_ids_falls_back_before_tool_and_ignores_output():
    updates = [
        {"kind": "text", "text": "answer"},
        {"kind": "tool_output", "payload": {"secret": "ignored"}},
        {"kind": "thought", "payload": {"secret": "ignored"}},
    ]
    assert ACPRunner._select_answer(updates) == "answer"


def test_final_answer_without_ids_uses_last_segment_before_new_tool():
    updates = [
        {"kind": "text", "text": "Planning. "},
        {"kind": "tool_call", "payload": {"toolCallId": "t1"}},
        {"kind": "text", "text": "Last answer. "},
        {"kind": "tool_call", "payload": {"toolCallId": "t2"}},
    ]
    assert ACPRunner._select_answer(updates) == "Last answer. "


def test_tool_progress_does_not_create_new_message_boundary():
    updates = [
        {"kind": "text", "text": "Planning. "},
        {"kind": "tool_call", "payload": {"toolCallId": "t1"}},
        {"kind": "text", "text": "Final answer. "},
        {"kind": "tool_call_update", "payload": {"toolCallId": "t1"}},
    ]
    assert ACPRunner._select_answer(updates) == "Final answer. "


@pytest.mark.asyncio
async def test_profile_resolution_uses_cache_and_strips_provider_keys(tmp_path, monkeypatch):
    adapter = tmp_path / "adapter"
    adapter.write_text("#!/bin/sh\n")
    adapter.chmod(0o755)
    config = tmp_path / "config"
    monkeypatch.setenv("KOLLAB_CONFIG_DIR", str(config))
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-pass")
    monkeypatch.setenv("CUSTOM_ENV", "kept")
    profile = ACPProfile("custom", (str(adapter),), cwd=str(tmp_path), pass_env=("OPENAI_API_KEY",))
    command, env, _ = profile.resolve()
    assert command[0] == str(adapter)
    assert env["OPENAI_API_KEY"] == "must-not-pass"
    assert env["CUSTOM_ENV"] == "kept"
    cache_path = config / "acp" / "resolved.json"
    assert cache_path.exists()
    assert __import__("json").loads(cache_path.read_text())["custom"]["path"] == str(adapter)
    adapter.unlink()
    with pytest.raises(ACPError, match="adapter executable not found"):
        profile.resolve()


def test_builtin_profile_and_missing_adapter_guidance(tmp_path, monkeypatch):
    profile = ACPProfile.builtin("claude-code", cwd=str(tmp_path))
    assert profile.command == ("claude-agent-acp",)
    assert "claude-agent-acp@0.89.0" in profile.install_guidance
    monkeypatch.setenv("KOLLAB_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(bridge_mod.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(ACPProfile, "_login_shell_path", lambda self: "")
    monkeypatch.setattr(bridge_mod.shutil, "which", lambda command: None)
    with pytest.raises(ACPError, match="Install the adapter"):
        profile.resolve()


@pytest.mark.parametrize("name", ["claude-code", "codex-cli"])
def test_builtin_resolves_managed_adapter_without_path_and_preserves_args(tmp_path, monkeypatch, name):
    monkeypatch.setenv("KOLLAB_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(bridge_mod.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(ACPProfile, "_login_shell_path", lambda self: "")
    monkeypatch.setattr(bridge_mod.shutil, "which", lambda command: None)
    profile = ACPProfile.builtin(name, cwd=str(tmp_path), env={"CUSTOM_SETTING": "kept"})
    adapter = bridge_mod.Path(profile._managed_adapter_path())
    adapter.parent.mkdir(parents=True)
    adapter.write_text("#!/usr/bin/env node\n")
    adapter.chmod(0o755)
    node = tmp_path / "node" / "node"
    node.parent.mkdir()
    node.write_text("#!/bin/sh\n")
    node.chmod(0o755)
    monkeypatch.setattr(ACPProfile, "_login_shell_node", lambda self: str(node))
    command, env, cwd = profile.resolve()
    assert command == (str(adapter), *profile.command[1:])
    assert env["PATH"].split(os.pathsep)[:2] == [str(adapter.parent), str(node.parent)]
    assert env["CUSTOM_SETTING"] == "kept"
    assert cwd == str(tmp_path)


def _managed_claude_shims(tmp_path, monkeypatch, **overrides):
    """Stand up the managed claude adapter + node shims so resolve() succeeds offline."""
    monkeypatch.setenv("KOLLAB_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(bridge_mod.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(ACPProfile, "_login_shell_path", lambda self: "")
    monkeypatch.setattr(bridge_mod.shutil, "which", lambda command: None)
    profile = ACPProfile.builtin("claude-code", cwd=str(tmp_path), **overrides)
    adapter = bridge_mod.Path(profile._managed_adapter_path())
    adapter.parent.mkdir(parents=True)
    adapter.write_text("#!/usr/bin/env node\n")
    adapter.chmod(0o755)
    node = tmp_path / "node" / "node"
    node.parent.mkdir()
    node.write_text("#!/bin/sh\n")
    node.chmod(0o755)
    monkeypatch.setattr(ACPProfile, "_login_shell_node", lambda self: str(node))
    return profile


def test_pass_env_forwards_only_listed_provider_keys(tmp_path, monkeypatch):
    profile = _managed_claude_shims(tmp_path, monkeypatch, pass_env=["GEMINI_API_KEY"])
    monkeypatch.setenv("GEMINI_API_KEY", "gem-key-e2e")
    monkeypatch.setenv("GOOGLE_API_KEY", "gcloud-key-e2e")
    _, env, _ = profile.resolve()
    assert env["GEMINI_API_KEY"] == "gem-key-e2e"
    assert "GOOGLE_API_KEY" not in env


def test_builtin_strips_unrelated_provider_keys(tmp_path, monkeypatch):
    profile = _managed_claude_shims(tmp_path, monkeypatch)
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CODEX_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.setenv(key, "must-not-pass")
    _, env, _ = profile.resolve()
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CODEX_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        assert key not in env


def test_env_map_cannot_inject_denylisted_keys(tmp_path, monkeypatch):
    profile = _managed_claude_shims(tmp_path, monkeypatch, env={"OPENAI_API_KEY": "smuggled"})
    monkeypatch.setenv("OPENAI_API_KEY", "exported-but-denylisted")
    _, env, _ = profile.resolve()
    assert "OPENAI_API_KEY" not in env


def test_managed_adapter_never_replaces_custom_builtin_command(tmp_path, monkeypatch):
    monkeypatch.setenv("KOLLAB_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(bridge_mod.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(ACPProfile, "_login_shell_path", lambda self: "")
    monkeypatch.setattr(bridge_mod.shutil, "which", lambda command: None)
    profile = ACPProfile.builtin("claude-code", command=["custom-claude-adapter"], cwd=str(tmp_path))
    assert profile._managed_adapter_path() == ""
    with pytest.raises(ACPError, match="custom-claude-adapter"):
        profile.resolve()



def test_profile_cache_re_resolves_when_command_changes(tmp_path, monkeypatch):
    config = tmp_path / "config"
    monkeypatch.setenv("KOLLAB_CONFIG_DIR", str(config))
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.write_text("#!/bin/sh\n")
    second.write_text("#!/bin/sh\n")
    first.chmod(0o755)
    second.chmod(0o755)
    ACPProfile("same", (str(first),), cwd=str(tmp_path)).resolve()
    command, _, _ = ACPProfile("same", (str(second),), cwd=str(tmp_path)).resolve()
    assert command[0] == str(second)



def test_profile_cache_invalid_or_non_executable_and_profile_keys_stay_stripped(tmp_path, monkeypatch):
    config = tmp_path / "config"
    monkeypatch.setenv("KOLLAB_CONFIG_DIR", str(config))
    monkeypatch.setenv("OPENAI_API_KEY", "exported")
    adapter = tmp_path / "adapter"
    adapter.write_text("#!/bin/sh\n")
    adapter.chmod(0o755)
    profile = ACPProfile(
        "edge", (str(adapter),), cwd=str(tmp_path),
        env={"OPENAI_API_KEY": "stored"}, pass_env=("OPENAI_API_KEY",),
    )
    _, env, _ = profile.resolve()
    assert env["OPENAI_API_KEY"] == "exported"
    (config / "acp" / "resolved.json").write_text("[]")
    _, _, _ = profile.resolve()
    adapter.chmod(0o644)
    with pytest.raises(ACPError, match="adapter executable not found"):
        profile.resolve()


def test_profile_rejects_non_executable_fallback_candidate(tmp_path, monkeypatch):
    monkeypatch.setenv("KOLLAB_CONFIG_DIR", str(tmp_path / "config"))
    adapter = tmp_path / "adapter"
    adapter.write_text("#!/bin/sh\n")
    adapter.chmod(0o644)
    monkeypatch.setattr(ACPProfile, "_login_shell_path", lambda self: str(adapter))
    profile = ACPProfile("fallback", (str(adapter),), cwd=str(tmp_path))
    with pytest.raises(ACPError, match="adapter executable not found"):
        profile.resolve()



def test_profile_prefixes_adapter_path_over_profile_path(tmp_path, monkeypatch):
    monkeypatch.setenv("KOLLAB_CONFIG_DIR", str(tmp_path / "config"))
    adapter = tmp_path / "adapter"
    adapter.write_text("#!/bin/sh\n")
    adapter.chmod(0o755)
    command, env, _ = ACPProfile(
        "path", (str(adapter),), cwd=str(tmp_path), env={"PATH": "/profile/path"}
    ).resolve()
    assert env["PATH"].split(os.pathsep)[0] == str(tmp_path)
    assert command[0] == str(adapter)


def test_node_shebang_requires_existing_executable_node(tmp_path, monkeypatch):
    monkeypatch.setenv("KOLLAB_CONFIG_DIR", str(tmp_path / "config"))
    adapter = tmp_path / "node-adapter"
    adapter.write_text("#!/usr/bin/env node\n")
    adapter.chmod(0o755)
    profile = ACPProfile("node", (str(adapter),), cwd=str(tmp_path))
    monkeypatch.setattr(ACPProfile, "_login_shell_node", lambda self: str(tmp_path / "missing-node"))
    with pytest.raises(ACPError, match="Node executable"):
        profile.resolve()


@pytest.mark.asyncio
async def test_prepare_cancel_holds_queue_for_delayed_owner(tmp_path):
    profile = ACPProfile("fake", (sys.executable, "-c", FAKE_ADAPTER), cwd=str(tmp_path))
    runner = ACPRunner(profile)
    await runner.start()
    first = asyncio.create_task(runner.prompt("first"))
    await asyncio.sleep(0.01)
    second = asyncio.create_task(runner.prompt("second"))
    session_id, generation = runner.session_id, runner.generation
    assert runner.prepare_cancel(
        expected_session_id=session_id,
        expected_generation=generation,
    )
    await asyncio.sleep(0.02)
    await runner.cancel(
        expected_session_id=session_id,
        expected_generation=generation,
    )
    with pytest.raises(ACPError):
        await first
    result = await second
    assert result.text == "hello "
    await runner.close()
