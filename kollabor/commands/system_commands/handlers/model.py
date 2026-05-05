"""Model command handler.

Handles /model command - quick model selector.
"""

import logging
from typing import Any, Dict

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


class ModelCommandHandler(BaseCommandHandler):
    """Handles /model command - quick model selector."""

    MODAL_ACTIONS = {"select_model"}

    def __init__(
        self,
        command_registry,
        event_bus,
        profile_manager=None,
        llm_service=None,
    ):
        """Initialize model command handler.

        Args:
            command_registry: Command registry for registration.
            event_bus: Event bus for service lookup.
            profile_manager: Profile manager instance.
            llm_service: Optional LLM service instance.
        """
        super().__init__(command_registry, event_bus)
        self.profile_manager = profile_manager
        self._llm_service_override = llm_service

    @property
    def llm_service(self):
        """Get LLM service via override or service registry."""
        if self._llm_service_override is not None:
            return self._llm_service_override
        return self.event_bus.get_service("llm_service")

    def register_commands(self) -> None:
        """Register /model command."""
        model_command = CommandDefinition(
            name="model",
            description="Quick model selector",
            handler=self.handle_model,
            plugin_name="system",
            category=CommandCategory.SYSTEM,
            mode=CommandMode.STATUS_TAKEOVER,
            aliases=["mod", "m"],
            icon="[MODEL]",
            subcommands=[
                SubcommandInfo("list", "", "Show model selection modal"),
                SubcommandInfo(
                    "set", "<name>", "Switch to profile with specified model"
                ),
            ],
            ui_config=UIConfig(
                type="modal",
                navigation=["? ?", "Enter", "Esc"],
                height=12,
                title="Model Selector",
                footer="↑↓ navigate • Enter select • Esc exit",
            ),
        )
        self.command_registry.register_command(model_command)

    async def handle_model(self, command: SlashCommand) -> CommandResult:
        """Handle /model command - quick model selector.

        Shows a modal with all unique models across profiles.
        Selecting a model switches to the first profile that uses it.

        Args:
            command: Parsed slash command.

        Returns:
            Command execution result.
        """
        try:
            profile_manager = self.profile_manager
            if not profile_manager:
                return CommandResult(
                    success=False,
                    message="Profile manager not available",
                    display_type="error",
                )

            args = command.args or []

            if not args or args[0] in ("list", "ls"):
                # Show model selection modal
                return await self._show_models_modal()
            elif args[0] == "set" and len(args) >= 2:
                # Switch to model: /model set <name>
                model_name = args[1]
                return await self._switch_to_model(model_name)
            else:
                # Direct switch: /model <name>
                model_name = args[0]
                return await self._switch_to_model(model_name)

        except Exception as e:
            self.logger.error(f"Error in model command: {e}")
            return CommandResult(
                success=False,
                message=f"Error selecting model: {str(e)}",
                display_type="error",
            )

    def _get_models_modal_definition(self) -> Dict[str, Any]:
        """Get modal definition for model selection.

        Returns:
            Modal definition dictionary with models from all profiles.
        """
        profile_manager = self.profile_manager
        if not profile_manager:
            return {}

        profiles = profile_manager.list_profiles()
        active_profile = profile_manager.get_active_profile()
        active_model = active_profile.get_model() if active_profile else None

        # Build model -> profile mapping (use first profile with that model)
        model_commands = []
        seen_models = set()

        for profile in profiles:
            model = profile.get_model() or "unknown"
            if model in seen_models:
                continue
            seen_models.add(model)

            is_active = model == active_model
            provider = profile.get_provider() or ""

            # Short provider display
            provider_display = ""
            if provider:
                provider_lower = provider.lower()
                if "anthropic" in provider_lower:
                    provider_display = "anthropic"
                elif "openai" in provider_lower:
                    provider_display = "openai"
                elif "glm" in provider_lower or "zhipu" in provider_lower:
                    provider_display = "glm"
                else:
                    provider_display = provider[:15]

            model_commands.append(
                {
                    "name": f"{'[*] ' if is_active else '    '}{model}",
                    "description": f"via {profile.name}"
                    + (f" ({provider_display})" if provider_display else ""),
                    "model_name": model,
                    "profile_name": profile.name,
                    "action": "select_model",
                }
            )

        return {
            "title": "Model Selector",
            "footer": "↑↓ navigate • Enter select • Esc close",
            "sections": [
                {
                    "title": f"Available Models (active: {active_model or 'none'})",
                    "commands": model_commands,
                }
            ],
            "actions": [
                {"key": "Enter", "label": "Select", "action": "select"},
                {"key": "Escape", "label": "Close", "action": "cancel"},
            ],
        }

    async def _show_models_modal(self) -> CommandResult:
        """Show the model selection modal.

        Returns:
            Command result with modal definition.
        """
        modal_def = self._get_models_modal_definition()

        # Check if any models exist
        model_commands = modal_def.get("sections", [{}])[0].get("commands", [])
        if not model_commands:
            return CommandResult(
                success=False, message="No models available", display_type="error"
            )

        return CommandResult(
            success=True,
            message="Select a model",
            ui_config=UIConfig(
                type="modal",
                title=modal_def["title"],
                height=int(modal_def.get("height", 20)),
                modal_config=modal_def,
            ),
            display_type="modal",
        )

    async def _switch_to_model(self, model_name: str) -> CommandResult:
        """Switch to profile with specified model.

        Args:
            model_name: Model name to switch to.

        Returns:
            Command result.
        """
        profile_manager = self.profile_manager
        if not profile_manager:
            return CommandResult(
                success=False,
                message="Profile manager not available",
                display_type="error",
            )

        # Find first profile with this model
        profiles = profile_manager.list_profiles()
        target_profile = None

        for profile in profiles:
            if profile.get_model() == model_name:
                target_profile = profile.name
                break

        if not target_profile:
            # Try partial match
            model_lower = model_name.lower()
            for profile in profiles:
                if model_lower in (profile.get_model() or "").lower():
                    target_profile = profile.name
                    break

        if target_profile:
            return await self._switch_profile(target_profile)
        else:
            available = "\n  ".join(p.get_model() for p in profiles if p.get_model())
            return CommandResult(
                success=False,
                message=f"Model not found: {model_name}\nAvailable:\n  {available}",
                display_type="error",
            )

    async def _switch_profile(self, profile_name: str) -> CommandResult:
        """Switch to a different profile.

        Args:
            profile_name: Name of profile to switch to.

        Returns:
            Command result.
        """
        profile_manager = self.profile_manager
        llm_service = self.llm_service

        if not profile_manager:
            return CommandResult(
                success=False,
                message="Profile manager not available",
                display_type="error",
            )

        if profile_manager.set_active_profile(profile_name):
            profile = profile_manager.get_active_profile()
            # Reinitialize the provider with new profile settings
            if llm_service and hasattr(llm_service, "api_service"):
                await llm_service.api_service.reinitialize_provider(profile)
                # Reload native tools (profile may have different supports_tools setting)
                await llm_service._load_native_tools()
            tools_mode = "enabled" if profile.get_supports_tools() else "disabled"
            return CommandResult(
                success=True,
                message=(
                    f"Switched to profile: {profile_name}\n"
                    f"  Base URL: {profile.get_endpoint()}\n"
                    f"  Model: {profile.get_model()}\n"
                    f"  Provider: {profile.get_provider()}\n"
                    f"  Tools: {tools_mode}"
                ),
                display_type="success",
            )
        else:
            available_list = "\n  ".join(profile_manager.get_profile_names())
            return CommandResult(
                success=False,
                message=f"Profile not found: {profile_name}\nAvailable:\n  {available_list}",
                display_type="error",
            )

    async def handle_modal_action(self, action: str, data: Dict) -> Dict:
        """Handle model modal actions. MUTATE data in-place."""
        if action == "select_model":
            profile_name = data.get("command", {}).get("profile_name")
            model_name = data.get("command", {}).get("model_name")
            if profile_name and self.profile_manager:
                if self.profile_manager.set_active_profile(profile_name):
                    profile = self.profile_manager.get_active_profile()
                    # Reinitialize the provider with new profile settings
                    llm_service = self.llm_service
                    if llm_service and hasattr(llm_service, "api_service"):
                        llm_service.create_background_task(
                            llm_service.api_service.reinitialize_provider(profile),
                            name="reinitialize_provider",
                        )
                        llm_service.create_background_task(
                            llm_service._load_native_tools(),
                            name="reload_native_tools",
                        )
                    tools_mode = (
                        "enabled" if profile.get_supports_tools() else "disabled"
                    )
                    data["display_messages"] = [
                        (
                            "system",
                            (
                                f"[ok] Switched to model: {model_name}\n"
                                f"  Profile: {profile_name}\n"
                                f"  Provider: {profile.get_provider()}\n"
                                f"  Tools: {tools_mode}"
                            ),
                            {},
                        ),
                    ]
                else:
                    data["display_messages"] = [
                        ("error", f"[err] Profile not found: {profile_name}", {}),
                    ]
        return data
