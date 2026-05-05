"""Skill modal action handlers.

Handles modal actions for skill loading, unloading, creation, editing, deletion, defaults.
"""

import logging

from kollabor_events.models import Event

from .skills import SkillCommandHandler

logger = logging.getLogger(__name__)


async def handle_skill_modal_actions(
    data: dict, event: Event, handler: SkillCommandHandler
) -> dict:
    """Handle skill-related modal actions.

    Args:
        data: Event data containing command info.
        event: Event object.
        handler: Skill command handler instance.

    Returns:
        Modified data dict with display_messages/show_modal keys.
    """
    command = data.get("command", {})
    action = command.get("action")

    logger.info(f"Skill modal action received: {action}")

    # Handle skill load
    if action == "load_skill":
        skill_name = command.get("skill_name")
        if skill_name and handler.agent_manager:
            agent = handler.agent_manager.get_active_agent()
            # Check agent's assigned skills first, fall back to library
            skill = agent.get_skill(skill_name) if agent else None
            if not skill and handler.agent_manager.skill_library:
                skill = handler.agent_manager.skill_library.get_skill(skill_name)
                # Add to agent's skills dict so it can be tracked as active
                if skill and agent:
                    agent.skills[skill_name] = skill
            if skill and agent and handler.agent_manager.load_skill(skill_name):
                # Inject skill content as user message instead of system prompt
                skill_message = f"## Skill: {skill_name}\n\n{skill.content}"
                if handler.llm_service:
                    handler.llm_service._add_conversation_message("user", skill_message)
                else:
                    logger.warning("llm_service is None - skill content NOT injected")
                # Display skill content in UI and show success message
                data["display_messages"] = [
                    ("user", skill_message, {}),
                    ("system", f"[ok] Loaded skill: {skill_name}", {}),
                ]
            else:
                data["display_messages"] = [
                    ("error", f"[err] Skill not found: {skill_name}", {}),
                ]

    # Handle skill unload
    elif action == "unload_skill":
        skill_name = command.get("skill_name")
        if skill_name and handler.agent_manager:
            if handler.agent_manager.unload_skill(skill_name):
                # Add message indicating skill was unloaded
                if handler.llm_service:
                    handler.llm_service._add_conversation_message(
                        "user",
                        f"[Skill '{skill_name}' has been unloaded - please disregard its instructions]",
                    )
                data["display_messages"] = [
                    ("system", f"[ok] Unloaded skill: {skill_name}", {}),
                ]
                # Reopen the skills modal (skip reload since memory is fresh)
                modal_def = handler._get_skills_modal_definition(skip_reload=True)
                if modal_def:
                    data["show_modal"] = modal_def
            else:
                data["display_messages"] = [
                    ("error", f"[err] Skill not loaded: {skill_name}", {}),
                ]

    # Handle toggle default skill (project scope)
    elif action == "toggle_default_skill":
        skill_name = command.get("skill_name")
        if skill_name and handler.agent_manager:
            success, is_default = handler.agent_manager.toggle_default_skill(
                skill_name, scope="project"
            )
            if success:
                status = "added to" if is_default else "removed from"
                data["display_messages"] = [
                    (
                        "system",
                        f"[ok] Skill '{skill_name}' {status} project defaults",
                        {},
                    ),
                ]
                # Reopen the skills modal
                modal_def = handler._get_skills_modal_definition(skip_reload=True)
                if modal_def:
                    data["show_modal"] = modal_def
            else:
                data["display_messages"] = [
                    (
                        "error",
                        f"[err] Failed to toggle project default for: {skill_name}",
                        {},
                    ),
                ]

    # Handle toggle global default skill
    elif action == "toggle_global_default_skill":
        skill_name = command.get("skill_name")
        if skill_name and handler.agent_manager:
            success, is_default = handler.agent_manager.toggle_default_skill(
                skill_name, scope="global"
            )
            if success:
                status = "added to" if is_default else "removed from"
                data["display_messages"] = [
                    (
                        "system",
                        f"[ok] Skill '{skill_name}' {status} global defaults",
                        {},
                    ),
                ]
                # Reopen the skills modal
                modal_def = handler._get_skills_modal_definition(skip_reload=True)
                if modal_def:
                    data["show_modal"] = modal_def
            else:
                data["display_messages"] = [
                    (
                        "error",
                        f"[err] Failed to toggle global default for: {skill_name}",
                        {},
                    ),
                ]

    # Handle create skill - show form modal
    elif action == "create_skill_prompt":
        if handler.agent_manager:
            active_agent = handler.agent_manager.get_active_agent()
            if active_agent:
                data["show_modal"] = handler._get_create_skill_modal_definition(
                    active_agent.name
                )
            else:
                data["display_messages"] = [
                    ("error", "[err] No active agent", {}),
                ]

    # Handle create skill form submission
    elif action == "create_skill_submit":
        form_data = command.get("form_data", {})
        name = form_data.get("name", "").strip()
        description = form_data.get("description", "").strip()

        if not name:
            data["display_messages"] = [
                ("error", "[err] Skill name is required", {}),
            ]
        elif not description:
            data["display_messages"] = [
                ("error", "[err] Description is required for AI generation", {}),
            ]
        elif handler.agent_manager:
            active_agent = handler.agent_manager.get_active_agent()
            if active_agent and handler.llm_service:
                # Build the generation prompt and send to LLM
                generation_prompt = handler._build_skill_generation_prompt(
                    agent_name=active_agent.name,
                    skill_name=name,
                    description=description,
                )
                # Send to LLM - it will use @@@FILE blocks to generate the file
                await handler.llm_service.process_user_input(generation_prompt)
                # Close modal - LLM handles the rest with existing tool infrastructure
                data["close_modal"] = True
            else:
                data["display_messages"] = [
                    ("error", "[err] LLM service not available", {}),
                ]

    # Handle edit skill - show form modal
    elif action == "edit_skill_prompt":
        skill_name = command.get("skill_name")
        if skill_name and handler.agent_manager:
            active_agent = handler.agent_manager.get_active_agent()
            if active_agent:
                modal_def = handler._get_edit_skill_modal_definition(
                    active_agent.name, skill_name
                )
                if modal_def:
                    data["show_modal"] = modal_def
                else:
                    data["display_messages"] = [
                        ("error", f"[err] Skill not found: {skill_name}", {}),
                    ]
            else:
                data["display_messages"] = [
                    ("error", "[err] Select a skill to edit", {}),
                ]

    # Handle edit skill form submission (rename only)
    elif action == "edit_skill_submit":
        form_data = command.get("form_data", {})
        original_name = command.get("edit_skill_name", "")
        new_name = form_data.get("name", "").strip()

        if not new_name:
            data["display_messages"] = [
                ("error", "[err] Skill name is required", {}),
            ]
        elif handler.agent_manager:
            active_agent = handler.agent_manager.get_active_agent()
            if active_agent:
                success = handler._rename_skill_file(
                    active_agent, original_name, new_name
                )
                if success:
                    handler.agent_manager.refresh()
                    msg = f"[ok] Updated skill: {new_name}"
                    if new_name != original_name:
                        msg += f"\n  Renamed from: {original_name}"
                    data["display_messages"] = [("system", msg, {})]
                    modal_def = handler._get_skills_modal_definition(skip_reload=True)
                    if modal_def:
                        data["show_modal"] = modal_def
                else:
                    data["display_messages"] = [
                        ("error", "[err] Failed to rename skill", {}),
                    ]

    # Handle delete skill - show confirmation modal
    elif action == "delete_skill_prompt":
        skill_name = command.get("skill_name")
        if skill_name and handler.agent_manager:
            active_agent = handler.agent_manager.get_active_agent()
            if active_agent:
                modal_def = handler._get_delete_skill_confirm_modal(
                    active_agent.name, skill_name
                )
                if modal_def:
                    data["show_modal"] = modal_def
                else:
                    data["display_messages"] = [
                        ("error", f"[err] Cannot delete skill: {skill_name}", {}),
                    ]
            else:
                data["display_messages"] = [
                    ("error", "[err] Select a skill to delete", {}),
                ]

    # Handle delete skill confirmation
    elif action == "delete_skill_confirm":
        skill_name = command.get("skill_name")
        if skill_name and handler.agent_manager:
            active_agent = handler.agent_manager.get_active_agent()
            if active_agent:
                success = handler._delete_skill_file(active_agent, skill_name)
                if success:
                    handler.agent_manager.refresh()
                    data["display_messages"] = [
                        ("system", f"[ok] Deleted skill: {skill_name}", {}),
                    ]
                    modal_def = handler._get_skills_modal_definition(skip_reload=True)
                    if modal_def:
                        data["show_modal"] = modal_def
                else:
                    data["display_messages"] = [
                        ("error", f"[err] Failed to delete skill: {skill_name}", {}),
                    ]

    return data
