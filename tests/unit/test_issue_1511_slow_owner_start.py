"""#1511: the detached ACP child test survives a driver that is slow to start."""

import os
import subprocess

import pytest

import tests.unit.test_owned_processes as owned_processes

pytestmark = pytest.mark.skipif(os.name == "nt", reason="requires POSIX process groups")

# Longer than the old 8 s wait, well inside the driver's own 45 s alarm.
STARTUP_DELAY_S = 9


def test_child_wait_outlasts_a_slow_driver_start(tmp_path, monkeypatch):
    real_popen = subprocess.Popen

    def slow_driver(args, *rest, **kwargs):
        if isinstance(args, list) and len(args) > 2 and args[1] == "-c" and "FAST_ORPHAN_ADAPTER" in args[2]:
            args = [args[0], "-c", f"import time; time.sleep({STARTUP_DELAY_S})\n" + args[2], *args[3:]]
        return real_popen(args, *rest, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", slow_driver)
    owned_processes.test_detached_acp_child_exits_after_test_owner_is_killed(tmp_path, "kill_owner")
