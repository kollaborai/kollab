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
import os
import tempfile
import time
from pathlib import Path

import pytest

from plugins.hub import presence

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
