"""#968: an agent reports the version it runs, and /restart restarts it in place.

Red-first: these name the contract before the code exists.
"""

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kollabor_agent.runtime import AgentRuntime

SHA = "a" * 40


class TestPresenceCarriesVersion:
    def test_own_row_carries_the_running_version_and_build(self):
        from kollabor.version import get_build_provenance, get_runtime_version

        row = AgentRuntime(
            name="coder", identity="alpha", project="/w", pid=os.getpid()
        ).to_presence_dict()
        assert row["version"] == get_runtime_version()
        assert row["build"] == get_build_provenance().sha

    def test_another_agents_version_survives_its_presence_file(self):
        restored = AgentRuntime.from_presence_dict(
            {"agent_name": "coder", "identity": "beta", "pid": 1,
             "version": "0.20.0+abc12345", "build": SHA}
        )
        assert (restored.version, restored.build) == ("0.20.0+abc12345", SHA)
        # Not this process: it keeps what that agent published, never ours.
        again = restored.to_presence_dict()
        assert (again["version"], again["build"]) == ("0.20.0+abc12345", SHA)

    def test_a_row_from_an_older_agent_has_no_version(self):
        restored = AgentRuntime.from_presence_dict({"agent_name": "coder", "identity": "old", "pid": 1})
        assert (restored.version, restored.build) == ("", "")


class TestRestartInPlace:
    def test_command_keeps_flags_and_stays_detached(self, monkeypatch):
        from kollabor import binary, daemon

        monkeypatch.setattr(binary, "kollab_argv", lambda: ["/opt/kollab"])
        argv = ["main.py", "--detached", "--as", "lapis", "--agent", "coder", "--llm", "local"]
        assert daemon.restart_command(argv) == [
            "/opt/kollab", "--detached", "--as", "lapis", "--agent", "coder", "--llm", "local",
        ]

    def test_env_keeps_the_detached_marker_and_names_the_conversation(self):
        from kollabor import daemon

        env = daemon.restart_env("sess-1", {"PATH": "/bin"})
        assert env["PATH"] == "/bin"
        assert env[daemon.DETACHED_CHILD_ENV] == "1"  # cli_main must not fork again
        assert env[daemon.RESUME_SESSION_ENV] == "sess-1"

    @pytest.mark.asyncio
    async def test_new_image_resumes_the_conversation_named_in_env(self):
        from kollabor import daemon

        resumed = []

        class State:
            async def resume_conversation(self, session_id):
                resumed.append(session_id)
                return {}

        env = {daemon.RESUME_SESSION_ENV: "sess-1"}
        assert await daemon.resume_after_restart(State(), env) is True
        assert resumed == ["sess-1"]
        assert daemon.RESUME_SESSION_ENV not in env  # a later restart names its own
        assert await daemon.resume_after_restart(State(), env) is False
        assert resumed == ["sess-1"]

    @pytest.mark.asyncio
    async def test_waits_for_the_turn_in_progress_and_never_cuts_it(self):
        from kollabor import daemon

        answers = iter([True, True, True, False])
        polls = []

        def busy():
            polls.append(1)
            return next(answers)

        assert await daemon.wait_until_idle(busy, poll=0.001, limit=5) is True
        assert len(polls) == 4  # it did not go on while the turn still ran
        assert await daemon.wait_until_idle(lambda: True, poll=0.001, limit=0.02) is False

    @pytest.mark.asyncio
    async def test_snapshot_save_failure_aborts_restart(self, monkeypatch):
        from kollabor_tui import display_tap
        from plugins.hub.plugin import HubPlugin

        plugin = HubPlugin.__new__(HubPlugin)
        plugin._identity = SimpleNamespace(identity="lapis")
        plugin.event_bus = MagicMock()
        plugin._self_restart_requested = True
        plugin.network_turn_open = lambda: False
        plugin._perform_self_restart = AsyncMock()

        async def no_watchdog(*args):
            return None

        plugin._self_restart_watchdog = no_watchdog
        published = []
        monkeypatch.setattr(display_tap, "publish_semantic", lambda *args, **kwargs: published.append((args, kwargs)))
        conversation = SimpleNamespace(
            current_session_id="session-1",
            messages=[{"role": "user", "content": "latest"}],
            save_conversation=MagicMock(side_effect=OSError("snapshot unavailable")),
        )
        llm = SimpleNamespace(is_processing=False, conversation_manager=conversation)

        await plugin._restart_when_idle(llm)

        plugin._perform_self_restart.assert_not_awaited()
        assert plugin._self_restart_requested is False
        assert published == [((plugin.event_bus, "agent_restart_failed"), {"identity": "lapis"})]


class TestRestartCommand:
    def _handler(self):
        from kollabor.commands.system_commands.handlers.system import SystemCommandHandler

        registry = MagicMock()
        state = SimpleNamespace(
            restart_agent=AsyncMock(return_value={"restarting": True, "waiting_for_turn": False}),
            restart_session=AsyncMock(),
        )
        bus = MagicMock()
        bus.get_service.side_effect = lambda name: state if name == "state_service" else None
        handler = SystemCommandHandler(command_registry=registry, event_bus=bus)
        handler.register_commands()
        commands = {c.args[0].name: c.args[0] for c in registry.register_command.call_args_list}
        return handler, commands, state

    def test_restart_no_longer_aliases_new_and_clear(self):
        _, commands, _ = self._handler()
        assert "restart" in commands and "new" in commands
        assert not {"new", "clear"} & set(commands["restart"].aliases)
        assert commands["new"].aliases == ["clear"]
        assert commands["restart"].handler != commands["new"].handler

    @pytest.mark.asyncio
    async def test_restart_restarts_the_agent_and_keeps_the_conversation(self):
        handler, commands, state = self._handler()
        result = await commands["restart"].handler(SimpleNamespace(name="restart", args=[], raw_input="/restart"))
        assert result.success
        state.restart_agent.assert_awaited_once()
        state.restart_session.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_new_still_clears_the_conversation(self):
        handler, commands, state = self._handler()
        state.restart_session = AsyncMock(
            return_value=SimpleNamespace(message_count=1, session_id="s", agent_name="default")
        )
        await commands["new"].handler(SimpleNamespace(name="new", args=[], raw_input="/new"))
        state.restart_session.assert_awaited_once()
        state.restart_agent.assert_not_awaited()
