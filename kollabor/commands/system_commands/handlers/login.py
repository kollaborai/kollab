"""Login command handler for OAuth authentication.

Provides /login command with subcommands:
  /login openai   - Authenticate via OpenAI OAuth device code flow
  /login status   - Show current OAuth authentication status
  /login logout   - Clear stored OAuth tokens
"""

import logging
import time

from kollabor_events.models import (
    CommandCategory,
    CommandDefinition,
    CommandMode,
    CommandResult,
    SlashCommand,
    SubcommandInfo,
)

from ..base import BaseCommandHandler

logger = logging.getLogger(__name__)


class LoginCommandHandler(BaseCommandHandler):
    """Handles /login command for OAuth authentication."""

    MODAL_ACTIONS: set[str] = set()

    def __init__(
        self,
        command_registry,
        event_bus,
        profile_manager=None,
        llm_service=None,
    ):
        super().__init__(command_registry, event_bus)
        self.profile_manager = profile_manager
        self._llm_service_override = llm_service

    @property
    def llm_service(self):
        if self._llm_service_override is not None:
            return self._llm_service_override
        return self.event_bus.get_service("llm_service")

    @property
    def renderer(self):
        return self.event_bus.get_service("renderer")

    def register_commands(self) -> None:
        """Register /login command."""
        login_command = CommandDefinition(
            name="login",
            description="OAuth login for LLM providers",
            handler=self.handle_login,
            plugin_name="system",
            category=CommandCategory.SYSTEM,
            mode=CommandMode.INSTANT,
            aliases=["auth", "oauth"],
            icon="[KEY]",
            subcommands=[
                SubcommandInfo("openai", "", "Login with OpenAI (ChatGPT account)"),
                SubcommandInfo("status", "", "Show authentication status"),
                SubcommandInfo("logout", "", "Clear stored tokens"),
            ],
        )
        self.command_registry.register_command(login_command)

    async def handle_login(self, command: SlashCommand) -> CommandResult:
        """Handle /login command."""
        if not command.args:
            return CommandResult(
                success=False,
                message=(
                    "usage: /login <subcommand>\n"
                    "  openai   login with OpenAI (ChatGPT account)\n"
                    "  status   show authentication status\n"
                    "  logout   clear stored tokens"
                ),
                display_type="info",
            )

        subcommand = command.args[0].lower()

        if subcommand == "openai":
            return await self._login_openai()
        elif subcommand == "status":
            return await self._show_status()
        elif subcommand == "logout":
            return await self._logout()
        else:
            return CommandResult(
                success=False,
                message=f"Unknown subcommand: {subcommand}. Use: openai, status, logout",
                display_type="error",
            )

    async def _login_openai(self) -> CommandResult:
        """Run OpenAI OAuth device code flow via LoginAltView."""
        try:
            from plugins.altview.login_altview import LoginAltView

            altview = LoginAltView()

            # Get the stack manager from event bus
            stack_mgr = self.event_bus.get_service("altview_stack_manager")
            if not stack_mgr:
                # Lazy-create via AltViewStackManager if not yet initialized
                try:
                    from kollabor_tui.altview.stack_manager import (
                        AltViewStackManager,
                    )

                    renderer = self.renderer
                    stack_mgr = AltViewStackManager(self.event_bus, renderer)
                    self.event_bus.register_service("altview_stack_manager", stack_mgr)
                except Exception as e:
                    logger.error("Failed to create AltView stack manager: %s", e)
                    return CommandResult(
                        success=False,
                        message=f"Login UI unavailable: {e}",
                        display_type="error",
                    )

            # push() blocks until the user exits or the flow completes
            await stack_mgr.push(altview, "login-openai")

            # Read result from the altview
            if altview.result_error:
                if altview.result_error in ("cancelled by user", "cancelled"):
                    return CommandResult(
                        success=False,
                        message="login cancelled",
                        display_type="info",
                    )
                return CommandResult(
                    success=False,
                    message=f"OpenAI login failed: {altview.result_error}",
                    display_type="error",
                )

            tokens = altview.result_tokens
            if not tokens:
                return CommandResult(
                    success=False,
                    message="OpenAI login failed: no tokens received",
                    display_type="error",
                )

            available_models = altview.result_models
            model = altview.result_best_model

            # Build extra headers for ChatGPT backend
            from kollabor_ai.oauth.openai_oauth import CODEX_API_BASE_URL

            extra_headers = {"originator": "kollabor"}
            if tokens.account_id:
                extra_headers["ChatGPT-Account-Id"] = tokens.account_id

            # Create/update oauth profile in profile manager
            if self.profile_manager:
                from kollabor_ai.profile_manager import LLMProfile

                profile_name = "openai-oauth"
                existing = self.profile_manager.get_profile(profile_name)

                if existing:
                    self.profile_manager.update_profile(
                        profile_name,
                        api_key=tokens.access_token,
                        base_url=CODEX_API_BASE_URL,
                        model=model,
                        provider="openai_responses",
                    )
                    existing.auth_type = "oauth"
                    existing.extra_headers = extra_headers
                else:
                    profile = LLMProfile(
                        name=profile_name,
                        provider="openai_responses",
                        model=model,
                        api_key=tokens.access_token,
                        base_url=CODEX_API_BASE_URL,
                        extra_headers=extra_headers,
                        description="OpenAI OAuth (ChatGPT account)",
                        auth_type="oauth",
                    )
                    self.profile_manager._profiles[profile_name] = profile

                # Switch profile (also reinitializes provider)
                llm = self.llm_service
                if llm and hasattr(llm, "switch_profile"):
                    await llm.switch_profile(profile_name)
                else:
                    self.profile_manager.set_active_profile(profile_name)

            expires_str = _format_expiry(tokens.expires_at)
            models_note = ""
            if available_models:
                models_note = f"\n  available: {', '.join(available_models)}"

            return CommandResult(
                success=True,
                message=(
                    f"logged in via OpenAI OAuth\n"
                    f"  profile: openai-oauth\n"
                    f"  model:   {model}\n"
                    f"  endpoint: chatgpt.com/backend-api/codex\n"
                    f"  expires: {expires_str}"
                    f"{models_note}"
                ),
                display_type="success",
            )

        except Exception as e:
            logger.error("OpenAI OAuth login failed: %s", e)
            return CommandResult(
                success=False,
                message=f"OpenAI login failed: {e}",
                display_type="error",
            )

    async def _show_status(self) -> CommandResult:
        """Show current OAuth authentication status."""
        try:
            from kollabor_ai.oauth import OAuthTokenStorage

            storage = OAuthTokenStorage()
            lines = []

            # Check OpenAI
            tokens = await storage.load_tokens("openai", auto_refresh=False)
            if tokens:
                if tokens.is_expired:
                    status = "expired (will auto-refresh on next use)"
                else:
                    expires_str = _format_expiry(tokens.expires_at)
                    status = f"authenticated (expires {expires_str})"
                lines.append(f"  openai: {status}")
            else:
                lines.append("  openai: not authenticated")

            if not lines:
                lines.append("  no providers authenticated")

            return CommandResult(
                success=True,
                message="oauth status:\n" + "\n".join(lines),
                display_type="info",
            )

        except ImportError:
            return CommandResult(
                success=False,
                message="OAuth module not available",
                display_type="error",
            )
        except Exception as e:
            return CommandResult(
                success=False,
                message=f"Failed to check status: {e}",
                display_type="error",
            )

    async def _logout(self) -> CommandResult:
        """Clear stored OAuth tokens."""
        try:
            from kollabor_ai.oauth import OAuthTokenStorage

            storage = OAuthTokenStorage()
            cleared = await storage.clear_tokens("openai")

            if cleared:
                # Deactivate oauth profile if active
                if self.profile_manager:
                    if self.profile_manager.active_profile_name == "openai-oauth":
                        self.profile_manager.set_active_profile("default")

                return CommandResult(
                    success=True,
                    message="logged out from OpenAI OAuth",
                    display_type="success",
                )
            else:
                return CommandResult(
                    success=True,
                    message="no OpenAI OAuth tokens to clear",
                    display_type="info",
                )

        except Exception as e:
            return CommandResult(
                success=False,
                message=f"Logout failed: {e}",
                display_type="error",
            )


def _format_expiry(expires_at: float) -> str:
    """Format expiry timestamp as human-readable relative time."""
    remaining = expires_at - time.time()
    if remaining <= 0:
        return "expired"
    elif remaining < 3600:
        return f"{int(remaining / 60)}m"
    elif remaining < 86400:
        return f"{remaining / 3600:.1f}h"
    else:
        return f"{remaining / 86400:.1f}d"
