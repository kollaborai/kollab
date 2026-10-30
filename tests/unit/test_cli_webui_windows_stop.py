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
    assert marker.exists()
    assert proc.poll() is not None
