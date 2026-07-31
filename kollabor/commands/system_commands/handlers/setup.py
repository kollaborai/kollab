"""Setup command handler — guided first-run provider configuration.

Provides the `/setup` command, which opens a fullscreen wizard that walks a
new user through picking a provider, entering credentials, choosing a model,
optionally testing the connection, then saving and activating a profile.

The wizard (``SetupAltView``) does the profile creation/activation itself so
its success screen reflects real state. This handler orchestrates the push,
then translates the wizard's result into a chat message. Selecting OpenAI's
"sign in with ChatGPT" delegates to the existing `/login openai` OAuth flow.
"""

import logging

from kollabor_events.models import (
    CommandCategory,
    CommandDefinition,
    CommandMode,
    CommandResult,
    SlashCommand,
    SubcommandInfo,
)

from ..base import BaseCommandHandler

logger = logging.getLogger(__name__)


class SetupCommandHandler(BaseCommandHandler):
    """Handles `/setup` — the guided provider setup wizard."""

    MODAL_ACTIONS: set[str] = set()

    def __init__(
        self,
        command_registry,
        event_bus,
        profile_manager=None,
        llm_service=None,
        login_handler=None,
    ):
        super().__init__(command_registry, event_bus)
        self._profile_manager = profile_manager
        self._llm_service_override = llm_service
        self._login_handler = login_handler

    @property
    def profile_manager(self):
        if self._profile_manager is not None:
            return self._profile_manager
        return self.event_bus.get_service("profile_manager")

    @property
    def llm_service(self):
        if self._llm_service_override is not None:
            return self._llm_service_override
        return self.event_bus.get_service("llm_service")

    @property
    def renderer(self):
        return self.event_bus.get_service("renderer")

    def register_commands(self) -> None:
        setup_command = CommandDefinition(
            name="setup",
            description="Guided setup for a new LLM provider",
            handler=self.handle_setup,
            plugin_name="system",
            category=CommandCategory.SYSTEM,
            mode=CommandMode.INSTANT,
            aliases=["onboard", "wizard"],
            icon="[SET]",
            subcommands=[
                SubcommandInfo("", "", "Walk through configuring a provider"),
            ],
        )
        self.command_registry.register_command(setup_command)

    async def handle_setup(self, command: SlashCommand) -> CommandResult:
        """Open the setup wizard and act on its result."""
        try:
            from plugins.altview.setup_altview import SetupAltView
        except Exception as exc:  # pragma: no cover - import guard
            logger.error("Setup wizard unavailable: %s", exc)
            return CommandResult(
                success=False,
                message=f"Setup wizard unavailable: {exc}",
                display_type="error",
            )

        stack_mgr = self._get_stack_manager()
        if stack_mgr is None:
            return CommandResult(
                success=False,
                message="Setup UI unavailable (no AltView stack manager).",
                display_type="error",
            )

        view = SetupAltView()
        view.set_context(
            profile_manager=self.profile_manager,
            llm_service=self.llm_service,
            event_bus=self.event_bus,
        )

        # reuse=False: the wizard is one-shot -- run the fresh view we built
        # (and read its result flags below), never a cached prior instance.
        await stack_mgr.push(view, "setup", reuse=False)

        # OAuth branch — reuse the existing /login openai flow.
        if getattr(view, "result_launch_oauth", False):
            return await self._run_oauth_login()

        # Advanced branch — Azure / fully custom are configured in config.json.
        if getattr(view, "result_advanced", False):
            return CommandResult(
                success=True,
                message=(
                    "for Azure OpenAI or a fully-custom endpoint, add a profile under\n"
                    "kollabor.llm.profiles in ~/.kollab/config.json (see docs/providers.md),\n"
                    "then switch models and presets with /llm."
                ),
                display_type="info",
            )

        if getattr(view, "result_saved", False):
            return CommandResult(
                success=True,
                message=view.result_summary
                or f"provider configured — profile '{view.result_profile_name}' is active.",
                display_type="success",
            )

        if getattr(view, "result_cancelled", False):
            return CommandResult(
                success=False,
                message="setup cancelled — no changes made. run /setup any time.",
                display_type="info",
            )

        # Wizard exited without saving (e.g. an error the user backed out of).
        return CommandResult(
            success=False,
            message="setup did not complete. run /setup to try again, or edit ~/.kollab/config.json manually.",
            display_type="info",
        )

    def _get_stack_manager(self):
        """Resolve the AltView stack manager, creating it lazily if needed."""
        stack_mgr = self.event_bus.get_service("altview_stack_manager")
        if stack_mgr is not None:
            return stack_mgr
        try:
            from kollabor_tui.altview.stack_manager import AltViewStackManager

            stack_mgr = AltViewStackManager(self.event_bus, self.renderer)
            self.event_bus.register_service("altview_stack_manager", stack_mgr)
            return stack_mgr
        except Exception as exc:
            logger.error("Failed to create AltView stack manager: %s", exc)
            return None

    async def _run_oauth_login(self) -> CommandResult:
        """Delegate to the shared OpenAI OAuth login flow."""
        login_handler = self._login_handler
        if login_handler is None:
            try:
                from .login import LoginCommandHandler

                login_handler = LoginCommandHandler(
                    self.command_registry,
                    self.event_bus,
                    self.profile_manager,
                    self.llm_service,
                )
            except Exception as exc:
                logger.error("Login handler unavailable for OAuth setup: %s", exc)
                return CommandResult(
                    success=False,
                    message=f"OAuth setup unavailable: {exc}. Try /login openai.",
                    display_type="error",
                )
        return await login_handler._login_openai()
