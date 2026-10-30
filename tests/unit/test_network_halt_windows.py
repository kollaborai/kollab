"""The Windows process readers behind the kill switch. The ``_win_*`` helpers are psutil-only, so they run on any OS."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import psutil
import pytest

from plugins.hub import network_halt as nh


class FakeProc:
    def __init__(self, info: dict):
        self.info = info


def _fake_processes(monkeypatch, rows):
    """Patch psutil.process_iter to yield (pid, ppid, start, username) rows as fake processes."""
    procs = [
        FakeProc({"pid": pid, "ppid": ppid, "create_time": start, "name": "x", "username": user})
        for pid, ppid, start, user in rows
    ]
    monkeypatch.setattr(psutil, "process_iter", lambda *_args, **_kw: iter(procs))


def _exited_pid() -> int:
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


def test_win_read_process_reads_this_process():
    me = psutil.Process()
    proc = nh._win_read_process(os.getpid())
    assert proc is not None
    assert proc.pid == os.getpid()
    assert proc.argv == me.cmdline()
    assert any(key.upper() == "PATH" for key in proc.env)
    assert proc.cwd == os.getcwd()
    assert os.path.realpath(proc.exe) == os.path.realpath(me.exe())


def test_win_read_process_exited_pid_is_none():
    assert nh._win_read_process(_exited_pid()) is None


def test_win_caller_chain_starts_at_this_process():
    chain = nh._win_caller_chain(os.getpid())
    assert chain is not None
    assert chain[0] == (os.getpid(), psutil.Process().username())
    assert chain[1][0] == os.getppid()


def test_win_caller_chain_exited_pid_is_none():
    assert nh._win_caller_chain(_exited_pid()) is None


def test_win_processes_links_this_process_to_its_parent():
    rows = nh.win_processes()
    assert rows[os.getpid()][0] == os.getppid()
    assert abs(rows[os.getpid()][1] - psutil.Process().create_time()) < 1.0


def test_win_processes_parent_started_later_reads_as_zero(monkeypatch):
    me = nh._win_owner(psutil.Process())
    _fake_processes(monkeypatch, [(100, 1, 2000.0, me), (200, 100, 1000.0, me)])
    assert nh.win_processes()[200][0] == 0


def test_win_processes_normal_link_is_kept(monkeypatch):
    me = nh._win_owner(psutil.Process())
    _fake_processes(monkeypatch, [(101, 1, 500.0, me), (300, 101, 600.0, me)])
    assert nh.win_processes()[300][0] == 101


def test_win_processes_leaves_out_other_users(monkeypatch):
    me = nh._win_owner(psutil.Process())
    _fake_processes(monkeypatch, [(400, 1, 500.0, "someone-else"), (401, 1, 500.0, me)])
    rows = nh.win_processes()
    assert 400 not in rows
    assert 401 in rows


@pytest.mark.skipif(os.name != "nt", reason="Windows process readers")
def test_windows_entry_points_read_this_process():
    assert nh.caller_chain(os.getpid())[0][0] == os.getpid()
    assert nh.read_process(os.getpid()).pid == os.getpid()
    assert nh.exe_path(os.getpid()) != ""
    assert nh.my_uid() == psutil.Process().username()
    assert nh._sid(os.getpid(), {}) == 0


@pytest.mark.skipif(os.name != "nt", reason="Windows folder and file ACLs")
def test_halt_file_round_trip_on_windows(tmp_path):
    store = nh.HaltFile(tmp_path)
    store.write({"networks": {}, "stopped": [], "marker": "kept"})
    assert store.read()["marker"] == "kept"


@pytest.mark.skipif(os.name != "nt", reason="console check is Windows only")
@pytest.mark.parametrize("stdin", [subprocess.PIPE, subprocess.DEVNULL], ids=["pipe", "nul"])
def test_stdin_is_console_false_without_a_console(stdin):
    repo = Path(__file__).resolve().parents[2]
    code = "from plugins.hub import network_halt as n; print(n._stdin_is_console())"
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=repo, stdin=stdin, capture_output=True, text=True, timeout=30
    )
    assert out.stdout.strip() == "False"
