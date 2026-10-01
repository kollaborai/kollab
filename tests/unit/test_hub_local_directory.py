"""Real local-socket/file checks for the silent workspace directory adapter."""

import json
import os
import socket
import stat
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from kollabor_config.config_utils import encode_project_path
from plugins.hub.device_names import default_device_name
from plugins.hub.local_directory import LocalAgentDirectory, LocalDirectoryError


def write_private(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(data))
    path.chmod(0o600)


@pytest.fixture
def directory(tmp_path):
    # Short real socket paths work on macOS as well as Linux.
    with tempfile.TemporaryDirectory(prefix="kld-", dir="/tmp") as root:
        config = tmp_path / "config"
        socket_dir = Path(root)
        view = LocalAgentDirectory(config, socket_dir=socket_dir)
        sockets = []

        def publish(workspace_name="one", agent_id="abc123", *, global_hub=False, **changes):
            workspace = (tmp_path / workspace_name).resolve()
            workspace.mkdir(parents=True, exist_ok=True)
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            route = socket_dir / f"{len(sockets)}.sock"
            sock.bind(str(route))
            route.chmod(0o600)
            sockets.append(sock)
            presence = (
                config / "hub" / "presence"
                if global_hub
                else (config / "projects" / encode_project_path(workspace) / "hub" / "presence")
            )
            data = {
                "agent_id": agent_id,
                "identity": "sapphire",
                "agent_name": "coder",
                "project": str(workspace),
                "pid": os.getpid(),
                "socket_path": str(route),
                "last_heartbeat": time.time(),
                "state": "ready",
                "is_coordinator": True,
                "current_task": "private task",
                "session_log": "/private/session.jsonl",
            }
            data.update(changes)
            path = presence / f"{agent_id}.json"
            write_private(path, data)
            return path, data, workspace

        yield view, publish
        for sock in sockets:
            sock.close()


def test_reads_existing_presence_across_workspaces_without_messages(directory, monkeypatch):
    view, publish = directory
    publish("one")
    publish("two")  # Same name and local ID must not collapse across workspaces.
    monkeypatch.setattr(socket.socket, "connect", lambda *_: pytest.fail("directory opened a connection"))
    rows = view.agents()
    assert len(rows) == 2
    assert len({r.global_id for r in rows}) == 2
    assert all(r.name == "sapphire" and r.is_coordinator and r.state == "ready" for r in rows)
    assert all(r.to_dict()["online"] for r in rows)
    assert {r.workspace_label for r in rows} == {"one", "two"}


def test_machine_id_is_private_stable_and_not_hostname(directory):
    view, _ = directory
    same = LocalAgentDirectory(view.config_dir, socket_dir=view.socket_dir)
    assert same.machine_id == view.machine_id
    assert len(view.machine_id) == 32
    assert stat.S_IMODE((view.config_dir / "machine-id").stat().st_mode) == 0o600
    assert sorted(p.name for p in view.config_dir.iterdir()) == ["machine-id"]


def test_machine_id_creation_is_atomic_across_readers(tmp_path):
    config = tmp_path / "concurrent"
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(lambda _: LocalAgentDirectory(config).machine_id, range(32)))
    assert len(set(ids)) == 1
    assert [p.name for p in config.iterdir()] == ["machine-id"]


def test_export_filters_explicit_workspace_and_strips_private_fields(directory):
    view, publish = directory
    _, _, workspace = publish("one")
    publish("two")
    rows = view.publishable_agents(workspace, "b" * 32)
    assert len(rows) == 1
    assert rows[0] == {
        "machine_id": view.machine_id,
        "workspace_id": "b" * 32,
        "agent_id": "abc123",
        "name": "sapphire",
        "is_coordinator": True,
        "state": "ready",
        "device": default_device_name(workspace),
    }
    assert str(workspace) not in json.dumps(rows)
    with pytest.raises(LocalDirectoryError):
        view.publishable_agents(None, "b" * 32)
    with pytest.raises(LocalDirectoryError):
        view.publishable_agents(workspace, "")


def test_export_uses_explicit_device_name_over_the_derived_default(directory):
    view, publish = directory
    _, _, workspace = publish("one")
    rows = view.publishable_agents(workspace, "b" * 32, "laptop-kollab")
    assert rows[0]["device"] == "laptop-kollab"


def test_existing_relay_workspace_identity_used_without_key_read(directory):
    import hashlib

    view, publish = directory
    _, _, workspace = publish()
    digest = hashlib.sha256(str(workspace).encode()).hexdigest()
    state = view.config_dir / "network" / digest
    write_private(state / "state.json", {"workspace_id": "d" * 32})
    (state / "device.key").symlink_to("/not-readable")
    assert view.agents()[0].workspace_id == "d" * 32


@pytest.mark.parametrize(
    "updates",
    [
        {"last_heartbeat": time.time() - 301},
        {"last_heartbeat": time.time() + 301},
        {"last_heartbeat": float("inf")},
        {"last_heartbeat": float("nan")},
        {"last_heartbeat": "today"},
        {"last_heartbeat": 10**400},
        {"pid": -1},
        {"pid": True},
        {"pid": 2**63},
        {"project": "relative/path"},
        {"project": "/path/../elsewhere"},
        {"identity": "bad\nname"},
        {"state": "dead"},
        {"state": ["ready"]},
        {"is_coordinator": "true"},
        {"socket_path": "/outside.sock"},
    ],
)
def test_unsafe_dead_stale_records_are_omitted_without_deleting(directory, updates):
    view, publish = directory
    path, _, _ = publish(**updates)
    assert view.agents() == []
    assert path.exists()


def test_dead_pid_omitted(directory, monkeypatch):
    view, publish = directory
    path, _, _ = publish()

    def dead(*_):
        raise ProcessLookupError()

    monkeypatch.setattr(os, "kill", dead)
    assert view.agents() == []
    assert path.exists()


@pytest.mark.parametrize("mode", [0o644, 0o666])
def test_nonprivate_presence_omitted(directory, mode):
    view, publish = directory
    path, _, _ = publish()
    path.chmod(mode)
    assert view.agents() == []


def test_symlink_presence_and_socket_are_not_followed(directory):
    view, publish = directory
    path, data, _ = publish()
    target = path.with_suffix(".saved")
    path.rename(target)
    path.symlink_to(target)
    assert view.agents() == []
    path.unlink()
    target.rename(path)
    route = Path(data["socket_path"])
    moved = route.with_suffix(".moved")
    route.rename(moved)
    route.symlink_to(moved)
    assert view.agents() == []


def test_symlink_runtime_directory_not_followed(directory):
    view, publish = directory
    path, _, _ = publish()
    presence = path.parent
    moved = presence.with_name("saved")
    presence.rename(moved)
    presence.symlink_to(moved, target_is_directory=True)
    assert view.agents() == []


def test_machine_identity_symlink_and_insecure_root_rejected(tmp_path):
    config = tmp_path / "config"
    config.mkdir(mode=0o700)
    (config / "machine-id").symlink_to(tmp_path / "elsewhere")
    with pytest.raises((OSError, LocalDirectoryError)):
        LocalAgentDirectory(config)
    config.chmod(0o777)
    with pytest.raises(LocalDirectoryError):
        LocalAgentDirectory(config)


def test_bounds_and_malformed_files(directory):
    view, publish = directory
    path, _, _ = publish()
    path.write_text("[1]")
    assert view.agents() == []
    path.write_text("{" + "x" * 65536)
    assert view.agents() == []
    publish("two")
    bounded = LocalAgentDirectory(view.config_dir, socket_dir=view.socket_dir, max_entries=1)
    assert len(bounded.agents()) <= 1
    assert bounded.truncated


def test_global_hub_mode_preserves_workspace_filter(directory):
    view, publish = directory
    _, _, one = publish("one", "one", global_hub=True)
    publish("two", "two", global_hub=True)
    assert len(view.agents()) == 2
    assert [r.agent_id for r in view.agents(one)] == ["one"]


def test_presence_file_cannot_claim_another_workspace(directory):
    view, publish = directory
    path, data, _ = publish()
    data["project"] = str(path.parent.resolve())
    write_private(path, data)
    assert view.agents() == []
