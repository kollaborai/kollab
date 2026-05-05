"""Agent command handler.

Handles /agent command - manage agents and their configurations.
"""

import logging
from typing import Any, Dict, Optional

from kollabor_events.models import (
    CommandCategory,
    CommandDefinition,
    CommandMode,
    CommandResult,
    SlashCommand,
    SubcommandInfo,
    UIConfig,
)

from ..base import BaseCommandHandler

logger = logging.getLogger(__name__)


class AgentCommandHandler(BaseCommandHandler):
    """Handles /agent command - manage agents and their configurations."""

    MODAL_ACTIONS = {
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
    }

    def __init__(
        self,
        command_registry,
        event_bus,
        agent_manager,
        profile_manager=None,
        llm_service=None,
    ):
        """Initialize agent command handler.

        Args:
            command_registry: Command registry for registration.
            event_bus: Event bus for service lookup.
            agent_manager: Agent manager instance.
            profile_manager: Optional profile manager (for create modal).
            llm_service: Optional LLM service (for dynamic lookup).
        """
        super().__init__(command_registry, event_bus)
        self.agent_manager = agent_manager
        self.profile_manager = profile_manager
        self._llm_service_override = llm_service

    @property
    def llm_service(self):
        """Get LLM service dynamically via event bus."""
        if self._llm_service_override is not None:
            return self._llm_service_override
        if self.event_bus and hasattr(self.event_bus, "get_service"):
            return self.event_bus.get_service("llm_service")
        return None

    def register_commands(self) -> None:
        """Register /agent command."""
        agent_command = CommandDefinition(
            name="agent",
            description="Manage agents and their configurations",
            handler=self.handle_agent,
            plugin_name="system",
            category=CommandCategory.AGENT,
            mode=CommandMode.STATUS_TAKEOVER,
            aliases=["ag"],
            icon="[AGENT]",
            subcommands=[
                SubcommandInfo("list", "", "Show agent selection modal"),
                SubcommandInfo("set", "<name>", "Switch to specified agent"),
                SubcommandInfo("create", "", "Open create agent form"),
                SubcommandInfo("clear", "", "Clear active agent"),
            ],
            ui_config=UIConfig(
                type="modal",
                navigation=["? ?", "Enter", "Esc"],
                height=15,
                title="Agents",
                footer="↑↓ navigate • Enter select • Esc exit",
            ),
        )
        self.command_registry.register_command(agent_command)

    async def handle_agent(self, command: SlashCommand) -> CommandResult:
        """Handle /agent command.

        Args:
            command: Parsed slash command.

        Returns:
            Command execution result.
        """
        try:
            if not self.agent_manager:
                return CommandResult(
                    success=False,
                    message="Agent manager not available",
                    display_type="error",
                )

            args = command.args or []

            if not args or args[0] in ("list", "ls"):
                # Show agent selection modal
                return await self._show_agents_modal()
            elif args[0] == "create":
                # Show create agent form modal
                return await self._show_create_agent_modal()
            elif args[0] == "clear":
                # Clear active agent
                self.agent_manager.clear_active_agent()
                return CommandResult(
                    success=True,
                    message="Cleared active agent, using default behavior",
                    display_type="success",
                )
            elif args[0] == "set" and len(args) > 1:
                # Switch to specified agent
                agent_name = args[1]
                return await self._switch_agent(agent_name)
            else:
                # Switch to specified agent (direct command)
                agent_name = args[0]
                return await self._switch_agent(agent_name)

        except Exception as e:
            self.logger.error(f"Error in agent command: {e}")
            return CommandResult(
                success=False,
                message=f"Error managing agents: {str(e)}",
                display_type="error",
            )

    def _get_agents_modal_definition(
        self, skip_reload: bool = False
    ) -> Optional[Dict[str, Any]]:
        """Get modal definition for agent selection with default indicators.

        Args:
            skip_reload: If True, don't reload from disk (use current state).

        Returns:
            Modal definition dictionary, or None if no agents found.
        """
        from kollabor_config.config_utils import get_all_default_agents

        # Get all default agents
        default_agents = (
            get_all_default_agents()
        )  # {"project": "coder", "global": "research"}
        project_default = default_agents.get("project")
        global_default = default_agents.get("global")

        # Refresh agents from directories to pick up any changes
        if not skip_reload:
            self.agent_manager.refresh()

        agents = self.agent_manager.list_agents()
        active_agent = self.agent_manager.get_active_agent()
        active_name = active_agent.name if active_agent else None

        if not agents:
            return None

        # Build agent list with indicators
        agent_items = []
        for agent in agents:
            is_active = agent.name == active_name
            is_project_default = agent.name == project_default
            is_global_default = agent.name == global_default

            # Build source indicator (L=local only, G=global only, *=both)
            if agent.source == "local" and agent.overrides_global:
                source_char = "*"
            elif agent.source == "local":
                source_char = "L"
            else:  # global
                source_char = "G"

            # Build default indicator
            default_parts = []
            if is_project_default:
                default_parts.append("D")
            if is_global_default:
                default_parts.append("g")
            default_str = "".join(default_parts) if default_parts else " "

            # Format: [active] source default - examples: [*G ] [ L] [ Gd]
            active_char = "*" if is_active else " "
            indicator = f"{active_char}{source_char}{default_str}"

            skills = agent.list_skills()
            skill_count = f" ({len(skills)} skills)" if skills else ""
            description = agent.description or "No description"

            agent_items.append(
                {
                    "name": f"[{indicator}] {agent.name}{skill_count}",
                    "description": description,
                    "agent_name": agent.name,
                    "action": "select_agent",
                    "is_active": is_active,
                    "is_project_default": is_project_default,
                    "is_global_default": is_global_default,
                }
            )

        # Add clear option
        agent_items.append(
            {
                "name": "    [Clear Agent]",
                "description": "Use default system prompt behavior",
                "agent_name": None,
                "action": "clear_agent",
            }
        )

        # Management options
        management_items = [
            {
                "name": "    [+] Create New Agent",
                "description": "Create a new agent with system prompt",
                "action": "create_agent_prompt",
            }
        ]

        return {
            "title": "Agents",
            "footer": "L=local G=global *=both | D=proj g=global | ↑↓ Enter",
            # Width and height are dynamic (terminal_width - 2, terminal_height - 4)
            "sections": [
                {
                    "title": f"Available Agents (active: {active_name or 'none'})",
                    "commands": agent_items,
                },
                {"title": "Management", "commands": management_items},
            ],
            "actions": [
                {"key": "Enter", "label": "Select", "action": "select"},
                {
                    "key": "d",
                    "label": "Project Default",
                    "action": "toggle_project_default",
                },
                {
                    "key": "g",
                    "label": "Global Default",
                    "action": "toggle_global_default",
                },
                {"key": "e", "label": "Edit", "action": "edit_agent_prompt"},
                {"key": "r", "label": "Delete", "action": "delete_agent_prompt"},
                {"key": "Escape", "label": "Close", "action": "cancel"},
            ],
        }

    async def _show_agents_modal(self) -> CommandResult:
        """Show agent selection modal.

        Returns:
            Command result with modal UI.
        """
        modal_definition = self._get_agents_modal_definition()

        if not modal_definition:
            return CommandResult(
                success=True,
                message="No agents found.\nCreate agents in .kollab/agents/<name>/system_prompt.md",
                display_type="info",
            )

        return CommandResult(
            success=True,
            message="Select an agent",
            ui_config=UIConfig(
                type="modal",
                title=modal_definition["title"],
                width=modal_definition.get("width"),
                height=int(modal_definition.get("height", 20)),
                modal_config=modal_definition,
            ),
            display_type="modal",
        )

    async def _show_create_agent_modal(self) -> CommandResult:
        """Show create agent form modal.

        Returns:
            Command result with modal UI.
        """
        modal_definition = self._get_create_agent_modal_definition()

        return CommandResult(
            success=True,
            message="Create a new agent",
            ui_config=UIConfig(
                type="modal",
                title=modal_definition.get("title", "Create Agent"),
                width=modal_definition.get("width"),
                height=int(modal_definition.get("height", 20)),
                modal_config=modal_definition,
            ),
            display_type="modal",
        )

    async def _switch_agent(self, agent_name: str) -> CommandResult:
        """Switch to a different agent via state_service.

        Phase 4.5 step 7: routes through state_service only. Works in
        both local and attach mode because state_service exposes the
        same interface on both sides. No legacy fallback.

        Args:
            agent_name: Name of agent to switch to.

        Returns:
            Command result.
        """
        state_service = None
        if self.event_bus and hasattr(self.event_bus, "get_service"):
            state_service = self.event_bus.get_service("state_service")
        if state_service is None:
            return CommandResult(
                success=False,
                message="State service not available -- cannot switch agent.",
                display_type="error",
            )

        try:
            snapshot = await state_service.set_agent(agent_name)
        except ValueError as e:
            return CommandResult(
                success=False,
                message=f"Agent not found: {agent_name}\n{e}",
                display_type="error",
            )
        except Exception as e:
            self.logger.error(f"state_service.set_agent failed: {e}")
            return CommandResult(
                success=False,
                message=f"Failed to switch agent: {e}",
                display_type="error",
            )

        skill_info = (
            f", {len(snapshot.active_skills)} skills active"
            if snapshot.active_skills
            else ""
        )
        profile_info = (
            f"\n  Preferred profile: {snapshot.profile}" if snapshot.profile else ""
        )
        return CommandResult(
            success=True,
            message=(
                f"Switched to agent: {snapshot.name}{skill_info}" f"{profile_info}"
            ),
            display_type="success",
        )

    def _get_create_agent_modal_definition(self) -> Dict[str, Any]:
        """Get modal definition for creating a new agent."""
        # Get available profiles for dropdown
        profile_options = ["(none)"]
        if self.profile_manager:
            profile_options.extend(self.profile_manager.get_profile_names())

        return {
            "title": "Create Agent",
            "footer": "Tab navigate • Enter confirm • Ctrl+S save • Esc cancel",
            "form_action": "create_agent_submit",
            "sections": [
                {
                    "title": "Agent Settings",
                    "widgets": [
                        {
                            "type": "text_input",
                            "label": "Agent Name",
                            "field": "name",
                            "placeholder": "my-agent",
                            "help": "Unique identifier (creates agents/<name>/ directory)",
                        },
                        {
                            "type": "text_input",
                            "label": "Description",
                            "field": "description",
                            "placeholder": "A Python web development specialist...",
                            "help": "Describe what this agent specializes in (AI generates from this)",
                        },
                        {
                            "type": "dropdown",
                            "label": "Source",
                            "field": "source",
                            "options": ["global", "local"],
                            "current_value": "global",
                            "help": "global=~/shared, local=project-specific",
                        },
                        {
                            "type": "dropdown",
                            "label": "Preferred Profile",
                            "field": "profile",
                            "options": profile_options,
                            "current_value": "(none)",
                            "help": "LLM profile to use with this agent",
                        },
                        {
                            "type": "label",
                            "label": "Generation",
                            "value": "AI will generate system prompt and 5-6 skills based on description",
                        },
                    ],
                }
            ],
            "actions": [
                {
                    "key": "Ctrl+S",
                    "label": "Generate",
                    "action": "submit",
                    "style": "primary",
                },
                {
                    "key": "Escape",
                    "label": "Cancel",
                    "action": "cancel",
                    "style": "secondary",
                },
            ],
        }

    def _build_agent_generation_prompt(
        self,
        name: str,
        description: str,
        profile: Optional[str] = None,
        source: str = "global",
    ) -> str:
        """Build prompt for LLM-powered agent generation.

        Args:
            name: Agent name (directory name).
            description: What the agent specializes in.
            profile: Optional preferred LLM profile.
            source: Agent source - "global" or "local".

        Returns:
            Prompt string for the LLM to generate agent files.
        """
        profile_value = f'"{profile}"' if profile else "null"

        return f"""Create a new agent called "{name}" that specializes in: {description}

IMPORTANT: First, review the structure of the default agent to understand the format:
- Read ~/.kollab/agents/default/system_prompt.md (the main system prompt template)
- Read ~/.kollab/agents/default/agent.json (the configuration format)
- Read ~/.kollab/agents/default/debugging.md (an example skill file format)

After reviewing the templates, create the new agent with the SAME level of detail and quality.

Create these files using <create> tags:

1. system_prompt.md - Comprehensive system prompt (500+ lines) following the default template structure:
   - Header with agent name
   - Core philosophy and mission
   - Session context with <trender> tags for dynamic content
   - Tool execution guidelines
   - Response patterns and examples
   - Quality assurance checklist
   - Error handling guidance

2. agent.json - Configuration file:
   {{"description": "{description}", "profile": {profile_value}}}

3. Create 5-6 skill files (.md) relevant to this agent's specialty. Each skill should:
   - Start with HTML comment description: <!-- Skill name - brief purpose -->
   - Include PHASE 0: Environment verification
   - Include multiple phases with detailed guidance
   - End with Mandatory rules section
   - Be 500+ lines with comprehensive, actionable content

CRITICAL: Use @@@FILE/@@@END blocks to generate all files. This protects your content
from being parsed as actual tool calls. The format is:







[repeat @@@FILE blocks for each of the 5-6 skill files]

Generate ONE file at a time using @@@FILE blocks. Match the quality and depth of the default agent templates."""

    def _get_delete_agent_confirm_modal(
        self, agent_name: str
    ) -> Optional[Dict[str, Any]]:
        """Get modal definition for delete agent confirmation.

        Args:
            agent_name: Name of the agent to delete.

        Returns:
            Modal definition dict for confirmation, or empty dict if cannot delete.
        """
        if not self.agent_manager:
            return {}

        agents = self.agent_manager.list_agents()
        agent = next((a for a in agents if a.name == agent_name), None)
        if not agent:
            return {}

        active_agent = self.agent_manager.get_active_agent()
        active_name = active_agent.name if active_agent else None
        is_active = agent_name == active_name

        warning_msg = ""
        if is_active:
            warning_msg = (
                "\n\n[!] This is the currently active agent.\n"
                "    You must clear or switch to another agent first."
            )
            can_delete = False
        else:
            can_delete = True

        skills = agent.list_skills()
        skill_info = f", {len(skills)} skills" if skills else ""

        return {
            "title": f"Delete Agent: {agent_name}?",
            "footer": "Enter confirm • Esc cancel",
            "width": 60,
            "height": 12,
            "sections": [
                {
                    "title": "Confirm Deletion",
                    "commands": [
                        {
                            "name": f"Delete '{agent_name}'",
                            "description": f"{agent.description or 'No description'}{skill_info}{warning_msg}",
                            "agent_name": agent_name,
                            "action": (
                                "delete_agent_confirm" if can_delete else "cancel"
                            ),
                        },
                        {
                            "name": "Cancel",
                            "description": "Keep the agent",
                            "action": "cancel",
                        },
                    ],
                }
            ],
            "actions": [
                {"key": "Enter", "label": "Confirm", "action": "select"},
                {"key": "Escape", "label": "Cancel", "action": "cancel"},
            ],
        }

    def _get_edit_agent_modal_definition(
        self, agent_name: str
    ) -> Optional[Dict[str, Any]]:
        """Get modal definition for editing an existing agent.

        Args:
            agent_name: Name of the agent to edit.

        Returns:
            Modal definition dict with pre-populated values, or None if not found.
        """
        if not self.agent_manager:
            return None

        agent = self.agent_manager.get_agent(agent_name)
        if not agent:
            return None

        # Get available profiles for dropdown
        profile_options = ["(none)"]
        if self.profile_manager:
            profile_options.extend(self.profile_manager.get_profile_names())

        # Determine current profile value
        current_profile = agent.profile if agent.profile else "(none)"

        # Get skill info for display
        skills = agent.list_skills()
        skill_info = f", {len(skills)} skills" if skills else ""

        # Show short path for system_prompt file
        short_path = f"agents/{agent_name}/system_prompt.md"

        return {
            "title": f"Edit Agent: {agent_name}",
            "footer": "Tab navigate • Ctrl+S save • Esc cancel",
            "form_action": "edit_agent_submit",
            "edit_agent_name": agent_name,  # Track original name for rename
            "sections": [
                {
                    "title": "Agent Settings",
                    "widgets": [
                        {
                            "type": "text_input",
                            "label": "Name",
                            "field": "name",
                            "value": agent.name,
                            "placeholder": "my-agent",
                            "help": "Renames agent directory",
                        },
                        {
                            "type": "text_input",
                            "label": "Desc",
                            "field": "description",
                            "value": agent.description or "",
                            "placeholder": "What this agent does",
                            "help": "Agent description",
                        },
                        {
                            "type": "dropdown",
                            "label": "Profile",
                            "field": "profile",
                            "options": profile_options,
                            "current_value": current_profile,
                            "help": "Preferred LLM profile",
                        },
                    ],
                },
                {
                    "title": f"Files{skill_info}",
                    "widgets": [
                        {
                            "type": "label",
                            "label": "Prompt",
                            "value": short_path,
                            "help": "nano or vim to edit",
                        },
                    ],
                },
            ],
            "actions": [
                {
                    "key": "Ctrl+S",
                    "label": "Save",
                    "action": "submit",
                    "style": "primary",
                },
                {
                    "key": "Escape",
                    "label": "Cancel",
                    "action": "cancel",
                    "style": "secondary",
                },
            ],
        }
