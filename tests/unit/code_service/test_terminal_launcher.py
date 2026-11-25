"""Probe only the launcher's own process; no host process discovery is needed."""

import json
import subprocess
import sys

import pytest

from kollabor.code_service import terminal

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Unix PTY launcher")


@pytest.mark.parametrize("limits", [(64, 256), (64, 512), (512, 512)])
@pytest.mark.parametrize("contract", ["descriptors", "limits"])
def test_darwin_launcher_preserves_descriptor_contract(limits, contract):
    import resource

    hard = resource.getrlimit(resource.RLIMIT_NOFILE)[1]
    if hard != resource.RLIM_INFINITY and hard < limits[1]:
        pytest.skip("the inherited hard limit cannot accommodate this probe")

    # Set limits only in the throwaway child. Exercise the Darwin branch on any Unix CI runner.
    # The existing real-PTY tests cover login_tty; this probe isolates descriptor policy from terminal setup.
    launcher = (
        "import os, resource, sys\n"
        "os.login_tty = lambda fd: None\n"
        f"resource.setrlimit(resource.RLIMIT_NOFILE, {limits!r})\n"
        "sys.platform = 'darwin'\n"
        + terminal._SHELL_LAUNCHER
    )
    observer = (
        "import json, os, resource\n"
        "fds = []\n"
        "for name in os.listdir('/dev/fd'):\n"
        "    fd = int(name)\n"
        "    try: os.fstat(fd)\n"
        "    except OSError: continue\n"
        "    fds.append(fd)\n"
        "data = {'descriptors': sorted(fds),\n"
        "        'limits': resource.getrlimit(resource.RLIMIT_NOFILE)}\n"
        "print(json.dumps(data))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-c", launcher, sys.executable, "-I", "-c", observer],
        input="", capture_output=True, text=True, close_fds=True, timeout=5, check=True,
    )
    data = json.loads(completed.stdout)
    expected = [0, 1, 2] if contract == "descriptors" else list(limits)
    assert data[contract] == expected
