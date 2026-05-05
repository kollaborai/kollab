"""Model modal action handlers.

Handles modal actions for model selection.
"""

import logging

from kollabor_events.models import Event

from .model import ModelCommandHandler

logger = logging.getLogger(__name__)


async def handle_model_modal_actions(
    data: dict, event: Event, handler: ModelCommandHandler
) -> dict:
    """Handle model-related modal actions.

    Args:
        data: Event data containing command info.
        event: Event object.
        handler: Model command handler instance.

    Returns:
        Modified data dict with display_messages/show_modal keys.
    """
    command = data.get("command", {})
    action = command.get("action")

    logger.info(f"Model modal action received: {action}")

    # Handle model selection
    if action == "select_model":
        model_name = command.get("model_name")
        profile_name = command.get("profile_name")
        if model_name and profile_name and handler.profile_manager:
            if handler.profile_manager.set_active_profile(profile_name):
                profile = handler.profile_manager.get_active_profile()
                # Reinitialize the provider with new profile settings
                if handler.llm_service and hasattr(handler.llm_service, "api_service"):
                    handler.llm_service.create_background_task(
                        handler.llm_service.api_service.reinitialize_provider(profile),
                        name="reinitialize_provider",
                    )
                    # Reload native tools (profile may have different supports_tools setting)
                    handler.llm_service.create_background_task(
                        handler.llm_service._load_native_tools(),
                        name="reload_native_tools",
                    )
                tools_mode = "enabled" if profile.get_supports_tools() else "disabled"
                data["display_messages"] = [
                    (
                        "system",
                        f"[ok] Switched to model: {model_name}\n  Profile: {profile_name}\n  Tools: {tools_mode}",
                        {},
                    ),
                ]
            else:
                data["display_messages"] = [
                    ("error", f"[err] Profile not found: {profile_name}", {}),
                ]

    return data
