"""The coordinator's presence sweep keeps live agents' presence files and removes dead ones (#268).

On Windows the sweep used ``os.kill(pid, 0)``, which sends Ctrl+C there, so it deleted live agents' files.
"""

import asyncio
import json
import os
from types import SimpleNamespace

from kollabor_config import config_utils
from kollabor_config.hub_paths import hub_project_socket_key
from plugins.hub import plugin as hub_plugin
from plugins.hub import presence


def _presence(path, pid):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"identity": path.stem, "pid": pid}))
    return path


def test_sweep_removes_only_dead_agents(tmp_path, monkeypatch):
    alive_pid = os.getpid()
    dead_pid = alive_pid + 1
    real_pid_alive = hub_plugin.pid_alive
    monkeypatch.setattr(
        hub_plugin,
        "pid_alive",
        lambda pid: pid == alive_pid and real_pid_alive(pid),
    )

    project = tmp_path / "projects" / "p" / "hub" / "presence"
    legacy = tmp_path / "hub" / "presence"
    live = [
        _presence(project / "lapis.json", alive_pid),
        _presence(legacy / "ruby.json", alive_pid),
    ]
    no_pid = _presence(project / "unknown.json", 0)  # the sweep cannot tell, so it keeps the file
    dead = [
        _presence(project / "peridot.json", dead_pid),
        _presence(legacy / "pearl.json", dead_pid),
    ]

    shared_live = []
    shared_dead = []
    if os.name != "nt":
        socket_root = tmp_path / "socket-root"
        socket_root.mkdir(mode=0o700)
        workspace = tmp_path / "shared-workspace"
        workspace.mkdir()
        shared = socket_root / hub_project_socket_key(workspace)
        shared.mkdir(mode=0o700)
        shared_live = [_presence(shared / "shared-live.json", alive_pid)]
        shared_dead = [_presence(shared / "shared-dead.json", dead_pid)]
        monkeypatch.setattr(presence, "SOCKET_DIR_ROOT", socket_root)

    passes = []

    async def one_pass(seconds):
        passes.append(seconds)
        if len(passes) > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr(config_utils, "get_config_directory", lambda: tmp_path)
    monkeypatch.setattr(
        hub_plugin,
        "asyncio",
        SimpleNamespace(sleep=one_pass, CancelledError=asyncio.CancelledError),
    )
    asyncio.run(hub_plugin.HubPlugin._presence_sweep_loop(SimpleNamespace(_task_ledger=None)))

    assert len(passes) == 2  # one full pass ran
    assert all(path.exists() for path in [*live, no_pid, *shared_live])
    assert not any(path.exists() for path in [*dead, *shared_dead])
