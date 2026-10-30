"""_group_alive must probe a pid without signalling it (on Windows os.kill(pid, 0) terminates the process)."""

import subprocess
import sys

from kollabor_agent.shell_executor import _group_alive


def test_group_alive_probes_a_child_without_killing_it():
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert _group_alive(p.pid) is True
        assert p.poll() is None  # the probe must not have terminated the child
        p.kill()
        p.wait(timeout=10)
        assert _group_alive(p.pid) is False
    finally:
        if p.poll() is None:
            p.kill()
