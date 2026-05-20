"""Regression tests: every registered slash-command handler must accept
a SlashCommand object, not a raw string.

The bug this guards against: a handler typed its parameter as `str` and
called `.strip()` / `.lower()` on it, but the executor always passes a
SlashCommand object -- causing AttributeError at runtime.

Rules for every test
--------------------
1. Pass a real SlashCommand object (built with _make_slash_command).
2. Wrap the call in a try/except for AttributeError and TypeError so the
   test fails with a clear message if the handler tries to call string
   methods on a SlashCommand.
3. Assert the return value is a CommandResult or str -- never None.
4. Do NOT mock the handler method itself. The whole point is to call the
   actual handler code with a SlashCommand.

Handlers already covered in other test files (skipped here):
- ContextCommandHandler  -> tests/unit/commands/test_context_command.py
- ResumeConversationPlugin.handle_sessions / handle_branch
                         -> tests/unit/commands/test_resume_command.py
"""

import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

# Project root -- needed for plugins that call Path.cwd() at import/init time.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)
))))


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_slash_command(*args, name="test"):
    """Build a SlashCommand with the given args list."""
    from kollabor_events.models import SlashCommand

    cmd = SlashCommand.__new__(SlashCommand)
    cmd.name = name
    cmd.args = list(args)
    cmd.raw_input = f"/{name} " + " ".join(args) if args else f"/{name}"
    cmd.parameters = {}
    return cmd


def _run(coro):
    """Run a coroutine to completion in an isolated event loop."""
    return asyncio.run(coro)


def _make_event_bus(extra_services=None):
    """Return a MagicMock event bus.

    state_service is set to None by default because many handlers do
    `await state_service.get_*()` — a MagicMock would succeed but is
    not awaitable, which raises TypeError. Returning None triggers the
    graceful 'not available' early-return instead.
    """
    extra = extra_services or {}
    defaults = {"state_service": None}
    defaults.update(extra)

    eb = MagicMock()

    def _get_service(name):
        if name in defaults:
            return defaults[name]
        return MagicMock()

    eb.get_service.side_effect = _get_service
    return eb


def _assert_result(result):
    """Assert the result is a CommandResult or str and return it."""
    from kollabor_events.models import CommandResult

    assert result is not None, "Handler returned None"
    assert isinstance(result, (CommandResult, str)), (
        f"Expected CommandResult or str, got {type(result).__name__}: {result!r}"
    )
    return result


def _safe_run(handler_coro):
    """Run handler; fail immediately on AttributeError / TypeError."""
    try:
        return _run(handler_coro)
    except (AttributeError, TypeError) as exc:
        raise AssertionError(
            f"Handler crashed on SlashCommand object -- likely called a str "
            f"method (.strip/.lower/etc) on the command arg: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# System command handlers
# ---------------------------------------------------------------------------


class TestSystemCommandHandler(unittest.TestCase):
    """Tests for SystemCommandHandler (/help, /config, /status, /permissions,
    /version, /restart)."""

    def _make_handler(self, extra_services=None):
        from kollabor.commands.system_commands.handlers.system import (
            SystemCommandHandler,
        )

        eb = _make_event_bus(extra_services)
        return SystemCommandHandler(
            command_registry=MagicMock(),
            event_bus=eb,
        )

    # /help
    def test_help_no_args(self):
        handler = self._make_handler()
        result = _safe_run(handler.handle_help(_make_slash_command()))
        _assert_result(result)

    def test_help_with_arg(self):
        handler = self._make_handler()
        result = _safe_run(handler.handle_help(_make_slash_command("save")))
        _assert_result(result)

    # /config -- opens AltView; with no renderer/stack_mgr it will fail
    # gracefully and return a CommandResult(success=False)
    def test_config_no_deps(self):
        handler = self._make_handler(
            extra_services={
                "altview_stack_manager": None,
                "renderer": None,
                "state_service": None,
            }
        )
        result = _safe_run(handler.handle_config(_make_slash_command()))
        _assert_result(result)

    # /status -- state_service=None causes early CommandResult(success=False)
    def test_status_no_state_service(self):
        handler = self._make_handler()
        result = _safe_run(handler.handle_status(_make_slash_command()))
        _assert_result(result)
        from kollabor_events.models import CommandResult

        assert isinstance(result, CommandResult)
        assert not result.success  # no state_service -> graceful failure

    def test_doctor_no_state_service_reports_blocked(self):
        handler = self._make_handler()
        result = _safe_run(handler.handle_doctor(_make_slash_command()))
        _assert_result(result)
        from kollabor_events.models import CommandResult

        assert isinstance(result, CommandResult)
        assert not result.success
        assert "verdict: blocked" in result.message
        assert "state service" in result.message
        assert "proof read" in result.message

    def test_doctor_ready_with_state_service(self):
        class StateService:
            async def get_active_profile(self):
                return SimpleNamespace(
                    name="openai-oauth",
                    provider="openai",
                    model="gpt-test",
                )

            async def get_permission_state(self):
                return SimpleNamespace(approval_mode="DEFAULT")

            async def get_mcp_state(self):
                return SimpleNamespace(
                    total_servers=1,
                    connected_servers=1,
                    total_tools=3,
                )

            async def get_hub_state(self):
                return SimpleNamespace(my_identity="koordinator", peer_count=0)

            async def get_active_agent(self):
                return SimpleNamespace(name="coder")

            async def get_system_info(self):
                return SimpleNamespace(daemon_pid=0, daemon_uptime_seconds=0)

        services = {
            "state_service": StateService(),
            "renderer": object(),
            "command_registry": object(),
            "permission_manager": object(),
            "llm_service": object(),
        }
        handler = self._make_handler(extra_services=services)
        result = _safe_run(handler.handle_doctor(_make_slash_command()))
        _assert_result(result)
        from kollabor_events.models import CommandResult

        assert isinstance(result, CommandResult)
        assert result.success
        assert "verdict: ready" in result.message
        assert "profile" in result.message
        assert "proof read" in result.message

    def test_doctor_proof_mode_checks_xml_native_and_mock_mcp_contracts(self):
        class StateService:
            async def get_active_profile(self):
                return SimpleNamespace(name="test", provider="test", model="model")

            async def get_permission_state(self):
                return SimpleNamespace(approval_mode="DEFAULT")

            async def get_mcp_state(self):
                return SimpleNamespace(
                    total_servers=0,
                    connected_servers=0,
                    total_tools=0,
                )

            async def get_hub_state(self):
                return SimpleNamespace(my_identity="koordinator", peer_count=0)

            async def get_active_agent(self):
                return SimpleNamespace(name="coder")

            async def get_system_info(self):
                return SimpleNamespace(daemon_pid=0, daemon_uptime_seconds=0)

        services = {
            "state_service": StateService(),
            "renderer": object(),
            "command_registry": object(),
            "permission_manager": object(),
            "llm_service": object(),
        }
        handler = self._make_handler(extra_services=services)
        result = _safe_run(handler.handle_doctor(_make_slash_command("proof")))
        _assert_result(result)
        from kollabor_events.models import CommandResult

        assert isinstance(result, CommandResult)
        assert result.success
        assert "proof xml" in result.message
        assert "proof native" in result.message
        assert "proof mock-mcp" in result.message

    # /permissions -- permission_manager=None -> early CommandResult(success=False)
    def test_permissions_no_manager(self):
        handler = self._make_handler(extra_services={"permission_manager": None})
        result = _safe_run(handler.handle_permissions(_make_slash_command("show")))
        _assert_result(result)
        from kollabor_events.models import CommandResult

        assert isinstance(result, CommandResult)
        assert not result.success

    # /version -- pure computation, always succeeds
    def test_version(self):
        handler = self._make_handler()
        result = _safe_run(handler.handle_version(_make_slash_command()))
        _assert_result(result)
        from kollabor_events.models import CommandResult

        assert isinstance(result, CommandResult)
        assert result.success

    # /restart -- state_service=None -> early CommandResult(success=False)
    def test_restart_no_state_service(self):
        handler = self._make_handler()
        result = _safe_run(handler.handle_restart(_make_slash_command()))
        _assert_result(result)
        from kollabor_events.models import CommandResult

        assert isinstance(result, CommandResult)
        assert not result.success


# ---------------------------------------------------------------------------
# SkillCommandHandler (/skills)
# ---------------------------------------------------------------------------


class TestSkillCommandHandler(unittest.TestCase):
    def _make_handler(self, agent_manager=None):
        from kollabor.commands.system_commands.handlers.skills import (
            SkillCommandHandler,
        )

        eb = _make_event_bus()
        return SkillCommandHandler(
            command_registry=MagicMock(),
            event_bus=eb,
            agent_manager=agent_manager,
        )

    def test_skills_no_agent_manager_returns_result(self):
        handler = self._make_handler(agent_manager=None)
        result = _safe_run(handler.handle_skill(_make_slash_command()))
        _assert_result(result)

    def test_skills_with_agent_manager(self):
        am = MagicMock()
        am.list_skills.return_value = []
        handler = self._make_handler(agent_manager=am)
        result = _safe_run(handler.handle_skill(_make_slash_command()))
        _assert_result(result)


# ---------------------------------------------------------------------------
# AgentCommandHandler (/agent)
# ---------------------------------------------------------------------------


class TestAgentCommandHandler(unittest.TestCase):
    def _make_handler(self, agent_manager=None):
        from kollabor.commands.system_commands.handlers.agent import (
            AgentCommandHandler,
        )

        eb = _make_event_bus()
        return AgentCommandHandler(
            command_registry=MagicMock(),
            event_bus=eb,
            agent_manager=agent_manager,
        )

    def test_agent_no_agent_manager(self):
        handler = self._make_handler(agent_manager=None)
        result = _safe_run(handler.handle_agent(_make_slash_command()))
        _assert_result(result)

    def test_agent_with_agent_manager(self):
        am = MagicMock()
        am.get_current_agent.return_value = None
        am.list_agents.return_value = []
        handler = self._make_handler(agent_manager=am)
        result = _safe_run(handler.handle_agent(_make_slash_command()))
        _assert_result(result)


# ---------------------------------------------------------------------------
# ProfileCommandHandler (/profile)
# ---------------------------------------------------------------------------


class TestProfileCommandHandler(unittest.TestCase):
    def _make_handler(self, profile_manager=None):
        from kollabor.commands.system_commands.handlers.profile import (
            ProfileCommandHandler,
        )

        eb = _make_event_bus()
        return ProfileCommandHandler(
            command_registry=MagicMock(),
            event_bus=eb,
            profile_manager=profile_manager,
        )

    def test_profile_no_profile_manager(self):
        handler = self._make_handler(profile_manager=None)
        result = _safe_run(handler.handle_profile(_make_slash_command()))
        _assert_result(result)

    def test_profile_list(self):
        pm = MagicMock()
        pm.list_profiles.return_value = []
        pm.get_current_profile.return_value = None
        handler = self._make_handler(profile_manager=pm)
        result = _safe_run(handler.handle_profile(_make_slash_command("list")))
        _assert_result(result)


# ---------------------------------------------------------------------------
# ModelCommandHandler (/model)
# ---------------------------------------------------------------------------


class TestModelCommandHandler(unittest.TestCase):
    def _make_handler(self, profile_manager=None):
        from kollabor.commands.system_commands.handlers.model import (
            ModelCommandHandler,
        )

        eb = _make_event_bus()
        return ModelCommandHandler(
            command_registry=MagicMock(),
            event_bus=eb,
            profile_manager=profile_manager,
        )

    def test_model_no_profile_manager(self):
        handler = self._make_handler(profile_manager=None)
        result = _safe_run(handler.handle_model(_make_slash_command()))
        _assert_result(result)

    def test_model_with_profile_manager(self):
        pm = MagicMock()
        pm.get_current_profile.return_value = MagicMock(model="gpt-4o", provider="openai")
        pm.list_profiles.return_value = []
        handler = self._make_handler(profile_manager=pm)
        result = _safe_run(handler.handle_model(_make_slash_command()))
        _assert_result(result)


# ---------------------------------------------------------------------------
# LoginCommandHandler (/login)
# ---------------------------------------------------------------------------


class TestLoginCommandHandler(unittest.TestCase):
    def _make_handler(self):
        from kollabor.commands.system_commands.handlers.login import (
            LoginCommandHandler,
        )

        eb = _make_event_bus()
        return LoginCommandHandler(
            command_registry=MagicMock(),
            event_bus=eb,
        )

    def test_login_no_args_returns_usage(self):
        handler = self._make_handler()
        result = _safe_run(handler.handle_login(_make_slash_command()))
        _assert_result(result)
        from kollabor_events.models import CommandResult

        assert isinstance(result, CommandResult)
        assert not result.success  # no subcommand -> usage error

    def test_login_status_subcommand(self):
        handler = self._make_handler()
        result = _safe_run(handler.handle_login(_make_slash_command("status")))
        _assert_result(result)


# ---------------------------------------------------------------------------
# DirectoryCommandHandler (/cd)
# ---------------------------------------------------------------------------


class TestDirectoryCommandHandler(unittest.TestCase):
    def _make_handler(self):
        from kollabor.commands.system_commands.handlers.directory import (
            DirectoryCommandHandler,
        )

        eb = _make_event_bus()
        return DirectoryCommandHandler(
            command_registry=MagicMock(),
            event_bus=eb,
            config_manager=MagicMock(),
        )

    def test_cd_no_args(self):
        handler = self._make_handler()
        result = _safe_run(handler.handle_cd(_make_slash_command()))
        _assert_result(result)

    def test_cd_with_path(self):
        import tempfile

        handler = self._make_handler()
        with tempfile.TemporaryDirectory() as tmpdir:
            result = _safe_run(handler.handle_cd(_make_slash_command(tmpdir)))
            _assert_result(result)

    def test_cd_invalid_path(self):
        handler = self._make_handler()
        result = _safe_run(handler.handle_cd(_make_slash_command("/no/such/path/exists")))
        _assert_result(result)


# ---------------------------------------------------------------------------
# MCPCommandHandler (/mcp)
# ---------------------------------------------------------------------------


class TestMCPCommandHandler(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # MCPCommandHandler.__init__ calls MCPManager() which calls Path.cwd().
        # Switch to project root before importing/instantiating so the manager
        # can locate its settings example file (even if cwd is already broken).
        os.chdir(_PROJECT_ROOT)

    def _make_handler(self, mcp_integration=None, app=None):
        from kollabor.commands.mcp_command import MCPCommandHandler

        return MCPCommandHandler(
            command_registry=MagicMock(),
            mcp_integration=mcp_integration,
            app=app,
        )

    def test_mcp_no_integration_no_args(self):
        handler = self._make_handler()
        result = _safe_run(handler.handle_mcp(_make_slash_command()))
        _assert_result(result)
        from kollabor_events.models import CommandResult

        assert isinstance(result, CommandResult)
        assert not result.success  # no mcp_integration -> graceful failure

    def test_mcp_no_integration_show(self):
        handler = self._make_handler()
        result = _safe_run(handler.handle_mcp(_make_slash_command("show")))
        _assert_result(result)

    def test_mcp_reload_uses_state_service(self):
        state_service = MagicMock()
        state_service.reload_mcp_servers = AsyncMock(
            return_value={"configured": 2, "discovered": 2, "reconnected": 1}
        )
        app = MagicMock()
        app.event_bus = _make_event_bus({"state_service": state_service})

        handler = self._make_handler(mcp_integration=MagicMock(), app=app)
        result = _safe_run(handler.handle_mcp(_make_slash_command("reload")))

        _assert_result(result)
        state_service.reload_mcp_servers.assert_awaited_once()
        assert result.success
        assert "Reconnected 1 server(s)." in result.message

    def test_mcp_reload_direct_fallback(self):
        mcp_integration = MagicMock()
        mcp_integration.reload_mcp_servers = AsyncMock(
            return_value={"configured": 1, "discovered": 1, "reconnected": 0}
        )

        handler = self._make_handler(mcp_integration=mcp_integration)
        result = _safe_run(handler.handle_mcp(_make_slash_command("reload")))

        _assert_result(result)
        mcp_integration.reload_mcp_servers.assert_awaited_once()
        assert result.success
        assert "Loaded 1 configured server(s)." in result.message


# ---------------------------------------------------------------------------
# Plugin: HubPlugin (/hub)
# ---------------------------------------------------------------------------


class TestHubPlugin(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # plugins.hub.models calls Path.cwd() at module import time to load gem
        # identities from YAML. Ensure cwd is the project root before the first
        # import so the YAML can be found.
        os.chdir(_PROJECT_ROOT)

    def _make_plugin(self):
        from plugins.hub.plugin import HubPlugin

        eb = _make_event_bus()
        renderer = MagicMock()
        config = MagicMock()
        config.get.return_value = False
        return HubPlugin(
            name="hub",
            event_bus=eb,
            renderer=renderer,
            config=config,
        )

    def test_hub_command_no_args(self):
        plugin = self._make_plugin()
        result = _safe_run(plugin._handle_hub_command(_make_slash_command(name="hub")))
        _assert_result(result)

    def test_hub_command_status_subcommand(self):
        plugin = self._make_plugin()
        cmd = _make_slash_command("status", name="hub")
        result = _safe_run(plugin._handle_hub_command(cmd))
        _assert_result(result)


# ---------------------------------------------------------------------------
# Plugin: ContextCompactionPlugin (/compact)
# ---------------------------------------------------------------------------


def _compact_config_get(key, default=None):
    """Side-effect for config.get() in compact plugin tests.

    The handler calls int(config.get("...token_threshold_k", 0)) so we must
    return values that are safe to pass through int(). Return 0 for numeric
    keys, False/True for bool keys, and default for everything else.
    """
    int_keys = {
        "plugins.context_compaction.token_threshold_k",
        "plugins.context_compaction.summary_max_tokens",
        "plugins.context_compaction.min_messages_before_compact",
    }
    bool_keys = {
        "plugins.context_compaction.enabled",
        "plugins.context_compaction.auto_compact",
    }
    if key in int_keys:
        return 0
    if key in bool_keys:
        return False
    return default


class TestContextCompactionPlugin(unittest.TestCase):
    def _make_plugin(self):
        from plugins.context_compaction_plugin import ContextCompactionPlugin

        eb = _make_event_bus()
        renderer = MagicMock()
        config = MagicMock()
        config.get.side_effect = _compact_config_get
        return ContextCompactionPlugin(
            name="compact",
            event_bus=eb,
            renderer=renderer,
            config=config,
        )

    def test_compact_no_args(self):
        plugin = self._make_plugin()
        result = _safe_run(plugin._handle_compact_command(_make_slash_command(name="compact")))
        _assert_result(result)

    def test_compact_status_subcommand(self):
        plugin = self._make_plugin()
        result = _safe_run(plugin._handle_compact_command(_make_slash_command("status", name="compact")))
        _assert_result(result)


# ---------------------------------------------------------------------------
# Plugin: SaveConversationPlugin (/save)
# ---------------------------------------------------------------------------


class TestSaveConversationPlugin(unittest.TestCase):
    def _make_plugin(self):
        from plugins.save_conversation_plugin import SaveConversationPlugin

        eb = _make_event_bus()
        renderer = MagicMock()
        config = MagicMock()
        config.get.return_value = "transcript"
        plugin = SaveConversationPlugin(
            name="save",
            event_bus=eb,
            renderer=renderer,
            config=config,
        )
        # config is set via initialize(), but _handle_save_command reads
        # self.config which is set by initialize(). We set it directly here
        # to avoid running the full async initialize path.
        plugin.config = config
        return plugin

    def test_save_no_args_returns_result(self):
        plugin = self._make_plugin()
        # config.get() will be called; state_service for save might be needed.
        # Without llm_service / state_service the plugin returns an error str.
        result = _safe_run(plugin._handle_save_command(_make_slash_command(name="save")))
        _assert_result(result)

    def test_save_transcript_format(self):
        plugin = self._make_plugin()
        result = _safe_run(plugin._handle_save_command(_make_slash_command("transcript", name="save")))
        _assert_result(result)

    def test_initialize_wires_event_bus_for_state_service(self):
        from plugins.save_conversation_plugin import SaveConversationPlugin

        state_service = MagicMock()
        state_service.save_conversation = AsyncMock(return_value="hello")
        eb = _make_event_bus({"state_service": state_service})
        config = MagicMock()

        def _get_config(key, default=None):
            values = {
                "plugins.save_conversation.default_format": "transcript",
                "plugins.save_conversation.default_destination": "clipboard",
                "plugins.save_conversation.auto_timestamp": True,
                "plugins.save_conversation.output_directory": "logs/transcripts",
            }
            return values.get(key, default)

        config.get.side_effect = _get_config
        command_registry = MagicMock()
        plugin = SaveConversationPlugin(name="save")
        plugin._copy_to_clipboard = MagicMock()

        _run(
            plugin.initialize(
                event_bus=eb,
                config=config,
                command_registry=command_registry,
                llm_service=MagicMock(),
            )
        )
        result = _run(plugin._handle_save_command(_make_slash_command(name="save")))

        self.assertEqual(result, "Conversation copied to clipboard")
        state_service.save_conversation.assert_awaited_once_with("transcript")
        plugin._copy_to_clipboard.assert_called_once_with("hello")


# ---------------------------------------------------------------------------
# Plugin: TerminalPlugin (/terminal / /tmux)
# ---------------------------------------------------------------------------


class TestTerminalPlugin(unittest.TestCase):
    def _make_plugin(self):
        # The class inside terminal_plugin.py is TmuxPlugin, not TerminalPlugin.
        from plugins.terminal_plugin import TmuxPlugin

        eb = _make_event_bus()
        renderer = MagicMock()
        config = MagicMock()
        config.get.return_value = None
        return TmuxPlugin(
            name="terminal",
            event_bus=eb,
            renderer=renderer,
            config=config,
        )

    @unittest.skip("requires live tmux session")
    def test_terminal_no_args_requires_tmux(self):
        plugin = self._make_plugin()
        result = _safe_run(plugin._handle_tmux_command(_make_slash_command(name="terminal")))
        _assert_result(result)

    def test_terminal_list_subcommand(self):
        plugin = self._make_plugin()
        result = _safe_run(plugin._handle_tmux_command(_make_slash_command("list", name="terminal")))
        _assert_result(result)

    def test_terminal_new_subcommand(self):
        plugin = self._make_plugin()
        result = _safe_run(plugin._handle_tmux_command(_make_slash_command("new", "test-session", name="terminal")))
        _assert_result(result)


# ---------------------------------------------------------------------------
# Plugin: DeepThoughtPlugin (/deepthought)
# ---------------------------------------------------------------------------


def _dt_config_get(key, default=None):
    """Side-effect for config.get() in deep_thought plugin tests.

    _get_dt_config() calls self.config.get("plugins.deep_thought", {}) and
    then calls .get("enabled", ...) on the result, so we must return a dict
    for that key, not a scalar.
    """
    if key == "plugins.deep_thought":
        return {"enabled": False, "always_on": False, "methodologies": []}
    return default


class TestDeepThoughtPlugin(unittest.TestCase):
    def _make_plugin(self):
        from plugins.deep_thought.plugin import DeepThoughtPlugin

        eb = _make_event_bus()
        renderer = MagicMock()
        config = MagicMock()
        config.get.side_effect = _dt_config_get
        return DeepThoughtPlugin(
            name="deep_thought",
            event_bus=eb,
            renderer=renderer,
            config=config,
        )

    def test_deep_thought_no_args(self):
        plugin = self._make_plugin()
        result = _safe_run(plugin._handle_command(_make_slash_command(name="think")))
        _assert_result(result)

    def test_deep_thought_status_subcommand(self):
        plugin = self._make_plugin()
        result = _safe_run(plugin._handle_command(_make_slash_command("status", name="think")))
        _assert_result(result)


# ---------------------------------------------------------------------------
# Plugin: AgentOrchestratorPlugin (/sub)
# ---------------------------------------------------------------------------


class TestAgentOrchestratorPlugin(unittest.TestCase):
    def _make_plugin(self):
        from plugins.agent_orchestrator.plugin import AgentOrchestratorPlugin

        eb = _make_event_bus()
        renderer = MagicMock()
        config = MagicMock()
        config.get.return_value = None
        plugin = AgentOrchestratorPlugin(
            name="agent_orchestrator",
            event_bus=eb,
            renderer=renderer,
            config=config,
        )
        # orchestrator is None by default (not initialized) -> _cmd_list
        # returns CommandResult(success=False, message="not initialized")
        return plugin

    def test_sub_no_args_returns_result(self):
        plugin = self._make_plugin()
        result = _safe_run(plugin._handle_sub_command(_make_slash_command(name="sub")))
        _assert_result(result)

    def test_sub_list_subcommand(self):
        plugin = self._make_plugin()
        result = _safe_run(plugin._handle_sub_command(_make_slash_command("list", name="sub")))
        _assert_result(result)

    def test_sub_help_subcommand(self):
        plugin = self._make_plugin()
        result = _safe_run(plugin._handle_sub_command(_make_slash_command("help", name="sub")))
        _assert_result(result)
        from kollabor_events.models import CommandResult

        assert isinstance(result, CommandResult)
        assert result.success

    def test_sub_unknown_subcommand(self):
        plugin = self._make_plugin()
        result = _safe_run(plugin._handle_sub_command(_make_slash_command("bogus", name="sub")))
        _assert_result(result)
        from kollabor_events.models import CommandResult

        assert isinstance(result, CommandResult)
        assert not result.success


if __name__ == "__main__":
    unittest.main()
