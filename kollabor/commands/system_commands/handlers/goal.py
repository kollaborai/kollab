"""Goal command handler for /goal (goal-command-harnesses spec section 5).

Goal control subcommands are daemon-side state operations: they must work
while a turn is processing (the RPC path routes them ahead of the
is_processing gate), pause/clear record stop intents immediately, and
status/history render from the durable store.
"""

from __future__ import annotations

import logging
import os
import socket
from typing import Optional, Set

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

CONTROL_SUBCOMMANDS = ("show", "pause", "resume", "clear", "history")


class GoalCommandHandler(BaseCommandHandler):
    """Handles the /goal command surface."""

    MODAL_ACTIONS: Set[str] = set()

    def __init__(self, command_registry, event_bus, config_manager=None):
        super().__init__(command_registry, event_bus, config_manager)
        self._service = None
        self._service_failed = False

    # ------------------------------------------------------------------
    # service access
    # ------------------------------------------------------------------

    def _daemon_id(self) -> str:
        return f"{socket.gethostname()}:{os.getpid()}"

    @property
    def service(self):
        """GoalService via the event bus registry, or a local fallback."""
        if self._service is not None:
            return self._service
        if self._service_failed:
            return None
        svc = None
        try:
            svc = self.event_bus.get_service("goal_service")
        except Exception:
            svc = None
        if svc is None:
            try:
                from kollabor.goals.service import GoalService
                from kollabor.state.goal_store import open_default_store

                svc = GoalService(open_default_store(), daemon_id=self._daemon_id())
            except Exception as exc:
                logger.warning("goal service unavailable: %s", exc)
                self._service_failed = True
                return None
        self._service = svc
        return svc

    def _conversation_uid(self) -> Optional[str]:
        try:
            state = self.event_bus.get_service("state_service")
        except Exception:
            state = None
        if state is not None and hasattr(state, "get_conversation_uid"):
            try:
                return state.get_conversation_uid()
            except Exception:
                return None
        return None

    def _project_root(self) -> str:
        try:
            from plugins.hub.project_scope import resolve_project_root

            return str(resolve_project_root())
        except Exception:
            return os.getcwd()

    # ------------------------------------------------------------------
    # registration
    # ------------------------------------------------------------------

    def register_commands(self) -> None:
        goal_command = CommandDefinition(
            name="goal",
            description="Durable session goal with bounded continuation",
            handler=self.handle_goal,
            plugin_name="system",
            category=CommandCategory.SYSTEM,
            mode=CommandMode.NORMAL,
            aliases=["goals"],
            icon="[GOAL]",
            subcommands=[
                SubcommandInfo("", "<objective>", "Create a goal and start"),
                SubcommandInfo("show", "", "Show the active goal in full"),
                SubcommandInfo("pause", "", "Pause at the next safe boundary"),
                SubcommandInfo(
                    "resume", "[--budget N] [--turns N]", "Resume after a stop"
                ),
                SubcommandInfo("clear", "", "Stop and keep as history"),
                SubcommandInfo("history", "[N]", "Recent finished goals"),
            ],
        )
        self.command_registry.register_command(goal_command)
        self.logger.info("Goal command registered")

    # ------------------------------------------------------------------
    # parsing (spec 5)
    # ------------------------------------------------------------------

    @staticmethod
    def _raw_remainder(command: SlashCommand) -> str:
        """Remainder after the command token, whitespace preserved."""
        raw = (command.raw_input or "").strip()
        if raw.startswith("/"):
            raw = raw[1:]
        lowered = raw.lower()
        for token in ("goal ", "goal\n", "goals ", "goals\n"):
            if lowered.startswith(token):
                raw = raw[len(token) - 1 :]
                break
        else:
            if lowered in ("goal", "goals"):
                raw = ""
        return raw.strip()

    @staticmethod
    def _parse_objective(
        remainder: str,
    ) -> tuple[Optional[float], Optional[str], Optional[str]]:
        """Split leading --budget N; honor -- end-of-options.

        Returns (budget, objective, error).
        """
        tokens = remainder.split(" ", 1)
        if tokens and tokens[0] == "--budget":
            rest = tokens[1] if len(tokens) > 1 else ""
            value_tokens = rest.split(" ", 1)
            if not value_tokens or not value_tokens[0]:
                return None, None, "--budget requires a positive number"
            try:
                budget = float(value_tokens[0])
            except ValueError:
                return (
                    None,
                    None,
                    f"--budget requires a number, got {value_tokens[0]!r}",
                )
            if budget <= 0:
                return None, None, "--budget must be positive"
            objective = value_tokens[1].strip() if len(value_tokens) > 1 else ""
            return budget, objective or None, None
        if tokens and tokens[0].startswith("--") and tokens[0] != "--":
            return (
                None,
                None,
                f"unknown option {tokens[0]!r}; use -- to start an objective that begins with --",
            )
        if tokens and tokens[0] == "--":
            objective = remainder.split(" ", 1)[1].strip() if len(tokens) > 1 else ""
            return None, objective or None, None
        return None, remainder or None, None

    # ------------------------------------------------------------------
    # handler
    # ------------------------------------------------------------------

    async def handle_goal(self, command: SlashCommand) -> CommandResult:
        try:
            return await self._dispatch(command)
        except Exception as exc:
            self.logger.error("goal command error: %s", exc, exc_info=True)
            return CommandResult(
                success=False,
                message=f"goal command error: {exc}",
                display_type="error",
            )

    async def _dispatch(self, command: SlashCommand) -> CommandResult:
        remainder = self._raw_remainder(command)
        first = remainder.split(" ", 1)[0].lower() if remainder else ""

        if not remainder or first == "show":
            return self._render_status(full=True)
        if first in ("pause", "clear", "history", "resume"):
            args = remainder.split(" ", 1)
            sub = args[0].lower()
            rest = args[1].strip() if len(args) > 1 else ""
            if sub == "pause":
                return self._do_pause()
            if sub == "clear":
                return self._do_clear()
            if sub == "history":
                return self._do_history(rest)
            return self._do_resume(rest)

        return self._do_create(remainder)

    # -- create --------------------------------------------------------

    def _do_create(self, remainder: str) -> CommandResult:
        svc = self.service
        if svc is None:
            return CommandResult(
                success=False,
                message="goal storage unavailable",
                display_type="error",
            )
        budget, objective, error = self._parse_objective(remainder)
        if error:
            return CommandResult(success=False, message=error, display_type="error")
        uid = self._conversation_uid()
        if not uid:
            return CommandResult(
                success=False,
                message="no conversation identity available; open a session first",
                display_type="error",
            )
        from kollabor.goals.service import GoalError

        attachments = svc.take_staged_attachments(uid)
        try:
            record = svc.create_goal(
                conversation_uid=uid,
                objective=objective or "",
                project_root=self._project_root(),
                session_id=self._session_id(),
                token_budget=budget,
                attachment_refs=attachments or None,
            )
        except GoalError as exc:
            return CommandResult(success=False, message=str(exc), display_type="error")
        self._schedule_goal_turn(record)
        budget_txt = (
            f"  budget: {int(record.token_budget)} tokens"
            if record.token_budget
            else ""
        )
        return CommandResult(
            success=True,
            message=(
                f"goal {record.short_id} created — turn 1 scheduled{budget_txt}\n"
                f"objective: {record.objective}"
            ),
            display_type="success",
            data={"goal_id": record.goal_id},
        )

    def _session_id(self) -> Optional[str]:
        try:
            state = self.event_bus.get_service("state_service")
            conv_mgr = getattr(
                getattr(state, "_llm_service", None), "conversation_manager", None
            )
            return getattr(conv_mgr, "current_session_id", None)
        except Exception:
            return None

    def _schedule_goal_turn(self, record) -> None:
        """Kick the goal driver's first turn through the normal queue (8.2:
        the command handler never calls a provider synchronously)."""
        try:
            driver = self.event_bus.get_service("goal_driver")
        except Exception:
            driver = None
        if driver is not None and hasattr(driver, "schedule"):
            try:
                driver.schedule(record.goal_id)
                return
            except Exception as exc:
                logger.warning("goal driver schedule failed: %s", exc)
        # no driver (read-only contexts): the goal persists; resume when a
        # full daemon attaches and the next boundary check runs

    # -- controls ------------------------------------------------------

    def _require_active(self):
        svc = self.service
        if svc is None:
            return None, CommandResult(
                success=False,
                message="goal storage unavailable",
                display_type="error",
            )
        uid = self._conversation_uid()
        record = svc.active(uid) if uid else None
        if record is None:
            return None, CommandResult(
                success=False,
                message="no active goal",
                display_type="info",
            )
        return record, None

    def _do_pause(self) -> CommandResult:
        record, err = self._require_active()
        if err:
            return err
        from kollabor.goals.service import GoalError

        try:
            updated = self.service.pause(record.goal_id)
        except GoalError as exc:
            return CommandResult(success=False, message=str(exc), display_type="error")
        if updated.pause_requested:
            return CommandResult(
                success=True,
                message=f"pause requested — applies at the next safe boundary ({record.short_id})",
                display_type="info",
            )
        return CommandResult(
            success=True,
            message=f"goal {record.short_id} paused",
            display_type="success",
        )

    def _do_clear(self) -> CommandResult:
        record, err = self._require_active()
        if err:
            return err
        from kollabor.goals.service import GoalError

        try:
            updated = self.service.clear(record.goal_id)
        except GoalError as exc:
            return CommandResult(success=False, message=str(exc), display_type="error")
        if updated.clear_requested:
            return CommandResult(
                success=True,
                message=f"clear requested — applies at the next safe boundary ({record.short_id}); history retained",
                display_type="info",
            )
        return CommandResult(
            success=True,
            message=f"goal {record.short_id} cleared (kept in /goal history)",
            display_type="success",
        )

    def _do_resume(self, rest: str) -> CommandResult:
        record, err = self._require_active()
        if err:
            return err
        from kollabor.goals.service import GoalError

        budget = None
        turn_limit = None
        while rest:
            parts = rest.split(" ", 1)
            flag = parts[0]
            value_rest = parts[1] if len(parts) > 1 else ""
            if flag == "--budget" and value_rest:
                try:
                    budget = float(value_rest.split(" ", 1)[0])
                    rest = value_rest.split(" ", 1)[1] if " " in value_rest else ""
                except ValueError:
                    return CommandResult(
                        success=False,
                        message="--budget requires a number",
                        display_type="error",
                    )
            elif flag == "--turns" and value_rest:
                try:
                    turn_limit = int(value_rest.split(" ", 1)[0])
                    rest = value_rest.split(" ", 1)[1] if " " in value_rest else ""
                except ValueError:
                    return CommandResult(
                        success=False,
                        message="--turns requires a whole number",
                        display_type="error",
                    )
            elif flag.startswith("--"):
                return CommandResult(
                    success=False,
                    message=f"unknown option {flag!r}",
                    display_type="error",
                )
            else:
                rest = ""
        try:
            updated = self.service.resume(
                record.goal_id, token_budget=budget, auto_turn_limit=turn_limit
            )
        except GoalError as exc:
            return CommandResult(success=False, message=str(exc), display_type="error")
        self._schedule_goal_turn(updated)
        return CommandResult(
            success=True,
            message=(
                f"goal {record.short_id} resumed "
                f"(generation {updated.execution_generation})"
            ),
            display_type="success",
        )

    def _do_history(self, rest: str) -> CommandResult:
        svc = self.service
        if svc is None:
            return CommandResult(
                success=False,
                message="goal storage unavailable",
                display_type="error",
            )
        limit = 10
        if rest:
            try:
                limit = max(1, min(50, int(rest.split(" ", 1)[0])))
            except ValueError:
                return CommandResult(
                    success=False,
                    message="history takes a count",
                    display_type="error",
                )
        uid = self._conversation_uid()
        records = svc.history(uid, limit) if uid else []
        if not records:
            return CommandResult(
                success=True,
                message="no finished goals yet",
                display_type="info",
            )
        lines = []
        for rec in records:
            status = rec.status
            declared = " (model-declared)" if rec.completion_declared else ""
            reason = f" — {rec.last_reason}" if rec.last_reason else ""
            lines.append(
                f"  {rec.short_id}  {status}{declared}  turns {rec.turn_count}{reason}"
            )
        return CommandResult(
            success=True,
            message="finished goals (newest first):\n" + "\n".join(lines),
            display_type="info",
        )

    # -- status (10.2) -------------------------------------------------

    def _render_status(self, full: bool = False) -> CommandResult:
        svc = self.service
        if svc is None:
            return CommandResult(
                success=False,
                message="goal storage unavailable",
                display_type="error",
            )
        uid = self._conversation_uid()
        record = svc.active(uid) if uid else None
        if record is None:
            return CommandResult(
                success=True,
                message="no active goal — create one with /goal <objective>",
                display_type="info",
            )
        tokens = (
            "unavailable"
            if record.tokens_used is None
            else f"{int(record.tokens_used)}/{int(record.token_budget) if record.token_budget else '-'}"
        )
        objective = (
            record.objective
            if full or len(record.objective) <= 80
            else record.objective[:77] + "..."
        )
        declared = " (model-declared)" if record.completion_declared else ""
        pending = ""
        if record.pause_requested:
            pending = "\npause requested — applies at next boundary"
        elif record.clear_requested:
            pending = "\nclear requested — applies at next boundary"
        nxt = {
            "active": "continue",
            "paused": "/goal resume",
            "blocked": f"resolve blocker and /goal resume — {record.blocked_reason}",
            "usage_limited": "resolve provider quota and /goal resume",
            "budget_limited": f"/goal resume --budget N — {record.blocked_reason or 'budget reached'}",
            "complete": "done",
            "cleared": "cleared",
        }.get(record.status, record.status)
        message = (
            f"goal {record.short_id}  {record.status}{declared}{pending}\n"
            f"objective: {objective}\n"
            f"turns: {record.turn_count}/{record.auto_turn_limit}  "
            f"elapsed: {int(record.time_used_seconds)}s  tokens: {tokens}\n"
            f"last: {record.last_reason or '-'}\n"
            f"evidence: {len(record.last_evidence)}  linked tasks: "
            f"{len(record.task_ids)}\n"
            f"next: {nxt}"
        )
        return CommandResult(
            success=True,
            message=message,
            display_type="info",
            data={"goal_id": record.goal_id},
        )
