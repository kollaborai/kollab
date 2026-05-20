"""Plugin facade for SystemCommands.

Provides backward compatibility with existing code that imports from
core.commands.system_commands while delegating to the new handler classes.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Set, cast

if TYPE_CHECKING:
    from .handlers import (
        AgentCommandHandler,
        ContextCommandHandler,
        DirectoryCommandHandler,
        LoginCommandHandler,
        ModelCommandHandler,
        ProfileCommandHandler,
        SkillCommandHandler,
        SystemCommandHandler,
    )

from kollabor_events.models import CommandResult, Event, SlashCommand

from .base import BaseCommandHandler

logger = logging.getLogger(__name__)


class SystemCommandsPlugin:
    """Facade for backward compatibility.

    Delegates commands to the appropriate handler classes.
    """

    # Class-level type annotations for mypy (body of untyped __init__ is skipped)
    _agent_handler: AgentCommandHandler | None
    _profile_handler: ProfileCommandHandler | None
    _skill_handler: SkillCommandHandler | None
    _system_handler: SystemCommandHandler | None
    _model_handler: ModelCommandHandler | None
    _directory_handler: DirectoryCommandHandler | None
    _login_handler: LoginCommandHandler | None
    _context_handler: ContextCommandHandler | None

    def __init__(
        self,
        command_registry,
        event_bus,
        config_manager=None,
        agent_manager=None,
        profile_manager=None,
        llm_service=None,
        permission_manager=None,
        agent_command_handler=None,
        profile_command_handler=None,
        skill_command_handler=None,
        system_command_handler=None,
        model_command_handler=None,
        directory_command_handler=None,
        login_command_handler=None,
    ):
        """Initialize plugin facade with handlers.

        Args:
            command_registry: Command registry instance.
            event_bus: Event bus for service lookup.
            config_manager: Configuration manager instance.
            agent_manager: Agent manager instance.
            profile_manager: Profile manager instance.
            llm_service: LLM service instance.
            permission_manager: Permission manager instance.
            agent_command_handler: Optional AgentCommandHandler instance.
            profile_command_handler: Optional ProfileCommandHandler instance.
            skill_command_handler: Optional SkillCommandHandler instance.
            system_command_handler: Optional SystemCommandHandler instance.
            model_command_handler: Optional ModelCommandHandler instance.
            directory_command_handler: Optional DirectoryCommandHandler instance.
        """
        self.name = "system"
        self.command_registry = command_registry
        self.event_bus = event_bus
        self.config_manager = config_manager
        self.agent_manager = agent_manager
        self.profile_manager = profile_manager
        self.llm_service = llm_service
        self.permission_manager = permission_manager

        # Lazy init - create handlers on first use or explicitly provided
        self._agent_handler: AgentCommandHandler | None = agent_command_handler
        self._profile_handler: ProfileCommandHandler | None = profile_command_handler
        self._skill_handler: SkillCommandHandler | None = skill_command_handler
        self._system_handler: SystemCommandHandler | None = system_command_handler
        self._model_handler: ModelCommandHandler | None = model_command_handler
        self._directory_handler: DirectoryCommandHandler | None = (
            directory_command_handler
        )
        self._login_handler: LoginCommandHandler | None = login_command_handler
        self._context_handler: ContextCommandHandler | None = None

        # Initialize handlers if not provided
        self._init_handlers()

    def _init_handlers(self):
        """Initialize handlers if not already provided."""
        if (
            self._agent_handler is None
            or self._profile_handler is None
            or self._skill_handler is None
        ):
            from .handlers import (
                AgentCommandHandler,
                ContextCommandHandler,
                DirectoryCommandHandler,
                LoginCommandHandler,
                ModelCommandHandler,
                ProfileCommandHandler,
                SkillCommandHandler,
                SystemCommandHandler,
            )

            if self._agent_handler is None:
                self._agent_handler = AgentCommandHandler(
                    self.command_registry,
                    self.event_bus,
                    self.agent_manager,
                    self.profile_manager,
                    self.llm_service,
                )

            if self._profile_handler is None:
                self._profile_handler = ProfileCommandHandler(
                    self.command_registry,
                    self.event_bus,
                    self.profile_manager,
                    self.llm_service,
                )

            if self._skill_handler is None:
                self._skill_handler = SkillCommandHandler(
                    self.command_registry,
                    self.event_bus,
                    self.agent_manager,
                    self.llm_service,
                )

            if self._system_handler is None:
                self._system_handler = SystemCommandHandler(
                    self.command_registry,
                    self.event_bus,
                )

            if self._model_handler is None:
                self._model_handler = ModelCommandHandler(
                    self.command_registry,
                    self.event_bus,
                    self.profile_manager,
                    self.llm_service,
                )

            if self._directory_handler is None:
                self._directory_handler = DirectoryCommandHandler(
                    self.command_registry,
                    self.event_bus,
                    self.config_manager,
                )

            if self._login_handler is None:
                self._login_handler = LoginCommandHandler(
                    self.command_registry,
                    self.event_bus,
                    self.profile_manager,
                    self.llm_service,
                )

            if not hasattr(self, "_context_handler") or self._context_handler is None:
                self._context_handler = ContextCommandHandler(
                    self.command_registry,
                    self.event_bus,
                )

    @property
    def MODAL_ACTIONS(self) -> Set[str]:
        """Aggregate MODAL_ACTIONS from all handlers."""
        actions = set()
        if self._agent_handler:
            actions.update(self._agent_handler.MODAL_ACTIONS)
        if self._profile_handler:
            actions.update(self._profile_handler.MODAL_ACTIONS)
        if self._skill_handler:
            actions.update(self._skill_handler.MODAL_ACTIONS)
        if self._system_handler:
            actions.update(self._system_handler.MODAL_ACTIONS)
        if self._model_handler:
            actions.update(self._model_handler.MODAL_ACTIONS)
        if self._directory_handler:
            actions.update(self._directory_handler.MODAL_ACTIONS)
        if self._login_handler:
            actions.update(self._login_handler.MODAL_ACTIONS)
        if self._context_handler:
            actions.update(self._context_handler.MODAL_ACTIONS)
        return actions

    def register_all_commands(self):
        """Register all commands from all handlers."""
        if self._agent_handler:
            self._agent_handler.register_commands()
        if self._profile_handler:
            self._profile_handler.register_commands()
        if self._skill_handler:
            self._skill_handler.register_commands()
        if self._system_handler:
            self._system_handler.register_commands()
        if self._model_handler:
            self._model_handler.register_commands()
        if self._directory_handler:
            self._directory_handler.register_commands()
        if self._login_handler:
            self._login_handler.register_commands()
        if self._context_handler:
            self._context_handler.register_commands()

    def register_commands(self):
        """Alias for register_all_commands for backward compatibility."""
        self.register_all_commands()

    async def register_hooks(self):
        """Register modal command hook for handling modal actions."""
        from kollabor_events.models import EventType, Hook, HookPriority

        hook = Hook(
            name="system_modal_command",
            plugin_name=self.name,
            event_type=EventType.MODAL_COMMAND_SELECTED,
            priority=HookPriority.LLM,  # Use LLM priority for command handling
            callback=self.handle_modal_action,
        )
        await self.event_bus.register_hook(hook)
        logger.info("System modal command hook registered")

    # Command handler methods (delegate to appropriate handler)

    async def handle_cd(self, command: SlashCommand) -> CommandResult:
        """Handle /cd command."""
        if self._directory_handler:
            return await self._directory_handler.handle_cd(command)
        return CommandResult(
            success=False,
            message="Directory handler not initialized",
            display_type="error",
        )

    async def handle_model(self, command: SlashCommand) -> CommandResult:
        """Handle /model command."""
        if self._model_handler:
            return await self._model_handler.handle_model(command)
        return CommandResult(
            success=False, message="Model handler not initialized", display_type="error"
        )

    async def handle_profile(self, command: SlashCommand) -> CommandResult:
        """Handle /profile command."""
        if self._profile_handler:
            return await self._profile_handler.handle_profile(command)
        return CommandResult(
            success=False,
            message="Profile handler not initialized",
            display_type="error",
        )

    async def handle_agent(self, command: SlashCommand) -> CommandResult:
        """Handle /agent command."""
        if self._agent_handler:
            return await self._agent_handler.handle_agent(command)
        return CommandResult(
            success=False, message="Agent handler not initialized", display_type="error"
        )

    async def handle_skill(self, command: SlashCommand) -> CommandResult:
        """Handle /skill command."""
        if self._skill_handler:
            return await self._skill_handler.handle_skill(command)
        return CommandResult(
            success=False, message="Skill handler not initialized", display_type="error"
        )

    async def handle_help(self, command: SlashCommand) -> CommandResult:
        """Handle /help command."""
        if self._system_handler:
            return await self._system_handler.handle_help(command)
        return CommandResult(
            success=False,
            message="System handler not initialized",
            display_type="error",
        )

    async def handle_config(self, command: SlashCommand) -> CommandResult:
        """Handle /config command."""
        if self._system_handler:
            return await self._system_handler.handle_config(command)
        return CommandResult(
            success=False,
            message="System handler not initialized",
            display_type="error",
        )

    async def handle_status(self, command: SlashCommand) -> CommandResult:
        """Handle /status command."""
        if self._system_handler:
            return await self._system_handler.handle_status(command)
        return CommandResult(
            success=False,
            message="System handler not initialized",
            display_type="error",
        )

    async def handle_doctor(self, command: SlashCommand) -> CommandResult:
        """Handle /doctor command."""
        if self._system_handler:
            return await self._system_handler.handle_doctor(command)
        return CommandResult(
            success=False,
            message="System handler not initialized",
            display_type="error",
        )

    async def handle_permissions(self, command: SlashCommand) -> CommandResult:
        """Handle /permissions command."""
        if self._system_handler:
            return await self._system_handler.handle_permissions(command)
        return CommandResult(
            success=False,
            message="System handler not initialized",
            display_type="error",
        )

    async def handle_version(self, command: SlashCommand) -> CommandResult:
        """Handle /version command."""
        if self._system_handler:
            return await self._system_handler.handle_version(command)
        return CommandResult(
            success=False,
            message="System handler not initialized",
            display_type="error",
        )

    async def handle_restart(self, command: SlashCommand) -> CommandResult:
        """Handle /restart command."""
        if self._system_handler:
            return await self._system_handler.handle_restart(command)
        return CommandResult(
            success=False,
            message="System handler not initialized",
            display_type="error",
        )

    async def handle_login(self, command: SlashCommand) -> CommandResult:
        """Handle /login command."""
        if self._login_handler:
            return await self._login_handler.handle_login(command)
        return CommandResult(
            success=False,
            message="Login handler not initialized",
            display_type="error",
        )

    # Modal action handler
    async def handle_modal_action(self, data: dict, event: Event) -> dict:
        """Handle modal actions by delegating to appropriate handler.

        Args:
            data: Event data containing command info.
            event: Event object.

        Returns:
            Modified data dict.
        """
        command = data.get("command", {})
        action = command.get("action", "")
        handler: BaseCommandHandler | None = None

        # Route action to appropriate handler
        if action in (
            "load_skill",
            "unload_skill",
            "create_skill_prompt",
            "create_skill_submit",
            "edit_skill_prompt",
            "edit_skill_submit",
            "delete_skill_prompt",
            "delete_skill_confirm",
            "toggle_default_skill",
            "toggle_global_default_skill",
        ):
            handler = self._skill_handler
        elif action in (
            "select_agent",
            "clear_agent",
            "create_agent_prompt",
            "create_agent_submit",
            "edit_agent_prompt",
            "edit_agent_submit",
            "delete_agent_prompt",
            "delete_agent_confirm",
            "toggle_project_default",
            "toggle_global_default",
        ):
            handler = self._agent_handler
        elif action in (
            "select_profile",
            "create_profile_submit",
            "edit_profile_prompt",
            "edit_profile_submit",
            "delete_profile_prompt",
            "delete_profile_confirm",
            "save_profile_to_config",
            "toggle_project_default_profile",
            "toggle_global_default_profile",
        ):
            handler = self._profile_handler
        elif action in ("select_model",):
            handler = self._model_handler
        elif action in (
            "select_directory",
            "cwd_show",
            "cwd_parent",
            "cwd_home",
            "cwd_custom",
        ):
            handler = self._directory_handler
        else:
            # Unknown action
            logger.warning(f"Unknown modal action: {action}")
            return data

        # Delegate to handler's action handler
        if handler:
            # Check if handler overrides handle_modal_action (not just inherits base stub)
            if (
                hasattr(handler, "handle_modal_action")
                and type(handler).handle_modal_action
                is not BaseCommandHandler.handle_modal_action
            ):
                return await handler.handle_modal_action(action, data)
            else:
                # Import the action handler module based on handler type
                handler_name = type(handler).__name__
                if handler_name == "SkillCommandHandler":
                    from .handlers.skill_actions import handle_skill_modal_actions

                    return await handle_skill_modal_actions(
                        data, event, cast("SkillCommandHandler", handler)
                    )
                elif handler_name == "AgentCommandHandler":
                    from .handlers.agent_actions import handle_agent_modal_actions

                    return await handle_agent_modal_actions(
                        data, event, cast("AgentCommandHandler", handler)
                    )
                elif handler_name == "ProfileCommandHandler":
                    from .handlers.profile_actions import handle_profile_modal_actions

                    return await handle_profile_modal_actions(
                        data, event, cast("ProfileCommandHandler", handler)
                    )
                elif handler_name == "ModelCommandHandler":
                    from .handlers.model_actions import handle_model_modal_actions

                    return await handle_model_modal_actions(
                        data, event, cast("ModelCommandHandler", handler)
                    )

        return data
