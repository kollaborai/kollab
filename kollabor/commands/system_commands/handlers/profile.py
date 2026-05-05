"""Profile command handler.

Handles /profile command - manage LLM API profiles.
"""

import json
import logging
from typing import Any, Dict

from kollabor_ai.profile_manager import EnvVarHint
from kollabor_config.config_utils import get_existing_global_config_path
from kollabor_config.loader import mask_api_key
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


class ProfileCommandHandler(BaseCommandHandler):
    """Handles /profile command - manage LLM API profiles."""

    MODAL_ACTIONS = {
        "select_profile",
        "create_profile_prompt",
        "create_profile_submit",
        "edit_profile_prompt",
        "edit_profile_submit",
        "delete_profile_prompt",
        "delete_profile_confirm",
        "save_profile_to_config",
        "toggle_project_default_profile",
        "toggle_global_default_profile",
    }

    def __init__(
        self,
        command_registry,
        event_bus,
        profile_manager,
        llm_service=None,
    ):
        """Initialize profile command handler.

        Args:
            command_registry: Command registry for registration.
            event_bus: Event bus for service lookup.
            profile_manager: Profile manager instance.
            llm_service: Optional LLM service (for dynamic lookup).
        """
        super().__init__(command_registry, event_bus)
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
        """Register /profile command."""
        profile_command = CommandDefinition(
            name="profile",
            description="Manage LLM API profiles",
            handler=self.handle_profile,
            plugin_name="system",
            category=CommandCategory.SYSTEM,
            mode=CommandMode.MODAL,
            aliases=["prof", "llm"],
            icon="[PROF]",
            ui_config=UIConfig(
                type="modal",
                navigation=["? ?", "Enter", "Esc"],
                height=15,
                title="LLM Profiles",
                footer="↑↓ navigate • Enter select • Esc exit",
            ),
            subcommands=[
                SubcommandInfo("list", "", "Show profile selection modal"),
                SubcommandInfo("set", "<name>", "Switch to specified profile"),
                SubcommandInfo("create", "", "Open create profile form"),
            ],
        )
        self.command_registry.register_command(profile_command)

    async def handle_profile(self, command: SlashCommand) -> CommandResult:
        """Handle /profile command.

        Args:
            command: Parsed slash command.

        Returns:
            Command execution result.
        """
        try:
            if not self.profile_manager:
                return CommandResult(
                    success=False,
                    message="Profile manager not available",
                    display_type="error",
                )

            args = command.args or []

            if not args or args[0] in ("list", "ls"):
                # Show profile selection modal
                return await self._show_profiles_modal()
            elif args[0] == "set" and len(args) >= 2:
                # Switch to profile: /profile set <name>
                profile_name = args[1]
                return await self._switch_profile(profile_name)
            elif args[0] == "create":
                # Show create profile form: /profile create
                return await self._show_create_profile_modal()
            else:
                # Switch to specified profile (direct command)
                profile_name = args[0]
                return await self._switch_profile(profile_name)

        except Exception as e:
            self.logger.error(f"Error in profile command: {e}")
            return CommandResult(
                success=False,
                message=f"Error managing profiles: {str(e)}",
                display_type="error",
            )

    async def _get_profiles_modal_definition(
        self, skip_reload: bool = False
    ) -> Dict[str, Any]:
        """Get modal definition for profile selection.

        Args:
            skip_reload: If True, don't reload from config (use current state).

        Returns:
            Modal definition dictionary.
        """
        from kollabor_tui.profile_modal_builder import build_profiles_modal

        # Load config to check for provider field (used in both paths for
        # provider_types mapping - the ProfileSnapshot exposes provider but
        # we also need to know which profiles were explicitly configured with
        # a provider field in config.json).
        config_path = get_existing_global_config_path()
        provider_profiles = set()
        provider_types = {}
        if config_path.exists():
            try:
                config_data = json.loads(config_path.read_text())
                profiles_config = (
                    config_data.get("kollabor", {}).get("llm", {}).get("profiles", {})
                )
                for profile_name, profile_config in profiles_config.items():
                    if "provider" in profile_config:
                        provider_profiles.add(profile_name)
                        provider_types[profile_name] = profile_config["provider"]
            except Exception as e:
                logger.debug(f"Failed to load provider config: {e}")

        # Resolve state_service via event bus. In attach mode this is a
        # RemoteStateService; in local mode it's a LocalStateService. Either
        # way the snapshot shape is identical.
        state_service = None
        if self.event_bus and hasattr(self.event_bus, "get_service"):
            state_service = self.event_bus.get_service("state_service")

        profiles_data = []
        active_name = ""

        if state_service is not None:
            try:
                profile_list = await state_service.list_profiles()
                active_name = profile_list.active
                for p in profile_list.profiles:
                    provider = p.provider or provider_types.get(p.name)
                    if provider:
                        provider_profiles.add(p.name)
                    profiles_data.append(
                        {
                            "name": p.name,
                            "model": p.model or "",
                            "api_url": p.endpoint or "",
                            "provider": provider,
                        }
                    )
            except Exception as e:
                logger.warning(f"state_service.list_profiles failed, falling back: {e}")
                state_service = None

        if state_service is None:
            # Fallback: direct in-process profile_manager read. This is kept
            # for edge cases where state_service isn't registered yet.
            if not skip_reload:
                self.profile_manager.reload()

            profiles = self.profile_manager.list_profiles()
            active_name = self.profile_manager.active_profile_name

            # Reset and rebuild provider_profiles in case we bailed out of
            # the state_service branch mid-iteration.
            profiles_data = []
            for profile in profiles:
                # All profiles have a provider - use it directly from the profile object
                provider = profile.get_provider() or provider_types.get(profile.name)
                if provider:
                    provider_profiles.add(profile.name)
                profiles_data.append(
                    {
                        "name": profile.name,
                        "model": profile.get_model() or "",
                        "api_url": profile.get_endpoint() or "",
                        "provider": provider,
                    }
                )

        from kollabor_config.config_utils import get_all_default_profiles

        profile_defaults = get_all_default_profiles()

        return build_profiles_modal(
            profiles_data=profiles_data,
            active_profile=active_name,
            provider_profiles=provider_profiles,
            project_default=profile_defaults.get("project"),
            global_default=profile_defaults.get("global"),
        )

    async def _show_profiles_modal(self) -> CommandResult:
        """Show profile selection modal.

        Returns:
            Command result with modal UI.
        """
        modal_definition = await self._get_profiles_modal_definition()

        return CommandResult(
            success=True,
            message="Select a profile",
            ui_config=UIConfig(
                type="modal",
                title=modal_definition["title"],
                width=modal_definition.get("width"),
                height=int(modal_definition.get("height", 0)),
                modal_config=modal_definition,
            ),
            display_type="modal",
        )

    async def _show_create_profile_modal(self) -> CommandResult:
        """Show create profile form modal.

        Returns:
            Command result with create profile form modal.
        """
        modal_definition = self._get_create_profile_modal_definition()

        return CommandResult(
            success=True,
            message="Create new profile",
            ui_config=UIConfig(
                type="modal",
                title=modal_definition["title"],
                width=modal_definition.get("width"),
                height=int(modal_definition.get("height", 0)),
                modal_config=modal_definition,
            ),
            display_type="modal",
        )

    async def _switch_profile(self, profile_name: str) -> CommandResult:
        """Switch to a different profile.

        Phase 4: routes through state_service.set_active_profile so
        the same command works in local AND attach mode. In attach
        mode the switch happens on the daemon (which owns the
        conversation, tool registry, and provider connection pool),
        and the next LLM turn picks up the new model.

        Args:
            profile_name: Name of profile to switch to.

        Returns:
            Command result. On success the message lists the new
            model/endpoint/provider/tools-mode so the user sees a
            full confirmation of what changed. On failure the message
            includes the list of available profile names for quick
            recovery.
        """
        state_service = None
        if self.event_bus and hasattr(self.event_bus, "get_service"):
            state_service = self.event_bus.get_service("state_service")

        if state_service is not None:
            try:
                snapshot = await state_service.set_active_profile(profile_name)
                tools_mode = "enabled" if snapshot.supports_tools else "disabled"
                return CommandResult(
                    success=True,
                    message=(
                        f"Switched to profile: {snapshot.name}\n"
                        f"  Base URL: {snapshot.endpoint}\n"
                        f"  Model: {snapshot.model}\n"
                        f"  Provider: {snapshot.provider}\n"
                        f"  Tools: {tools_mode}"
                    ),
                    display_type="success",
                )
            except ValueError as e:
                # Surface the daemon's error message (which already
                # includes the available profile names when it was
                # raised from LocalStateService.set_active_profile).
                return CommandResult(
                    success=False,
                    message=f"Profile not found: {profile_name}\n{e}",
                    display_type="error",
                )
            except Exception as e:
                self.logger.warning(
                    f"state_service.set_active_profile failed, "
                    f"falling back to direct write: {e}"
                )

        # Fallback: direct profile_manager write (pre-migration path).
        # Only used if state_service isn't wired for some reason.
        if self.profile_manager.set_active_profile(profile_name):
            profile = self.profile_manager.get_active_profile()
            # Reinitialize the provider with new profile settings
            if self.llm_service and hasattr(self.llm_service, "api_service"):
                self.llm_service.create_background_task(
                    self.llm_service.api_service.reinitialize_provider(profile),
                    name="reinitialize_provider",
                )
                # Reload native tools (profile may have different supports_tools setting)
                self.llm_service.create_background_task(
                    self.llm_service._load_native_tools(),
                    name="reload_native_tools",
                )
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
            available_list = "\n  ".join(self.profile_manager.get_profile_names())
            return CommandResult(
                success=False,
                message=f"Profile not found: {profile_name}\nAvailable:\n  {available_list}",
                display_type="error",
            )

    def _get_create_profile_modal_definition(self) -> Dict[str, Any]:
        """Get modal definition for creating a new profile.

        Uses unified provider system format with base_url, provider, and api_key.
        """
        from kollabor_tui.profile_modal_builder import build_create_profile_modal

        return build_create_profile_modal()

    def _get_edit_profile_modal_definition(self, profile_name: str) -> Dict[str, Any]:
        """Get modal definition for editing an existing profile.

        Args:
            profile_name: Name of the profile to edit.

        Returns:
            Modal definition dict with pre-populated values.
        """
        from kollabor_tui.profile_modal_builder import build_edit_profile_modal

        if not self.profile_manager:
            return {}

        profile = self.profile_manager.get_profile(profile_name)
        if not profile:
            return {}

        # Get env var hints for this profile
        env_hints = profile.get_env_var_hints()

        # Determine API key status
        api_key_from_env = env_hints["api_key"].is_set
        api_key_in_config = bool(profile.api_key)
        if api_key_from_env:
            api_key_status = f"(using env: {env_hints['api_key'].name})"
            api_key_placeholder = "Leave empty to use env var"
        elif api_key_in_config:
            api_key_status = "(set in config)"
            api_key_placeholder = ""
        else:
            api_key_status = "[REQUIRED - not set]"
            api_key_placeholder = "Enter API key or set env var"

        # Check if provider is overridden by env var
        provider_hint = env_hints.get("provider", EnvVarHint("", False))
        provider_from_env = provider_hint.is_set

        # Determine overall status
        issues = []
        if not profile.get_endpoint():
            issues.append("base URL missing")
        if not profile.model:
            issues.append("model missing")
        if not api_key_from_env and not api_key_in_config:
            issues.append("API key missing")

        if issues:
            status_line = f"[!] Fix {len(issues)} issue(s): {', '.join(issues)}"
        else:
            status_line = "[ok] Ready to use"

        # Build profile data dict for modal builder
        profile_data = {
            "name": profile.name,
            "model": profile.model,
            "base_url": profile.get_endpoint() or "",
            "provider": profile.provider or "openai",
            "supports_tools": profile.supports_tools,
            "temperature": profile.temperature,
            "description": profile.description or "",
            "api_key_masked": mask_api_key(profile.api_key) if profile.api_key else "",
            "api_key_status": api_key_status,
            "api_key_placeholder": api_key_placeholder,
            "env_api_key_set": api_key_from_env,
            "env_api_key_name": env_hints["api_key"].name,
            "provider_from_env": provider_from_env,
            "env_provider_name": provider_hint.name if provider_from_env else "",
            "status_line": status_line,
        }

        return build_edit_profile_modal(profile_data=profile_data)

    def _get_delete_profile_confirm_modal(self, profile_name: str) -> Dict[str, Any]:
        """Get modal definition for delete profile confirmation.

        Args:
            profile_name: Name of the profile to delete.

        Returns:
            Modal definition dict for confirmation, or empty dict if cannot delete.
        """
        from kollabor_tui.profile_modal_builder import (
            build_delete_profile_confirm_modal,
        )

        if not self.profile_manager:
            return {}

        profile = self.profile_manager.get_profile(profile_name)
        if not profile:
            return {}

        # Cannot delete built-in profiles
        if profile_name in self.profile_manager.DEFAULT_PROFILES:
            return {}

        is_active = self.profile_manager.is_active(profile_name)

        return build_delete_profile_confirm_modal(
            profile_name=profile_name,
            model=profile.model,
            api_url=profile.get_endpoint() or "unknown",
            is_active=is_active,
        )
