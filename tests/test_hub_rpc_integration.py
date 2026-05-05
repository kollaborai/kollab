"""Integration tests: RpcServer wired into the hub AgentSocketServer.

Spans the boundary between kollabor_rpc and plugins.hub.messenger. Verifies
that attach clients can send rpc_request frames over both paths:

1. Handshake dispatcher (``_handle_connection`` action branch): a connection
   that sends a single ``rpc_request`` without first sending ``attach``.
2. Attached streaming loop (``_recv_input`` branch): a connection that first
   sends ``attach``, receives the ack, then sends ``rpc_request`` on the same
   socket alongside the bidirectional event stream.

Uses a real ``asyncio.start_unix_server`` via ``AgentSocketServer.start()``
so the whole frame-parsing path is exercised.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any, AsyncIterator

import pytest
import pytest_asyncio

from kollabor_rpc import RpcServer
from kollabor_tui.display_tap import DisplayTap
from plugins.hub.messenger import AgentSocketServer


@pytest.fixture
def short_socket_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect hub socket directory to a short /tmp path.

    macOS caps unix socket paths at ~104 chars. The default hub socket dir
    (``~/.kollab/hub/sockets``) plus a long agent id can overflow,
    and pytest's tmp_path under ``/var/folders/...`` is even worse. We mint
    a short path under ``/tmp`` and patch the hub's get_socket_dir to use it.
    """
    short_dir = Path(f"/tmp/khrpc-{uuid.uuid4().hex[:8]}")
    short_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "plugins.hub.messenger.get_socket_dir",
        lambda: short_dir,
    )
    yield short_dir
    # Best-effort cleanup
    try:
        for f in short_dir.iterdir():
            try:
                f.unlink()
            except OSError:
                pass
        short_dir.rmdir()
    except OSError:
        pass


@pytest_asyncio.fixture
async def running_server(
    short_socket_dir: Path,
) -> AsyncIterator[tuple[AgentSocketServer, RpcServer, str]]:
    """Start an AgentSocketServer with an RpcServer + DisplayTap installed.

    Registers a ping handler on the RpcServer. Yields the server, the rpc
    dispatcher, and the socket path. Tears everything down on exit.
    """
    rpc_server = RpcServer()

    async def ping_handler(params: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "ok",
            "identity": "hub-rpc-test",
            "echo": params.get("echo", ""),
        }

    rpc_server.register("ping", ping_handler)

    async def _on_message(msg: Any) -> None:  # unused but required by constructor
        return None

    # Use a short agent id so the socket path stays under 104 chars.
    agent_id = f"t{uuid.uuid4().hex[:6]}"
    server = AgentSocketServer(
        agent_id=agent_id,
        on_message=_on_message,
    )
    server._rpc_server = rpc_server
    server._display_tap = DisplayTap(history_size=10)

    socket_path = await server.start()
    try:
        yield server, rpc_server, socket_path
    finally:
        await server.stop()


async def _readline(reader: asyncio.StreamReader) -> dict[str, Any]:
    """Read one NDJSON line and decode it. Raises on EOF or bad JSON."""
    raw = await asyncio.wait_for(reader.readline(), timeout=3.0)
    if not raw:
        raise AssertionError("unexpected EOF from server")
    return json.loads(raw.decode("utf-8").strip())


@pytest.mark.asyncio
async def test_rpc_request_on_handshake_path(
    running_server: tuple[AgentSocketServer, RpcServer, str],
) -> None:
    """Send rpc_request without attaching first; handshake dispatcher replies."""
    _, _, socket_path = running_server
    reader, writer = await asyncio.open_unix_connection(path=socket_path)
    try:
        req = {
            "action": "rpc_request",
            "request_id": "test1",
            "method": "ping",
            "params": {"echo": "hello"},
        }
        writer.write((json.dumps(req) + "\n").encode("utf-8"))
        await writer.drain()

        reply = await _readline(reader)
        assert reply["action"] == "rpc_reply"
        assert reply["request_id"] == "test1"
        assert reply["error"] is None
        assert reply["error_kind"] is None
        assert reply["result"]["status"] == "ok"
        assert reply["result"]["identity"] == "hub-rpc-test"
        assert reply["result"]["echo"] == "hello"
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


@pytest.mark.asyncio
async def test_rpc_request_on_attached_path(
    running_server: tuple[AgentSocketServer, RpcServer, str],
) -> None:
    """Attach first, then send rpc_request on the attached connection.

    This is the critical test: it validates that ``_recv_input`` dispatches
    rpc frames to the RpcServer while ``_send_events`` keeps streaming
    display events. The write lock must prevent the two write paths from
    interleaving.
    """
    _, _, socket_path = running_server
    reader, writer = await asyncio.open_unix_connection(path=socket_path)
    try:
        # Attach
        attach_msg = {
            "action": "attach",
            "mode": "interactive",
            "client_id": "test-attached-client",
        }
        writer.write((json.dumps(attach_msg) + "\n").encode("utf-8"))
        await writer.drain()

        # First frame must be the attach_ack
        ack = await _readline(reader)
        assert ack["type"] == "attach_ack"
        assert ack["mode"] == "interactive"

        # Now send an rpc_request on the same (attached) connection
        req = {
            "action": "rpc_request",
            "request_id": "test2",
            "method": "ping",
            "params": {"echo": "attached"},
        }
        writer.write((json.dumps(req) + "\n").encode("utf-8"))
        await writer.drain()

        # The reply may arrive interleaved with heartbeats or other events.
        # Read a small number of frames and find the rpc_reply with our id.
        found_reply: dict[str, Any] | None = None
        for _ in range(20):
            frame = await _readline(reader)
            if (
                frame.get("action") == "rpc_reply"
                and frame.get("request_id") == "test2"
            ):
                found_reply = frame
                break

        assert found_reply is not None, "rpc_reply for test2 not received"
        assert found_reply["error"] is None
        assert found_reply["error_kind"] is None
        assert found_reply["result"]["status"] == "ok"
        assert found_reply["result"]["echo"] == "attached"
    finally:
        # Tell the server to close the attached loop cleanly
        try:
            writer.write((json.dumps({"type": "detach"}) + "\n").encode("utf-8"))
            await writer.drain()
        except Exception:
            pass
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


@pytest_asyncio.fixture
async def running_server_with_state(
    short_socket_dir: Path,
) -> AsyncIterator[tuple[AgentSocketServer, RpcServer, str]]:
    """Variant of running_server with phase 4.5 state handlers registered.

    Wires a mock LocalStateService-shaped object through
    register_state_handlers so the new state.set_agent / state.activate_skill
    / state.set_system_prompt paths can be exercised end-to-end over a
    real unix socket.
    """
    from unittest.mock import MagicMock

    from kollabor.state import (
        AgentSnapshot,
        ContextListSnapshot,
        ConversationContext,
        SkillListSnapshot,
        SystemPromptSnapshot,
        register_state_handlers,
    )

    rpc_server = RpcServer()

    # Minimal ping handler so the existing tests aren't broken by shared use.
    async def ping_handler(params: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "ok",
            "identity": "hub-rpc-test",
            "echo": params.get("echo", ""),
        }

    rpc_server.register("ping", ping_handler)

    # Build a mock state_service that satisfies register_state_handlers.
    # Each method is a simple AsyncMock returning the expected snapshot.
    state_service = MagicMock()

    from unittest.mock import AsyncMock

    state_service.get_active_agent = AsyncMock(
        return_value=AgentSnapshot(name="coder", description="", is_active=True)
    )
    state_service.list_agents = AsyncMock(
        return_value=MagicMock(
            to_dict=lambda: {
                "active": "coder",
                "agents": [
                    {
                        "name": "coder",
                        "is_active": True,
                        "description": "",
                        "profile": "",
                        "active_skills": [],
                        "available_skills": [],
                        "default_skills": [],
                        "source": "bundled",
                    }
                ],
            }
        )
    )

    async def _set_agent(name: str) -> AgentSnapshot:
        if name == "missing":
            raise ValueError(f"agent not found: {name!r}")
        return AgentSnapshot(name=name, is_active=True)

    state_service.set_agent = AsyncMock(side_effect=_set_agent)
    state_service.clear_agent = AsyncMock(
        return_value=AgentSnapshot(name="coder", is_active=False)
    )

    async def _list_skills(agent_name: str = "") -> SkillListSnapshot:
        return SkillListSnapshot(agent_name=agent_name or "coder", skills=[])

    state_service.list_skills = AsyncMock(side_effect=_list_skills)

    async def _activate_skill(name: str) -> SkillListSnapshot:
        if name == "missing":
            raise ValueError(f"skill not found on active agent: {name!r}")
        return SkillListSnapshot(agent_name="coder", skills=[])

    state_service.activate_skill = AsyncMock(side_effect=_activate_skill)

    async def _deactivate_skill(name: str) -> SkillListSnapshot:
        return SkillListSnapshot(agent_name="coder", skills=[])

    state_service.deactivate_skill = AsyncMock(side_effect=_deactivate_skill)

    async def _get_system_prompt() -> SystemPromptSnapshot:
        return SystemPromptSnapshot(
            source="default", content="You are helpful.", size_chars=16
        )

    state_service.get_system_prompt = AsyncMock(side_effect=_get_system_prompt)

    async def _set_system_prompt(
        content: str, *, source: str = "file", path: str = ""
    ) -> SystemPromptSnapshot:
        if len(content) > 1_048_576:
            raise ValueError(f"system prompt too large: {len(content)} bytes")
        if not content.strip():
            raise ValueError("system prompt content is empty")
        return SystemPromptSnapshot(
            source=source, path=path, content=content, size_chars=len(content)
        )

    state_service.set_system_prompt = AsyncMock(side_effect=_set_system_prompt)

    # Phase 4.5 step 6: contexts
    _context_store: dict[str, ConversationContext] = {
        "main": ConversationContext(name="main"),
    }
    _active_context_name = {"v": "main"}

    async def _list_contexts(include_archived: bool = False) -> ContextListSnapshot:
        filtered = [
            c for c in _context_store.values() if include_archived or not c.archived
        ]
        return ContextListSnapshot(active=_active_context_name["v"], contexts=filtered)

    async def _get_active_context() -> ConversationContext:
        return _context_store[_active_context_name["v"]]

    async def _create_context(
        name: str,
        *,
        profile_name: str = "",
        agent_name: str = "",
        system_prompt: str = "",
    ) -> ConversationContext:
        if not name or not name.strip():
            raise ValueError("context name is required")
        name = name.strip()
        if name in _context_store:
            raise ValueError(f"context already exists: {name!r}")
        ctx = ConversationContext(
            name=name,
            active_profile_name=profile_name,
            active_agent_name=agent_name,
            system_prompt=system_prompt,
        )
        _context_store[name] = ctx
        return ctx

    async def _attach_to_context(name: str) -> ConversationContext:
        if not name or not name.strip():
            raise ValueError("context name is required")
        name = name.strip()
        if name not in _context_store:
            raise ValueError(f"context not found: {name!r}")
        if _context_store[name].archived:
            raise ValueError(f"context is archived: {name!r}")
        _active_context_name["v"] = name
        _context_store[name].touch()
        return _context_store[name]

    async def _archive_context(name: str) -> ConversationContext:
        if not name or not name.strip():
            raise ValueError("context name is required")
        name = name.strip()
        if name not in _context_store:
            raise ValueError(f"context not found: {name!r}")
        if name == _active_context_name["v"]:
            raise ValueError(f"cannot archive the live context: {name!r}")
        _context_store[name].archived = True
        return _context_store[name]

    state_service.list_contexts = AsyncMock(side_effect=_list_contexts)
    state_service.get_active_context = AsyncMock(side_effect=_get_active_context)
    state_service.create_context = AsyncMock(side_effect=_create_context)
    state_service.attach_to_context = AsyncMock(side_effect=_attach_to_context)
    state_service.archive_context = AsyncMock(side_effect=_archive_context)

    # register_state_handlers also wires up the phase 2-4 handlers. Those
    # don't need to be exercised in this test but they do need mocks.
    for method in [
        "get_conversation",
        "save_conversation",
        "get_session_stats",
        "get_active_profile",
        "list_profiles",
        "get_permission_state",
        "get_mcp_state",
        "get_hub_state",
        "get_processing_state",
        "get_system_info",
        "set_active_profile",
        "set_approval_mode",
    ]:
        if not hasattr(state_service, method) or not isinstance(
            getattr(state_service, method), AsyncMock
        ):
            async_stub = AsyncMock(return_value=MagicMock(to_dict=lambda: {}))
            setattr(state_service, method, async_stub)

    register_state_handlers(rpc_server, state_service)

    async def _on_message(msg: Any) -> None:
        return None

    agent_id = f"t{uuid.uuid4().hex[:6]}"
    server = AgentSocketServer(agent_id=agent_id, on_message=_on_message)
    server._rpc_server = rpc_server
    server._display_tap = DisplayTap(history_size=10)

    socket_path = await server.start()
    try:
        yield server, rpc_server, socket_path
    finally:
        await server.stop()


async def _rpc_call_on_fresh_socket(
    socket_path: str, method: str, params: dict[str, Any]
) -> dict[str, Any]:
    """Open a fresh unix connection, send one rpc_request, read one reply."""
    reader, writer = await asyncio.open_unix_connection(path=socket_path)
    try:
        req = {
            "action": "rpc_request",
            "request_id": f"r-{uuid.uuid4().hex[:8]}",
            "method": method,
            "params": params,
        }
        writer.write((json.dumps(req) + "\n").encode("utf-8"))
        await writer.drain()
        reply = await _readline(reader)
        return reply
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


@pytest.mark.asyncio
async def test_phase_4_5_set_agent_success(
    running_server_with_state: tuple[AgentSocketServer, RpcServer, str],
) -> None:
    """state.set_agent round-trips and returns the new agent snapshot."""
    _, _, socket_path = running_server_with_state
    reply = await _rpc_call_on_fresh_socket(
        socket_path, "state.set_agent", {"name": "backend"}
    )
    assert reply["action"] == "rpc_reply"
    assert reply["error"] is None
    result = reply["result"]
    assert result["name"] == "backend"
    assert result["is_active"] is True


@pytest.mark.asyncio
async def test_phase_4_5_set_agent_unknown_returns_error_envelope(
    running_server_with_state: tuple[AgentSocketServer, RpcServer, str],
) -> None:
    """Unknown agent returns an {error: ...} envelope, not a handler crash."""
    _, _, socket_path = running_server_with_state
    reply = await _rpc_call_on_fresh_socket(
        socket_path, "state.set_agent", {"name": "missing"}
    )
    assert reply["action"] == "rpc_reply"
    # Successful RPC call (no transport error)
    assert reply["error"] is None
    # But the result dict carries an error envelope
    result = reply["result"]
    assert "error" in result
    assert "agent not found" in result["error"]


@pytest.mark.asyncio
async def test_phase_4_5_activate_skill_success(
    running_server_with_state: tuple[AgentSocketServer, RpcServer, str],
) -> None:
    """state.activate_skill returns a SkillListSnapshot dict."""
    _, _, socket_path = running_server_with_state
    reply = await _rpc_call_on_fresh_socket(
        socket_path, "state.activate_skill", {"name": "tdd"}
    )
    assert reply["error"] is None
    result = reply["result"]
    assert result["agent_name"] == "coder"
    assert "skills" in result


@pytest.mark.asyncio
async def test_phase_4_5_activate_skill_unknown_returns_error_envelope(
    running_server_with_state: tuple[AgentSocketServer, RpcServer, str],
) -> None:
    _, _, socket_path = running_server_with_state
    reply = await _rpc_call_on_fresh_socket(
        socket_path, "state.activate_skill", {"name": "missing"}
    )
    assert reply["error"] is None
    result = reply["result"]
    assert "error" in result
    assert "skill not found" in result["error"]


@pytest.mark.asyncio
async def test_phase_4_5_set_system_prompt_success(
    running_server_with_state: tuple[AgentSocketServer, RpcServer, str],
) -> None:
    _, _, socket_path = running_server_with_state
    reply = await _rpc_call_on_fresh_socket(
        socket_path,
        "state.set_system_prompt",
        {
            "content": "You are a helpful assistant.",
            "source": "file",
            "path": "/tmp/prompt.md",
        },
    )
    assert reply["error"] is None
    result = reply["result"]
    assert result["content"] == "You are a helpful assistant."
    assert result["source"] == "file"
    assert result["path"] == "/tmp/prompt.md"
    assert result["size_chars"] == len("You are a helpful assistant.")


@pytest.mark.asyncio
async def test_phase_4_5_set_system_prompt_empty_returns_error_envelope(
    running_server_with_state: tuple[AgentSocketServer, RpcServer, str],
) -> None:
    _, _, socket_path = running_server_with_state
    reply = await _rpc_call_on_fresh_socket(
        socket_path, "state.set_system_prompt", {"content": "   "}
    )
    assert reply["error"] is None
    result = reply["result"]
    assert "error" in result
    assert "empty" in result["error"]


@pytest.mark.asyncio
async def test_phase_4_5_get_system_prompt_read(
    running_server_with_state: tuple[AgentSocketServer, RpcServer, str],
) -> None:
    _, _, socket_path = running_server_with_state
    reply = await _rpc_call_on_fresh_socket(socket_path, "state.get_system_prompt", {})
    assert reply["error"] is None
    result = reply["result"]
    assert result["content"] == "You are helpful."
    assert result["source"] == "default"


# === Phase 4.5 step 6: context rpc ===


@pytest.mark.asyncio
async def test_phase_4_5_step_6_list_contexts(
    running_server_with_state: tuple[AgentSocketServer, RpcServer, str],
) -> None:
    """state.list_contexts returns the initial 'main' context."""
    _, _, socket_path = running_server_with_state
    reply = await _rpc_call_on_fresh_socket(socket_path, "state.list_contexts", {})
    assert reply["error"] is None
    result = reply["result"]
    assert result["active"] == "main"
    names = {c["name"] for c in result["contexts"]}
    assert "main" in names


@pytest.mark.asyncio
async def test_phase_4_5_step_6_create_context(
    running_server_with_state: tuple[AgentSocketServer, RpcServer, str],
) -> None:
    """state.create_context adds a new context to the registry."""
    _, _, socket_path = running_server_with_state
    reply = await _rpc_call_on_fresh_socket(
        socket_path,
        "state.create_context",
        {
            "name": "feature-a",
            "profile_name": "claude",
            "agent_name": "coder",
            "system_prompt": "Write tests first.",
        },
    )
    assert reply["error"] is None
    result = reply["result"]
    assert result["name"] == "feature-a"
    assert result["active_profile_name"] == "claude"
    assert result["active_agent_name"] == "coder"
    assert result["system_prompt"] == "Write tests first."

    # Follow-up: it should now appear in list_contexts
    list_reply = await _rpc_call_on_fresh_socket(socket_path, "state.list_contexts", {})
    names = {c["name"] for c in list_reply["result"]["contexts"]}
    assert "feature-a" in names


@pytest.mark.asyncio
async def test_phase_4_5_step_6_create_duplicate_returns_error_envelope(
    running_server_with_state: tuple[AgentSocketServer, RpcServer, str],
) -> None:
    """Creating a duplicate context returns an error envelope, not a crash."""
    _, _, socket_path = running_server_with_state
    reply = await _rpc_call_on_fresh_socket(
        socket_path, "state.create_context", {"name": "main"}
    )
    assert reply["error"] is None  # transport-level success
    result = reply["result"]
    assert "error" in result
    assert "already exists" in result["error"]


@pytest.mark.asyncio
async def test_phase_4_5_step_6_create_empty_name_returns_error_envelope(
    running_server_with_state: tuple[AgentSocketServer, RpcServer, str],
) -> None:
    _, _, socket_path = running_server_with_state
    reply = await _rpc_call_on_fresh_socket(
        socket_path, "state.create_context", {"name": "   "}
    )
    assert reply["error"] is None
    result = reply["result"]
    assert "error" in result
    # The handler validates non-empty before delegating to the mock
    # state_service, so we either get "is required" from the handler
    # or the mock's ValueError text.
    assert "required" in result["error"] or "context name" in result["error"]


@pytest.mark.asyncio
async def test_phase_4_5_step_6_attach_to_context(
    running_server_with_state: tuple[AgentSocketServer, RpcServer, str],
) -> None:
    """state.attach_to_context switches the live context."""
    _, _, socket_path = running_server_with_state
    # Create a new context first
    create_reply = await _rpc_call_on_fresh_socket(
        socket_path, "state.create_context", {"name": "feature-b"}
    )
    assert create_reply["result"]["name"] == "feature-b"

    # Attach to it
    attach_reply = await _rpc_call_on_fresh_socket(
        socket_path, "state.attach_to_context", {"name": "feature-b"}
    )
    assert attach_reply["error"] is None
    result = attach_reply["result"]
    assert result["name"] == "feature-b"

    # list_contexts should now report feature-b as active
    list_reply = await _rpc_call_on_fresh_socket(socket_path, "state.list_contexts", {})
    assert list_reply["result"]["active"] == "feature-b"


@pytest.mark.asyncio
async def test_phase_4_5_step_6_attach_unknown_returns_error_envelope(
    running_server_with_state: tuple[AgentSocketServer, RpcServer, str],
) -> None:
    _, _, socket_path = running_server_with_state
    reply = await _rpc_call_on_fresh_socket(
        socket_path,
        "state.attach_to_context",
        {"name": "does-not-exist"},
    )
    assert reply["error"] is None
    result = reply["result"]
    assert "error" in result
    assert "not found" in result["error"]


@pytest.mark.asyncio
async def test_phase_4_5_step_6_archive_context(
    running_server_with_state: tuple[AgentSocketServer, RpcServer, str],
) -> None:
    """archive_context soft-deletes a non-live context."""
    _, _, socket_path = running_server_with_state
    # Create a context we'll archive
    await _rpc_call_on_fresh_socket(
        socket_path, "state.create_context", {"name": "to-archive"}
    )
    # Archive it
    reply = await _rpc_call_on_fresh_socket(
        socket_path, "state.archive_context", {"name": "to-archive"}
    )
    assert reply["error"] is None
    result = reply["result"]
    assert result["archived"] is True

    # list_contexts with default should NOT include it
    list_default = await _rpc_call_on_fresh_socket(
        socket_path, "state.list_contexts", {}
    )
    names = {c["name"] for c in list_default["result"]["contexts"]}
    assert "to-archive" not in names

    # list_contexts with include_archived=True should include it
    list_all = await _rpc_call_on_fresh_socket(
        socket_path,
        "state.list_contexts",
        {"include_archived": True},
    )
    names_all = {c["name"] for c in list_all["result"]["contexts"]}
    assert "to-archive" in names_all


@pytest.mark.asyncio
async def test_phase_4_5_step_6_cannot_archive_live_returns_error(
    running_server_with_state: tuple[AgentSocketServer, RpcServer, str],
) -> None:
    _, _, socket_path = running_server_with_state
    # The initial live context is 'main' -- can't archive it
    reply = await _rpc_call_on_fresh_socket(
        socket_path, "state.archive_context", {"name": "main"}
    )
    assert reply["error"] is None
    result = reply["result"]
    assert "error" in result
    assert "live context" in result["error"]


@pytest.mark.asyncio
async def test_phase_4_5_step_6_get_active_context(
    running_server_with_state: tuple[AgentSocketServer, RpcServer, str],
) -> None:
    """state.get_active_context returns the currently live context."""
    _, _, socket_path = running_server_with_state
    reply = await _rpc_call_on_fresh_socket(socket_path, "state.get_active_context", {})
    assert reply["error"] is None
    result = reply["result"]
    assert result["name"] == "main"
    # The mock doesn't populate messages, just verifies shape
    assert "conversation_history" in result
    assert "active_profile_name" in result


@pytest.mark.asyncio
async def test_rpc_unknown_method_on_attached_path(
    running_server: tuple[AgentSocketServer, RpcServer, str],
) -> None:
    """Unknown method on attached connection produces not_found error reply."""
    _, _, socket_path = running_server
    reader, writer = await asyncio.open_unix_connection(path=socket_path)
    try:
        attach_msg = {
            "action": "attach",
            "mode": "interactive",
            "client_id": "test-nf-client",
        }
        writer.write((json.dumps(attach_msg) + "\n").encode("utf-8"))
        await writer.drain()

        ack = await _readline(reader)
        assert ack["type"] == "attach_ack"

        req = {
            "action": "rpc_request",
            "request_id": "test3",
            "method": "does_not_exist",
            "params": {},
        }
        writer.write((json.dumps(req) + "\n").encode("utf-8"))
        await writer.drain()

        found_reply: dict[str, Any] | None = None
        for _ in range(20):
            frame = await _readline(reader)
            if (
                frame.get("action") == "rpc_reply"
                and frame.get("request_id") == "test3"
            ):
                found_reply = frame
                break

        assert found_reply is not None, "rpc_reply for test3 not received"
        assert found_reply["result"] is None
        assert found_reply["error_kind"] == "not_found"
        assert "does_not_exist" in found_reply["error"]
    finally:
        try:
            writer.write((json.dumps({"type": "detach"}) + "\n").encode("utf-8"))
            await writer.drain()
        except Exception:
            pass
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
