"""GoalTurnDriver: bridges GoalService policy to the existing queue
processor execution path (spec section 11 seam 5).

GoalService owns policy and state only; this driver runs goal-owned turns
through the SAME continuation machinery the Hub path uses
(`_continue_conversation` + tool loop). There is no second provider-calling
loop. After each turn settles, the driver re-runs the guarded eligibility
check — user input wins, stop intents apply, bounds gate dispatch.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, List, Optional

from kollabor.goals.service import (
    BoundaryContext,
    GoalService,
    TurnOutcome,
)
from kollabor.state.goal_store import GoalStoreError

logger = logging.getLogger(__name__)

MAX_GOAL_CHAIN_TURNS = 50  # per-attempt spin backstop, mirrors hub continue

#: tool types whose results count as file/state mutations for evidence
#: freshness (8.5). Unknown tools default to non-mutating.
MUTATING_TOOL_TYPES = {
    "file_write",
    "file_edit",
    "edit_file",
    "write_file",
    "create_file",
    "shell",
    "bash",
    "terminal",
    "apply_patch",
}


class GoalTurnDriver:
    def __init__(self, service: GoalService, coordinator):
        self.service = service
        self.coord = coordinator
        self._running_goals: set[str] = set()
        self._tool_seq = 0

        # single reporting surface (9.1) through the unified tool pipeline
        from kollabor.goals.tooling import register_goal_tools

        register_goal_tools(
            coordinator.response_parser, coordinator.tool_executor, service
        )
        # evidence provenance: the runtime records tool results itself
        qp = getattr(coordinator, "_queue_processor", None)
        if qp is not None:
            qp.on_tool_batch = self._on_tool_batch

    # ------------------------------------------------------------------
    # public entry
    # ------------------------------------------------------------------

    def schedule(self, goal_id: str) -> None:
        """Kick the goal loop as a background task (idempotent per goal)."""
        if goal_id in self._running_goals:
            return
        # pipe mode is one query + exit (same guard as hub continue);
        # a goal loop there would race process teardown
        renderer = getattr(self.coord, "renderer", None)
        if getattr(renderer, "pipe_mode", False):
            logger.info("goal driver: pipe mode active, not scheduling")
            return
        runner = getattr(self.coord, "create_background_task", None)

        def _fallback_runner(coro, name=None):
            asyncio.get_running_loop().create_task(coro)

        runner = runner or _fallback_runner
        runner(self.start(goal_id), name=f"goal_driver_{goal_id[:8]}")

    async def start(self, goal_id: str) -> None:
        if goal_id in self._running_goals:
            return
        self._running_goals.add(goal_id)
        try:
            await self._drive(goal_id)
        except Exception as exc:
            logger.error("goal driver error for %s: %s", goal_id, exc)
            self._safe_error_pause(goal_id, exc)
        finally:
            self._running_goals.discard(goal_id)
            self.service.clear_dispatched(goal_id)

    # ------------------------------------------------------------------
    # the loop
    # ------------------------------------------------------------------

    async def _drive(self, goal_id: str) -> None:
        first = True
        while True:
            try:
                record = self.service.store.get(goal_id)
            except GoalStoreError:
                return
            if record.status != "active":
                return

            # boundary eligibility: fresh queue state each iteration; user
            # input or pending work here means no dispatch this round (the
            # settle-driven wake or next user turn re-runs the check)
            ctx = self._boundary_ctx()
            if first:
                outcome = TurnOutcome(
                    turn_id="goal-start",
                    origin="goal",
                    status="ok",
                    goal_id=goal_id,
                )
                first = False
            else:
                # settle-driven wake path: caller re-invoked us; treat as
                # a fresh ok boundary
                outcome = TurnOutcome(
                    turn_id=f"wake-{int(time.time())}",
                    origin="goal",
                    status="ok",
                    goal_id=goal_id,
                )

            attempt = self.service.maybe_continue(goal_id, outcome, ctx)
            if attempt is None:
                return

            self.service.note_dispatched(goal_id, attempt)
            turn_status, error = await self._run_one_turn(goal_id, attempt)

            final = self.service.store.get(goal_id)
            real_outcome = TurnOutcome(
                turn_id=attempt.attempt_id[:12],
                origin="goal",
                status=turn_status,
                goal_id=goal_id,
                error=error,
            )
            try:
                self.service.finish_turn(goal_id, attempt, real_outcome)
            except GoalStoreError as exc:
                logger.warning("finish_turn failed for %s: %s", goal_id, exc)

            # commit completion only after the tool batch settled (8.5)
            try:
                done = self.service.apply_pending_completion(goal_id)
            except GoalStoreError:
                done = None
            if done is not None:
                logger.info("goal %s complete", goal_id[:8])
                return

            if turn_status != "ok":
                # cancelled/error outcomes apply their policy at the
                # boundary (8.3): pause, never auto-restart
                try:
                    self.service.maybe_continue(
                        goal_id, real_outcome, self._boundary_ctx()
                    )
                except GoalStoreError:
                    pass
                return

            final = self.service.store.get(goal_id)
            if final.status != "active":
                return
            # loop: next iteration re-runs the full checklist

    async def _run_one_turn(self, goal_id: str, attempt) -> tuple[str, Optional[str]]:
        """Inject steering (9.3), run the continuation loop, return outcome."""
        record = self.service.store.get(goal_id)

        # rehydrate attachments (9.2): missing artifact pauses hard
        for ref in record.attachment_refs:
            if ref.get("kind") == "image" and self.service.load_attachment(ref) is None:
                self.service.pause_artifact_missing(goal_id, str(ref.get("ref")))
                return "cancelled", f"artifact_missing: {ref.get('ref')}"

        note = self.service.render_goal_context(record)
        try:
            await self.coord.inject_system_message(note, subtype="goal_context")
        except Exception as exc:
            logger.error("goal steering injection failed: %s", exc)
            return "error", f"steering injection failed: {exc}"

        qp = self.coord._queue_processor
        self._tool_seq = 0
        cancelled = qp.cancel_processing
        error: Optional[str] = None
        try:
            qp.is_processing = True
            qp.turn_completed = False
            await self.coord._continue_conversation()
            chain_turns = 0
            while not qp.turn_completed and not qp.cancel_processing:
                if not qp.processing_queue.empty():
                    # user input wins: yield the chain (8.3)
                    logger.info("goal turn: user message arrived, yielding")
                    break
                if chain_turns >= MAX_GOAL_CHAIN_TURNS:
                    logger.error("goal turn: spin backstop hit, stopping chain")
                    qp.turn_completed = True
                    break
                chain_turns += 1
                try:
                    await self.coord._continue_conversation()
                except Exception as exc:
                    logger.error("goal turn continuation error: %s", exc)
                    error = str(exc)
                    break
        except Exception as exc:
            logger.error("goal turn error: %s", exc)
            error = str(exc)
        finally:
            qp.is_processing = False
            cancelled = bool(qp.cancel_processing)
            # drain any user messages that queued while we held processing
            drain = getattr(self.coord, "_process_queue", None)
            if (
                drain is not None
                and not qp.processing_queue.empty()
                and not qp.cancel_processing
            ):
                try:
                    self.coord.create_background_task(
                        lambda: drain(),
                        name="process_queue_drain_after_goal_turn",
                    )
                except Exception as drain_exc:
                    logger.debug("goal turn queue drain failed: %s", drain_exc)
        if cancelled:
            return "cancelled", None
        if error:
            return "error", error
        return "ok", None

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _boundary_ctx(self) -> BoundaryContext:
        qp = getattr(self.coord, "_queue_processor", None)
        if qp is None:
            return BoundaryContext(queue_empty=False, pending_work=True)
        queue_empty = qp.processing_queue.empty()
        pending_work = bool(getattr(qp, "pending_tools", None)) or bool(
            getattr(self.coord, "pending_tools", None)
        )
        return BoundaryContext(
            queue_empty=queue_empty,
            pending_work=pending_work,
            invalidated=bool(qp.cancel_processing),
        )

    def _on_tool_batch(self, results: List[Any]) -> None:
        """Runtime-recorded evidence provenance (8.5): every tool result of
        a goal-owned turn becomes an evidence row the model may cite."""
        current = self.service.current_goal_attempt()
        if current is None:
            return
        record, attempt = current
        for result in results:
            self._tool_seq += 1
            tool_type = str(getattr(result, "tool_type", "tool"))
            output = str(getattr(result, "output", "") or "")
            ref = f"{tool_type}:{output[:80]}".strip()
            try:
                self.service.note_tool_result(
                    goal_id=record.goal_id,
                    attempt_id=attempt.attempt_id,
                    tool_result_id=str(getattr(result, "tool_id", self._tool_seq)),
                    kind=tool_type,
                    ref=ref,
                    tool_seq=self._tool_seq,
                    turn_id=attempt.attempt_id[:12],
                    is_mutating=tool_type in MUTATING_TOOL_TYPES,
                )
            except GoalStoreError as exc:
                logger.debug("evidence recording skipped: %s", exc)

    def _safe_error_pause(self, goal_id: str, exc: Exception) -> None:
        try:
            record = self.service.store.get(goal_id)
            self.service.store.transition(
                goal_id,
                record.record_version,
                "error",
                actor="system",
                daemon_id=self.service.daemon_id,
                status="paused",
                reason=f"driver error: {exc}",
                last_reason=f"driver error: {exc}",
            )
        except GoalStoreError:
            pass
