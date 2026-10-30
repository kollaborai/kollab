"""The Windows branches of the kill switch's runtime (plugins/hub/halt_runtime.py).

The pretend-Windows tests patch ``os.name`` only for the call they test. The Windows-only tests start a real
status socket, lock and ping, so they run on a Windows host and are skipped everywhere else.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys

import pytest

from kollabor_config import local_ipc
from plugins.hub import halt_runtime as hr
from plugins.hub import network_halt as nh

windows_only = pytest.mark.skipif(os.name != "nt", reason="Windows only: real status socket, lock and ping")


def test_group_kill_on_windows_ends_the_tree(monkeypatch):
    calls = []
    monkeypatch.setattr(hr.proc_ctl, "kill_tree", lambda pid, sig, **kw: calls.append((pid, sig, kw)))
    monkeypatch.setattr(hr.os, "name", "nt")
    kill = hr.group_kill({1234})
    kill(777, 15)
    assert calls == [(777, hr.proc_ctl.HARD, {"protect": {1234}})]


def test_alive_on_windows_asks_proc_ctl(monkeypatch):
    asked = []
    monkeypatch.setattr(hr.proc_ctl, "pid_alive", lambda pid: asked.append(pid) or True)
    monkeypatch.setattr(hr.os, "name", "nt")
    assert hr._alive(4242) is True
    assert asked == [4242]


@pytest.mark.parametrize("alive", [True, False])
def test_running_on_windows_follows_alive(monkeypatch, alive):
    monkeypatch.setattr(hr, "_alive", lambda pid: alive)
    monkeypatch.setattr(hr.os, "name", "nt")
    assert hr._running(4242) is alive


def test_process_tree_on_windows_is_built_from_process_table(monkeypatch):
    monkeypatch.setattr(hr, "process_table", lambda: {5: 1, 6: 5})
    monkeypatch.setattr(hr.os, "name", "nt")
    assert hr.process_tree() == {5: (1, 0, 0), 6: (5, 0, 0)}


def test_process_info_on_windows_comes_from_win_processes(monkeypatch):
    monkeypatch.setattr(nh, "win_processes", lambda: {5: (1, 100.0, "kollab.exe")}, raising=False)
    monkeypatch.setattr(hr.os, "name", "nt")
    assert hr.process_info() == {5: (100.0, "kollab.exe", "")}


def test_process_table_on_windows_comes_from_win_processes_without_us(monkeypatch):
    rows = {5: (1, 100.0, "a.exe"), 6: (5, 101.0, "b.exe"), os.getpid(): (1, 1.0, "pytest.exe")}
    monkeypatch.setattr(nh, "win_processes", lambda: rows, raising=False)
    monkeypatch.setattr(hr.os, "name", "nt")
    assert hr.process_table() == {5: 1, 6: 5}


@windows_only
def test_status_socket_answers_over_local_ipc(tmp_path):
    server = hr._StatusSocket(tmp_path / "w.sock", "lapis", tmp_path)
    try:
        with local_ipc.connect_blocking(tmp_path / "w.sock", 5) as sock:
            sock.sendall(json.dumps({"action": "get_status"}).encode() + b"\n")
            reply = json.loads(sock.makefile("rb").readline())
    finally:
        server.stop()
    assert reply["type"] == "status"
    assert reply["identity"] == "lapis"


@windows_only
def test_halt_listener_takes_lock(tmp_path):
    listener = hr.HaltListener(root=tmp_path)
    assert listener.take_lock() is True
    try:
        assert (hr.private_dir(tmp_path) / hr.LOCK).read_text().strip() == str(os.getpid())
        assert hr.listener_running(tmp_path) is True
    finally:
        listener.drop_lock()


@windows_only
def test_listener_serve_answers_ping(tmp_path):
    async def run() -> dict:
        listener = hr.HaltListener(root=tmp_path)
        await listener._serve()
        try:
            return await hr.sock_call({"op": "ping"}, tmp_path)
        finally:
            listener._server.close()
            await listener._server.wait_closed()

    assert asyncio.run(run()) == {"ok": True}


@windows_only
def test_alive_and_running_on_windows_host():
    assert hr._alive(os.getpid()) is True
    assert hr._running(os.getpid()) is True
    exited = subprocess.Popen([sys.executable, "-c", "pass"])
    exited.wait()
    assert hr._alive(exited.pid) is False
    assert os.getppid() in hr.process_table()


@windows_only
def test_caller_refusal_passes_pytest(monkeypatch):
    monkeypatch.delenv("KOLLAB_TOOL_SHELL", raising=False)
    assert hr.caller_refusal(os.getpid()) == ""
