"""Stopping a wrapper also stops stubborn members of its child process group."""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import psutil


def test_stop_kills_group_after_leader_exits(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    from kollabor_config import file_lock
    from kollabor_plugins import services
    from kollabor_plugins.packages import files_sha256

    package_paths = [Path(module.__file__).resolve().parent.parent for module in (services, file_lock)]
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(map(str, package_paths)))
    home = tmp_path / ".kollab"
    root = home / "plugins" / "hello"
    root.mkdir(parents=True)
    child_pid_file = tmp_path / "grandchild.pid"
    grandchild_code = "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"
    code = (
        "import subprocess,sys,time; "
        f"p=subprocess.Popen([sys.executable,'-c',{grandchild_code!r}], "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
        f"open({str(child_pid_file)!r},'w').write(str(p.pid)); "
        "time.sleep(60)"
    )
    (root / "kollab-plugin.json").write_text(
        json.dumps({"services": {"worker": {"command": [sys.executable, "-c", code], "stop_timeout_s": 0.3}}})
    )
    (home / "plugins.json").write_text(
        json.dumps(
            {
                "plugins": {
                    "hello": {
                        "enabled": True,
                        "enabled_path": "/usr/bin:/bin",
                        "granted": [],
                        "files_sha256": files_sha256(root),
                    }
                },
                "dev": {},
            }
        )
    )
    wrapper = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from kollabor_plugins.services import run_service; run_service('hello','worker','abcd')",
            "abcd",
        ]
    )
    grandchild = None
    try:
        deadline = time.monotonic() + 5
        while not child_pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert child_pid_file.exists()
        grandchild = psutil.Process(int(child_pid_file.read_text()))
        time.sleep(0.15)
        wrapper.send_signal(signal.SIGTERM)
        wrapper.wait(timeout=4)
        deadline = time.monotonic() + 2
        while grandchild.is_running() and grandchild.status() != psutil.STATUS_ZOMBIE and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not grandchild.is_running() or grandchild.status() == psutil.STATUS_ZOMBIE
    finally:
        if wrapper.poll() is None:
            wrapper.kill()
            wrapper.wait(timeout=3)
        if grandchild and grandchild.is_running():
            try:
                os.kill(grandchild.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
