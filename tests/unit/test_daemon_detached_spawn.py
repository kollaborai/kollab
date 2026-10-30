"""Detached start without fork: the relaunch command and the Windows spawn (kollab #268)."""

import subprocess

from kollabor import daemon
from kollabor.binary import kollab_argv


def test_detached_command_adds_the_flag_and_drops_daemon():
    cmd = daemon.detached_command(["kollab", "--daemon", "--agent", "coder"])
    assert cmd == [*kollab_argv(), "--detached", "--agent", "coder"]


def test_detached_command_keeps_an_existing_flag():
    cmd = daemon.detached_command(["kollab", "-d", "--agent", "coder"])
    assert cmd == [*kollab_argv(), "-d", "--agent", "coder"]


def test_spawn_detached_child_starts_a_detached_copy(monkeypatch):
    seen = {}

    class Child:
        pid = 4321

    def start_child(argv, *, detached=False, **kwargs):
        seen.update(argv=argv, detached=detached, **kwargs)
        return Child()

    from kollabor_config import proc_ctl

    monkeypatch.setattr(proc_ctl, "start_child", start_child)
    assert daemon.spawn_detached_child(["kollab", "--detached"]) == 4321
    assert seen["argv"] == [*kollab_argv(), "--detached"]
    assert seen["detached"] is True
    assert seen["env"][daemon.DETACHED_CHILD_ENV] == "1"
    assert seen["stdin"] is seen["stdout"] is seen["stderr"] is subprocess.DEVNULL
