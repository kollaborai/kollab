"""Profile modal action handlers.

Handles modal actions for profile creation, editing, deletion.
"""

import logging
from typing import Optional

from kollabor_ai.profile_manager import EnvVarHint
from kollabor_events.models import Event

from .profile import ProfileCommandHandler


def _mask_api_key(key: Optional[str]) -> str:
    """Mask an API key for display, showing only first 3 and last 4 chars."""
    if not key:
        return ""
    if len(key) <= 8:
        return key[:2] + "..."
    return key[:3] + "..." + key[-4:]


logger = logging.getLogger(__name__)


async def handle_profile_modal_actions(
    data: dict, event: Event, handler: ProfileCommandHandler
) -> dict:
    """Handle profile-related modal actions.

    Args:
        data: Event data containing command info.
        event: Event object.
        handler: Profile command handler instance.

    Returns:
        Modified data dict with display_messages/show_modal keys.
    """
    command = data.get("command", {})
    action = command.get("action")

    logger.info(f"Profile modal action received: {action}")

    # Handle profile selection
    if action == "select_profile":
        profile_name = command.get("profile_name")
        if profile_name and handler.profile_manager:
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
                        (
                            f"Switched to profile: {profile_name}\n"
                            f"  Model: {profile.get_model()}\n"
                            f"  Base URL: {profile.get_endpoint()}\n"
                            f"  Provider: {profile.get_provider()}\n"
                            f"  Tools: {tools_mode}"
                        ),
                        {"display_type": "success"},
                    ),
                ]
            else:
                data["display_messages"] = [
                    ("error", f"Profile not found: {profile_name}", {}),
                ]

    # Handle save profile to config
    elif action == "save_profile_to_config":
        if handler.profile_manager:
            profile = handler.profile_manager.get_active_profile()
            if profile:
                success = handler.profile_manager.save_profile_values_to_config(profile)

                if success:
                    # Reload profiles from config to pick up saved values
                    handler.profile_manager.reload()
                    data["display_messages"] = [
                        (
                            "system",
                            f"Saved '{profile.name}' profile to config",
                            {"display_type": "success"},
                        ),
                    ]
                else:
                    data["display_messages"] = [
                        (
                            "error",
                            f"Failed to save profile '{profile.name}'.",
                            {},
                        ),
                    ]
                # Reopen the profile modal (skip_reload since we just reloaded above)
                data["show_modal"] = await handler._get_profiles_modal_definition(
                    skip_reload=True
                )
            else:
                data["display_messages"] = [
                    ("error", "No active profile to save.", {}),
                ]

    # Handle create profile - show form modal
    elif action == "create_profile_prompt":
        data["show_modal"] = handler._get_create_profile_modal_definition()

    # Handle create profile form submission
    elif action == "create_profile_submit":
        form_data = command.get("form_data", {})
        name = form_data.get("name", "").strip()
        model = form_data.get("model", "").strip()
        temperature = float(form_data.get("temperature", 0.7))
        max_tokens = form_data.get("max_tokens", "").strip()
        max_tokens = int(max_tokens) if max_tokens else None
        description = form_data.get("description", "").strip()

        # Profile fields (unified format)
        provider = form_data.get("provider", "custom").strip() or "custom"
        api_key = form_data.get("api_key", "").strip() or None
        base_url = form_data.get("base_url", "").strip()

        # Validation
        if not name or not model:
            data["display_messages"] = [
                ("error", "Name and Model are required", {}),
            ]
        elif not base_url:
            data["display_messages"] = [
                ("error", "Base URL is required", {}),
            ]
        elif handler.profile_manager:
            # Create profile with unified format
            profile = handler.profile_manager.create_profile(
                name=name,
                base_url=base_url,
                model=model,
                api_key=api_key,
                temperature=temperature,
                max_tokens=max_tokens,
                provider=provider,
                supports_tools=True,
                description=description or "Created via /profile",
                save_to_config=True,
            )
            if profile:
                data["display_messages"] = [
                    (
                        "system",
                        (
                            f"Created profile: {name}\n"
                            f"  Base URL: {base_url}\n"
                            f"  Model: {model}\n"
                            f"  Provider: {provider}\n"
                            f"  Saved to config.json"
                        ),
                        {"display_type": "success"},
                    ),
                ]
                data["show_modal"] = await handler._get_profiles_modal_definition(
                    skip_reload=True
                )
            else:
                data["display_messages"] = [
                    (
                        "error",
                        f"Failed to create profile '{name}' — may already exist.",
                        {},
                    ),
                ]

    # Handle edit profile - show form modal with profile data
    elif action == "edit_profile_prompt":
        profile_name = command.get("profile_name")
        if profile_name and handler.profile_manager:
            modal_def = handler._get_edit_profile_modal_definition(profile_name)
            if modal_def:
                data["show_modal"] = modal_def
            else:
                data["display_messages"] = [
                    ("error", f"Profile not found: {profile_name}", {}),
                ]
        else:
            data["display_messages"] = [
                ("error", "Select a profile to edit", {}),
            ]

    # Handle edit profile form submission
    elif action == "edit_profile_submit":
        form_data = command.get("form_data", {})
        original_name = command.get("edit_profile_name", "")
        new_name = form_data.get("name", "").strip()
        base_url = form_data.get("base_url", "").strip()
        model = form_data.get("model", "").strip()
        submitted_api_key = form_data.get("api_key", "").strip()
        temperature = float(form_data.get("temperature", 0.7))
        provider = form_data.get("provider", "openai")
        # Convert dropdown value to bool (enabled=True, disabled=False)
        supports_tools = form_data.get("supports_tools", "enabled") == "enabled"
        description = form_data.get("description", "").strip()

        # Check if API key was actually changed (not just the masked display value)
        # The form pre-populates with masked value like "sk-...abcd"
        # Only update if user typed a NEW key (not the masked placeholder)
        api_key = None  # Default: don't update
        if submitted_api_key:
            original_profile = (
                handler.profile_manager.get_profile(original_name)
                if handler.profile_manager
                else None
            )
            original_api_key = original_profile.api_key if original_profile else None
            masked_original = _mask_api_key(original_api_key)

            if submitted_api_key != masked_original:
                # User entered a new API key - use it
                api_key = submitted_api_key
            # else: submitted value matches masked display - don't overwrite the real key

        if not new_name or not base_url or not model:
            data["display_messages"] = [
                ("error", "Name, Base URL, and Model are required", {}),
            ]
        elif handler.profile_manager:
            success = handler.profile_manager.update_profile(
                original_name=original_name,
                new_name=new_name,
                base_url=base_url,
                model=model,
                api_key=api_key,
                temperature=temperature,
                provider=provider,
                supports_tools=supports_tools,
                description=description,
                save_to_config=True,
            )
            if success:
                # If this profile is active (check both original and new name), reinitialize the provider
                is_active = handler.profile_manager.is_active(
                    new_name
                ) or handler.profile_manager.is_active(original_name)
                if (
                    is_active
                    and handler.llm_service
                    and hasattr(handler.llm_service, "api_service")
                ):
                    profile = handler.profile_manager.get_profile(
                        new_name
                    ) or handler.profile_manager.get_profile(original_name)
                    if profile:
                        # Reinitialize provider (handles provider type changes)
                        handler.llm_service.create_background_task(
                            handler.llm_service.api_service.reinitialize_provider(
                                profile
                            ),
                            name="reinitialize_provider",
                        )
                        # Reload native tools (tool calling mode may have changed)
                        handler.llm_service.create_background_task(
                            handler.llm_service._load_native_tools(),
                            name="reload_native_tools",
                        )

                # Check for env var overrides and warn user
                profile = handler.profile_manager.get_profile(new_name)
                env_hints = profile.get_env_var_hints() if profile else {}
                env_overrides = []
                if env_hints.get("provider", EnvVarHint("", False)).is_set:
                    env_overrides.append(f"provider ({env_hints['provider'].name})")
                if env_hints.get("model", EnvVarHint("", False)).is_set:
                    env_overrides.append(f"model ({env_hints['model'].name})")
                if env_hints.get("base_url", EnvVarHint("", False)).is_set:
                    env_overrides.append(f"base_url ({env_hints['base_url'].name})")

                tools_mode = "enabled" if supports_tools else "disabled"
                msg = (
                    f"Updated profile: {new_name}\n"
                    f"  Base URL: {base_url}\n"
                    f"  Model: {model}\n"
                    f"  Provider: {provider}\n"
                    f"  Tools: {tools_mode}"
                )
                if is_active:
                    msg += "\n  [reloaded - changes applied]"
                if env_overrides:
                    msg += (
                        f"\n\n[warn] Env vars will override: {', '.join(env_overrides)}"
                    )
                    msg += "\n  Unset these env vars to use config values"
                data["display_messages"] = [
                    ("system", msg, {"display_type": "success"})
                ]
                # Reopen the profile modal
                data["show_modal"] = await handler._get_profiles_modal_definition(
                    skip_reload=True
                )
            else:
                data["display_messages"] = [
                    ("error", "Failed to update profile", {}),
                ]

    # Handle delete profile prompt - show confirmation modal
    elif action == "delete_profile_prompt":
        profile_name = command.get("profile_name")
        if profile_name and handler.profile_manager:
            modal_def = handler._get_delete_profile_confirm_modal(profile_name)
            if modal_def:
                data["show_modal"] = modal_def
            else:
                data["display_messages"] = [
                    ("error", f"Cannot delete profile: {profile_name}", {}),
                ]
        else:
            data["display_messages"] = [
                ("error", "Select a profile to delete", {}),
            ]

    # Handle delete profile confirmation
    elif action == "delete_profile_confirm":
        profile_name = command.get("profile_name")
        if profile_name and handler.profile_manager:
            success = handler.profile_manager.delete_profile(profile_name)
            if success:
                data["display_messages"] = [
                    (
                        "system",
                        f"Deleted profile: {profile_name}",
                        {"display_type": "info"},
                    ),
                ]
                # Reopen the profile modal so user can continue managing
                # Skip reload since memory state is already updated
                data["show_modal"] = await handler._get_profiles_modal_definition(
                    skip_reload=True
                )
            else:
                data["display_messages"] = [
                    ("error", f"Failed to delete profile: {profile_name}", {}),
                ]

    # Handle toggle project default
    elif action == "toggle_project_default_profile":
        profile_name = command.get("profile_name")
        if profile_name and handler.profile_manager:
            from kollabor_config.config_utils import (
                clear_default_profile,
                get_all_default_profiles,
                set_default_profile,
            )

            defaults = get_all_default_profiles()
            current_project_default = defaults.get("project")

            if current_project_default == profile_name:
                if clear_default_profile("project"):
                    data["display_messages"] = [
                        (
                            "system",
                            "Cleared project default profile",
                            {"display_type": "info"},
                        ),
                    ]
            else:
                if set_default_profile(profile_name, "project"):
                    data["display_messages"] = [
                        (
                            "system",
                            f"Set '{profile_name}' as project default profile",
                            {"display_type": "success"},
                        ),
                    ]
                else:
                    data["display_messages"] = [
                        ("error", "Failed to set project default profile", {}),
                    ]

            data["show_modal"] = await handler._get_profiles_modal_definition(
                skip_reload=True
            )

    # Handle toggle global default
    elif action == "toggle_global_default_profile":
        profile_name = command.get("profile_name")
        if profile_name and handler.profile_manager:
            from kollabor_config.config_utils import (
                clear_default_profile,
                get_all_default_profiles,
                set_default_profile,
            )

            defaults = get_all_default_profiles()
            current_global_default = defaults.get("global")

            if current_global_default == profile_name:
                if clear_default_profile("global"):
                    data["display_messages"] = [
                        (
                            "system",
                            "Cleared global default profile",
                            {"display_type": "info"},
                        ),
                    ]
            else:
                if set_default_profile(profile_name, "global"):
                    data["display_messages"] = [
                        (
                            "system",
                            f"Set '{profile_name}' as global default profile",
                            {"display_type": "success"},
                        ),
                    ]
                else:
                    data["display_messages"] = [
                        ("error", "Failed to set global default profile", {}),
                    ]

            data["show_modal"] = await handler._get_profiles_modal_definition(
                skip_reload=True
            )

    return data
