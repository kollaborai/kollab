"""kollab --web-ui reuses only an engine of its own version and install."""

import socket
import subprocess
import sys

import pytest

from kollabor.cli import _engine_is_ours, _pick_engine_port


def mine(port):
    return True


def test_reuses_an_engine_of_this_version():
    assert _pick_engine_port("0.12.0", {7433: "0.12.0"}.get, mine) == (7433, True)


def test_starts_its_own_engine_when_none_runs():
    assert _pick_engine_port("0.12.0", {}.get, mine) == (7433, False)


def test_leaves_a_stale_engine_running_and_takes_the_next_port():
    running = {7433: "0.10.2"}
    assert _pick_engine_port("0.12.0", running.get, mine) == (7434, False)
    assert _pick_engine_port("0.12.0", {**running, 7434: "0.12.0"}.get, mine) == (7434, True)


def test_leaves_another_installs_engine_running_and_takes_the_next_port(capsys):
    # A test build of the desktop app on 7433 runs this version from its own runtime.
    assert _pick_engine_port("0.12.0", {7433: "0.12.0"}.get, lambda port: port != 7433) == (7434, False)
    assert "another kollab install" in capsys.readouterr().out


def test_gives_up_after_its_port_range():
    stale = {port: "0.10.2" for port in range(7433, 7443)}
    with pytest.raises(RuntimeError, match="7433-7442"):
        _pick_engine_port("0.12.0", stale.get, mine)


# A stand-in engine: its command line names kollabor_engine, as the real one does.
_ENGINE = (
    "import socket, time  # kollabor_engine\n"
    "s = socket.socket(); s.bind(('127.0.0.1', 0)); s.listen()\n"
    "print(s.getsockname()[1], flush=True); time.sleep(60)\n"
)


def _serve(python):
    engine = subprocess.Popen([python, "-c", _ENGINE], stdout=subprocess.PIPE, text=True)
    return engine, int(engine.stdout.readline())


def test_an_engine_on_this_interpreter_is_ours():
    engine, port = _serve(sys.executable)
    try:
        assert _engine_is_ours(port)
    finally:
        engine.kill()


def test_an_engine_on_another_interpreter_is_not(tmp_path):
    other = tmp_path / "Kollab.app" / "runtime" / "bin" / "python3"
    other.parent.mkdir(parents=True)
    other.symlink_to(sys.executable)
    engine, port = _serve(str(other))
    try:
        assert not _engine_is_ours(port)
    finally:
        engine.kill()


def test_a_port_with_no_engine_is_not_ours():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    assert not _engine_is_ours(port)
