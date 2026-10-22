"""Persistence records survive engine-process death without storing secrets."""

from __future__ import annotations

import asyncio
import json
import os
import select
import signal
import stat
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest
from kollabor_engine.handover import HandoverManifestError, HandoverStore
from kollabor_engine.hub_bridge import HubBridge


def _sleeping_process() -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _spawn_manifest_owner(
    config_dir: Path,
    workspace: Path,
    socket_path: Path,
    marker_path: Path,
    port: int,
) -> tuple[subprocess.Popen, int]:
    """Run the real store API in a throwaway owner and socket-serving daemon."""
    agent_program = r"""
import asyncio
import json
import sys
from pathlib import Path

async def handle(reader, writer):
    try:
        attach = await reader.readline()
        if attach:
            writer.write(b'{"type":"attach_ack"}\n')
            await writer.drain()
            detach = await reader.readline()
            if detach and json.loads(detach).get("type") == "detach":
                Path(sys.argv[2]).touch()
    finally:
        writer.close()
        await writer.wait_closed()

async def main():
    server = await asyncio.start_unix_server(handle, path=sys.argv[1])
    print("READY", flush=True)
    async with server:
        await server.serve_forever()

asyncio.run(main())
"""
    owner_program = r"""
import json
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from kollabor_engine.handover import HandoverStore
from kollabor_engine.hub_bridge import HubBridge
HubBridge.get_agent_by_identity = lambda *args, **kwargs: None
port = int(sys.argv[1])
config_dir = Path(sys.argv[2])
workspace = Path(sys.argv[3])
socket_path = sys.argv[4]
agent_program = sys.argv[5]
marker_path = sys.argv[6]
store = HandoverStore(port, config_dir=config_dir)
store.acquire()
daemon = subprocess.Popen(
    [sys.executable, "-c", agent_program, socket_path, marker_path],
    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    text=True, start_new_session=True,
)
assert daemon.stdout is not None and daemon.stdout.readline().strip() == "READY"
session = SimpleNamespace(
    session_id="web-session-1", requested_identity="lapis", agent="default",
    workspace=str(workspace), created_at=datetime.now(timezone.utc),
    conversation_id="conversation-abc", display_name="Work agent",
    profile=SimpleNamespace(name="anthropic", provider="anthropic", model="claude-test"),
    daemon=SimpleNamespace(
        pid=daemon.pid, identity="lapis", agent_name="default",
        project=str(workspace), socket_path=socket_path,
    ),
)
store.register_session(session)
print(json.dumps({"daemon_pid": daemon.pid}), flush=True)
signal.pause()
"""
    owner = subprocess.Popen(
        [
            sys.executable,
            "-c",
            owner_program,
            str(port),
            str(config_dir),
            str(workspace),
            str(socket_path),
            agent_program,
            str(marker_path),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        start_new_session=True,
    )
    assert owner.stdout is not None
    ready, _, _ = select.select([owner.stdout], [], [], 10)
    if not ready:
        owner.kill()
        owner.wait(timeout=3)
        raise AssertionError("fake engine owner did not register the daemon")
    record = json.loads(owner.stdout.readline())
    owner.stdout.close()
    return owner, int(record["daemon_pid"])



def _session(tmp_path: Path, process: subprocess.Popen, socket_path: Path):
    return SimpleNamespace(
        session_id="web-session-1",
        requested_identity="lapis",
        agent="default",
        workspace=str(tmp_path),
        created_at=datetime.now(timezone.utc),
        conversation_id="conversation-abc",
        display_name="Work agent",
        profile=SimpleNamespace(
            name="anthropic",
            provider="anthropic",
            model="claude-test",
            api_key="must-never-be-persisted",
            base_url="https://user:password@example.test?token=secret",
        ),
        daemon=SimpleNamespace(
            pid=process.pid,
            identity="lapis",
            agent_name="default",
            project=str(tmp_path),
            socket_path=str(socket_path),
        ),
    )


def _stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def _disable_presence_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(HubBridge, "get_agent_by_identity", lambda *args, **kwargs: None)


def test_manifest_is_port_scoped_private_and_excludes_profile_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _disable_presence_lookup(monkeypatch)
    daemon = _sleeping_process()
    socket_path = tmp_path / "agent.sock"
    socket_path.touch()
    store = HandoverStore(7433, config_dir=tmp_path / "config")
    other_port = HandoverStore(7434, config_dir=tmp_path / "config")
    try:
        assert store.path != other_port.path
        assert store.acquire() == []
        store.register_session(_session(tmp_path, daemon, socket_path))
        assert store.prepare_handover() == 1

        payload = store.path.read_text(encoding="utf-8")
        saved = json.loads(payload)
        row = saved["sessions"][0]
        assert saved["port"] == 7433
        assert saved["owner"]["source"] == "webui"
        assert row["source"] == "webui"
        assert row["daemon_pid"] == daemon.pid
        assert row["identity"] == "lapis"
        assert row["conversation_id"] == "conversation-abc"
        assert row["profile"] == {
            "name": "anthropic",
            "provider": "anthropic",
            "model": "claude-test",
        }
        assert "must-never-be-persisted" not in payload
        assert "password" not in payload
        if os.name != "nt":
            assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
            assert stat.S_IMODE(store.path.parent.stat().st_mode) == 0o700
    finally:
        store.close()
        _stop_process(daemon)


def test_second_live_engine_cannot_claim_same_port_manifest(
    tmp_path: Path,
) -> None:
    first = HandoverStore(7433, config_dir=tmp_path)
    second = HandoverStore(7433, config_dir=tmp_path)
    first.acquire()
    try:
        with pytest.raises(HandoverManifestError, match="already owns"):
            second.acquire()
    finally:
        first.close()
        second.close()


@pytest.mark.parametrize(
    "shutdown_signal",
    [signal.SIGINT, signal.SIGTERM, signal.SIGKILL],
    ids=["ctrl-c-sigint", "sigterm", "sigkill"],
)
@pytest.mark.asyncio
async def test_same_daemon_survives_engine_signal_and_is_reowned(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    shutdown_signal: int,
) -> None:
    """A real child PID survives owner signals and is adopted by a new lifespan."""
    if os.name != "posix":
        pytest.skip("signal-based process ownership test requires POSIX")
    from kollabor_engine import server
    from kollabor_engine.daemon_pool import DaemonPool
    from kollabor_engine.session import EngineSession

    _disable_presence_lookup(monkeypatch)
    monkeypatch.setattr(HubBridge, "discover_sessions", lambda self: [])
    monkeypatch.setattr(server, "_sessions", {})
    pool = DaemonPool()
    monkeypatch.setattr("kollabor_engine.session.get_daemon_pool", lambda: pool)

    async def no_op(self):
        return None

    monkeypatch.setattr(EngineSession, "_read_agent_facts", no_op)
    monkeypatch.setattr(EngineSession, "_track_events", no_op)
    monkeypatch.setattr(EngineSession, "_schedule_refresh", lambda self: None)

    socket_path = Path("/tmp") / f"k281-{os.getpid()}-{uuid.uuid4().hex[:8]}.sock"
    marker_path = tmp_path / "daemon-detached.txt"

    owner = None
    daemon_pid = 0
    second = None
    try:
        owner, daemon_pid = _spawn_manifest_owner(
            tmp_path / "config", tmp_path, socket_path, marker_path, 7433
        )
        owner_pid = owner.pid
        os.kill(owner_pid, shutdown_signal)
        owner.wait(timeout=5)
        manifest = json.loads(
            (tmp_path / "config" / "engine" / "webui-7433.json").read_text(
                encoding="utf-8"
            )
        )
        assert manifest["owner"]["shutdown"] is False

        # The operating system released the old engine's lock without a
        # graceful marker; the next engine can only reown after PID validation.
        second = HandoverStore(7433, config_dir=tmp_path / "config")
        app = SimpleNamespace(state=SimpleNamespace(handover_store=second))
        async with server._engine_lifespan(app):
            adopted = server._sessions["web-session-1"]
            assert adopted.webui_owned is True
            assert adopted.external is False
            assert adopted.daemon.pid == daemon_pid
            assert adopted.daemon.owned is True
            assert adopted.conversation_id == "conversation-abc"

            restored_sidebar_row = adopted.to_dict()
            assert restored_sidebar_row["session_id"] == "web-session-1"
            assert restored_sidebar_row["conversation_id"] == "conversation-abc"
            assert psutil.Process(daemon_pid).is_running()

        deadline = asyncio.get_running_loop().time() + 2
        while not marker_path.exists() and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.05)
        assert marker_path.exists()

        # Ordinary engine shutdown detached the reowned handle; the process is
        # still available for an explicit WebUI Delete or /quit.
        assert psutil.Process(daemon_pid).is_running()
    finally:
        if owner is not None and owner.poll() is None:
            owner.kill()
            owner.wait(timeout=3)
        if second is not None:
            second.close()
        socket_path.unlink(missing_ok=True)
        if daemon_pid:
            _stop_pid(daemon_pid)


def test_manifest_rejects_mismatched_discovered_pid_and_purges_dead_daemon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _disable_presence_lookup(monkeypatch)
    daemon = _sleeping_process()
    socket_path = tmp_path / "agent.sock"
    socket_path.touch()
    store = HandoverStore(7433, config_dir=tmp_path / "config")
    try:
        store.acquire()
        store.register_session(_session(tmp_path, daemon, socket_path))
        wrong = {
            "session_id": "web-session-1",
            "daemon_pid": daemon.pid + 1,
            "identity": "lapis",
            "workspace": str(tmp_path),
            "socket_path": str(socket_path),
        }
        assert store.reown_rows([wrong]) == []
        _stop_process(daemon)
        assert store.reown_rows([]) == []

        assert store.prune_dead_sessions() == 1
    finally:
        store.close()
        _stop_process(daemon)


def test_graceful_lifespan_can_restart_in_same_process(tmp_path: Path) -> None:
    first = HandoverStore(7433, config_dir=tmp_path)
    first.acquire()
    first.close()

    second = HandoverStore(7433, config_dir=tmp_path)
    try:
        assert second.acquire() == []
    finally:
        second.close()


def test_corrupt_manifest_is_preserved_and_startup_fails_closed(tmp_path: Path) -> None:
    store = HandoverStore(7433, config_dir=tmp_path)
    store.path.parent.mkdir(mode=0o700, parents=True)
    original = "{not valid json"
    store.path.write_text(original, encoding="utf-8")

    with pytest.raises(HandoverManifestError, match="cannot read handover manifest"):
        store.acquire()

    assert store.path.read_text(encoding="utf-8") == original
    store.close()


def _stop_pid(pid: int) -> None:
    try:
        process = psutil.Process(pid)
        process.terminate()
        process.wait(timeout=3)
    except psutil.NoSuchProcess:
        return
    except psutil.TimeoutExpired:
        try:
            process.kill()
            process.wait(timeout=3)
        except psutil.NoSuchProcess:
            pass


def test_remove_session_write_failure_restores_in_memory_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = HandoverStore(7433, config_dir=tmp_path / "config")
    store.acquire()
    row = {"session_id": "web-session-retry", "daemon_pid": 4321}
    store._data["sessions"] = [row]

    def fail_write() -> None:
        raise OSError("simulated manifest fsync failure")

    monkeypatch.setattr(store, "_write", fail_write)
    try:
        with pytest.raises(OSError, match="fsync"):
            store.remove_session("web-session-retry")
        assert store._data["sessions"] == [row]
    finally:
        store.close()
