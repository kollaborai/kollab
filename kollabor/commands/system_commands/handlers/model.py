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
                SubcommandInfo("list", "[query]", "Show model selection modal"),
                SubcommandInfo("search", "<query>", "Search provider model catalog"),
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

            if not args:
                # Show model selection modal
                return await self._show_models_modal()
            elif args[0] in ("list", "ls"):
                query = " ".join(args[1:]).strip() or None
                return await self._show_models_modal(query)
            elif args[0] in ("search", "find") and len(args) >= 2:
                query = " ".join(args[1:]).strip()
                return await self._show_models_modal(query)
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

    async def _get_models_modal_definition(
        self, filter_query: str | None = None
    ) -> Dict[str, Any]:
        """Get modal definition for model selection.

        Returns:
            Modal definition dictionary with models from all profiles.
        """
        profile_manager = self.profile_manager
        if not profile_manager:
            return {}

        active_profile = profile_manager.get_active_profile()
        active_model = active_profile.get_model() if active_profile else None
        active_provider = active_profile.get_provider() if active_profile else ""

        if active_provider == "openrouter":
            commands = await self._get_openrouter_model_commands(
                active_model, filter_query
            )
            if commands:
                title = "OpenRouter Available Models"
                if filter_query:
                    title += f" matching '{filter_query}'"
                return {
                    "title": "OpenRouter Models",
                    "footer": "↑↓ navigate • Enter select • Esc close",
                    "sections": [
                        {
                            "title": (
                                f"{title} (active: {active_model or 'none'})"
                            ),
                            "commands": commands,
                        }
                    ],
                    "actions": [
                        {"key": "Enter", "label": "Select", "action": "select"},
                        {"key": "Escape", "label": "Close", "action": "cancel"},
                    ],
                }

        # Build model -> profile mapping (use first profile with that model)
        model_commands = []
        seen_models = set()

        profiles = profile_manager.list_profiles()
        for profile in profiles:
            model = profile.get_model() or "unknown"
            if filter_query and filter_query.lower() not in model.lower():
                continue
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

    async def _get_openrouter_model_commands(
        self, active_model: str | None, filter_query: str | None = None
    ) -> list[Dict[str, Any]]:
        """Fetch OpenRouter's live model catalog for the active profile."""
        try:
            from kollabor_ai.providers.openrouter_model_info import OpenRouterModelInfo

            model_info = OpenRouterModelInfo()
            models = await model_info.list_models()
        except Exception as e:
            self.logger.warning(f"Unable to fetch OpenRouter models: {e}")
            return []

        commands: list[Dict[str, Any]] = []
        needle = filter_query.lower() if filter_query else ""
        for model in models:
            model_id = str(model.get("id") or "")
            if not model_id:
                continue
            model_name = str(model.get("name") or "")
            if (
                needle
                and needle not in model_id.lower()
                and needle not in model_name.lower()
            ):
                continue
            context_length = model.get("context_length")
            supported_parameters = model.get("supported_parameters") or []
            supports_tools = (
                "tools" in supported_parameters
                or "tool_choice" in supported_parameters
            )
            token_note = (
                f"{context_length:,} ctx"
                if isinstance(context_length, int)
                else "OpenRouter"
            )
            tool_note = "tools" if supports_tools else "no tools"
            commands.append(
                {
                    "name": f"{'[*] ' if model_id == active_model else '    '}{model_id}",
                    "description": f"{token_note} • {tool_note}",
                    "model_name": model_id,
                    "provider_catalog": "openrouter",
                    "supports_tools": supports_tools,
                    "action": "select_model",
                }
            )
        return commands

    async def _show_models_modal(
        self, filter_query: str | None = None
    ) -> CommandResult:
        """Show the model selection modal.

        Returns:
            Command result with modal definition.
        """
        modal_def = await self._get_models_modal_definition(filter_query)

        # Check if any models exist
        model_commands = modal_def.get("sections", [{}])[0].get("commands", [])
        if not model_commands:
            return CommandResult(
                success=False,
                message=(
                    f"No models available matching: {filter_query}"
                    if filter_query
                    else "No models available"
                ),
                display_type="error",
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

        active_profile = profile_manager.get_active_profile()
        if active_profile and active_profile.get_provider() == "openrouter":
            supports_tools = await self._lookup_openrouter_tool_support(model_name)
            return await self._set_active_profile_model(model_name, supports_tools)
        else:
            available = "\n  ".join(p.get_model() for p in profiles if p.get_model())
            return CommandResult(
                success=False,
                message=f"Model not found: {model_name}\nAvailable:\n  {available}",
                display_type="error",
            )

    async def _lookup_openrouter_tool_support(self, model_name: str) -> bool | None:
        """Return OpenRouter tool support for a model when catalog data exists."""
        try:
            commands = await self._get_openrouter_model_commands(
                active_model=None, filter_query=model_name
            )
        except Exception:
            return None
        for command in commands:
            if command.get("model_name") == model_name:
                supports = command.get("supports_tools")
                return bool(supports) if supports is not None else None
        return None

    async def _set_active_profile_model(
        self, model_name: str, supports_tools: bool | None = None
    ) -> CommandResult:
        """Update the active profile to a provider-catalog model."""
        profile_manager = self.profile_manager
        llm_service = self.llm_service
        if not profile_manager:
            return CommandResult(
                success=False,
                message="Profile manager not available",
                display_type="error",
            )

        profile = profile_manager.get_active_profile()
        if not profile:
            return CommandResult(
                success=False,
                message="Active profile not available",
                display_type="error",
            )

        if not profile_manager.update_profile(
            profile.name,
            model=model_name,
            supports_tools=supports_tools,
            save_to_config=True,
        ):
            return CommandResult(
                success=False,
                message=f"Unable to update active profile model: {model_name}",
                display_type="error",
            )

        profile = profile_manager.get_active_profile()
        if llm_service and hasattr(llm_service, "api_service"):
            await llm_service.api_service.reinitialize_provider(profile)
            await llm_service._load_native_tools()

        return CommandResult(
            success=True,
            message=(
                f"Updated active profile model\n"
                f"  Profile: {profile.name}\n"
                f"  Provider: {profile.get_provider()}\n"
                f"  Model: {profile.get_model()}"
            ),
            display_type="success",
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
            provider_catalog = data.get("command", {}).get("provider_catalog")
            supports_tools = data.get("command", {}).get("supports_tools")
            if provider_catalog == "openrouter" and model_name and self.profile_manager:
                profile = self.profile_manager.get_active_profile()
                if profile and self.profile_manager.update_profile(
                    profile.name,
                    model=model_name,
                    supports_tools=supports_tools,
                    save_to_config=True,
                ):
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
                    data["display_messages"] = [
                        (
                            "system",
                            (
                                f"[ok] Updated active model: {model_name}\n"
                                f"  Profile: {profile.name}\n"
                                f"  Provider: {profile.get_provider()}"
                            ),
                            {},
                        ),
                    ]
                return data
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
