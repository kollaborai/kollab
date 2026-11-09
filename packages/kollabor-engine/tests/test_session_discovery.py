"""Tests for discovery of detached sessions through hub presence."""
from types import SimpleNamespace

import pytest
from kollabor_engine.hub_bridge import HubBridge
from kollabor_engine.routes import sessions as sessions_route

from kollabor_ai.session_naming import session_display_name


def test_discover_sessions_lists_every_live_agent_with_a_socket(tmp_path, monkeypatch):
    # Every agent publishes launch_strategy "interactive" (terminal sessions and
    # --detached daemons alike), so it filters nothing. Gem names repeat across
    # folders: the presence agent id keeps the rows apart.
    socket = tmp_path / "agent.sock"
    socket.touch()
    bridge = HubBridge()
    monkeypatch.setattr(
        bridge,
        "get_agents",
        lambda use_cache=False: [
            {"agent_id": "a1", "identity": "koordinator", "launch_strategy": "interactive",
             "socket_path": str(socket), "pid": 12, "project": "/w/one",
             "session_log": "/k/conversations/2610082249-nexus-drift.jsonl"},
            {"agent_id": "a2", "identity": "koordinator", "launch_strategy": "interactive",
             "socket_path": str(socket), "pid": 13, "project": "/w/two"},
            {"agent_id": "a3", "identity": "gone", "socket_path": str(tmp_path / "gone.sock")},
        ],
    )

    found = bridge.discover_sessions(use_cache=False)

    assert [(item["session_id"], item["workspace"]) for item in found] == [("a1", "/w/one"), ("a2", "/w/two")]
    assert found[0]["identity"] == "koordinator"
    assert found[0]["name"] == "2610082249-nexus-drift"  # the conversation its terminal shows
    assert found[0]["daemon_pid"] == 12
    assert found[0]["external"] is True
    assert found[0]["source"] == "hub_presence"


def test_an_agent_that_has_not_published_its_socket_is_not_listed(tmp_path, monkeypatch):
    # #555: an agent's first presence record (#38) has no socket_path. Guessing
    # from its name found a same-named agent's socket in another folder, so a
    # new chat attached to that agent.
    import glob

    other = tmp_path / "other-folder" / "lapis.sock"
    other.parent.mkdir()
    other.touch()
    real = glob.glob
    monkeypatch.setattr(glob, "glob", lambda pattern, **kw: (
        [str(other)] if pattern.endswith("lapis.sock") else real(pattern, **kw)))
    bridge = HubBridge()
    starting = {"agent_id": "b1", "identity": "lapis", "pid": 14, "project": "/w/new"}
    monkeypatch.setattr(bridge, "get_agents", lambda use_cache=False: [starting])

    assert bridge._socket_path_for_agent(starting) is None
    assert bridge.discover_sessions(use_cache=False) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("by_identity", [False, True])
async def test_a_message_reaches_a_socket_published_after_the_cached_record(tmp_path, monkeypatch, by_identity):
    # Benitoite on #556: the cached first record, with no socket_path, hid the
    # socket the agent published next, so a message to it found none for up to
    # five seconds.
    import json
    import os
    import time
    from unittest.mock import AsyncMock

    from kollabor_engine import hub_bridge

    presence = tmp_path / "presence"
    presence.mkdir()
    record = presence / "c1.json"
    first = {"agent_id": "c1", "identity": "lapis", "pid": os.getpid(), "last_heartbeat": time.time()}
    record.write_text(json.dumps(first))
    monkeypatch.setattr(hub_bridge, "_find_presence_dirs", lambda: [presence])
    bridge = HubBridge()
    assert bridge.get_agent("c1")["identity"] == "lapis"  # caches the first record
    socket = tmp_path / "lapis.sock"
    socket.touch()
    record.write_text(json.dumps({**first, "socket_path": str(socket)}))
    send = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(bridge, "_query_socket_path", send)

    if by_identity:
        assert await bridge.query_socket_by_identity("lapis", "ping") == {"ok": True}
    else:
        assert await bridge.query_socket("c1", "ping") == {"ok": True}
    assert send.await_args.args[0] == str(socket)


def _presence_bridge(tmp_path, monkeypatch, *records):
    """A bridge that reads these presence records from one folder, and that folder."""
    import json

    from kollabor_engine import hub_bridge

    presence = tmp_path / "presence"
    presence.mkdir()
    for record in records:
        (presence / f"{record['agent_id']}.json").write_text(json.dumps(record))
    monkeypatch.setattr(hub_bridge, "_find_presence_dirs", lambda: [presence])
    return HubBridge(), presence


def _record(agent_id, identity, **fields):
    import os
    import time

    return {"agent_id": agent_id, "identity": identity, "pid": os.getpid(), "last_heartbeat": time.time(), **fields}


@pytest.mark.asyncio
async def test_a_record_read_again_is_the_same_agent_never_a_namesake_elsewhere(tmp_path, monkeypatch):
    # Hiddenite on #556 round 2: the reread looked the name up again, and once
    # this folder's lapis had gone it found another folder's lapis and sent there.
    import json
    from unittest.mock import AsyncMock

    bridge, presence = _presence_bridge(tmp_path, monkeypatch, _record("a1", "lapis", project="/w/here"))
    assert bridge.get_agent_by_identity("lapis")["agent_id"] == "a1"  # caches this folder's lapis
    (presence / "a1.json").unlink()
    socket = tmp_path / "lapis.sock"
    socket.touch()
    namesake = _record("b1", "lapis", project="/w/elsewhere", socket_path=str(socket))
    (presence / "b1.json").write_text(json.dumps(namesake))
    send = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(bridge, "_query_socket_path", send)

    assert await bridge.query_socket_by_identity("lapis", "ping") is None
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_message_for_an_agent_id_reaches_an_agent_that_started_after_the_cache(tmp_path, monkeypatch):
    # Hiddenite on #556 round 2: an agent missing from a cache taken before it
    # started stayed unreachable for up to five seconds, its socket published.
    import json
    from unittest.mock import AsyncMock

    bridge, presence = _presence_bridge(tmp_path, monkeypatch, _record("x1", "ruby"))
    assert [agent["agent_id"] for agent in bridge.get_agents()] == ["x1"]  # a cache with no lapis
    socket = tmp_path / "lapis.sock"
    socket.touch()
    (presence / "c1.json").write_text(json.dumps(_record("c1", "lapis", socket_path=str(socket))))
    send = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(bridge, "_query_socket_path", send)

    assert await bridge.query_socket("c1", "ping") == {"ok": True}
    assert send.await_args.args[0] == str(socket)


@pytest.mark.asyncio
async def test_a_name_the_cache_lacks_is_refused_never_found_in_another_folder(tmp_path, monkeypatch):
    # Tsavorite on #556 round 3: this folder's lapis had left a cached roster, a
    # fresh lookup by name found another folder's lapis, and the message went there.
    import json
    from unittest.mock import AsyncMock

    bridge, presence = _presence_bridge(tmp_path, monkeypatch, _record("x1", "ruby", project="/w/here"))
    assert [agent["agent_id"] for agent in bridge.get_agents()] == ["x1"]  # a cache with no lapis
    socket = tmp_path / "lapis.sock"
    socket.touch()
    namesake = _record("b1", "lapis", project="/w/elsewhere", socket_path=str(socket))
    (presence / "b1.json").write_text(json.dumps(namesake))
    send = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(bridge, "_query_socket_path", send)

    assert await bridge.query_socket_by_identity("lapis", "ping") is None
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_fresh_read_by_id_never_hands_the_name_to_another_folder(tmp_path, monkeypatch):
    # Kyanite on #556 round 4: the reread by id replaced the cached roster, so the
    # next message for lapis found another folder's lapis while main still refused.
    import json
    from unittest.mock import AsyncMock

    here = tmp_path / "here.sock"
    here.touch()
    first = _record("a1", "lapis", project="/w/here", socket_path=str(here))
    bridge, presence = _presence_bridge(tmp_path, monkeypatch, first)
    assert [agent["agent_id"] for agent in bridge.get_agents()] == ["a1"]
    (presence / "a1.json").unlink()
    here.unlink()
    elsewhere = tmp_path / "elsewhere.sock"
    elsewhere.touch()
    namesake = _record("b1", "lapis", project="/w/elsewhere", socket_path=str(elsewhere))
    (presence / "b1.json").write_text(json.dumps(namesake))
    send = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(bridge, "_query_socket_path", send)

    assert await bridge.query_socket_by_identity("lapis", "ping") is None
    assert await bridge.query_socket_by_identity("lapis", "ping") is None
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_fresh_read_for_another_agent_never_hands_the_name_to_another_folder(tmp_path, monkeypatch):
    # Kyanite on #556 round 4: a cache miss for any agent id refreshed the shared
    # roster, and a name the cache lacked then found another folder's agent.
    import json
    from unittest.mock import AsyncMock

    bridge, presence = _presence_bridge(tmp_path, monkeypatch, _record("x1", "ruby", project="/w/here"))
    assert [agent["agent_id"] for agent in bridge.get_agents()] == ["x1"]
    sapphire = tmp_path / "sapphire.sock"
    sapphire.touch()
    peer = _record("c1", "sapphire", project="/w/here", socket_path=str(sapphire))
    (presence / "c1.json").write_text(json.dumps(peer))
    lapis = tmp_path / "lapis.sock"
    lapis.touch()
    namesake = _record("b1", "lapis", project="/w/elsewhere", socket_path=str(lapis))
    (presence / "b1.json").write_text(json.dumps(namesake))
    send = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(bridge, "_query_socket_path", send)

    assert await bridge.query_socket("c1", "ping") == {"ok": True}
    assert await bridge.query_socket_by_identity("lapis", "ping") is None
    assert [call.args[0] for call in send.await_args_list] == [str(sapphire)]


@pytest.mark.asyncio
async def test_a_message_for_an_agent_the_cache_lacks_is_addressed_to_its_identity(tmp_path, monkeypatch):
    # Kyanite on #556 round 4: the message named the id, so the agent took it as
    # someone else's and never woke for it.
    import json
    from unittest.mock import AsyncMock

    bridge, presence = _presence_bridge(tmp_path, monkeypatch, _record("x1", "ruby"))
    assert [agent["agent_id"] for agent in bridge.get_agents()] == ["x1"]
    socket = tmp_path / "lapis.sock"
    socket.touch()
    (presence / "c1.json").write_text(json.dumps(_record("c1", "lapis", socket_path=str(socket))))
    send = AsyncMock(return_value={"type": "ack"})
    monkeypatch.setattr(bridge, "_query_socket_path", send)

    assert await bridge.send_message("c1", "hello") is True
    assert send.await_args.args[2]["to"] == "lapis"


@pytest.mark.asyncio
async def test_hub_messages_refuses_a_namesake_after_an_id_refresh(tmp_path, monkeypatch):
    """The route must not rebind the second message after a failed refresh (#556)."""
    import json
    from unittest.mock import AsyncMock

    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from kollabor_engine.routes import hub as hub_route

    first_socket = tmp_path / "here.sock"
    first_socket.touch()
    first = _record("a1", "lapis", project="/w/here", socket_path=str(first_socket))
    bridge, presence = _presence_bridge(tmp_path, monkeypatch, first)
    assert bridge.get_agents()[0]["agent_id"] == "a1"
    (presence / "a1.json").unlink()
    first_socket.unlink()
    other_socket = tmp_path / "elsewhere.sock"
    other_socket.touch()
    other = _record("b1", "lapis", project="/w/elsewhere", socket_path=str(other_socket))
    (presence / "b1.json").write_text(json.dumps(other))
    send = AsyncMock(return_value={"type": "ack"})
    monkeypatch.setattr(bridge, "_query_socket_path", send)
    monkeypatch.setattr(hub_route, "_bridge", bridge)
    app = FastAPI()
    app.include_router(hub_route.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        statuses = [
            (await client.post("/hub/messages", json={"target": "lapis", "content": "hello"})).status_code
            for _ in range(2)
        ]

    assert statuses == [503, 503]
    send.assert_not_awaited()


def test_discover_sessions_carries_what_the_agent_publishes(tmp_path, monkeypatch):
    # The row reads the same before the engine opens the agent as after:
    # EngineSession.to_dict answers profile and model, so presence must too.
    socket = tmp_path / "agent.sock"
    socket.touch()
    bridge = HubBridge()
    monkeypatch.setattr(
        bridge,
        "get_agents",
        lambda use_cache=False: [
            {"agent_id": "a1", "identity": "koordinator", "socket_path": str(socket),
             "profile_name": "anthropic", "tags": {"model": "claude-opus-5-5"}},
            {"agent_id": "a2", "identity": "lapis", "socket_path": str(socket)},
        ],
    )

    found = bridge.discover_sessions(use_cache=False)

    assert (found[0]["profile"], found[0]["model"]) == ("anthropic", "claude-opus-5-5")
    assert (found[1]["profile"], found[1]["model"]) == ("", "")


def test_discover_sessions_uses_friendly_name_for_opaque_identity(tmp_path, monkeypatch):
    socket = tmp_path / "agent.sock"
    socket.touch()
    identity = "1c753a7d2def481084e3a64bcb09a7d2"
    bridge = HubBridge()
    monkeypatch.setattr(
        bridge,
        "get_agents",
        lambda use_cache=False: [
            {
                "identity": identity,
                "launch_strategy": "subprocess",
                "socket_path": str(socket),
            }
        ],
    )

    found = bridge.discover_sessions(use_cache=False)

    assert found[0]["session_id"] == identity  # no agent id: the identity stands in
    assert found[0]["name"] == session_display_name(identity)
    assert found[0]["name"] != identity


@pytest.mark.asyncio
async def test_list_sessions_merges_discovered_without_duplicates(monkeypatch):
    local = SimpleNamespace(
        session_id="local", alive=True, to_dict=lambda: {"session_id": "local"}
    )
    registry = sessions_route.get_session_registry()
    registry["local"] = local
    monkeypatch.setattr(
        sessions_route.HubBridge,
        "discover_sessions",
        lambda self, use_cache=False: [
            {"session_id": "local", "discovered": True},
            {"session_id": "remote", "discovered": True},
        ],
    )
    try:
        result = await sessions_route.list_sessions()
        assert [item["session_id"] for item in result["sessions"]] == ["local", "remote"]
        assert result["active_count"] == 2
        assert result["discovered"] == [{"session_id": "remote", "discovered": True, "instance": "remote"}]
    finally:
        registry.pop("local", None)
