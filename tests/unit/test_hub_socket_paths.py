"""Platform-specific Hub discovery and local IPC endpoint semantics."""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from kollabor_engine import hub_bridge

from kollabor_config import hub_paths, local_ipc


@pytest.mark.parametrize("platform", ["nt", "posix"])
def test_socket_root_uses_the_platform(monkeypatch, tmp_path, platform):
    profile = tmp_path / "profile [test]"
    monkeypatch.setattr(hub_paths, "os", SimpleNamespace(name=platform))
    monkeypatch.setattr(Path, "home", lambda: profile)

    expected = (
        profile / ".kollab" / "hub-sockets"
        if platform == "nt"
        else Path("/tmp/kollabor-hub")
    )
    assert hub_paths.hub_socket_root() == expected
    assert not profile.exists()


def test_writer_and_engine_share_the_platform_root():
    # A fresh process is not affected by the unit suite's isolated Hub fixture.
    code = (
        "from plugins.hub.presence import SOCKET_DIR_ROOT; "
        "from kollabor_config.hub_paths import hub_socket_root; "
        "assert SOCKET_DIR_ROOT == hub_socket_root()"
    )
    subprocess.run([sys.executable, "-c", code], check=True, timeout=30)


@pytest.mark.parametrize("nested", [False, True])
def test_bridge_fallback_finds_literal_identity_under_shared_root(
    monkeypatch, tmp_path, nested
):
    root = tmp_path / "profile [test]" / "hub-sockets"
    directory = root / "project-hash" if nested else root
    directory.mkdir(parents=True)
    endpoint = directory / "ruby[1].sock"
    endpoint.touch()
    monkeypatch.setattr(hub_bridge, "hub_socket_root", lambda: root)

    bridge = hub_bridge.HubBridge()
    agent = {"identity": "ruby[1]", "agent_id": "ruby-id"}
    monkeypatch.setattr(bridge, "get_agents", lambda use_cache=True: [agent])
    assert bridge._socket_path_for_agent(agent) == endpoint
    assert bridge.discover_sessions(use_cache=False)[0]["socket_path"] == str(endpoint)


def test_presence_path_takes_precedence_and_missing_root_is_not_created(
    monkeypatch, tmp_path
):
    root = tmp_path / "absent"
    monkeypatch.setattr(hub_bridge, "hub_socket_root", lambda: root)
    bridge = hub_bridge.HubBridge()
    explicit = tmp_path / "published.sock"

    assert bridge._socket_path_for_agent({"identity": "ruby", "socket_path": str(explicit)}) == explicit
    assert bridge._socket_path_for_agent({"identity": "ruby"}) is None
    assert bridge._socket_path_for_agent({}) is None
    assert not root.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX Unix socket check")
def test_posix_endpoint_check_requires_a_unix_socket():
    with tempfile.TemporaryDirectory(prefix="kh-", dir="/tmp") as folder:
        endpoint = Path(folder) / "ruby.sock"
        endpoint.touch()
        assert not local_ipc.is_local_endpoint(endpoint)
        endpoint.unlink()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(endpoint))
            assert local_ipc.is_local_endpoint(endpoint)


@pytest.mark.skipif(os.name != "nt", reason="Windows endpoint file and ACL")
@pytest.mark.asyncio
async def test_windows_presence_discovers_and_connects_to_real_local_ipc(
    monkeypatch, tmp_path
):
    from kollabor_agent.runtime import AgentRuntime
    from plugins.hub import presence, project_scope

    project = tmp_path / "project"
    project.mkdir()
    config = tmp_path / ".kollab"
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("KOLLAB_CONFIG_DIR", str(config))
    monkeypatch.setenv("KOLLAB_PROJECT_ROOT", str(project))
    monkeypatch.setenv("KOLLAB_HUB_PROJECT_SCOPED", "1")
    monkeypatch.chdir(project)
    monkeypatch.setattr(presence, "SOCKET_DIR_ROOT", hub_paths.hub_socket_root())
    project_scope.resolve_project_root.cache_clear()
    endpoint = presence.get_socket_dir() / "ruby.sock"

    async def reply(reader, writer):
        request = json.loads(await reader.readline())
        writer.write(json.dumps({"type": "pong", "action": request["action"]}).encode() + b"\n")
        await writer.drain()
        writer.close()

    server = await local_ipc.serve(reply, endpoint)
    try:
        agent = AgentRuntime(
            identity="ruby", agent_id="ruby-id", project=str(project), socket_path=str(endpoint)
        )
        manager = presence.PresenceManager(agent)
        manager.publish()
        bridge = hub_bridge.HubBridge()
        assert [row["identity"] for row in bridge.discover_sessions(use_cache=False)] == ["ruby"]
        assert await bridge.query_socket("ruby-id", "ping") == {"type": "pong", "action": "ping"}

        malformed = endpoint.parent / "malformed.sock"
        malformed.write_text("{}", encoding="utf-8")
        (manager._presence_dir / "bad.json").write_text(json.dumps({
            "agent_id": "bad", "identity": "bad", "pid": os.getpid(),
            "socket_path": str(malformed), "last_heartbeat": agent.last_heartbeat,
        }), encoding="utf-8")

        assert endpoint.is_file() and not endpoint.is_socket()
        assert local_ipc.is_local_endpoint(endpoint)
        assert not local_ipc.is_local_endpoint(malformed)
        found = presence.live_agents_on_machine()
        assert [record["identity"] for _, record in found] == ["ruby"]
        assert presence.find_live_socket("ruby") == str(endpoint)
    finally:
        server.close()
        project_scope.resolve_project_root.cache_clear()


@pytest.mark.skipif(os.name == "nt", reason="POSIX socket root is HOME-independent")
def test_presence_discovery_is_independent_of_home_directory(monkeypatch, tmp_path):
    from kollabor_agent.runtime import AgentRuntime
    from kollabor_config.config_utils import get_project_data_dir
    from plugins.hub import presence, project_scope
    from plugins.hub.org_launcher import OrgLauncher

    project = tmp_path / "workspace"
    home_a = tmp_path / "home-a"
    home_b = tmp_path / "home-b"
    socket_root = Path(tempfile.mkdtemp(prefix="kh-670-", dir="/tmp"))
    for directory in (project, home_a, home_b):
        directory.mkdir()

    monkeypatch.setenv("KOLLAB_PROJECT_ROOT", str(project))
    monkeypatch.setenv("KOLLAB_HUB_PROJECT_SCOPED", "1")
    monkeypatch.chdir(project)
    monkeypatch.setattr(presence, "SOCKET_DIR_ROOT", socket_root)
    monkeypatch.setattr(Path, "home", lambda: home_a)
    project_scope.resolve_project_root.cache_clear()

    endpoint = presence.get_socket_dir() / "publisher.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(endpoint))
    server.listen()
    try:
        publisher = AgentRuntime(
            identity="publisher",
            agent_id="publisher-id",
            project=str(project),
            socket_path=str(endpoint),
        )
        publisher_manager = presence.PresenceManager(publisher)
        publisher_manager.publish()

        monkeypatch.setattr(Path, "home", lambda: home_b)
        reader = AgentRuntime(
            identity="reader",
            agent_id="reader-id",
            project=str(project),
            socket_path=str(endpoint.parent / "reader.sock"),
        )
        reader_manager = presence.PresenceManager(reader)

        assert [agent.identity for agent in reader_manager.discover_agents()] == [
            "publisher"
        ]

        monkeypatch.setattr(local_ipc, "is_local_endpoint", lambda path: Path(path).is_socket())
        live_agents = presence.live_agents_on_machine()
        assert [record["identity"] for _, record in live_agents] == ["publisher"]
        expected_hub = get_project_data_dir(project) / "hub"
        assert [hub_dir for hub_dir, _ in live_agents] == [expected_hub]
        assert OrgLauncher(str(project))._online() == {"publisher"}

        monkeypatch.setattr(hub_bridge, "hub_socket_root", lambda: socket_root)
        assert [row["identity"] for row in hub_bridge.HubBridge().discover_sessions(use_cache=False)] == [
            "publisher"
        ]
    finally:
        server.close()
        shutil.rmtree(socket_root, ignore_errors=True)
        project_scope.resolve_project_root.cache_clear()


@pytest.mark.skipif(os.name == "nt", reason="POSIX presence uses the shared socket root")
def test_global_shared_presence_reports_logical_hub_dir(monkeypatch, tmp_path):
    from kollabor_agent.runtime import AgentRuntime
    from kollabor_config.config_utils import get_config_directory
    from plugins.hub import presence

    project = tmp_path / "workspace"
    home = tmp_path / "home"
    project.mkdir()
    home.mkdir()
    socket_root = Path(tempfile.mkdtemp(prefix="kg-670-", dir="/tmp"))

    monkeypatch.setenv("KOLLAB_HUB_PROJECT_SCOPED", "0")
    monkeypatch.chdir(project)
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(presence, "SOCKET_DIR_ROOT", socket_root)

    endpoint = presence.get_socket_dir() / "publisher.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(endpoint))
    server.listen()
    try:
        publisher = AgentRuntime(
            identity="publisher",
            agent_id="publisher-id",
            project=str(project),
            socket_path=str(endpoint),
        )
        presence.PresenceManager(publisher).publish()
        monkeypatch.setattr(
            local_ipc, "is_local_endpoint", lambda path: Path(path).is_socket()
        )

        live_agents = presence.live_agents_on_machine()
        assert [record["identity"] for _, record in live_agents] == ["publisher"]
        assert [hub_dir for hub_dir, _ in live_agents] == [
            get_config_directory() / "hub"
        ]
    finally:
        server.close()
        shutil.rmtree(socket_root, ignore_errors=True)


@pytest.mark.skipif(
    os.name == "nt", reason="legacy POSIX presence lives under HOME"
)
def test_presence_files_include_current_home_legacy_directory(monkeypatch, tmp_path):
    from plugins.hub import presence, project_scope

    project = tmp_path / "workspace"
    home = tmp_path / "home"
    project.mkdir()
    home.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("KOLLAB_PROJECT_ROOT", str(project))
    monkeypatch.setenv("KOLLAB_HUB_PROJECT_SCOPED", "1")
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(presence, "SOCKET_DIR_ROOT", tmp_path / "hub-sockets")
    project_scope.resolve_project_root.cache_clear()

    try:
        legacy_dir = presence._legacy_presence_dir()
        assert legacy_dir is not None
        legacy_dir.mkdir(parents=True)
        legacy_record = legacy_dir / "legacy-agent.json"
        legacy_record.write_text(json.dumps({"identity": "legacy-agent"}))

        shared_dir = presence.get_presence_dir()
        assert shared_dir != legacy_dir
        assert list(presence._presence_files(shared_dir)) == [legacy_record]
    finally:
        project_scope.resolve_project_root.cache_clear()
