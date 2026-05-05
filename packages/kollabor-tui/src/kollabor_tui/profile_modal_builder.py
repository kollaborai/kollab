"""Profile modal definition builders.

Pure functions that construct modal definition dicts for profile management.
These take data as parameters and return UI configuration, with no dependencies
on handler state or self.

EXPORTS TO ADD TO packages/kollabor-tui/src/kollabor_tui/__init__.py:

    from .profile_modal_builder import (
        build_profiles_modal,
        build_create_profile_modal,
        build_edit_profile_modal,
        build_delete_profile_confirm_modal,
    )

And add these to __all__:
    "build_profiles_modal",
    "build_create_profile_modal",
    "build_edit_profile_modal",
    "build_delete_profile_confirm_modal",
"""

from typing import Any, Dict, List, Optional


def build_profiles_modal(
    profiles_data: List[Dict[str, Any]],
    active_profile: str,
    provider_profiles: Optional[set] = None,
    project_default: Optional[str] = None,
    global_default: Optional[str] = None,
) -> Dict[str, Any]:
    """Build modal definition for profile selection.

    Args:
        profiles_data: List of profile dicts with name, model, api_url, provider.
        active_profile: Name of currently active profile.
        provider_profiles: Set of profile names that use provider format.
        project_default: Profile name set as project default (or None).
        global_default: Profile name set as global default (or None).

    Returns:
        Modal definition dictionary.
    """
    provider_profiles = provider_profiles or set()

    # Build profile list for modal
    profile_items = []
    for profile in profiles_data:
        is_active = profile.get("name") == active_profile
        model = profile.get("model") or ""
        api_url = profile.get("api_url") or ""
        profile_name = profile.get("name")

        # Check if this is a provider-format profile
        is_provider = profile_name in provider_profiles
        provider_type = profile.get("provider")

        # Build description
        if is_provider and provider_type:
            if model:
                description = f"[{provider_type}] {model}"
            else:
                description = f"[{provider_type}] no model set"
        elif model and api_url:
            description = f"{model} @ {api_url}"
        elif model:
            description = model
        elif api_url:
            description = f"@ {api_url}"
        else:
            description = "not configured"

        # Build default indicators
        indicators = []
        if profile_name == project_default:
            indicators.append("P")
        if profile_name == global_default:
            indicators.append("G")
        indicator_str = f"[{''.join(indicators)}] " if indicators else "    "

        profile_items.append(
            {
                "name": f"{'[*] ' if is_active else '    '}{profile_name}  {indicator_str}",
                "description": description,
                "profile_name": profile_name,
                "action": "select_profile",
            }
        )

    # Add management options
    management_items = [
        {
            "name": "    [+] Save to Config",
            "description": "Save current profile to config.json",
            "action": "save_profile_to_config",
        },
        {
            "name": "    [+] Create New",
            "description": "Create a new profile",
            "action": "create_profile_prompt",
        },
    ]

    # Env var help section (non-selectable info items)
    env_help_items = [
        {
            "name": "auto-create from env vars",
            "description": "kollab --profile NAME --save",
            "action": "noop",
            "selectable": False,
        },
        {
            "name": "model (required)",
            "description": "KOLLAB_{NAME}_MODEL",
            "action": "noop",
            "selectable": False,
        },
        {
            "name": "provider",
            "description": "KOLLAB_{NAME}_PROVIDER",
            "action": "noop",
            "selectable": False,
        },
        {
            "name": "base URL",
            "description": "KOLLAB_{NAME}_BASE_URL",
            "action": "noop",
            "selectable": False,
        },
        {
            "name": "API key",
            "description": "KOLLAB_{NAME}_API_KEY",
            "action": "noop",
            "selectable": False,
        },
        {
            "name": "max tokens",
            "description": "KOLLAB_{NAME}_MAX_TOKENS",
            "action": "noop",
            "selectable": False,
        },
        {
            "name": "temperature",
            "description": "KOLLAB_{NAME}_TEMPERATURE",
            "action": "noop",
            "selectable": False,
        },
        {
            "name": "timeout (ms)",
            "description": "KOLLAB_{NAME}_TIMEOUT",
            "action": "noop",
            "selectable": False,
        },
        {
            "name": "supports tools",
            "description": "KOLLAB_{NAME}_SUPPORTS_TOOLS",
            "action": "noop",
            "selectable": False,
        },
    ]

    return {
        "title": "LLM Profiles",
        "footer": "↑↓ navigate • Enter select • e edit • d delete • p project default • g global default • Esc exit",
        "sections": [
            {
                "title": f"Available Profiles (active: {active_profile})",
                "commands": profile_items,
            },
            {"title": "Management", "commands": management_items},
            {
                "title": "Create via Environment Variables",
                "commands": env_help_items,
            },
        ],
        "actions": [
            {"key": "Enter", "label": "Select", "action": "select"},
            {"key": "e", "label": "Edit", "action": "edit_profile_prompt"},
            {"key": "d", "label": "Delete", "action": "delete_profile_prompt"},
            {
                "key": "p",
                "label": "Project default",
                "action": "toggle_project_default_profile",
            },
            {
                "key": "g",
                "label": "Global default",
                "action": "toggle_global_default_profile",
            },
            {"key": "Escape", "label": "Close", "action": "cancel"},
        ],
    }


def build_create_profile_modal(
    providers: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Build modal definition for creating a new profile.

    Args:
        providers: List of provider names for dropdown (uses default if None).

    Returns:
        Modal definition dictionary.
    """
    if providers is None:
        providers = [
            "custom",
            "openai",
            "anthropic",
            "azure_openai",
            "gemini",
            "openai_responses",
            "openrouter",
        ]

    return {
        "title": "Create New Profile",
        "footer": "Tab: next • Ctrl+S: create • Esc: cancel",
        "form_action": "create_profile_submit",
        "sections": [
            {
                "title": "Profile Name (required)",
                "widgets": [
                    {
                        "type": "text_input",
                        "label": "Name *",
                        "field": "name",
                        "placeholder": "my-llm, claude-prod, openai-dev, etc.",
                        "help": "Used for env vars: KOLLAB_{NAME}_API_KEY",
                    },
                    {
                        "type": "text_input",
                        "label": "Description",
                        "field": "description",
                        "placeholder": "Optional description",
                        "help": "Describe this profile's purpose",
                    },
                ],
            },
            {
                "title": "Connection (required)",
                "widgets": [
                    {
                        "type": "text_input",
                        "label": "Base URL *",
                        "field": "base_url",
                        "placeholder": "https://api.openai.com/v1/chat/completions",
                        "help": "API endpoint URL",
                    },
                    {
                        "type": "dropdown",
                        "label": "Provider",
                        "field": "provider",
                        "options": providers,
                        "current_value": "custom",
                        "help": "API provider type",
                    },
                    {
                        "type": "text_input",
                        "label": "API Key",
                        "field": "api_key",
                        "placeholder": "sk-... or leave empty for env var",
                        "password": True,
                        "help": "API key (or set via KOLLAB_{NAME}_API_KEY)",
                    },
                ],
            },
            {
                "title": "Model Settings (required)",
                "widgets": [
                    {
                        "type": "text_input",
                        "label": "Model *",
                        "field": "model",
                        "placeholder": "gpt-5.4, claude-sonnet-4-6, qwen/qwen3-4b",
                        "help": "Model identifier",
                    },
                    {
                        "type": "slider",
                        "label": "Temperature",
                        "field": "temperature",
                        "min_value": 0.0,
                        "max_value": 2.0,
                        "step": 0.1,
                        "current_value": 0.7,
                        "help": "0.0 = precise, 2.0 = creative",
                    },
                    {
                        "type": "text_input",
                        "label": "Max Tokens",
                        "field": "max_tokens",
                        "placeholder": "4096",
                        "help": "Optional: leave empty for API default",
                    },
                ],
            },
        ],
        "actions": [
            {
                "key": "Ctrl+S",
                "label": "[ Create ]",
                "action": "submit",
                "style": "primary",
            },
            {
                "key": "Escape",
                "label": "[ Cancel ]",
                "action": "cancel",
                "style": "secondary",
            },
        ],
    }


def build_edit_profile_modal(
    profile_data: Dict[str, Any],
    providers: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Build modal definition for editing an existing profile.

    Args:
        profile_data: Dict with profile fields (name, model, base_url, provider,
                      api_key, supports_tools, temperature, description).
        providers: List of provider names for dropdown (uses default if None).

    Returns:
        Modal definition dict with pre-populated values.
    """
    if providers is None:
        providers = [
            "openai",
            "anthropic",
            "azure_openai",
            "gemini",
            "openai_responses",
            "custom",
            "openrouter",
        ]

    # Extract profile fields
    profile_name = profile_data.get("name", "")
    model = profile_data.get("model", "")
    base_url = profile_data.get("base_url") or profile_data.get("api_url") or ""
    provider = profile_data.get("provider") or "openai"
    supports_tools = profile_data.get("supports_tools", True)
    temperature = profile_data.get("temperature", 0.7)
    description = profile_data.get("description", "")

    # API key fields
    api_key_masked = profile_data.get("api_key_masked", "")
    api_key_status = profile_data.get("api_key_status", "")
    api_key_placeholder = profile_data.get("api_key_placeholder", "")

    # Env var hints
    env_api_key_set = profile_data.get("env_api_key_set", False)
    env_api_key_name = profile_data.get("env_api_key_name", "")
    provider_from_env = profile_data.get("provider_from_env", False)
    env_provider_name = profile_data.get("env_provider_name", "")

    # Status line
    status_line = profile_data.get("status_line", "[ok] Ready to use")

    # Build help text based on env var override
    if provider_from_env:
        provider_help = f"[!] Overridden by {env_provider_name}={provider} (unset env var to use config)"
    else:
        provider_help = "API provider type"

    return {
        "title": f"Edit Profile: {profile_name}",
        "footer": "Tab: next • Ctrl+S: save • Ctrl+T: test • Esc: cancel",
        "form_action": "edit_profile_submit",
        "edit_profile_name": profile_name,
        "sections": [
            {
                "title": "Connection (required)",
                "widgets": [
                    {
                        "type": "text_input",
                        "label": "Base URL *",
                        "field": "base_url",
                        "value": base_url,
                        "placeholder": "https://api.openai.com/v1/chat/completions",
                        "help": "API endpoint URL",
                    },
                    {
                        "type": "dropdown",
                        "label": "Provider",
                        "field": "provider",
                        "options": providers,
                        "current_value": provider,
                        "help": provider_help,
                    },
                    {
                        "type": "dropdown",
                        "label": "Tool Calling",
                        "field": "supports_tools",
                        "options": ["enabled", "disabled"],
                        "current_value": "enabled" if supports_tools else "disabled",
                        "help": "Enable function/tool calling",
                    },
                ],
            },
            {
                "title": "Authentication (required)",
                "widgets": [
                    {
                        "type": "text_input",
                        "label": f"API Key * {api_key_status}",
                        "field": "api_key",
                        "value": api_key_masked,
                        "placeholder": api_key_placeholder,
                        "password": True,
                    },
                ],
            },
            {
                "title": "Model (required)",
                "widgets": [
                    {
                        "type": "text_input",
                        "label": "Model *",
                        "field": "model",
                        "value": model,
                        "placeholder": "gpt-5.4, claude-sonnet-4-6, etc.",
                        "help": "Model identifier",
                    },
                ],
            },
            {
                "title": "Advanced (optional)",
                "widgets": [
                    {
                        "type": "text_input",
                        "label": "Profile Name",
                        "field": "name",
                        "value": profile_name,
                        "placeholder": "my-profile",
                        "help": "Determines env var prefix: KOLLAB_{NAME}_*",
                    },
                    {
                        "type": "slider",
                        "label": "Temperature",
                        "field": "temperature",
                        "min_value": 0.0,
                        "max_value": 2.0,
                        "step": 0.1,
                        "current_value": temperature,
                        "help": "0.0 = precise, 2.0 = creative",
                    },
                    {
                        "type": "text_input",
                        "label": "Description",
                        "field": "description",
                        "value": description,
                        "placeholder": "Optional description",
                    },
                ],
            },
            {
                "title": f"Status: {status_line}",
                "widgets": [
                    {
                        "type": "label",
                        "label": "Env vars",
                        "value": f"{env_api_key_name}={'[set]' if env_api_key_set else '[not set]'}",
                    },
                ],
            },
        ],
        "actions": [
            {
                "key": "Ctrl+S",
                "label": "[ Save ]",
                "action": "submit",
                "style": "primary",
            },
            {
                "key": "Ctrl+T",
                "label": "[ Test ]",
                "action": "test_connection",
                "style": "secondary",
            },
            {
                "key": "Escape",
                "label": "[ Cancel ]",
                "action": "cancel",
                "style": "secondary",
            },
        ],
    }


def build_delete_profile_confirm_modal(
    profile_name: str,
    model: str,
    api_url: str,
    is_active: bool,
) -> Dict[str, Any]:
    """Build modal definition for delete profile confirmation.

    Args:
        profile_name: Name of the profile to delete.
        model: Profile model name.
        api_url: Profile API endpoint.
        is_active: Whether this is the currently active profile.

    Returns:
        Modal definition dict for confirmation.
    """
    warning_msg = ""
    if is_active:
        warning_msg = "\n\n[!] This is the currently active profile.\n    You must switch to another profile first."
        action = "cancel"
    else:
        action = "delete_profile_confirm"

    return {
        "title": f"Delete Profile: {profile_name}?",
        "footer": "Enter confirm • Esc cancel",
        "width": 60,
        "height": 12,
        "sections": [
            {
                "title": "Confirm Deletion",
                "commands": [
                    {
                        "name": f"Delete '{profile_name}'",
                        "description": f"Model: {model} @ {api_url or 'unknown'}{warning_msg}",
                        "profile_name": profile_name,
                        "action": action,
                    },
                    {
                        "name": "Cancel",
                        "description": "Keep the profile",
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
