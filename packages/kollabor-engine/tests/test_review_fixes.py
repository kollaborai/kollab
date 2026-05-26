"""Regression tests for engine review fixes."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from kollabor_ai import LLMProfile


class _FakeProfileManager:
    DEFAULT_PROFILES = {}
    active_profile_name = "secret"

    def __init__(
        self,
        *,
        provider: str = "openai",
        model: str = "gpt-4",
        api_key: str = "sk-secret-value",
        base_url: str = "https://api.openai.com/v1",
    ) -> None:
        self.profile = LLMProfile(
            name="secret",
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=base_url,
        )

    def list_profiles(self):
        return [self.profile]

    def get_profile(self, name: str):
        return self.profile if name == self.profile.name else None

    def is_active(self, name: str) -> bool:
        return name == self.active_profile_name

    def update_profile(self, name: str, **kwargs):
        if name != self.profile.name:
            return False
        if "model" in kwargs:
            self.profile.model = kwargs["model"]
        return True


@pytest.fixture
def profile_app(monkeypatch, bypass_auth):
    import kollabor_engine.routes.profiles as profile_routes
    from kollabor_engine.server import create_app

    manager = _FakeProfileManager()
    monkeypatch.setattr(profile_routes, "_get_profile_manager", lambda: manager)
    return create_app()


@pytest.fixture
def app(bypass_auth):
    from kollabor_engine.server import create_app

    return create_app()


@pytest.mark.asyncio
async def test_profile_routes_redact_api_key(profile_app):
    async with AsyncClient(
        transport=ASGITransport(app=profile_app), base_url="http://test"
    ) as client:
        list_response = await client.get("/profiles")
        get_response = await client.get("/profiles/secret")
        update_response = await client.put(
            "/profiles/secret", json={"model": "gpt-4.1"}
        )

    assert list_response.status_code == 200
    listed = list_response.json()["profiles"][0]
    assert "api_key" not in listed

    assert get_response.status_code == 200
    profile = get_response.json()
    assert "api_key" not in profile
    assert profile["env_var_hints"]["api_key"]["is_set"] is False

    assert update_response.status_code == 200
    updated = update_response.json()
    assert "api_key" not in updated
    assert updated["updated"] is True


@pytest.mark.asyncio
async def test_ready_does_not_resolve_or_migrate_api_keys(monkeypatch, bypass_auth):
    import kollabor_ai
    from kollabor_engine.server import create_app

    class _ReadinessProfile(LLMProfile):
        def get_api_key(self):
            raise AssertionError("readiness must not resolve secrets")

    profile = _ReadinessProfile(
        name="secret",
        provider="openai",
        model="gpt-4",
        api_key="sk-secret-value",
    )
    manager = SimpleNamespace(list_profiles=lambda: [profile])
    monkeypatch.setattr(kollabor_ai, "ProfileManager", lambda: manager)

    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        response = await client.get("/ready")

    assert response.status_code == 200
    assert response.json()["ready"] is True
    assert response.json()["checks"]["api_credentials"] == "ok"


@pytest.mark.asyncio
async def test_anthropic_profile_test_uses_api_communication_service(
    monkeypatch, bypass_auth
):
    import kollabor_engine.routes.profiles as profile_routes
    from kollabor_engine.server import create_app

    calls = {"initialized": False, "called": False, "shutdown": False}
    manager = _FakeProfileManager(
        provider="anthropic",
        model="claude-3-5-sonnet-latest",
        api_key="sk-ant-secret-value",
        base_url=None,
    )

    class _FakeAPICommunicationService:
        def __init__(self, config, raw_conversations_dir, profile):
            assert raw_conversations_dir is None
            assert profile.provider == "anthropic"
            self._provider_error = None

        async def initialize(self):
            calls["initialized"] = True
            return True

        async def call_llm(self, conversation_history):
            calls["called"] = True
            assert conversation_history == [{"role": "user", "content": "Hi"}]
            return "ok"

        async def shutdown(self):
            calls["shutdown"] = True

    monkeypatch.setattr(profile_routes, "_get_profile_manager", lambda: manager)
    monkeypatch.setattr(
        profile_routes,
        "APICommunicationService",
        _FakeAPICommunicationService,
    )
    app = create_app()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/profiles/secret/test")

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["message"] == "API key is valid"
    assert calls == {"initialized": True, "called": True, "shutdown": True}


def test_workspace_resolved_paths_still_honor_protected_files(tmp_path):
    from kollabor_agent.file_operations_executor import FileOperationsExecutor

    protected = tmp_path / "main.py"
    protected.write_text("print('do not touch')\n", encoding="utf-8")

    executor = FileOperationsExecutor(workspace=tmp_path)

    assert executor.is_protected_path(str(protected))
    assert executor.is_protected_path("main.py")


def test_engine_session_applies_workspace_to_tools_and_mcp(tmp_path):
    from kollabor_engine.session import EngineSession

    profile = LLMProfile(
        name="inline",
        provider="custom",
        model="local-model",
        api_key="local-key",
        base_url="http://localhost:1234/v1",
    )

    session = EngineSession(
        session_id="sess_workspace",
        profile=profile,
        workspace=str(tmp_path),
    )

    assert session.workspace == str(tmp_path.resolve())
    assert session.workspace_path == tmp_path.resolve()
    assert session.mcp_integration.workspace == tmp_path.resolve()
    assert session.mcp_integration.local_mcp_dirs == [
        tmp_path.resolve() / ".kollab" / "mcp"
    ]
    assert session.tool_executor.workspace == tmp_path.resolve()
    assert session.tool_executor.file_ops_executor.project_root == tmp_path.resolve()


def test_engine_session_rejects_invalid_workspace(tmp_path):
    from kollabor_engine.session import EngineSession

    profile = LLMProfile(
        name="inline",
        provider="custom",
        model="local-model",
        api_key="local-key",
        base_url="http://localhost:1234/v1",
    )

    with pytest.raises(ValueError, match="Workspace does not exist"):
        EngineSession(
            session_id="sess_bad_workspace",
            profile=profile,
            workspace=str(tmp_path / "missing"),
        )


@pytest.mark.asyncio
async def test_tool_executor_uses_workspace_for_terminal_and_files(tmp_path):
    from kollabor_agent.tool_executor import ToolExecutor

    class _EventBus:
        def get_service(self, name: str):
            return None

        async def emit_with_hooks(self, *args, **kwargs):
            return {}

    file_path = tmp_path / "note.txt"
    file_path.write_text("hello workspace", encoding="utf-8")

    executor = ToolExecutor(
        mcp_integration=SimpleNamespace(),
        event_bus=_EventBus(),
        workspace=tmp_path,
    )

    pwd_result = await executor.execute_tool(
        {"id": "term", "type": "terminal", "command": "pwd"}
    )
    assert pwd_result.success
    assert pwd_result.output.strip() == str(tmp_path.resolve())

    read_result = await executor.execute_tool(
        {"id": "read", "type": "file_read", "file": "note.txt"}
    )
    assert read_result.success
    assert "hello workspace" in read_result.output
    assert str(file_path.resolve()) in read_result.metadata["file_path"]

    relative_cwd_result = await executor.execute_tool(
        {"id": "term2", "type": "terminal", "command": "pwd", "cwd": "."}
    )
    assert relative_cwd_result.success
    assert relative_cwd_result.output.strip() == str(tmp_path.resolve())


@pytest.mark.asyncio
async def test_session_mcp_connect_endpoint_connects(app):
    from kollabor_engine.server import get_session_registry

    async def connect(server_name: str, command: str):
        mock_mcp.server_connections[server_name] = SimpleNamespace(initialized=True)
        mock_mcp.tool_registry["memory_query"] = {
            "server": server_name,
            "definition": {"description": "Query memory"},
        }
        return [{"name": "memory_query", "description": "Query memory"}]

    mock_mcp = SimpleNamespace(
        mcp_servers={
            "memory": {
                "enabled": True,
                "command": "npx -y @modelcontextprotocol/server-memory",
            }
        },
        server_connections={},
        tool_registry={},
        _connect_and_list_tools=AsyncMock(side_effect=connect),
    )
    mock_session = SimpleNamespace(session_id="sess_connect", mcp_integration=mock_mcp)

    registry = get_session_registry()
    registry["sess_connect"] = mock_session
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/sessions/sess_connect/mcp/memory/connect")

        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["status"] == "connected"
        assert data["tool_count"] == 1
        assert data["tools"] == ["memory_query"]
        mock_mcp._connect_and_list_tools.assert_awaited_once_with(
            "memory", "npx -y @modelcontextprotocol/server-memory"
        )
    finally:
        registry.pop("sess_connect", None)
