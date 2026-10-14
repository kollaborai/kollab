"""Application-owned ACP runner orchestration.

This module adapts configured ACP profiles to the existing event/history seam;
ACP transport and permission protocol details remain in ``acp_bridge``.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import uuid
from collections import deque
from dataclasses import replace
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
from .acp_state import ACPBindingStore

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
        self._selected_profile: LLMProfile | None = None
        self._parents: dict[str, str] = {}
        self._history_tasks: set[asyncio.Task] = set()
        self._user_history_tasks: dict[str, asyncio.Task] = {}
        from pathlib import Path
        from kollabor_config.config_utils import get_project_data_dir
        self._bindings = ACPBindingStore(Path(get_project_data_dir()) / "acp" / "sessions")
        self._binding_conversation_id: str | None = None
        self._observations: deque[str] = deque(maxlen=20)
        self._observation_count = 0

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

    async def start(self, profile: LLMProfile, *, resume_conversation: str | None = None) -> None:
        if profile.provider != "acp":
            raise ValueError(f"profile {profile.name!r} is not an ACP profile")
        if not profile.command:
            raise ValueError(f"ACP profile {profile.name!r} has no command")
        binding = self._bindings.load(resume_conversation) if resume_conversation else None
        if self._runner is not None:
            # A restart is transactional: retire the old child/session before
            # acquiring a replacement, without creating a second owner.
            await self.close()
        self._closed = False
        self._terminal_session_id = None
        self._observations.clear()
        self._observation_count = 0
        self._selected_profile = profile
        self.hub_plugin = self.event_bus.get_service("hub_plugin") or self.hub_plugin

        try:
            command = tuple(profile.command)
            acp_profile = ACPProfile(
                name=profile.name,
                command=command,
                cwd=profile.cwd or profile.workspace or None,
                workspace=profile.workspace or None,
                pass_env=profile.pass_env,
                env=profile.env,
            )
            permission_bridge = ACPPermissionBridge(self.permission_decider)
            self._runner = ACPRunner(
                acp_profile,
                permission_bridge=permission_bridge,
                update_callback=self._on_runner_update,
                lifecycle_callback=self._runner_lifecycle,
            )
            self._profile_name = profile.name
            llm = self.event_bus.get_service("llm_service")
            stats = getattr(llm, "session_stats", None)
            if isinstance(stats, dict):
                stats.pop("acp_usage", None)
            if self.hub_plugin is not None and getattr(self.hub_plugin, "_identity", None) is not None:
                self.hub_plugin._identity.tags["harness"] = profile.description or profile.name
                self.hub_plugin._identity.tags["provider"] = "acp"
                if self.hub_plugin._presence:
                    self.hub_plugin._presence.publish()
            self._runner._preface = (
                f"You are {self.identity}, an agent on the Kollab mesh. Your harness owns your tools, "
                "login and context. The final message of a turn answering a Hub request is returned "
                "to its requester automatically on that thread; do not send the final answer with hub_msg. "
                "Replies and broadcasts get no automatic answer. Use hub_msg, hub_ask, hub_status and "
                "hub_agents to collaborate. A thread handle may be used only after you received or started it."
            )
            self._binding_conversation_id = self._conversation_id()
            await self._runner.start(binding=binding)
            self.event_bus.register_service("acp_orchestrator", self)
            self._save_binding()
        except BaseException:
            try:
                await self.close()
            except BaseException as cleanup_exc:
                logger.warning("ACP runner restart cleanup failed: %s", cleanup_exc)
            # close retains a failed teardown owner for an explicit retry.
            self._binding = None
            self._profile_name = None
            self._closed = True
            raise

    async def _runner_lifecycle(self, state: str, runner: ACPRunner) -> None:
        if state == "starting" and self.hub_plugin is not None:
            token, generation = self.hub_plugin.rotate_acp_hub_binding()
            self._binding = (token, generation)
            socket_path = getattr(getattr(self.hub_plugin, "_socket_server", None), "socket_path", "")
            if not socket_path:
                raise RuntimeError("ACP hub socket is not ready before session/new")
            from kollabor.mcp_hub import kollab_hub_argv
            argv = kollab_hub_argv()
            runner.profile = replace(runner.profile, mcp_servers=({
                "name": "kollab-hub", "command": argv[0], "args": argv[1:],
                "env": {"KOLLAB_ACP_HUB_TOKEN": token,
                        "KOLLAB_ACP_GENERATION": str(generation),
                        "KOLLAB_ACP_HUB_SOCKET": str(socket_path)},
            },))
        if state == "ready":
            self._save_binding()
            profile = self._selected_profile
            publish_semantic(self.event_bus, "acp_session",
                             harness=profile.description or profile.name if profile else self._profile_name,
                             profile=self._profile_name, state=runner.state, context=runner.resume_mode)
            llm = self.event_bus.get_service("llm_service")
            display = getattr(llm, "message_display_service", None)
            if display is not None:
                line = (f"{self.identity} resumed its session" if runner.resume_mode in {"resumed", "loaded"}
                        else f"{self.identity} starts with fresh context")
                display.display_system_message(line)

    def admit(
        self, text: str, *, request_id: str, source: str, thread_id: str | None = None,
        origin: dict[str, Any] | None = None,
    ) -> ACPAdmission:
        if self._runner is None or self._closed:
            raise RuntimeError("ACP runner is not active")
        if source == "agent" and (origin or {}).get("sender"):
            text = f"Message from {origin['sender']} [thread:{thread_id or ''}]\n{text}"
        admission = self._runner.admit(
            text, request_id=request_id, source=source, thread_id=thread_id, origin=origin
        )
        task = asyncio.create_task(self._complete(admission), name=f"acp-turn-{request_id}")
        self._completion_tasks.add(task)
        task.add_done_callback(self._completion_tasks.discard)
        try:
            self._record_user(request_id, text, source, thread_id, track_completion=True)
        except Exception as exc:
            # Admission is already owned. Remove it before the worker can run,
            # and let that owner's terminal event report the persistence error.
            if not admission.future.done():
                admission.future.set_exception(exc)
            self._runner.cancel_queued(request_id)
        return admission

    def observe_notification(self, message: Any) -> None:
        label = "Reply" if message.reply_to else "Broadcast"
        self._observations.append(f"{label} from {message.from_identity} [thread:{message.thread_id}]\n{message.content}")
        self._observation_count += 1
        if not self.is_processing:
            self._flush_observations()

    def _flush_observations(self) -> None:
        if not self._observations or self._closed or self._runner is None:
            return
        discarded = self._observation_count - len(self._observations)
        text = "\n\n".join(self._observations)
        if discarded:
            text = f"{discarded} older observations omitted.\n\n" + text
        try:
            self.admit(text, request_id=f"observations-{uuid.uuid4().hex}", source="observation")
        except Exception:
            return
        self._observations.clear()
        self._observation_count = 0

    def _conversation_id(self) -> str:
        llm = self.event_bus.get_service("llm_service")
        manager = getattr(llm, "conversation_manager", None)
        return str(getattr(manager, "current_session_id", "") or self.identity)

    def _save_binding(self) -> None:
        if self._runner is not None and self._runner.binding is not None:
            self._bindings.save(self._binding_conversation_id or self._conversation_id(), self._runner.binding)

    def _history_task_done(self, task: asyncio.Task) -> None:
        self._history_tasks.discard(task)
        if not task.cancelled():
            error = task.exception()
            if error is not None:
                logger.warning("ACP user history write failed (%s)", type(error).__name__)

    def _record_user(
        self, request_id: str, text: str, source: str, thread_id: str | None,
        *, track_completion: bool = False,
    ) -> None:
        llm = self.event_bus.get_service("llm_service")
        manager = getattr(llm, "conversation_manager", None)
        metadata = {"provider": "acp", "request_id": request_id, "source": source, "thread_id": thread_id}
        if manager is not None:
            self._parents[request_id] = manager.add_message("user", text, metadata=metadata)
        logger_service = getattr(llm, "conversation_logger", None)
        if logger_service is not None:
            task = asyncio.create_task(logger_service.log_user_message(text))
            self._history_tasks.add(task)
            if track_completion:
                self._user_history_tasks[request_id] = task
            task.add_done_callback(self._history_task_done)
        history = getattr(llm, "conversation_history", None)
        if isinstance(history, list) and history is not getattr(manager, "messages", None):
            history.append({"role": "user", "content": text, "metadata": metadata})
        display = getattr(llm, "message_display_service", None)
        if display is not None and source != "agent":
            display.display_user_message(text)

    async def steer(self, text: str, *, origin: dict[str, Any], thread_id: str | None = None) -> bool:
        if not self._runner or not await self._runner.steer(text, origin=origin, thread_id=thread_id):
            return False
        request_id = f"steering-{uuid.uuid4().hex}"
        self._record_user(request_id, text, "steering", thread_id)
        self._parents.pop(request_id, None)
        return True

    async def restore(self, conversation_id: str | None = None) -> None:
        profile = self._selected_profile
        if profile is not None:
            await self.start(profile, resume_conversation=conversation_id)

    async def cancel_request(self, request_id: str) -> dict[str, Any]:
        runner = self._runner
        if runner is None:
            return {"cancelled": False}
        if runner.cancel_queued(request_id):
            return {"cancelled": True, "queued": True}
        active = getattr(runner, "_active_context", None)
        if active is None or active.request_id != request_id:
            return {"cancelled": False}
        if self.hub_plugin is not None:
            runtime = getattr(self.hub_plugin, "_acp_hub_tools", None)
            if runtime is not None:
                runtime.stop()
        await self.cancel(session_id=runner.session_id, generation=runner.generation)
        return {"cancelled": True}

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
        result: ACPResult | None = None
        try:
            result = await admission.future
            user_history = self._user_history_tasks.get(admission.request_id)
            if user_history is not None:
                await user_history
            self._save_binding()
            await self._record_result(result)
        except BaseException as exc:
            # Admission is immediately acknowledged, so every admitted request
            # must still receive one correlated terminal event on close/failure.
            session_id = result.session_id if result is not None else self._terminal_session_id
            if session_id is None and self._runner is not None:
                session_id = self._runner.session_id
            session_id = session_id or ""
            generation = result.generation if result is not None else admission.generation
            stop_reason = (
                "cancelled"
                if isinstance(exc, asyncio.CancelledError)
                or (isinstance(exc, ACPError) and str(exc) == "ACP turn cancelled")
                else "error"
            )
            end_frame = {
                "type": "turn_end", "generation": generation,
                "turnId": admission.turn_id, "sessionId": session_id,
                "stopReason": stop_reason, "error": str(exc) or type(exc).__name__,
                "outcome": "failed",
            }
            if stop_reason == "error":
                publish_semantic(
                    self.event_bus, "error", message=end_frame["error"],
                    request_id=admission.request_id, turn_id=admission.turn_id,
                    session_id=session_id, generation=generation,
                    input_source=admission.source, thread_id=admission.thread_id,
                )
            publish_semantic(
                self.event_bus, "turn_complete", request_id=admission.request_id,
                session_id=session_id, generation=generation,
                turn_id=admission.turn_id, stop_reason=stop_reason,
                input_source=admission.source, thread_id=admission.thread_id,
                error=str(exc) or type(exc).__name__,
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
            self._flush_observations()
            return {"text": "", "session_id": session_id,
                    "generation": generation, "turn_id": admission.turn_id,
                    "request_id": admission.request_id,
                    "stop_reason": stop_reason, "end_frame": end_frame}
        finally:
            self._parents.pop(admission.request_id, None)
            self._user_history_tasks.pop(admission.request_id, None)
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
        self._flush_observations()
        return {"text": result.text, "session_id": result.session_id,
                "generation": result.generation, "turn_id": result.turn_id,
                "request_id": result.request_id, "stop_reason": result.stop_reason,
                "end_frame": end_frame}

    async def _record_result(self, result: ACPResult) -> None:
        llm = self.event_bus.get_service("llm_service")
        manager = getattr(llm, "conversation_manager", None)
        text = "".join(str(update.get("text") or "") for update in result.updates if update.get("kind") == "text")
        thoughts = [str((update.get("payload", {}).get("content") or {}).get("text") or "")
                    for update in result.updates if update.get("kind") == "agent_thought_chunk"]
        metadata = {"provider": "acp", "profile": self._profile_name, "request_id": result.request_id,
                    "acp_session_id": result.session_id,
                    "acp_updates": [update for update in result.updates if update.get("kind") not in {"plan", "usage_update"}],
                    "stop_reason": result.stop_reason, "thinking": thoughts}
        tools: dict[str, dict[str, Any]] = {}
        for update in result.updates:
            if update.get("kind") not in {"tool_call", "tool_call_update"}:
                continue
            payload = update.get("payload") or {}
            tool_id = str(payload.get("toolCallId") or "")
            tools[tool_id] = {"id": tool_id, "name": payload.get("title") or payload.get("kind") or "Harness Tool",
                "input": payload.get("rawInput") or {}, "status": payload.get("status"),
                "result": payload.get("rawOutput") or payload.get("content"),
                "is_error": payload.get("status") == "failed",
                "metadata": {"acp": dict(payload)}}
        metadata["tool_calls"] = list(tools.values())
        if manager is not None:
            manager.add_message("assistant", text or result.text,
                                parent_uuid=self._parents.get(result.request_id), metadata=metadata)
            manager.save_conversation()
        history = getattr(llm, "conversation_history", None)
        if isinstance(history, list) and history is not getattr(manager, "messages", None):
            history.append({"role": "assistant", "content": text or result.text, "metadata": metadata,
                            "thinking": "".join(thoughts)})
        logger_service = getattr(llm, "conversation_logger", None)
        if logger_service is not None:
            await logger_service.log_assistant_message(text or result.text,
                parent_uuid=self._parents.get(result.request_id, ""), model=self._profile_name,
                thinking_content=thoughts, tool_calls=list(tools.values()))
        self._parents.pop(result.request_id, None)

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
        payload = update.get("payload") or {}
        llm = self.event_bus.get_service("llm_service")
        display = getattr(llm, "message_display_service", None)
        if display is not None:
            if kind == "turn_start":
                display.start_streaming_response()
                display.show_loading("Harness is working")
            elif kind == "token":
                display.hide_loading()
                display.message_coordinator.write_streaming_chunk(str(update.get("text") or ""))
            elif kind == "turn_finished":
                display.hide_loading()
                display.end_streaming_response()
                display.message_coordinator.write_streaming_chunk("\n")
            elif kind == "agent_thought_chunk":
                display.show_loading(str((payload.get("content") or {}).get("text") or "Harness is thinking")[:120])
        if kind in {"tool_call", "tool_call_update"}:
            tool_id = str(payload.get("toolCallId") or "")
            if kind == "tool_call":
                publish_semantic(self.event_bus, "tool_start", tool_id=tool_id,
                    tool_name=payload.get("title") or payload.get("kind") or "Harness Tool",
                    tool_type=payload.get("kind") or "acp", input=payload.get("rawInput") or {},
                    request_id=update.get("request_id"), turn_id=update.get("turn_id"), acp_passthrough=True)
            if payload.get("status") in {"completed", "failed"}:
                import json
                output = payload.get("rawOutput") or payload.get("content") or ""
                if not isinstance(output, str):
                    output = json.dumps(output, ensure_ascii=False)
                publish_semantic(self.event_bus, "tool_result", tool_id=tool_id,
                    tool_name=payload.get("title") or "Harness Tool", success=payload.get("status") == "completed",
                    output=output,
                    metadata={"acp": payload}, request_id=update.get("request_id"), turn_id=update.get("turn_id"), acp_passthrough=True)
                if display is not None:
                    display.display_system_message(str(payload.get("title") or "Harness Tool") + ": " + str(payload.get("status")))
        if kind == "usage_update":
            stats = getattr(llm, "session_stats", None)
            if isinstance(stats, dict):
                stats["acp_usage"] = dict(payload)
            if display is not None:
                cost = payload.get("cost") or {}
                label = f"Context: {payload.get('used', 0)}/{payload.get('size', 0)} tokens"
                if cost.get("amount") is not None:
                    label += f" · {cost['amount']} {cost.get('currency', '')}"
                display.show_loading(label)
        if kind == "plan":
            self.event_bus.register_service("acp_plan", dict(payload))
        if kind in {"tool_call", "tool_call_update", "agent_thought_chunk", "plan", "usage_update"}:
            publish_semantic(self.event_bus, "acp_update", update=payload,
                             request_id=update.get("request_id"), turn_id=update.get("turn_id"))
        if kind == "agent_thought_chunk":
            publish_semantic(self.event_bus, "thinking", text=str((payload.get("content") or {}).get("text") or ""),
                             request_id=update.get("request_id"), turn_id=update.get("turn_id"), acp_passthrough=True)
        if kind == "permission_request":
            publish_semantic(self.event_bus, "acp_waiting", waiting=True, harness=self._profile_name,
                             tool_id=update.get("request_id"), title=payload.get("title") or "Tool permission")
            if self.hub_plugin is not None:
                callback = getattr(self.hub_plugin, "on_acp_waiting", None)
                if callback is not None:
                    active = getattr(self._runner, "_active_context", None)
                    callback(getattr(active, "request_id", ""), True, payload)
        elif kind in {"permission_resolved", "turn_finished"}:
            # A stopped prompt can finish before its SDK permission callback
            # resumes. Clear its waiting state at this still-owned turn boundary.
            publish_semantic(self.event_bus, "acp_waiting", waiting=False, harness=self._profile_name)
            if self.hub_plugin is not None:
                callback = getattr(self.hub_plugin, "on_acp_waiting", None)
                if callback is not None:
                    active = getattr(self._runner, "_active_context", None)
                    callback(getattr(active, "request_id", ""), False, {})
        if kind in {"permission_request", "permission_resolved", "turn_finished"}:
            return
        publish_semantic(
            self.event_bus,
            kind,
            **{k: v for k, v in update.items() if k not in {"kind", "request_id", "turn_id", "source", "thread_id"}},
            request_id=update.get("request_id"),
            turn_id=update.get("turn_id"),
            input_source=update.get("source", "acp"),
            thread_id=update.get("thread_id"),
            acp_passthrough=kind in {"tool_call", "tool_call_update", "agent_thought_chunk", "plan", "usage_update"},
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
        accepted = bool(prepare(
            expected_session_id=session_id,
            expected_generation=generation,
        ))
        if accepted and self.hub_plugin is not None:
            runtime = getattr(self.hub_plugin, "_acp_hub_tools", None)
            if runtime is not None:
                runtime.stop()
        return accepted

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
        if self._closed and self._runner is None:
            return
        self._closed = True
        close_error: BaseException | None = None
        runner = self._runner
        try:
            self._save_binding()
        except BaseException as exc:
            # A failed disk write must not leave the owned adapter running.
            close_error = exc
        if self.hub_plugin is not None:
            runtime = getattr(self.hub_plugin, "_acp_hub_tools", None)
            if runtime is not None:
                try:
                    runtime.stop()
                except BaseException as exc:
                    if close_error is None:
                        close_error = exc
        if runner is not None:
            self._terminal_session_id = getattr(runner, "session_id", None)
            try:
                await runner.close()
            except BaseException as exc:
                # Cancellation must not skip process/binding cleanup.  Keep the
                # original error for the caller after all owned resources settle.
                if close_error is None:
                    close_error = exc
                else:
                    logger.warning("ACP runner teardown also failed (%s)", type(exc).__name__)
            else:
                self._runner = None
        if self._cancel_tasks:
            pending = tuple(self._cancel_tasks)
            for task in pending:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            self._cancel_tasks.clear()
        if self._completion_tasks:
            await asyncio.gather(*tuple(self._completion_tasks), return_exceptions=True)
        if self._history_tasks:
            await asyncio.gather(*tuple(self._history_tasks), return_exceptions=True)
        self._terminal_session_id = None
        llm = self.event_bus.get_service("llm_service")
        stats = getattr(llm, "session_stats", None)
        if isinstance(stats, dict):
            stats.pop("acp_usage", None)
        if self.hub_plugin is not None and self.hub_plugin._identity is not None:
            self.hub_plugin._identity.tags.pop("harness", None)
            self.hub_plugin._identity.tags.pop("provider", None)
            self.hub_plugin._identity.tags.pop("acp_waiting", None)
            if self.hub_plugin._presence:
                self.hub_plugin._presence.publish()
        if self.hub_plugin is not None and self._binding is not None:
            self.hub_plugin.rotate_acp_hub_binding()
            self._binding = None
        publish_semantic(self.event_bus, "acp_session", state="closed", harness="", context="")
        if close_error is not None:
            raise close_error
