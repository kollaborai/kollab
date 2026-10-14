"""Application-owned ACP runner orchestration.

This module adapts configured ACP profiles to the existing event/history seam;
ACP transport and permission protocol details remain in ``acp_bridge``.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import uuid
from typing import Any

from kollabor_ai.profile_manager import LLMProfile
from kollabor_tui.display_tap import publish_semantic

from .acp_bridge import (
    ACPAdmission,
    ACPError,
    ACPPermissionBridge,
    ACPProfile,
    ACPResult,
    ACPRunner,
)

logger = logging.getLogger(__name__)


class ACPOrchestrator:
    """Own one ACP runner for one daemon identity/profile."""

    def __init__(
        self,
        event_bus: Any,
        *,
        identity: str = "default",
        hub_plugin: Any = None,
        permission_decider: Any = None,
    ) -> None:
        self.event_bus = event_bus
        self.identity = identity
        self.hub_plugin = hub_plugin
        self.permission_decider = permission_decider
        self._binding: tuple[str, int] | None = None
        self._runner: ACPRunner | None = None
        self._profile_name: str | None = None
        self._closed = False
        self._completion_tasks: set[asyncio.Task[Any]] = set()
        self._delivery_tasks: set[asyncio.Task[Any]] = set()
        self._cancel_tasks: set[asyncio.Task[Any]] = set()
        self._terminal_session_id: str | None = None

    @property
    def is_processing(self) -> bool:
        """Whether an admitted ACP turn is active or waiting in FIFO."""
        runner = self._runner
        return bool(
            self._completion_tasks
            or (runner is not None and getattr(runner, "is_processing", False) is True)
        )

    @property
    def active(self) -> bool:
        return self._runner is not None and not self._closed

    @property
    def runner(self) -> ACPRunner | None:
        return self._runner

    async def start(self, profile: LLMProfile) -> None:
        if profile.provider != "acp":
            raise ValueError(f"profile {profile.name!r} is not an ACP profile")
        if not profile.command:
            raise ValueError(f"ACP profile {profile.name!r} has no command")
        if self._runner is not None:
            # A restart is transactional: retire the old child/session before
            # acquiring a replacement, without creating a second owner.
            await self.close()
        self._closed = False
        self._terminal_session_id = None

        try:
            command = tuple(profile.command)
            mcp_servers = []
            if self.hub_plugin is not None:
                token, generation = self.hub_plugin.rotate_acp_hub_binding()
                self._binding = (token, generation)
                socket_path = getattr(getattr(self.hub_plugin, "_socket_server", None), "socket_path", "")
                if not socket_path:
                    raise RuntimeError("ACP hub socket is not ready before session/new")
                from kollabor.mcp_hub import kollab_hub_argv
                mcp_servers.append({
                    "name": "kollab-hub",
                    "command": kollab_hub_argv()[0],
                    "args": kollab_hub_argv()[1:],
                    "env": {
                        "KOLLAB_ACP_HUB_TOKEN": token,
                        "KOLLAB_ACP_GENERATION": str(generation),
                        "KOLLAB_ACP_HUB_SOCKET": str(socket_path),
                    },
                })
            acp_profile = ACPProfile(
                name=profile.name,
                command=command,
                cwd=profile.cwd or profile.workspace or None,
                workspace=profile.workspace or None,
                pass_env=profile.pass_env,
                mcp_servers=tuple(mcp_servers),
            )
            permission_bridge = ACPPermissionBridge(self.permission_decider)
            self._runner = ACPRunner(
                acp_profile,
                permission_bridge=permission_bridge,
                update_callback=self._on_runner_update,
            )
            self._profile_name = profile.name
            await self._runner.start()
            self.event_bus.register_service("acp_orchestrator", self)
        except BaseException:
            try:
                await self.close()
            except BaseException as cleanup_exc:
                logger.warning("ACP runner restart cleanup failed: %s", cleanup_exc)
            self._runner = None
            self._binding = None
            self._profile_name = None
            self._closed = True
            raise

    def admit(
        self, text: str, *, request_id: str, source: str, thread_id: str | None = None
    ) -> ACPAdmission:
        if self._runner is None or self._closed:
            raise RuntimeError("ACP runner is not active")
        admission = self._runner.admit(
            text, request_id=request_id, source=source, thread_id=thread_id
        )
        task = asyncio.create_task(self._complete(admission), name=f"acp-turn-{request_id}")
        self._completion_tasks.add(task)
        task.add_done_callback(self._completion_tasks.discard)
        return admission

    async def _notify_hub_completion(
        self, *, request_id: str, turn_id: int, thread_id: str | None,
        source: str, text: str, end_frame: dict[str, Any], failed: bool = False,
    ) -> None:
        callback = getattr(self.hub_plugin, "on_acp_completion", None)
        if callback is None:
            return
        try:
            result = callback(
                request_id=request_id, turn_id=turn_id, thread_id=thread_id,
                source=source, text=text, end_frame=dict(end_frame), failed=failed,
            )
            if inspect.isawaitable(result):
                await asyncio.wait_for(result, timeout=10.0)
        except Exception:
            logger.exception("ACP Hub completion callback failed")

    async def _complete(self, admission: ACPAdmission) -> dict[str, Any]:
        try:
            result: ACPResult = await admission.future
        except BaseException as exc:
            # Admission is immediately acknowledged, so every admitted request
            # must still receive one correlated terminal event on close/failure.
            session_id = self._terminal_session_id
            if session_id is None and self._runner is not None:
                session_id = self._runner.session_id
            session_id = session_id or ""
            generation = admission.generation
            stop_reason = (
                "cancelled"
                if isinstance(exc, asyncio.CancelledError)
                or (isinstance(exc, ACPError) and str(exc) == "ACP turn cancelled")
                else "error"
            )
            end_frame = {
                "type": "turn_end", "generation": generation,
                "turnId": admission.turn_id, "sessionId": session_id,
                "stopReason": stop_reason, "error": type(exc).__name__,
                "outcome": "failed",
            }
            publish_semantic(
                self.event_bus, "turn_complete", request_id=admission.request_id,
                session_id=session_id, generation=generation,
                turn_id=admission.turn_id, stop_reason=stop_reason,
                input_source=admission.source, thread_id=admission.thread_id,
            )
            publish_semantic(
                self.event_bus, "turn_end", **end_frame, hidden=True,
                request_id=admission.request_id, input_source=admission.source,
                thread_id=admission.thread_id,
            )
            await self._notify_hub_completion(
                request_id=admission.request_id, turn_id=admission.turn_id,
                thread_id=admission.thread_id, source=admission.source,
                text="", end_frame=end_frame, failed=True,
            )
            return {"text": "", "session_id": session_id,
                    "generation": generation, "turn_id": admission.turn_id,
                    "request_id": admission.request_id,
                    "stop_reason": stop_reason, "end_frame": end_frame}
        publish_semantic(
            self.event_bus, "turn_complete", request_id=result.request_id,
            session_id=result.session_id, generation=result.generation,
            turn_id=result.turn_id, stop_reason=result.stop_reason,
            input_source=result.source, thread_id=result.thread_id,
        )
        end_frame = dict(result.end_frame)
        end_frame.setdefault(
            "outcome", "success" if result.stop_reason == "end_turn" else "failed"
        )
        publish_semantic(
            self.event_bus, "turn_end", **end_frame, hidden=True,
            request_id=result.request_id, input_source=result.source,
            thread_id=result.thread_id,
        )
        await self._notify_hub_completion(
            request_id=result.request_id, turn_id=result.turn_id,
            thread_id=result.thread_id, source=result.source, text=result.text,
            end_frame=result.end_frame,
            failed=result.stop_reason != "end_turn",
        )
        return {"text": result.text, "session_id": result.session_id,
                "generation": result.generation, "turn_id": result.turn_id,
                "request_id": result.request_id, "stop_reason": result.stop_reason,
                "end_frame": end_frame}

    async def submit(self, text: str, *, source: str = "rpc", thread_id: str | None = None) -> dict[str, Any]:
        request_id = f"request-{uuid.uuid4().hex}"
        admission = self.admit(text, request_id=request_id, source=source, thread_id=thread_id)
        result: ACPResult = await admission.future
        return {"text": result.text, "session_id": result.session_id,
                "generation": result.generation, "turn_id": result.turn_id,
                "request_id": result.request_id, "stop_reason": result.stop_reason,
                "end_frame": result.end_frame}

    def _on_runner_update(self, update: dict[str, Any]) -> None:
        kind = "token" if update.get("kind") == "text" else str(update.get("kind", "update"))
        publish_semantic(
            self.event_bus,
            kind,
            **{k: v for k, v in update.items() if k not in {"kind", "request_id", "turn_id", "source", "thread_id"}},
            request_id=update.get("request_id"),
            turn_id=update.get("turn_id"),
            input_source=update.get("source", "acp"),
            thread_id=update.get("thread_id"),
        )


    def prepare_cancel(
        self,
        *,
        session_id: str | None = None,
        generation: int | None = None,
    ) -> bool:
        runner = self._runner
        if runner is None or self._closed:
            return False
        prepare = getattr(runner, "prepare_cancel", None)
        if not callable(prepare):
            return False
        return bool(prepare(
            expected_session_id=session_id,
            expected_generation=generation,
        ))

    async def cancel(
        self,
        *,
        session_id: str | None = None,
        generation: int | None = None,
    ) -> None:
        if self._runner is not None and not self._closed:
            await self._runner.cancel(
                expected_session_id=session_id,
                expected_generation=generation,
            )


    def schedule_cancel(
        self,
        *,
        session_id: str | None = None,
        generation: int | None = None,
    ) -> bool:
        """Reserve and own a nonblocking ACP cancellation task."""
        if self._runner is None or self._closed:
            return False
        if not self.prepare_cancel(session_id=session_id, generation=generation):
            return False
        task = asyncio.create_task(
            self.cancel(session_id=session_id, generation=generation)
        )
        self._cancel_tasks.add(task)
        task.add_done_callback(self._cancel_tasks.discard)
        return True

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        close_error: BaseException | None = None
        runner = self._runner
        if runner is not None:
            self._terminal_session_id = getattr(runner, "session_id", None)
            try:
                await runner.close()
            except BaseException as exc:
                # Cancellation must not skip process/binding cleanup.  Keep the
                # original error for the caller after all owned resources settle.
                close_error = exc
            finally:
                self._runner = None
        if self._cancel_tasks:
            await asyncio.gather(*tuple(self._cancel_tasks), return_exceptions=True)
        if self._completion_tasks:
            await asyncio.gather(*tuple(self._completion_tasks), return_exceptions=True)
        self._terminal_session_id = None
        if self.hub_plugin is not None and self._binding is not None:
            self.hub_plugin.rotate_acp_hub_binding()
            self._binding = None
        if close_error is not None:
            raise close_error
