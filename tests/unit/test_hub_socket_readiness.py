"""Advertised hub sockets must answer a handshake before presence exposes them (#692).

A bound listener is not a serving endpoint: under a busy startup loop the
first local IPC handshake can land seconds after the socket file exists
(windows-smoke ConnectionRefused at an already-bound path, #692). Two
surfaces carry the readiness contract:

- ``plugins.hub.presence.wait_until_socket_answers``: the hub plugin gates
  its presence publish on the endpoint actually answering.
- ``scripts/windows_relay_halt_smoke.py`` ``folder_socket``: the CI harness
  must not release its presence wait on a record whose socket never answers.
"""

import asyncio
import importlib.util
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.hub import plugin as hub_plugin
from plugins.hub import presence
from plugins.hub.plugin import HubPlugin

pytestmark = pytest.mark.skipif(
    os.name == "nt",
    reason="readiness probes here use AF_UNIX listeners; the nt contract is exercised by the windows smoke itself",
)

SMOKE = Path(__file__).resolve().parents[2] / "scripts" / "windows_relay_halt_smoke.py"


def _load_smoke():
    spec = importlib.util.spec_from_file_location(
        "windows_relay_halt_smoke_under_test", SMOKE
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_presence(home: Path, project: Path, socket_path: str) -> None:
    record_dir = home / ".kollab" / "hub" / "presence"
    record_dir.mkdir(parents=True, exist_ok=True)
    (record_dir / "agent-b.json").write_text(
        json.dumps(
            {
                "identity": "agent-b",
                "pid": os.getpid(),
                "project": str(project),
                "socket_path": socket_path,
            }
        )
    )


@pytest.mark.asyncio
async def test_wait_until_socket_answers_true_for_live_listener():
    endpoint = Path(tempfile.mkdtemp(prefix="ksr-")) / "s.sock"  # short path: AF_UNIX limit

    async def _accept(reader, writer):
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_unix_server(_accept, path=str(endpoint))
    async with server:
        answered = await presence.wait_until_socket_answers(
            str(endpoint), timeout=2.0, interval=0.05
        )
    assert answered


@pytest.mark.asyncio
async def test_wait_until_socket_answers_false_when_nothing_listens(tmp_path):
    dead = tmp_path / "dead.sock"
    dead.write_text("regular file, no listener")
    started = time.monotonic()
    answered = await presence.wait_until_socket_answers(
        str(dead), timeout=0.6, interval=0.2
    )
    assert not answered
    assert time.monotonic() - started < 5


def test_smoke_folder_socket_ignores_record_with_dead_socket(tmp_path):
    smoke = _load_smoke()
    home = tmp_path / "win-b"
    project = tmp_path / "project"
    project.mkdir()
    dead = home / "hub"
    dead.mkdir(parents=True)
    dead = dead / "dead.sock"
    dead.write_text("")
    _write_presence(home, project, str(dead))
    assert smoke.folder_socket(home, project) is None


@pytest.mark.asyncio
async def test_smoke_folder_socket_returns_live_socket(tmp_path):
    smoke = _load_smoke()
    home = tmp_path / "win-b"
    project = tmp_path / "project"
    project.mkdir()
    live = Path(tempfile.mkdtemp(prefix="ksr-")) / "s.sock"  # short path: AF_UNIX limit

    async def _accept(reader, writer):
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_unix_server(_accept, path=str(live))
    async with server:
        _write_presence(home, project, str(live))
        assert smoke.folder_socket(home, project) == str(live)

@pytest.mark.asyncio
async def test_wait_probe_does_not_starve_the_answering_loop():
    """nt-shaped regression (#692): the probe must not block the loop it waits on.

    The local_ipc server that answers the handshake runs on the SAME event
    loop as the caller of wait_until_socket_answers. On Windows the probe
    is a full HMAC handshake: connect succeeds at kernel level against the
    listen backlog, but the ANSWER only exists after the server loop runs,
    so a probe that blocks the loop thread (connect_blocking's 1.0s recv)
    can never be answered -- measured 1.00s unanswered loop-thread vs 0.00s
    worker-thread. The wait loop must probe without starving the loop.
    """
    endpoint = Path(tempfile.mkdtemp(prefix="ksr-")) / "s.sock"  # short path: AF_UNIX limit
    handled = asyncio.Event()

    async def _accept(reader, writer):
        handled.set()
        try:
            writer.write(b"ok")
            await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            pass
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_unix_server(_accept, path=str(endpoint))

    import socket as sock_mod

    def nt_shaped_probe(path: str) -> bool:
        # connect_blocking's nt contract: kernel-level connect (no loop
        # needed), then a blocking recv for the handshake answer (needs
        # the server loop to run).
        sock = sock_mod.socket(sock_mod.AF_UNIX, sock_mod.SOCK_STREAM)
        sock.settimeout(1.0)
        try:
            sock.connect(path)
            return sock.recv(4) == b"ok"
        except OSError:
            return False
        finally:
            sock.close()

    try:
        started = time.monotonic()
        with patch.object(presence.PresenceManager, "_socket_responds", nt_shaped_probe):
            answered = await presence.wait_until_socket_answers(
                str(endpoint), timeout=3.0, interval=0.05
            )
    finally:
        server.close()
        await server.wait_closed()
    assert answered is True
    assert handled.is_set()
    assert time.monotonic() - started < 2.0


@pytest.mark.asyncio
async def test_advertise_publishes_degraded_with_error_when_socket_never_answers(caplog):
    hub = HubPlugin(event_bus=MagicMock())
    hub._presence = MagicMock()
    with patch.object(
        hub_plugin, "wait_until_socket_answers", new=AsyncMock(return_value=False)
    ):
        with caplog.at_level(logging.ERROR, logger="plugins.hub.plugin"):
            await hub._advertise_presence_when_socket_answers("/no/such/endpoint")
    hub._presence.publish.assert_called_once()
    assert any(
        "never answered a handshake" in rec.message and rec.levelno == logging.ERROR
        for rec in caplog.records
    )


@pytest.mark.asyncio
async def test_advertise_publishes_quietly_when_socket_answers(caplog):
    hub = HubPlugin(event_bus=MagicMock())
    hub._presence = MagicMock()
    with patch.object(
        hub_plugin, "wait_until_socket_answers", new=AsyncMock(return_value=True)
    ):
        with caplog.at_level(logging.ERROR, logger="plugins.hub.plugin"):
            await hub._advertise_presence_when_socket_answers("/live/endpoint")
    hub._presence.publish.assert_called_once()
    assert not [r for r in caplog.records if "never answered a handshake" in r.message]
