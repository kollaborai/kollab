"""Context service slash command handlers.

Handles /context command - inspect and manage the context service ledger.
"""

import logging
from typing import Any

from kollabor_events.models import (
    CommandCategory,
    CommandDefinition,
    CommandResult,
    SubcommandInfo,
)

from ..base import BaseCommandHandler

logger = logging.getLogger(__name__)


class ContextCommandHandler(BaseCommandHandler):
    """Handles /context command - manage the context service ledger."""

    MODAL_ACTIONS: set = set()

    def register_commands(self) -> None:
        """Register /context command and subcommands."""
        context_cmd = CommandDefinition(
            name="context",
            description="Context service ledger management",
            category=CommandCategory.SYSTEM,
            plugin_name="system",
            handler=self._handle_context,
            subcommands=[
                SubcommandInfo(
                    "show", "", "Display the current ledger"
                ),
                SubcommandInfo(
                    "evict",
                    "<ctx_id> [reason]",
                    "Evict a ledger entry",
                ),
                SubcommandInfo(
                    "stats", "", "Show ledger statistics"
                ),
                SubcommandInfo(
                    "clear", "", "Clear the ledger (does not touch history)"
                ),
            ],
        )
        self.command_registry.register_command(context_cmd)

    def _get_context_service(self) -> Any:
        """Get the context service from event bus."""
        if self.event_bus is None:
            return None
        svc = self.event_bus.get_service("context_service")
        if svc is not None and type(svc).__module__ != "unittest.mock":
            return svc
        return None

    async def _handle_context(self, command) -> CommandResult:
        """Handle /context and subcommands."""
        # command is a SlashCommand; args is List[str]
        cmd_args = command.args if hasattr(command, "args") else []
        subcmd = cmd_args[0] if cmd_args else ""
        rest = " ".join(cmd_args[1:]) if len(cmd_args) > 1 else ""

        context_service = self._get_context_service()
        if context_service is None:
            return CommandResult(
                success=False,
                message="context service is not running",
                display_type="error",
            )

        if not subcmd or subcmd == "show":
            snapshot = context_service.build_context_snapshot_display()
            return CommandResult(
                success=True, message=snapshot, display_type="info"
            )

        if subcmd == "evict":
            evict_parts = rest.split(maxsplit=1)
            if not evict_parts or not evict_parts[0]:
                return CommandResult(
                    success=False,
                    message="usage: /context evict <ctx_id> [reason]",
                    display_type="error",
                )
            target = evict_parts[0]
            reason = (
                evict_parts[1]
                if len(evict_parts) > 1
                else "user requested eviction"
            )
            if context_service.evict(target, reason):
                return CommandResult(
                    success=True,
                    message=f"evicted {target}",
                    display_type="info",
                )
            return CommandResult(
                success=False,
                message=f"entry {target} not found",
                display_type="error",
            )

        if subcmd == "stats":
            entries = context_service.all_entries()
            total_bytes = sum(
                e.size_bytes for e in entries if e.decision != "evicted"
            )
            stats = {
                "total_entries": len(entries),
                "total_bytes": total_bytes,
                "pending": sum(
                    1 for e in entries if e.decision == "pending"
                ),
                "keep": sum(1 for e in entries if e.decision == "keep"),
                "summary": sum(
                    1 for e in entries if e.decision == "summary"
                ),
                "evicted": sum(
                    1 for e in entries if e.decision == "evicted"
                ),
            }
            msg = (
                f"context service stats:\n"
                f"  entries:   {stats['total_entries']}\n"
                f"  bytes:     {stats['total_bytes'] // 1024}KB\n"
                f"  pending:   {stats['pending']}\n"
                f"  keep:      {stats['keep']}\n"
                f"  summary:   {stats['summary']}\n"
                f"  evicted:   {stats['evicted']}"
            )
            return CommandResult(
                success=True, message=msg, display_type="info"
            )

        if subcmd == "clear":
            context_service._ledger._entries.clear()
            return CommandResult(
                success=True,
                message="ledger cleared",
                display_type="info",
            )

        return CommandResult(
            success=False,
            message=f"unknown subcommand: {subcmd}",
            display_type="error",
        )
