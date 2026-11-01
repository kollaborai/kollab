"""Platform-specific Hub discovery and local IPC endpoint semantics."""

import json
import os
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
