"""`kollab --web-ui` stops on stdin EOF, and stops its children gracefully on Windows."""

import os
import subprocess
import sys
import types

import pytest

import kollabor.cli as cli

# Installs a SIGBREAK handler, reports ready, then waits. Short sleeps because a
# Python signal handler only runs between bytecodes.
CHILD = """
import signal, sys, time

marker = sys.argv[1]

def on_break(signum, frame):
    with open(marker, "w") as fh:
        fh.write("graceful")
    sys.exit(0)

signal.signal(signal.SIGBREAK, on_break)
print("ready", flush=True)
for _ in range(300):
    time.sleep(0.1)
"""


def test_stdin_eof_interrupts_main_once(monkeypatch):
    import _thread

    interrupts = []
    fake_stdin = types.SimpleNamespace(buffer=types.SimpleNamespace(read=lambda: b""))
    monkeypatch.setattr(sys, "stdin", fake_stdin)
    monkeypatch.setattr(_thread, "interrupt_main", lambda: interrupts.append(1))
    cli._stop_on_stdin_eof()
    assert len(interrupts) == 1


@pytest.mark.skipif(os.name != "nt", reason="CTRL_BREAK_EVENT is Windows only")
def test_terminate_child_stops_gracefully_on_windows(tmp_path):
    marker = tmp_path / "graceful.txt"
    proc = subprocess.Popen(
        [sys.executable, "-c", CHILD, str(marker)],
        stdout=subprocess.PIPE,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    try:
        assert proc.stdout.readline().strip() == b"ready"
        cli._terminate_child(proc)
    finally:
        proc.stdout.close()
        if proc.poll() is None:
            proc.kill()
    assert marker.read_text() == "graceful"  # the SIGBREAK handler ran: not terminate()
    assert proc.wait(timeout=5) == 0


class FakeChild:
    """A child that exits when waited on, or never does."""

    def __init__(self, pid: int, exits: bool):
        self.pid, self.exits, self.code = pid, exits, None

    def poll(self):
        return self.code

    def wait(self, timeout=None):
        if not self.exits:
            raise subprocess.TimeoutExpired("child", timeout)
        self.code = 0
        return 0


def test_children_get_one_break_each_and_only_a_stuck_one_is_ended(monkeypatch):
    import signal

    from kollabor_config import proc_ctl

    breaks, ended = [], []
    monkeypatch.setattr(signal, "CTRL_BREAK_EVENT", 1, raising=False)
    monkeypatch.setattr(cli.os, "kill", lambda pid, sig: breaks.append((pid, sig)))
    monkeypatch.setattr(proc_ctl, "kill_tree", lambda pid, sig: ended.append(pid))
    webui, engine = FakeChild(11, exits=True), FakeChild(22, exits=False)
    cli._stop_children_nt([webui, engine, None], timeout=0.1)
    assert breaks == [(11, 1), (22, 1)]  # a second CTRL_BREAK would make uvicorn skip its shutdown
    assert ended == [22]
