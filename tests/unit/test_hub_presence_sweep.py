"""The coordinator's presence sweep keeps live agents' presence files and removes dead ones (#268).

On Windows the sweep used ``os.kill(pid, 0)``, which sends Ctrl+C there, so it deleted live agents' files.
"""

import asyncio
import json
import os
import subprocess
import sys
from types import SimpleNamespace

from kollabor_config import config_utils
from plugins.hub import plugin as hub_plugin


def _presence(path, pid):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"identity": path.stem, "pid": pid}))
    return path


def test_sweep_removes_only_dead_agents(tmp_path, monkeypatch):
    exited = subprocess.Popen([sys.executable, "-c", "pass"])
    exited.wait()  # reaped: its pid names no process now
    project = tmp_path / "projects" / "p" / "hub" / "presence"
    legacy = tmp_path / "hub" / "presence"
    live = [_presence(project / "lapis.json", os.getpid()), _presence(legacy / "ruby.json", os.getpid())]
    no_pid = _presence(project / "unknown.json", 0)  # the sweep cannot tell, so it keeps the file
    dead = [_presence(project / "peridot.json", exited.pid), _presence(legacy / "pearl.json", exited.pid)]
    passes = []

    async def one_pass(seconds):
        passes.append(seconds)
        if len(passes) > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr(config_utils, "get_config_directory", lambda: tmp_path)
    monkeypatch.setattr(hub_plugin, "asyncio", SimpleNamespace(sleep=one_pass, CancelledError=asyncio.CancelledError))
    asyncio.run(hub_plugin.HubPlugin._presence_sweep_loop(SimpleNamespace(_task_ledger=None)))

    assert len(passes) == 2  # one full pass ran
    assert all(path.exists() for path in [*live, no_pid])
    assert not any(path.exists() for path in dead)
