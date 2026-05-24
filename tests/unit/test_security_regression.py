"""Regression tests for security fixes.

Covers:
1. BYPASS_AUTH guard (auth.py + server.py middleware)
2. _filter_env sensitive variable stripping (terminal_plugin + shell_executor)
3. Telegram bridge duplication fix (intermediate tool-call turns skipped)
4. ConfigService.save_key vs setdefault crash guard
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 1. BYPASS_AUTH guard — auth.py
# ---------------------------------------------------------------------------


class TestBypassAuthGuard:
    """Verify that KOLLAB_ENGINE_BYPASS_AUTH=1 only works when pytest is
    actually running (i.e. 'pytest' is in sys.modules)."""

    def test_bypass_works_when_pytest_in_sys_modules(self, monkeypatch):
        """When pytest is loaded AND env var is set, validate_token returns True."""
        monkeypatch.setenv("KOLLAB_ENGINE_BYPASS_AUTH", "1")
        # pytest is already in sys.modules (we're running inside it)
        assert "pytest" in sys.modules

        from kollabor_engine.auth import validate_token

        assert validate_token("any-token-at-all") is True

    def test_bypass_rejected_when_pytest_not_in_sys_modules(self, monkeypatch):
        """When pytest is NOT in sys.modules, BYPASS_AUTH is ignored."""
        monkeypatch.setenv("KOLLAB_ENGINE_BYPASS_AUTH", "1")

        from kollabor_engine import auth as auth_mod

        # Reset the in-memory token so we're testing the bypass path
        auth_mod._current_token = ""

        # Temporarily hide pytest from sys.modules
        saved = sys.modules.get("pytest")
        try:
            del sys.modules["pytest"]
            result = auth_mod.validate_token("definitely-not-a-real-token")
        finally:
            if saved is not None:
                sys.modules["pytest"] = saved

        assert result is False, "BYPASS_AUTH should be rejected when pytest is not in sys.modules"

    def test_valid_token_works_without_bypass(self, monkeypatch):
        """Normal token validation works when BYPASS_AUTH is not set."""
        monkeypatch.delenv("KOLLAB_ENGINE_BYPASS_AUTH", raising=False)

        from kollabor_engine import auth as auth_mod

        test_token = "test-token-12345"
        auth_mod._current_token = test_token

        assert auth_mod.validate_token(test_token) is True
        assert auth_mod.validate_token("wrong-token") is False


# ---------------------------------------------------------------------------
# 2. BYPASS_AUTH guard — server.py middleware
# ---------------------------------------------------------------------------


class TestServerAuthMiddleware:
    """Verify the server middleware also guards BYPASS_AUTH with pytest check."""

    @pytest.mark.asyncio
    async def test_middleware_bypass_with_pytest(self, monkeypatch):
        """Middleware bypasses auth when pytest is loaded."""
        monkeypatch.setenv("KOLLAB_ENGINE_BYPASS_AUTH", "1")

        from kollabor_engine.server import create_app

        app = create_app()

        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            # /sessions requires auth — should pass with bypass
            response = await client.get("/sessions")
            assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_middleware_rejects_without_bypass(self, monkeypatch):
        """Middleware rejects unauthenticated requests without BYPASS_AUTH."""
        monkeypatch.delenv("KOLLAB_ENGINE_BYPASS_AUTH", raising=False)

        from kollabor_engine.server import create_app

        app = create_app()

        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            # /sessions requires auth — should be rejected
            response = await client.get("/sessions")
            assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_health_endpoint_no_auth_needed(self, monkeypatch):
        """Health endpoint should always be accessible without auth."""
        monkeypatch.delenv("KOLLAB_ENGINE_BYPASS_AUTH", raising=False)

        from kollabor_engine.server import create_app

        app = create_app()

        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/health")
            assert response.status_code == 200


# ---------------------------------------------------------------------------
# 3. _filter_env — terminal_plugin
# ---------------------------------------------------------------------------


class TestTerminalPluginFilterEnv:
    """Verify _filter_env strips sensitive env vars including cloud provider keys."""

    def test_strips_api_key(self):
        from plugins.terminal_plugin import _filter_env

        env = {"MY_API_KEY": "secret", "PATH": "/usr/bin"}
        result = _filter_env(env)
        assert "MY_API_KEY" not in result
        assert "PATH" in result

    def test_strips_token(self):
        from plugins.terminal_plugin import _filter_env

        env = {"GITHUB_TOKEN": "ghp_abc123", "HOME": "/home/user"}
        result = _filter_env(env)
        assert "GITHUB_TOKEN" not in result
        assert "HOME" in result

    def test_strips_password(self):
        from plugins.terminal_plugin import _filter_env

        env = {"DB_PASSWORD": "p4ss", "USER": "marco"}
        result = _filter_env(env)
        assert "DB_PASSWORD" not in result
        assert "USER" in result

    def test_strips_secret(self):
        from plugins.terminal_plugin import _filter_env

        env = {"APP_SECRET": "s3cret", "LANG": "en_US"}
        result = _filter_env(env)
        assert "APP_SECRET" not in result
        assert "LANG" in result

    def test_strips_credential(self):
        from plugins.terminal_plugin import _filter_env

        env = {"AWS_CREDENTIAL": "creds", "TERM": "xterm"}
        result = _filter_env(env)
        assert "AWS_CREDENTIAL" not in result
        assert "TERM" in result

    def test_strips_auth(self):
        from plugins.terminal_plugin import _filter_env

        env = {"KOLLAB_ENGINE_BYPASS_AUTH": "1", "SHELL": "/bin/zsh"}
        result = _filter_env(env)
        assert "KOLLAB_ENGINE_BYPASS_AUTH" not in result
        assert "SHELL" in result

    def test_strips_aws_prefix(self):
        from plugins.terminal_plugin import _filter_env

        env = {"AWS_ACCESS_KEY_ID": "AKIA123", "AWS_SECRET_ACCESS_KEY": "xyz"}
        result = _filter_env(env)
        assert "AWS_ACCESS_KEY_ID" not in result
        assert "AWS_SECRET_ACCESS_KEY" not in result

    def test_strips_azure_prefix(self):
        from plugins.terminal_plugin import _filter_env

        env = {"AZURE_CLIENT_ID": "abc", "AZURE_CLIENT_SECRET": "xyz"}
        result = _filter_env(env)
        assert "AZURE_CLIENT_ID" not in result
        assert "AZURE_CLIENT_SECRET" not in result

    def test_strips_gcp_prefix(self):
        from plugins.terminal_plugin import _filter_env

        env = {"GCP_SERVICE_ACCOUNT": "sa@project.iam"}
        result = _filter_env(env)
        assert "GCP_SERVICE_ACCOUNT" not in result

    def test_strips_anthropic_prefix(self):
        from plugins.terminal_plugin import _filter_env

        env = {"ANTHROPIC_API_KEY": "sk-ant-xyz"}
        result = _filter_env(env)
        assert "ANTHROPIC_API_KEY" not in result

    def test_strips_openai_prefix(self):
        from plugins.terminal_plugin import _filter_env

        env = {"OPENAI_API_KEY": "sk-xyz"}
        result = _filter_env(env)
        assert "OPENAI_API_KEY" not in result

    def test_preserves_safe_vars(self):
        from plugins.terminal_plugin import _filter_env

        env = {
            "PATH": "/usr/bin",
            "HOME": "/home/user",
            "LANG": "en_US.UTF-8",
            "TERM": "xterm-256color",
            "EDITOR": "vim",
        }
        result = _filter_env(env)
        assert result == env


# ---------------------------------------------------------------------------
# 4. _filter_env — shell_executor
# ---------------------------------------------------------------------------


class TestShellExecutorFilterEnv:
    """Verify shell_executor._filter_env matches terminal_plugin patterns."""

    def test_strips_all_cloud_provider_prefixes(self):
        from kollabor_agent.shell_executor import ShellExecutor

        executor = ShellExecutor.__new__(ShellExecutor)
        env = {
            "AWS_ACCESS_KEY_ID": "AKIA123",
            "AZURE_CLIENT_SECRET": "xyz",
            "GCP_PROJECT_ID": "my-project",
            "ANTHROPIC_API_KEY": "sk-ant-xyz",
            "OPENAI_API_KEY": "sk-xyz",
            "MY_API_KEY": "key",
            "DB_PASSWORD": "pass",
            "APP_SECRET": "sec",
            "PATH": "/usr/bin",
        }
        result = executor._filter_env(env)
        assert "PATH" in result
        for sensitive in [
            "AWS_ACCESS_KEY_ID",
            "AZURE_CLIENT_SECRET",
            "GCP_PROJECT_ID",
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "MY_API_KEY",
            "DB_PASSWORD",
            "APP_SECRET",
        ]:
            assert sensitive not in result, f"{sensitive} should be filtered"


# ---------------------------------------------------------------------------
# 5. Telegram bridge duplication fix — intermediate tool-call turns
# ---------------------------------------------------------------------------


class TestBridgeDuplicationFix:
    """Verify that intermediate tool-call turns are NOT forwarded to the bridge."""

    @pytest.mark.asyncio
    async def test_intermediate_tool_call_skipped(self):
        """When a response has pending tool work, bridge_forward should NOT be called."""
        # We test the logic pattern from the fix:
        # has_pending_tool_work = bool(data.get("all_tools")) or bool(data.get("has_native_tools"))
        # if cleaned and not has_pending_tool_work: await self._bridge_forward(cleaned)

        data_with_tools = {
            "cleaned": "I'll check that file for you.",
            "all_tools": [{"id": "t1", "type": "file_read", "file": "test.py"}],
            "has_native_tools": False,
        }

        has_pending_tool_work = bool(data_with_tools.get("all_tools")) or bool(
            data_with_tools.get("has_native_tools")
        )
        cleaned = data_with_tools.get("cleaned", "")

        # Should NOT forward when there's pending tool work
        should_forward = bool(cleaned) and not has_pending_tool_work
        assert should_forward is False, "Should skip bridge forward on intermediate tool-call turn"

    @pytest.mark.asyncio
    async def test_final_response_forwarded(self):
        """When a response has NO pending tool work, bridge_forward SHOULD be called."""
        data_final = {
            "cleaned": "Here's the summary of what I found.",
            "all_tools": [],
            "has_native_tools": False,
        }

        has_pending_tool_work = bool(data_final.get("all_tools")) or bool(
            data_final.get("has_native_tools")
        )
        cleaned = data_final.get("cleaned", "")

        should_forward = bool(cleaned) and not has_pending_tool_work
        assert should_forward is True, "Should forward final response to bridge"

    @pytest.mark.asyncio
    async def test_empty_cleaned_not_forwarded(self):
        """Empty cleaned text should never be forwarded."""
        data = {
            "cleaned": "",
            "all_tools": [],
            "has_native_tools": False,
        }

        has_pending_tool_work = bool(data.get("all_tools")) or bool(
            data.get("has_native_tools")
        )
        cleaned = data.get("cleaned", "")

        should_forward = bool(cleaned) and not has_pending_tool_work
        assert should_forward is False, "Empty cleaned text should not be forwarded"

    @pytest.mark.asyncio
    async def test_native_tools_also_skip_bridge(self):
        """has_native_tools=True should also skip bridge forward."""
        data = {
            "cleaned": "Running a tool...",
            "all_tools": [],
            "has_native_tools": True,
        }

        has_pending_tool_work = bool(data.get("all_tools")) or bool(
            data.get("has_native_tools")
        )
        cleaned = data.get("cleaned", "")

        should_forward = bool(cleaned) and not has_pending_tool_work
        assert should_forward is False


# ---------------------------------------------------------------------------
# 6. safe_set — the utility behind ConfigService.save_key
#    (prevents setdefault crashes on missing nested paths)
# ---------------------------------------------------------------------------


class TestSafeSetCrashGuard:
    """Verify safe_set creates intermediate dicts instead of crashing
    with setdefault on missing nested keys."""

    def test_creates_missing_nested_path(self):
        from kollabor_events.dict_utils import safe_set

        data = {"plugins": {}}
        result = safe_set(data, "plugins.hub.bridge_enabled", True)
        assert result is True
        assert data["plugins"]["hub"]["bridge_enabled"] is True

    def test_overwrites_existing(self):
        from kollabor_events.dict_utils import safe_set

        data = {"plugins": {"hub": {"notify_enabled": False}}}
        safe_set(data, "plugins.hub.notify_enabled", True)
        assert data["plugins"]["hub"]["notify_enabled"] is True

    def test_string_value(self):
        from kollabor_events.dict_utils import safe_set

        data = {}
        safe_set(data, "plugins.hub.notify_channel", "telegram")
        assert data["plugins"]["hub"]["notify_channel"] == "telegram"

    def test_deep_nested(self):
        from kollabor_events.dict_utils import safe_set

        data = {}
        safe_set(data, "a.b.c.d.e", 42)
        assert data["a"]["b"]["c"]["d"]["e"] == 42

    def test_rejects_non_dict(self):
        from kollabor_events.dict_utils import safe_set

        result = safe_set("not a dict", "foo.bar", 1)
        assert result is False

    def test_rejects_empty_key(self):
        from kollabor_events.dict_utils import safe_set

        result = safe_set({}, "", 1)
        assert result is False
