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
    runner._process_group_guard = SimpleNamespace(returncode=0)
    checks = iter((True, False))
    group_states = []
    observed = []

    async def wait_for_guard():
        return 0

    runner._process_group_guard.wait = wait_for_guard
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
    assert group_states == [True, False]
    assert runner._process_group_id is None


@pytest.mark.asyncio
async def test_process_group_guard_keeps_group_identity_after_adapter_exits(tmp_path):
    runner = ACPRunner(
        ACPProfile("owned", (sys.executable, "-c", FAKE_ADAPTER), cwd=str(tmp_path)),
        persist_profile_resolution=False,
    )
    try:
        await runner._start_process()
        assert runner._process is not None
        assert runner._process_group_guard is not None
        assert runner._process_group_id == runner._process_group_guard.pid
        runner._process.terminate()
        await runner._process.wait()
        assert os.getpgid(runner._process_group_guard.pid) == runner._process_group_id
    finally:
        await runner._terminate_process()


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
async def test_setsid_orphan_lost_before_a_tree_sample_is_the_documented_limit(tmp_path):
    """#608 trade-off: without reading environments no portable signal links a
    descendant that called setsid() and lost its parent between two 100 ms tree
    samples to the adapter. Teardown cannot find it and reports clean. If a
    portable signal is ever added, this test should flip to asserting recovery.
    """
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
        assert child.is_running() and child.status() != psutil.STATUS_ZOMBIE
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
async def test_private_process_group_cleans_orphan_without_session_lookup(tmp_path):
    """A same-group orphan is cleaned without inspecting the host process table."""
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
async def test_start_and_close_never_read_any_process_environment(tmp_path, monkeypatch):
    """#608: finding owned processes must not read other processes' environments."""
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
        # A same-user process born after the adapter, in its own session.
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
