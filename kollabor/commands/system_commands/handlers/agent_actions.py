"""Agent modal action handlers.

Handles modal actions for agent creation, editing, deletion, defaults.
"""

import logging

from kollabor_events.models import Event

from .agent import AgentCommandHandler

logger = logging.getLogger(__name__)


async def handle_agent_modal_actions(
    data: dict, event: Event, handler: AgentCommandHandler
) -> dict:
    """Handle agent-related modal actions.

    Args:
        data: Event data containing command info.
        event: Event object.
        handler: Agent command handler instance.

    Returns:
        Modified data dict with display_messages/show_modal keys.
    """
    command = data.get("command", {})
    action = command.get("action")

    logger.info(f"Agent modal action received: {action}")

    # Phase 4.5 step 7: all state mutations go through state_service.
    # No legacy fallback to direct agent_manager access -- the service
    # is wired in both local and attach mode, so a missing service is
    # a real error the user should see.
    state_service = None
    if handler.event_bus and hasattr(handler.event_bus, "get_service"):
        state_service = handler.event_bus.get_service("state_service")

    # Handle agent selection
    if action == "select_agent":
        agent_name = command.get("agent_name")
        if not agent_name:
            return data
        if state_service is None:
            data["display_messages"] = [
                (
                    "error",
                    "[err] State service not available -- cannot switch agent.",
                    {},
                ),
            ]
        else:
            try:
                snapshot = await state_service.set_agent(agent_name)
                skill_info = (
                    f" ({len(snapshot.active_skills)} skills)"
                    if snapshot.active_skills
                    else ""
                )
                msg = f"[ok] Switched to agent: {snapshot.name}{skill_info}"
                if snapshot.profile:
                    msg += f"\n  Preferred profile: {snapshot.profile}"
                data["display_messages"] = [("system", msg, {})]
            except ValueError as e:
                data["display_messages"] = [
                    ("error", f"[err] Agent not found: {agent_name}\n{e}", {}),
                ]
            except Exception as e:
                logger.error(f"state_service.set_agent failed: {e}")
                data["display_messages"] = [
                    ("error", f"[err] Failed to switch agent: {e}", {}),
                ]

    # Handle agent clear
    elif action == "clear_agent":
        if state_service is None:
            data["display_messages"] = [
                (
                    "error",
                    "[err] State service not available -- cannot clear agent.",
                    {},
                ),
            ]
        else:
            try:
                await state_service.clear_agent()
                data["display_messages"] = [
                    ("system", "[ok] Cleared active agent", {}),
                ]
            except Exception as e:
                logger.error(f"state_service.clear_agent failed: {e}")
                data["display_messages"] = [
                    ("error", f"[err] Failed to clear agent: {e}", {}),
                ]

    # Handle create agent - show form modal
    elif action == "create_agent_prompt":
        data["show_modal"] = handler._get_create_agent_modal_definition()

    # Handle create agent form submission - AI generation
    elif action == "create_agent_submit":
        form_data = command.get("form_data", {})
        name = form_data.get("name", "").strip()
        description = form_data.get("description", "").strip()
        profile = form_data.get("profile", "").strip()
        source = form_data.get("source", "global").strip()

        if not name:
            data["display_messages"] = [
                ("error", "[err] Agent name is required", {}),
            ]
        elif not description:
            data["display_messages"] = [
                ("error", "[err] Description is required for AI generation", {}),
            ]
        elif handler.llm_service:
            # Build the generation prompt and send to LLM
            generation_prompt = handler._build_agent_generation_prompt(
                name=name,
                description=description,
                profile=profile if profile and profile != "(none)" else None,
                source=source,
            )
            # Send to LLM - it will use @@@FILE blocks to generate files
            await handler.llm_service.process_user_input(generation_prompt)
            # Close modal - LLM handles the rest with existing tool infrastructure
            data["close_modal"] = True
        else:
            data["display_messages"] = [
                ("error", "[err] LLM service not available", {}),
            ]

    # Handle edit agent - show form modal with agent data
    elif action == "edit_agent_prompt":
        agent_name = command.get("agent_name")
        if agent_name and handler.agent_manager:
            modal_def = handler._get_edit_agent_modal_definition(agent_name)
            if modal_def:
                data["show_modal"] = modal_def
            else:
                data["display_messages"] = [
                    ("error", f"[err] Agent not found: {agent_name}", {}),
                ]
        else:
            data["display_messages"] = [
                ("error", "[err] Select an agent to edit", {}),
            ]

    # Handle toggle project default
    elif action == "toggle_project_default":
        agent_name = command.get("agent_name")
        if agent_name and handler.agent_manager:
            from kollabor_config.config_utils import (
                clear_default_agent,
                get_all_default_agents,
                set_default_agent,
            )

            # Check if this agent is already project default
            defaults = get_all_default_agents()
            current_project_default = defaults.get("project")

            if current_project_default == agent_name:
                # Clear it
                if clear_default_agent("project"):
                    data["display_messages"] = [
                        ("system", "[ok] Cleared project default agent", {}),
                    ]
            else:
                # Set it
                if set_default_agent(agent_name, "project"):
                    data["display_messages"] = [
                        (
                            "system",
                            f"[ok] Set '{agent_name}' as project default agent",
                            {},
                        ),
                    ]
                else:
                    data["display_messages"] = [
                        ("error", "[err] Failed to set project default", {}),
                    ]

            # Reopen modal to show updated indicators
            modal_def = handler._get_agents_modal_definition(skip_reload=True)
            if modal_def:
                data["show_modal"] = modal_def

    # Handle toggle global default
    elif action == "toggle_global_default":
        agent_name = command.get("agent_name")
        if agent_name and handler.agent_manager:
            from kollabor_config.config_utils import (
                clear_default_agent,
                get_all_default_agents,
                set_default_agent,
            )

            # Check if this agent is already global default
            defaults = get_all_default_agents()
            current_global_default = defaults.get("global")

            if current_global_default == agent_name:
                # Clear it
                if clear_default_agent("global"):
                    data["display_messages"] = [
                        ("system", "[ok] Cleared global default agent", {}),
                    ]
            else:
                # Set it
                if set_default_agent(agent_name, "global"):
                    data["display_messages"] = [
                        (
                            "system",
                            f"[ok] Set '{agent_name}' as global default agent",
                            {},
                        ),
                    ]
                else:
                    data["display_messages"] = [
                        ("error", "[err] Failed to set global default", {}),
                    ]

            # Reopen modal to show updated indicators
            modal_def = handler._get_agents_modal_definition(skip_reload=True)
            if modal_def:
                data["show_modal"] = modal_def

    # Handle edit agent form submission
    elif action == "edit_agent_submit":
        form_data = command.get("form_data", {})
        original_name = command.get("edit_agent_name", "")
        new_name = form_data.get("name", "").strip()
        description = form_data.get("description", "").strip()
        profile = form_data.get("profile", "").strip()

        if not new_name:
            data["display_messages"] = [
                ("error", "[err] Agent name is required", {}),
            ]
        elif handler.agent_manager:
            success = handler.agent_manager.update_agent(
                original_name=original_name,
                new_name=new_name,
                description=description,
                profile=profile if profile and profile != "(none)" else None,
                system_prompt=None,  # Don't update system_prompt via modal
            )
            if success:
                msg = f"[ok] Updated agent: {new_name}"
                if new_name != original_name:
                    msg += f"\n  Renamed from: {original_name}"
                if description:
                    msg += f"\n  Description: {description[:50]}..."
                data["display_messages"] = [("system", msg, {})]
                # Reopen the agents modal
                data["show_modal"] = handler._get_agents_modal_definition(
                    skip_reload=True
                )
            else:
                data["display_messages"] = [
                    ("error", "[err] Failed to update agent", {}),
                ]

    # Handle delete agent prompt - show confirmation modal
    elif action == "delete_agent_prompt":
        agent_name = command.get("agent_name")
        if agent_name and handler.agent_manager:
            modal_def = handler._get_delete_agent_confirm_modal(agent_name)
            if modal_def:
                data["show_modal"] = modal_def
            else:
                data["display_messages"] = [
                    ("error", f"[err] Cannot delete agent: {agent_name}", {}),
                ]
        else:
            data["display_messages"] = [
                ("error", "[err] Select an agent to delete", {}),
            ]

    # Handle delete agent confirmation
    elif action == "delete_agent_confirm":
        agent_name = command.get("agent_name")
        if agent_name and handler.agent_manager:
            success = handler.agent_manager.delete_agent(agent_name)
            if success:
                data["display_messages"] = [
                    ("system", f"[ok] Deleted agent: {agent_name}", {}),
                ]
                # Reopen the agents modal so user can continue managing
                # Skip reload since memory state is already updated
                data["show_modal"] = handler._get_agents_modal_definition(
                    skip_reload=True
                )
            else:
                data["display_messages"] = [
                    ("error", f"[err] Failed to delete agent: {agent_name}", {}),
                ]

    return data
