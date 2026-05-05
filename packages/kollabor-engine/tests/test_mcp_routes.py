"""Tests for MCP routes following TDD principles."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def temp_mcp_config():
    """Create a temp MCP config file for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "mcp_settings.json"
        test_config = {
            "servers": {
                "memory": {
                    "type": "stdio",
                    "command": "npx -y @modelcontextprotocol/server-memory",
                    "enabled": True,
                    "description": "Memory server",
                },
                "filesystem": {
                    "type": "stdio",
                    "command": "npx -y @modelcontextprotocol/server-filesystem /tmp",
                    "enabled": False,
                    "description": "Filesystem server",
                },
            }
        }
        with open(config_path, "w") as f:
            json.dump(test_config, f)
        yield config_path, test_config


@pytest.fixture
def app(temp_mcp_config, bypass_auth):
    """Create test app with mocked MCP config."""
    config_path, _ = temp_mcp_config

    # Patch module-level constants BEFORE importing/creating server
    import kollabor_engine.routes.mcp as mcp_routes  # type: ignore[import-not-found]

    original_config = mcp_routes.GLOBAL_MCP_CONFIG
    original_dir = mcp_routes.GLOBAL_MCP_DIR

    mcp_routes.GLOBAL_MCP_CONFIG = config_path
    mcp_routes.GLOBAL_MCP_DIR = config_path.parent

    # Import create_app AFTER patching
    from kollabor_engine.server import create_app  # type: ignore[import-not-found]

    app = create_app()

    # The mcp router is already included by create_app
    # Just need to ensure paths stay patched during test

    yield app

    # Restore original paths after test
    mcp_routes.GLOBAL_MCP_CONFIG = original_config
    mcp_routes.GLOBAL_MCP_DIR = original_dir


@pytest_asyncio.fixture
async def client(app):
    """Async test client with auth bypassed."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


class TestConfigHelpers:
    """Test config loading/saving helpers."""

    def test_load_mcp_config_reads_file(self, temp_mcp_config):
        """Test _load_mcp_config reads and parses JSON."""
        config_path, expected = temp_mcp_config

        # Patch the path
        import kollabor_engine.routes.mcp as mcp_routes  # type: ignore[import-not-found]
        from kollabor_engine.routes.mcp import _load_mcp_config

        original = mcp_routes.GLOBAL_MCP_CONFIG
        mcp_routes.GLOBAL_MCP_CONFIG = config_path

        result = _load_mcp_config()

        # Restore
        mcp_routes.GLOBAL_MCP_CONFIG = original

        assert result == expected

    def test_load_mcp_config_returns_empty_when_missing(self):
        """Test _load_mcp_config returns empty dict when file missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            missing_path = Path(tmpdir) / "nonexistent.json"

            import kollabor_engine.routes.mcp as mcp_routes  # type: ignore[import-not-found]
            from kollabor_engine.routes.mcp import _load_mcp_config

            original = mcp_routes.GLOBAL_MCP_CONFIG
            mcp_routes.GLOBAL_MCP_CONFIG = missing_path

            result = _load_mcp_config()

            mcp_routes.GLOBAL_MCP_CONFIG = original

            assert result == {"servers": {}}

    def test_load_mcp_config_raises_on_invalid_json(self, temp_mcp_config):
        """Test _load_mcp_config raises HTTPException on bad JSON."""
        config_path, _ = temp_mcp_config
        with open(config_path, "w") as f:
            f.write("{invalid json")

        import kollabor_engine.routes.mcp as mcp_routes  # type: ignore[import-not-found]
        from fastapi import HTTPException  # type: ignore[import-not-found]
        from kollabor_engine.routes.mcp import _load_mcp_config  # type: ignore[import-not-found]

        original = mcp_routes.GLOBAL_MCP_CONFIG
        mcp_routes.GLOBAL_MCP_CONFIG = config_path

        with pytest.raises(HTTPException) as exc:
            _load_mcp_config()

        mcp_routes.GLOBAL_MCP_CONFIG = original

        assert exc.value.status_code == 500
        assert "Invalid JSON" in exc.value.detail

    def test_save_mcp_config_writes_file(self):
        """Test _save_mcp_config writes JSON file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "mcp_settings.json"
            test_config = {"servers": {"test": {"type": "stdio", "command": "test"}}}

            import kollabor_engine.routes.mcp as mcp_routes  # type: ignore[import-not-found]
            from kollabor_engine.routes.mcp import _save_mcp_config

            original_config = mcp_routes.GLOBAL_MCP_CONFIG
            original_dir = mcp_routes.GLOBAL_MCP_DIR
            mcp_routes.GLOBAL_MCP_CONFIG = config_path
            mcp_routes.GLOBAL_MCP_DIR = Path(tmpdir)

            _save_mcp_config(test_config)

            mcp_routes.GLOBAL_MCP_CONFIG = original_config
            mcp_routes.GLOBAL_MCP_DIR = original_dir

            assert config_path.exists()
            with open(config_path, "r") as f:
                result = json.load(f)
            assert result == test_config


class TestListServers:
    """Test GET /mcp/servers."""

    @pytest.mark.asyncio
    async def test_list_servers_returns_all(self, client):
        """Test listing returns all configured servers."""
        response = await client.get("/mcp/servers")
        assert response.status_code == 200

        data = response.json()
        assert "servers" in data
        assert "total" in data
        assert "enabled" in data

        assert data["total"] == 2
        assert data["enabled"] == 1  # Only memory is enabled
        assert "memory" in data["servers"]
        assert "filesystem" in data["servers"]
        assert data["servers"]["memory"]["enabled"] is True
        assert data["servers"]["filesystem"]["enabled"] is False

    @pytest.mark.asyncio
    async def test_list_servers_empty_config(self, bypass_auth):
        """Test listing with no config returns empty."""
        with tempfile.TemporaryDirectory() as tmpdir:
            empty_path = Path(tmpdir) / "empty.json"

            import kollabor_engine.routes.mcp as mcp_routes  # type: ignore[import-not-found]

            original_config = mcp_routes.GLOBAL_MCP_CONFIG
            original_dir = mcp_routes.GLOBAL_MCP_DIR

            mcp_routes.GLOBAL_MCP_CONFIG = empty_path
            mcp_routes.GLOBAL_MCP_DIR = Path(tmpdir)

            from kollabor_engine.server import create_app  # type: ignore[import-not-found]

            app = create_app()

            try:
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as ac:
                    response = await ac.get("/mcp/servers")
                    assert response.status_code == 200
                    assert response.json()["total"] == 0
            finally:
                mcp_routes.GLOBAL_MCP_CONFIG = original_config
                mcp_routes.GLOBAL_MCP_DIR = original_dir


class TestAddServer:
    """Test POST /mcp/servers."""

    @pytest.mark.asyncio
    async def test_add_server_success(self, client):
        """Test adding a new server."""
        new_server = {
            "name": "github",
            "type": "stdio",
            "command": "npx -y @modelcontextprotocol/server-github",
            "enabled": False,
            "description": "GitHub integration",
            "env": {"GITHUB_TOKEN": "test"},
        }

        response = await client.post("/mcp/servers", json=new_server)
        assert response.status_code == 201

        data = response.json()
        assert data["ok"] is True
        assert data["name"] == "github"
        assert data["config"]["command"] == new_server["command"]
        assert data["config"]["env"]["GITHUB_TOKEN"] == "test"

    @pytest.mark.asyncio
    async def test_add_server_duplicate_returns_409(self, client):
        """Test adding duplicate server returns 409."""
        duplicate = {
            "name": "memory",  # Already exists
            "type": "stdio",
            "command": "some command",
        }

        response = await client.post("/mcp/servers", json=duplicate)
        assert response.status_code == 409

        data = response.json()
        assert "AlreadyExists" in data["detail"]["error"]

    @pytest.mark.asyncio
    async def test_add_server_invalid_type_returns_400(self, client):
        """Test adding server with non-stdio type returns 400."""
        invalid = {
            "name": "test",
            "type": "http",  # Only stdio supported
            "command": "test",
        }

        response = await client.post("/mcp/servers", json=invalid)
        assert response.status_code == 400
        assert "'stdio' type" in response.json()["detail"]


class TestRemoveServer:
    """Test DELETE /mcp/servers/{name}."""

    @pytest.mark.asyncio
    async def test_remove_server_success(self, client):
        """Test removing a server."""
        response = await client.delete("/mcp/servers/filesystem")
        assert response.status_code == 200

        data = response.json()
        assert data["ok"] is True
        assert data["name"] == "filesystem"
        assert "note" in data

        # Verify it's gone
        list_response = await client.get("/mcp/servers")
        servers = list_response.json()["servers"]
        assert "filesystem" not in servers

    @pytest.mark.asyncio
    async def test_remove_server_not_found_returns_404(self, client):
        """Test removing non-existent server returns 404."""
        response = await client.delete("/mcp/servers/nonexistent")
        assert response.status_code == 404

        data = response.json()
        assert "NotFound" in data["detail"]["error"]


class TestUpdateServer:
    """Test PUT /mcp/servers/{name}."""

    @pytest.mark.asyncio
    async def test_update_server_success(self, client):
        """Test updating an existing server."""
        update = {
            "command": "npx -y @modelcontextprotocol/server-memory --updated",
            "enabled": False,
            "description": "Updated description",
        }

        response = await client.put("/mcp/servers/memory", json=update)
        assert response.status_code == 200

        data = response.json()
        assert data["ok"] is True
        assert data["config"]["command"] == update["command"]
        assert data["config"]["enabled"] is False

    @pytest.mark.asyncio
    async def test_update_server_not_found_returns_404(self, client):
        """Test updating non-existent server returns 404."""
        response = await client.put(
            "/mcp/servers/nonexistent", json={"command": "test"}
        )
        assert response.status_code == 404


class TestSessionMCPStatus:
    """Test GET /sessions/{id}/mcp."""

    @pytest.mark.asyncio
    async def test_get_session_mcp_status(self, app):
        """Test getting MCP status for a session."""
        from kollabor_engine.server import get_session_registry

        # Create a mock session
        mock_session = MagicMock()
        mock_session.session_id = "sess_test123"
        mock_session.mcp_integration.mcp_servers = {
            "memory": {"enabled": True},
            "filesystem": {"enabled": False},
        }
        mock_session.mcp_integration.server_connections = {}
        mock_session.mcp_integration.tool_registry = {}

        registry = get_session_registry()
        registry["sess_test123"] = mock_session

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.get("/sessions/sess_test123/mcp")
            assert response.status_code == 200

            data = response.json()
            assert data["session_id"] == "sess_test123"
            assert "servers" in data
            assert "total_tools" in data

        # Cleanup
        registry.pop("sess_test123", None)

    @pytest.mark.asyncio
    async def test_get_session_mcp_not_found(self, app):
        """Test getting MCP status for non-existent session."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.get("/sessions/sess_nonexistent/mcp")
            assert response.status_code == 404


class TestServerTools:
    """Test GET /sessions/{id}/mcp/{server}/tools."""

    @pytest.mark.asyncio
    async def test_list_server_tools(self, app):
        """Test listing tools from a connected server."""
        from kollabor_engine.server import get_session_registry

        # Create mock session with tools
        mock_session = MagicMock()
        mock_session.session_id = "sess_tools"

        # Mock MCP integration with tools
        mock_mcp = MagicMock()
        mock_mcp.tool_registry = {
            "query_memory": {
                "server": "memory",
                "definition": {
                    "description": "Query memories",
                    "parameters": {"type": "object"},
                },
            },
            "create_file": {
                "server": "filesystem",
                "definition": {
                    "description": "Create file",
                    "parameters": {"type": "object"},
                },
            },
        }
        mock_session.mcp_integration = mock_mcp

        registry = get_session_registry()
        registry["sess_tools"] = mock_session

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.get("/sessions/sess_tools/mcp/memory/tools")
            assert response.status_code == 200

            data = response.json()
            assert data["server_name"] == "memory"
            assert len(data["tools"]) == 1
            assert data["tools"][0]["name"] == "query_memory"

        registry.pop("sess_tools", None)
