"""Slash commands for private generated-image artifacts."""

import re

from kollabor_events.models import (
    CommandCategory,
    CommandDefinition,
    CommandResult,
    SlashCommand,
    SubcommandInfo,
)

from ..base import BaseCommandHandler

_MEDIA_ID_RE = re.compile(r"^img_[A-Za-z0-9_-]{16,64}$")


class ArtifactCommandHandler(BaseCommandHandler):
    """Handle terminal actions for session-scoped generated images."""

    MODAL_ACTIONS: set[str] = set()

    def register_commands(self) -> None:
        self.command_registry.register_command(
            CommandDefinition(
                name="artifact",
                description="Open a private generated image artifact",
                category=CommandCategory.SYSTEM,
                plugin_name="system",
                handler=self.handle_artifact,
                subcommands=[
                    SubcommandInfo(
                        "open",
                        "<media_id>",
                        "Open a generated image in the default viewer",
                    )
                ],
            )
        )

    def _get_api_service(self):
        if self.event_bus is None or not hasattr(self.event_bus, "get_service"):
            return None
        llm_service = self.event_bus.get_service("llm_service")
        return getattr(llm_service, "api_service", None)

    async def handle_artifact(self, command: SlashCommand) -> CommandResult:
        args = command.args or []
        if len(args) != 2 or args[0].lower() != "open" or not args[1]:
            return CommandResult(
                success=False,
                message="usage: /artifact open <media_id>",
                display_type="error",
            )

        media_id = args[1]
        if not _MEDIA_ID_RE.fullmatch(media_id):
            return CommandResult(
                success=False,
                message="invalid generated image media ID",
                display_type="error",
            )
        api_service = self._get_api_service()
        opener = getattr(api_service, "open_generated_artifact", None)
        if not callable(opener):
            return CommandResult(
                success=False,
                message="generated image artifacts are unavailable in this session",
                display_type="error",
            )

        try:
            opened = bool(opener(media_id))
        except Exception:
            self.logger.warning("Failed to open generated image artifact")
            opened = False

        if not opened:
            return CommandResult(
                success=False,
                message=f"generated image {media_id} is unavailable in this session",
                display_type="error",
            )

        return CommandResult(
            success=True,
            message=f"opened generated image {media_id}",
            display_type="success",
        )
