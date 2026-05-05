"""Skill command handler.

Handles /skills command - load or unload agent skills.
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


class SkillCommandHandler(BaseCommandHandler):
    """Handles /skills command - manage, browse, load, and unload agent skills."""

    MODAL_ACTIONS = {
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
    }

    def __init__(
        self,
        command_registry,
        event_bus,
        agent_manager,
        llm_service=None,
    ):
        """Initialize skill command handler.

        Args:
            command_registry: Command registry for registration.
            event_bus: Event bus for service lookup.
            agent_manager: Agent manager instance.
            llm_service: Optional LLM service (for dynamic lookup).
        """
        super().__init__(command_registry, event_bus)
        self.agent_manager = agent_manager
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
        """Register /skills command."""
        skill_command = CommandDefinition(
            name="skills",
            description="Browse, load, and manage agent skills",
            handler=self.handle_skill,
            plugin_name="system",
            category=CommandCategory.AGENT,
            mode=CommandMode.STATUS_TAKEOVER,
            aliases=["skill", "sk"],
            icon="[SKILL]",
            subcommands=[
                SubcommandInfo("list", "", "Show skill selection modal"),
                SubcommandInfo("browse", "", "Browse all skills in the library"),
                SubcommandInfo("load", "<name>", "Load specified skill"),
                SubcommandInfo("unload", "<name>", "Unload specified skill"),
                SubcommandInfo("create", "", "Open create skill form"),
            ],
            ui_config=UIConfig(
                type="modal",
                navigation=["? ?", "Enter", "Esc"],
                height=15,
                title="Agent Skills",
                footer="↑↓/Tab navigate | / search | PgUp/PgDn page | Enter select | Esc exit",
            ),
        )
        self.command_registry.register_command(skill_command)

    async def handle_skill(self, command: SlashCommand) -> CommandResult:
        """Handle /skills command.

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

            # Browse doesn't require an active agent
            if args and args[0] == "browse":
                return await self._show_browse_modal()

            active_agent = self.agent_manager.get_active_agent()
            if not active_agent:
                return CommandResult(
                    success=False,
                    message="No active agent. Use /agent <name> first.",
                    display_type="error",
                )

            if not args:
                # Show skill selection modal
                return await self._show_skills_modal()
            elif args[0] in ("list", "ls"):
                # Show skill selection modal
                return await self._show_skills_modal()
            elif args[0] == "create":
                # Show create skill form modal
                return await self._show_create_skill_modal()
            elif args[0] == "load" and len(args) > 1:
                # Load skill
                skill_name = args[1]
                return await self._load_skill(skill_name)
            elif args[0] == "unload" and len(args) > 1:
                # Unload skill
                skill_name = args[1]
                return await self._unload_skill(skill_name)
            else:
                # Try to load skill by name directly
                skill_name = args[0]
                return await self._load_skill(skill_name)

        except Exception as e:
            self.logger.error(f"Error in skill command: {e}")
            return CommandResult(
                success=False,
                message=f"Error managing skills: {str(e)}",
                display_type="error",
            )

    def _get_skills_modal_definition(
        self, skip_reload: bool = False
    ) -> Optional[Dict[str, Any]]:
        """Get modal definition for skill selection.

        Args:
            skip_reload: If True, don't reload from disk (use current state).

        Returns:
            Modal definition dictionary, or None if no skills available.
        """
        active_agent = self.agent_manager.get_active_agent()
        if not active_agent:
            return None

        # Refresh agent from disk to pick up any changes (unless skipped)
        if not skip_reload:
            self.agent_manager.refresh()
            # Re-get active agent in case it was refreshed
            active_agent = self.agent_manager.get_active_agent()
            if not active_agent:
                return None

        skills = active_agent.list_skills()
        active_skills = active_agent.active_skills

        # Check project and global defaults
        import json

        local_config = (
            self.agent_manager.local_agents_dir / active_agent.name / "agent.json"
            if self.agent_manager.local_agents_dir
            else None
        )
        global_config = (
            self.agent_manager.global_agents_dir / active_agent.name / "agent.json"
        )

        project_defaults = set()
        global_defaults = set()

        if local_config and local_config.exists():
            try:
                config_data = json.loads(local_config.read_text(encoding="utf-8"))
                project_defaults = set(config_data.get("default_skills", []))
            except Exception as e:
                logger.debug(f"Failed to load local skill config: {e}")

        if global_config.exists():
            try:
                config_data = json.loads(global_config.read_text(encoding="utf-8"))
                global_defaults = set(config_data.get("default_skills", []))
            except Exception as e:
                logger.debug(f"Failed to load global skill config: {e}")

        if not skills:
            return None

        # Build skill list for modal
        skill_items = []
        for skill in skills:
            is_loaded = skill.name in active_skills
            is_proj_default = skill.name in project_defaults
            is_global_default = skill.name in global_defaults

            action = "unload_skill" if is_loaded else "load_skill"
            description = skill.description or "no description"

            # Build status indicators for description
            status_parts = []
            if is_proj_default:
                status_parts.append("proj")
            if is_global_default:
                status_parts.append("global")
            if status_parts:
                description = f"[{'+'.join(status_parts)}] {description}"

            skill_items.append(
                {
                    "name": skill.name,
                    "description": description,
                    "skill_name": skill.name,
                    "action": action,
                    "loaded": is_loaded,
                    "is_default": is_proj_default or is_global_default,
                }
            )

        loaded_count = len(active_skills)
        total_count = len(skills)
        default_count = len(project_defaults | global_defaults)

        # Management options
        management_items = [
            {
                "name": "Create New Skill",
                "description": "Create a new skill file for this agent",
                "action": "create_skill_prompt",
            }
        ]

        return {
            "title": f"Skills - {active_agent.name}",
            "footer": "↑↓/Tab | / search | d/g default | e edit | r delete | Enter toggle",
            # Width and height are dynamic (terminal_width - 2, terminal_height - 4)
            "sections": [
                {
                    "title": f"Available Skills ({loaded_count}/{total_count} loaded, {default_count} default)",
                    "commands": skill_items,
                },
                {"title": "Management", "commands": management_items},
            ],
            "actions": [
                {"key": "Enter", "label": "Toggle", "action": "toggle"},
                {
                    "key": "d",
                    "label": "Project Default",
                    "action": "toggle_default_skill",
                },
                {
                    "key": "g",
                    "label": "Global Default",
                    "action": "toggle_global_default_skill",
                },
                {"key": "e", "label": "Edit", "action": "edit_skill_prompt"},
                {"key": "r", "label": "Delete", "action": "delete_skill_prompt"},
                {"key": "Escape", "label": "Close", "action": "cancel"},
            ],
        }

    async def _show_skills_modal(self) -> CommandResult:
        """Show skill selection modal for active agent.

        Returns:
            Command result with modal UI.
        """
        active_agent = self.agent_manager.get_active_agent()
        if not active_agent:
            return CommandResult(
                success=False, message="No active agent", display_type="error"
            )

        modal_definition = self._get_skills_modal_definition()
        if not modal_definition:
            return CommandResult(
                success=True,
                message=(
                    f"Agent '{active_agent.name}' has no skills assigned.\n"
                    'Add skill names to the "skills" field in agent.json.'
                ),
                display_type="info",
            )

        return CommandResult(
            success=True,
            message="Select a skill to load/unload",
            ui_config=UIConfig(
                type="modal",
                title=modal_definition["title"],
                width=modal_definition.get("width"),
                height=int(modal_definition.get("height", 20)),
                modal_config=modal_definition,
            ),
            display_type="modal",
        )

    async def _show_browse_modal(self) -> CommandResult:
        """Show skill library browser showing ALL skills across all tiers.

        Returns:
            Command result with modal UI.
        """
        if not self.agent_manager or not self.agent_manager.skill_library:
            return CommandResult(
                success=False,
                message="Skill library not available",
                display_type="error",
            )

        all_skills = self.agent_manager.skill_library.list_all()
        if not all_skills:
            return CommandResult(
                success=True,
                message="No skills found in the library.",
                display_type="info",
            )

        # Check which skills the active agent has assigned
        active_agent = self.agent_manager.get_active_agent()
        agent_skill_names = set(active_agent.skills.keys()) if active_agent else set()
        active_skill_names = set(active_agent.active_skills) if active_agent else set()

        # Group by source
        bundled = []
        global_skills = []
        local = []
        for skill in sorted(all_skills, key=lambda s: s.name):
            desc = skill.description or "no description"

            assigned = skill.name in agent_skill_names
            loaded = skill.name in active_skill_names

            # Build tags showing what the skill contains
            tags = []
            if loaded:
                tags.append("loaded")
            elif assigned:
                tags.append("assigned")
            if skill.skill_dir:
                if (skill.skill_dir / "scripts").is_dir():
                    tags.append("scripts")
                if (skill.skill_dir / "references").is_dir():
                    tags.append("refs")
                if (skill.skill_dir / "assets").is_dir():
                    tags.append("assets")
                # Check for loose reference .md files (like pdf/forms.md)
                extra_md = [
                    f.stem for f in skill.skill_dir.glob("*.md") if f.name != "SKILL.md"
                ]
                if extra_md:
                    tags.append("+".join(extra_md))
            tag_str = f" [{', '.join(tags)}]" if tags else ""

            # Truncate desc after tags are known
            max_desc = max(30, 80 - len(tag_str))
            if len(desc) > max_desc:
                desc = desc[: max_desc - 3] + "..."

            item = {
                "name": f"{skill.name}{tag_str}",
                "description": desc,
                "skill_name": skill.name,
                "action": "unload_skill" if loaded else "load_skill",
                "loaded": loaded,
            }

            if skill.source == "bundled":
                bundled.append(item)
            elif skill.source == "global":
                global_skills.append(item)
            else:
                local.append(item)

        sections = []
        if local:
            sections.append(
                {"title": f"Local Skills ({len(local)})", "commands": local}
            )
        if global_skills:
            sections.append(
                {
                    "title": f"Global Skills ({len(global_skills)})",
                    "commands": global_skills,
                }
            )
        if bundled:
            sections.append(
                {"title": f"Bundled Skills ({len(bundled)})", "commands": bundled}
            )

        modal_definition = {
            "title": f"Skill Library ({len(all_skills)} skills)",
            "footer": "↑↓/Tab | / search | Enter load | Esc exit",
            "sections": sections,
            "actions": [
                {"key": "Enter", "label": "Load", "action": "toggle"},
                {"key": "Escape", "label": "Close", "action": "cancel"},
            ],
        }

        return CommandResult(
            success=True,
            message="Browse all available skills",
            ui_config=UIConfig(
                type="modal",
                title=modal_definition["title"],
                width=modal_definition.get("width"),
                height=int(modal_definition.get("height", 20)),
                modal_config=modal_definition,
            ),
            display_type="modal",
        )

    async def _show_create_skill_modal(self) -> CommandResult:
        """Show create skill form modal for active agent.

        Returns:
            Command result with modal UI.
        """
        active_agent = self.agent_manager.get_active_agent()
        if not active_agent:
            return CommandResult(
                success=False,
                message="No active agent. Use /agent <name> first.",
                display_type="error",
            )

        modal_definition = self._get_create_skill_modal_definition(active_agent.name)

        return CommandResult(
            success=True,
            message="Create a new skill",
            ui_config=UIConfig(
                type="modal",
                title=modal_definition.get("title", "Create Skill"),
                width=modal_definition.get("width"),
                height=int(modal_definition.get("height", 20)),
                modal_config=modal_definition,
            ),
            display_type="modal",
        )

    async def _load_skill(self, skill_name: str) -> CommandResult:
        """Load a skill into active agent via state_service.

        Phase 4.5 step 7: state_service is the only path. The
        daemon-side activate_skill uses the canonical inject_system_message
        primitive and acquires the hub _history_lock to avoid racing
        hub message injection. No legacy fallback.

        Args:
            skill_name: Name of skill to load.

        Returns:
            Command result.
        """
        state_service = None
        if self.event_bus and hasattr(self.event_bus, "get_service"):
            state_service = self.event_bus.get_service("state_service")
        if state_service is None:
            return CommandResult(
                success=False,
                message="State service not available -- cannot load skill.",
                display_type="error",
            )

        try:
            await state_service.activate_skill(skill_name)
        except ValueError as e:
            return CommandResult(
                success=False,
                message=f"Skill not found: {skill_name}\n{e}",
                display_type="error",
            )
        except Exception as e:
            self.logger.error(f"state_service.activate_skill failed: {e}")
            return CommandResult(
                success=False,
                message=f"Failed to load skill: {e}",
                display_type="error",
            )

        return CommandResult(
            success=True,
            message=f"Loaded skill: {skill_name}",
            display_type="success",
        )

    async def _unload_skill(self, skill_name: str) -> CommandResult:
        """Unload a skill from active agent via state_service.

        Phase 4.5 step 7: state_service is the only path.

        Args:
            skill_name: Name of skill to unload.

        Returns:
            Command result.
        """
        state_service = None
        if self.event_bus and hasattr(self.event_bus, "get_service"):
            state_service = self.event_bus.get_service("state_service")
        if state_service is None:
            return CommandResult(
                success=False,
                message="State service not available -- cannot unload skill.",
                display_type="error",
            )

        try:
            await state_service.deactivate_skill(skill_name)
        except ValueError as e:
            return CommandResult(
                success=False,
                message=f"Skill not loaded: {skill_name}\n{e}",
                display_type="error",
            )
        except Exception as e:
            self.logger.error(f"state_service.deactivate_skill failed: {e}")
            return CommandResult(
                success=False,
                message=f"Failed to unload skill: {e}",
                display_type="error",
            )

        return CommandResult(
            success=True,
            message=f"Unloaded skill: {skill_name}",
            display_type="success",
        )

    def _build_skill_generation_prompt(
        self, agent_name: str, skill_name: str, description: str
    ) -> str:
        """Build prompt for LLM-powered skill generation.

        Args:
            agent_name: Name of the agent this skill belongs to.
            skill_name: Skill name (lowercase-with-hyphens).
            description: What the skill helps with.

        Returns:
            Prompt string for the LLM to generate the SKILL.md file.
        """
        from kollabor_config.config_utils import get_local_skills_path

        skills_dir = str(get_local_skills_path())

        return f"""Create a new skill called "{skill_name}" following the Agent Skills standard.

The skill should help with: {description}

IMPORTANT: First, review existing skills to understand the format:
- Read bundles/skills/debugging/SKILL.md (example of the SKILL.md format)
- Read bundles/skills/code-review/SKILL.md (another example)

Create a SKILL.md file at: {skills_dir}/{skill_name}/SKILL.md

The file MUST follow this format:
---
name: {skill_name}
description: "{description}"
---

(comprehensive skill content here)

The skill content should:
1. Have a clear header with skill name and mode
2. Include PHASE 0: Environment/context verification
3. Have multiple phases with detailed, actionable guidance
4. Include examples and code snippets where relevant
5. End with a quality checklist section
6. Be comprehensive (500+ lines) with real, actionable content

CRITICAL: Use @@@FILE/@@@END blocks to generate the file."""

    def _get_create_skill_modal_definition(self, agent_name: str) -> Dict[str, Any]:
        """Get modal definition for creating a new skill."""
        return {
            "title": f"Create Skill - {agent_name}",
            "footer": "Ctrl+S: create | Esc: cancel",
            "form_action": "create_skill_submit",
            "sections": [
                {
                    "title": "New Skill (Agent Skills Standard)",
                    "widgets": [
                        {
                            "type": "text_input",
                            "label": "Name",
                            "field": "name",
                            "placeholder": "my-skill",
                            "help": "lowercase + hyphens only (e.g. code-review)",
                        },
                        {
                            "type": "text_input",
                            "label": "Description",
                            "field": "description",
                            "placeholder": "What this skill helps with...",
                            "help": "AI generates SKILL.md from this description",
                        },
                    ],
                },
                {
                    "title": "Info",
                    "widgets": [
                        {
                            "type": "label",
                            "label": "Location",
                            "value": ".kollab/skills/<name>/SKILL.md",
                            "help": "Creates in local skill library",
                        },
                    ],
                },
            ],
            "actions": [
                {
                    "key": "Ctrl+S",
                    "label": "Create",
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

    def _get_edit_skill_modal_definition(
        self, agent_name: str, skill_name: str
    ) -> Optional[Dict[str, Any]]:
        """Get modal definition for editing an existing skill."""
        if not self.agent_manager:
            return None

        active_agent = self.agent_manager.get_active_agent()
        if not active_agent or active_agent.name != agent_name:
            return None

        skill = active_agent.get_skill(skill_name)
        if not skill:
            return None

        # Show skill library path
        short_path = (
            str(skill.skill_dir) if skill.skill_dir else f"skills/{skill_name}/SKILL.md"
        )

        return {
            "title": f"Edit Skill: {skill_name}",
            "footer": "Tab navigate | Ctrl+S save | Esc cancel",
            "form_action": "edit_skill_submit",
            "edit_skill_name": skill_name,
            "sections": [
                {
                    "title": "Skill Settings",
                    "widgets": [
                        {
                            "type": "text_input",
                            "label": "Name",
                            "field": "name",
                            "value": skill_name,
                            "placeholder": "my-skill",
                            "help": "Rename the skill directory",
                        },
                    ],
                },
                {
                    "title": "File",
                    "widgets": [
                        {
                            "type": "label",
                            "label": "Path",
                            "value": short_path,
                            "help": f"source: {skill.source}",
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

    def _get_delete_skill_confirm_modal(
        self, agent_name: str, skill_name: str
    ) -> Optional[Dict[str, Any]]:
        """Get modal definition for delete skill confirmation."""
        if not self.agent_manager:
            return None

        active_agent = self.agent_manager.get_active_agent()
        if not active_agent or active_agent.name != agent_name:
            return None

        # Find the skill
        skill = None
        for s in active_agent.list_skills():
            if s.name == skill_name:
                skill = s
                break

        if not skill:
            return None

        is_loaded = skill_name in active_agent.active_skills
        warning_msg = ""
        if is_loaded:
            warning_msg = "\n\n[!] This skill is currently loaded."

        return {
            "title": f"Delete Skill: {skill_name}?",
            "footer": "Enter confirm • Esc cancel",
            "width": 60,
            "height": 12,
            "sections": [
                {
                    "title": "Confirm Deletion",
                    "commands": [
                        {
                            "name": f"Delete '{skill_name}'",
                            "description": f"{skill.description or skill.file_path.name}{warning_msg}",
                            "skill_name": skill_name,
                            "action": "delete_skill_confirm",
                        },
                        {
                            "name": "Cancel",
                            "description": "Keep the skill",
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

    def _rename_skill_file(self, agent, original_name: str, new_name: str) -> bool:
        """Rename a skill directory in the skill library."""
        try:
            if original_name == new_name:
                return True

            # Find the skill to get its directory
            skill = agent.get_skill(original_name)
            if not skill or not skill.skill_dir:
                return False

            new_dir = skill.skill_dir.parent / new_name
            if new_dir.exists():
                return False

            # Rename the directory
            skill.skill_dir.rename(new_dir)

            # Update SKILL.md frontmatter with new name
            skill_md = new_dir / "SKILL.md"
            if skill_md.exists():
                content = skill_md.read_text(encoding="utf-8")
                content = content.replace(
                    f"name: {original_name}", f"name: {new_name}", 1
                )
                skill_md.write_text(content, encoding="utf-8")

            return True
        except Exception as e:
            logger.error(f"Failed to rename skill {original_name} -> {new_name}: {e}")
            return False

    def _delete_skill_file(self, agent, skill_name: str) -> bool:
        """Delete a skill directory from the skill library."""
        import shutil

        try:
            skill = agent.get_skill(skill_name)
            if not skill or not skill.skill_dir:
                return False

            if not skill.skill_dir.exists():
                return False

            shutil.rmtree(skill.skill_dir)
            return True
        except Exception as e:
            logger.error(f"Failed to delete skill {skill_name}: {e}")
            return False
