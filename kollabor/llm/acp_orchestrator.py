"""Application-owned ACP runner orchestration.

This module adapts configured ACP profiles to the existing event/history seam;
ACP transport and permission protocol details remain in ``acp_bridge``.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import sys
import uuid
from collections import deque
from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import Any

from kollabor_ai.profile_manager import EFFORT_LEVELS, LLMProfile
from kollabor_events.data_models import ConversationMessage
from kollabor_tui.display_tap import publish_semantic
from kollabor_tui.terminal_state import TerminalMode

from .acp_bridge import (
    ACPAdmission,
    ACPError,
    ACPPermissionBridge,
    ACPProfile,
    ACPResult,
    ACPRunner,
)
from .acp_state import ACPBindingStore
from .agent_hud import AgentHudEntry, format_agent_hud

logger = logging.getLogger(__name__)


@dataclass
class _ObservedTurn:
    session_id: str
    generation: int
    text: str = ""
    updates: list[dict[str, Any]] = field(default_factory=list)
    finished: asyncio.Event = field(default_factory=asyncio.Event)


# Saved picks are keyed by what the option means, so a harness that renames its option still restores it.
_CANONICAL_PICK_KEYS = {"model": "model", "thought_level": "effort"}


def _effort_rank(level: str) -> int:
    """Position of an effort level in EFFORT_LEVELS; a level we do not know ranks lowest."""
    return EFFORT_LEVELS.index(level) if level in EFFORT_LEVELS else -1


def option_values(option: dict[str, Any] | None) -> list[str]:
    """Advertised values of an ACP config option, nested option groups included."""
    values: list[str] = []
    for candidate in (option or {}).get("options") or []:
        if not isinstance(candidate, dict):
            continue
        if "value" in candidate:
            values.append(str(candidate["value"]))
        for nested in candidate.get("options") or []:
            if isinstance(nested, dict) and "value" in nested:
                values.append(str(nested["value"]))
    return values


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
        self.startup_error: str | None = None
        self._parents: dict[str, str] = {}
        self._history_tasks: set[asyncio.Task] = set()
        self._user_history_tasks: dict[str, asyncio.Task] = {}
        self._observed_turns: dict[tuple[str, int], _ObservedTurn] = {}
        self._display_turn: tuple[str, int] | None = None
        # Voice data of a mic turn, by request id, until its reply is spoken.
        self.voice_turns: dict[str, dict[str, Any]] = {}
        # Voice epochs whose rules already went to the harness.
        self.voice_rules_epochs: set[Any] = set()
        from pathlib import Path

        from kollabor_config.config_utils import get_project_data_dir
        self._bindings = ACPBindingStore(Path(get_project_data_dir()) / "acp" / "sessions")
        # The model/effort picked for each harness profile, re-applied at start.
        self._options_path = Path(get_project_data_dir()) / "acp" / "options.json"
        self._binding_conversation_id: str | None = None
        self.restore_notices: list[str] = []
        self._observations: deque[AgentHudEntry] = deque(maxlen=20)
        self._observation_rows: list[str] = []
        self._unwritten_observations: deque[AgentHudEntry] = deque(maxlen=20)
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

    @property
    def supports_image_input(self) -> bool:
        return bool(self._runner and getattr(self._runner, "supports_image_input", False))

    def advertised_config_options(self) -> list[dict[str, Any]]:
        """Return config options advertised by the active ACP session."""
        runner = self._runner
        if runner is None or self._closed:
            return []
        return runner.advertised_config_options()

    async def set_config_option(self, config_id: str, value: str | bool) -> list[dict[str, Any]]:
        """Set an advertised ACP config option on the active session."""
        runner = self._runner
        if runner is None or self._closed:
            raise ACPError("ACP session is not active")
        category = next((str(o.get("category") or "") for o in runner.advertised_config_options()
                         if o.get("id") == config_id), "")
        options = await runner.set_config_option(config_id, value)
        reported = next((o.get("currentValue") for o in options or [] if o.get("id") == config_id), None)
        if reported == value:
            # A pick the user confirmed this session: a replay of saved options must not undo it.
            self._user_picks = {**getattr(self, "_user_picks", {}), config_id: value}
            effort = self._effort_option()
            if effort is not None and effort.get("id") == config_id:
                self._effort_pick = value  # the effort option, found by category or by its ID if uncategorized
        if self._profile_name and reported == value:
            # Persist only a value the harness confirmed; a refused pick must not replay at the next start.
            saved = self._saved_options()
            key = _CANONICAL_PICK_KEYS.get(category, config_id)
            saved.setdefault(self._profile_name, {})[key] = value
            try:
                self._options_path.parent.mkdir(parents=True, exist_ok=True)
                self._options_path.write_text(json.dumps(saved, indent=2))
            except OSError as exc:
                logger.warning("ACP option save failed: %s", exc)
        if category == "model" or config_id == "model":
            await self._reapply_effort_after_model()
            options = runner.advertised_config_options()  # the effort may have changed after the model reply
        return options

    async def _reapply_effort_after_model(self) -> None:
        """A model change can reset or drop effort on ACP: put the user's pick back, or say why it could not be.

        Never raises. A pick the new model does not offer falls back to the highest level it offers.
        """
        runner = self._runner
        wanted = getattr(self, "_effort_pick", None)
        if runner is None or wanted is None:
            return
        effort = self._effort_option()
        if effort is None:
            self._show_notice(f"effort {wanted} was not kept: the new model advertises no effort option")
            return
        notice = None
        offered = option_values(effort)
        target = wanted
        if offered and wanted not in offered:
            target = max(offered, key=_effort_rank)
            notice = f"effort {wanted} is not offered by the new model; using {target}, the highest it offers"
        if effort.get("currentValue") != target:
            try:
                options = await runner.set_config_option(str(effort["id"]), target)
            except Exception as exc:
                notice = f"effort {wanted} was refused by the new model: {exc}"
            else:
                reported = next((o.get("currentValue") for o in options or [] if o.get("id") == effort["id"]), None)
                if reported != target:
                    notice = f"effort {wanted} did not survive the model change (the harness reports {reported})"
        if notice:
            self._show_notice(notice)

    def _forget_session_picks(self) -> None:
        """Picks belong to one harness session: a restart into another profile must not replay them.
        Confirmed picks are already in the saved file, so a restart of the same profile still restores them."""
        self._effort_pick = None
        self._user_picks = {}

    def _saved_options(self) -> dict[str, dict[str, Any]]:
        try:
            saved = json.loads(self._options_path.read_text())
        except (OSError, ValueError):
            return {}
        return saved if isinstance(saved, dict) else {}

    def _effort_option(self) -> dict[str, Any] | None:
        """The advertised reasoning-effort option, found by category. The ID "effort" is only the
        fallback for a harness that advertises it with no category."""
        options = self.advertised_config_options()
        return (next((o for o in options if str(o.get("category") or "").lower() == "thought_level"), None)
                or next((o for o in options if o.get("id") == "effort" and not o.get("category")), None))

    async def _restore_options(self, profile: LLMProfile) -> list[str]:
        """Re-apply the loadout's model and effort, then the other saved picks.

        Order: model first (a model change alters which efforts exist), then
        effort, then saved picks. The loadout wins over saved values. Returns
        one line per requested value the harness did not take.
        """
        notices: list[str] = []
        runner = self._runner
        if runner is None:
            return notices
        saved = dict(self._saved_options().get(profile.name) or {})
        picked = getattr(self, "_user_picks", {})  # confirmed this session: a replay must not undo them
        loadout_model = str(getattr(profile, "model", "") or "")
        loadout_effort = str(getattr(profile, "effort", "") or "")
        if not (saved or loadout_model or loadout_effort):
            return notices  # nothing to re-apply: stay lazy, as before
        model = next((o for o in self.advertised_config_options()
                      if o.get("category") == "model" or o.get("id") == "model"), None)
        saved_model = saved.pop("model", None)
        if saved_model is None and model is not None and str(model["id"]) not in picked:
            saved_model = saved.pop(str(model["id"]), None)  # an older save under the raw ID
        model_value = loadout_model or saved_model
        model_ok = True
        if model is None and model_value:
            model_ok = False
            notices.append(f"model {model_value} was not applied: the harness advertises no model option")
        if model is not None:
            model_id = str(model["id"])
            if model_value and model_value != model.get("currentValue"):
                try:
                    await runner.set_config_option(model_id, model_value)
                except Exception as exc:
                    model_ok = False
                    notices.append(f"model {model_value} was not applied: {exc}")
            if model_value and model_ok:
                active = next((o.get("currentValue") for o in self.advertised_config_options()
                               if o.get("id") == model_id), None)
                if active != model_value:
                    model_ok = False
                    notices.append(f"model {model_value} did not become active (harness reports {active})")
        effort = self._effort_option()
        effort_id = str(effort["id"]) if effort is not None else None
        saved_effort = saved.pop("effort", None)
        if saved_effort is None and effort is not None and effort_id not in picked:
            saved_effort = saved.pop(effort_id, None)  # an older save under the raw ID
        if not model_ok:
            if loadout_effort or saved_effort:
                notices.append(
                    f"effort {loadout_effort or saved_effort} was not applied because the model is not active"
                )
        elif effort is not None:
            effort_value = loadout_effort or saved_effort
            if effort_value and str(effort_value) == effort.get("currentValue"):
                self._effort_pick = str(effort_value)  # already set: still the pick a model change must keep
            if effort_value and str(effort_value) != effort.get("currentValue"):
                value = str(effort_value)
                offered = option_values(effort)
                if value not in offered:
                    notices.append(
                        f"effort {value} is not offered by this model "
                        f"(available: {', '.join(offered) or 'none'})"
                    )
                else:
                    try:
                        options = await runner.set_config_option(effort_id, value)
                    except Exception as exc:
                        notices.append(f"effort {value} was not applied: {exc}")
                    else:
                        reported = next((o.get("currentValue") for o in options or []
                                         if o.get("id") == effort_id), None)
                        if reported != value:
                            notices.append(f"effort {value} was accepted but the harness reports {reported}")
                        else:
                            self._effort_pick = value  # the loadout's effort is kept across a model change
        elif loadout_effort or saved_effort:
            notices.append(
                f"this harness advertises no effort option, so effort {loadout_effort or saved_effort} was not applied"
            )
        # An option the loadout or a canonical saved pick already set is not re-sent under its
        # raw ID (an older save): the loadout wins, and a raw copy must not undo it.
        handled = set()
        if model is not None and model_value:
            handled.add(str(model["id"]))
        if effort_id is not None and (loadout_effort or saved_effort):
            handled.add(effort_id)
        handled |= {option_id for option_id in (effort_id, model and str(model["id"])) if option_id in picked}
        for config_id, value in saved.items():
            if config_id in handled:
                continue
            if effort_id is not None and config_id == effort_id and not model_ok:
                continue
            option = next((o for o in self.advertised_config_options() if o.get("id") == config_id), None)
            if option is None:
                notices.append(f"saved pick {config_id}={value} is not offered by this harness, so it was not applied")
                continue
            if option.get("currentValue") == value:
                continue
            try:
                await runner.set_config_option(config_id, value)
            except Exception as exc:
                notices.append(f"saved pick {config_id}={value} was not applied: {exc}")
        for notice in notices:
            logger.warning("ACP restore: %s", notice)
        return notices

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
        self._forget_session_picks()
        self.startup_error = None
        self._terminal_session_id = None
        self._observations.clear()
        self._observation_count = 0
        self._observation_rows.clear()
        self._unwritten_observations.clear()
        self._selected_profile = profile
        self.hub_plugin = self.event_bus.get_service("hub_plugin") or self.hub_plugin
        if not getattr(self.hub_plugin, "enabled", True):
            self.hub_plugin = None  # hub off (pipe mode, KOLLAB_NO_HUB): a harness with no mesh tools

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
            self._runner.stderr_callback = self._on_runner_stderr
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
            self.restore_notices = await self._restore_options(profile)
            self._announce_restore_notices()
            self.event_bus.register_service("acp_orchestrator", self)
            self._save_binding()
        except BaseException as exc:
            self.startup_error = str(exc) or type(exc).__name__
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
                             profile=self._profile_name, state=runner.state, context=runner.resume_mode,
                             supports_vision=getattr(runner, "supports_image_input", False) is True)
            llm = self.event_bus.get_service("llm_service")
            display = getattr(llm, "message_display_service", None)
            if display is not None:
                line = (f"{self.identity} resumed its session" if runner.resume_mode in {"resumed", "loaded"}
                        else f"{self.identity} starts with fresh context")
                display.display_system_message(line)

    def admit(
        self, text: str, *, request_id: str, source: str, thread_id: str | None = None,
        origin: dict[str, Any] | None = None,
        record: bool = True,
        images: tuple[dict[str, str], ...] = (),
    ) -> ACPAdmission:
        if self._runner is None or self._closed:
            raise ACPError(self.startup_error or "ACP runner is not active")
        if source == "agent" and (origin or {}).get("sender"):
            text = f"Message from {origin['sender']} [thread:{thread_id or ''}]\n{text}"
        kwargs = {"images": images} if images else {}
        admission = self._runner.admit(
            text, request_id=request_id, source=source, thread_id=thread_id, origin=origin,
            **kwargs,
        )
        task = asyncio.create_task(self._complete(admission), name=f"acp-turn-{request_id}")
        self._completion_tasks.add(task)
        task.add_done_callback(self._completion_tasks.discard)
        if record:
            try:
                self._record_user(
                    request_id, text, source, thread_id, track_completion=True
                )
            except Exception as exc:
                # Admission is already owned. Remove it before the worker can run,
                # and let that owner's terminal event report the persistence error.
                if not admission.future.done():
                    admission.future.set_exception(exc)
                self._runner.cancel_queued(request_id)
        return admission

    def observe_notification(self, message: Any) -> None:
        thread = f" [thread:{message.thread_id}]" if message.thread_id else ""
        reply = f" [reply-to:{message.reply_to}]" if message.reply_to else ""
        entry = AgentHudEntry(
            section="hub", label=f"{message.from_identity}->{message.to}",
            content=f"[hub channel: {message.from_identity} -> {message.to}{thread}{reply}]\n{message.content}",
        )
        self._observations.append(entry)
        self._observation_count += 1
        # Written as it arrives, like a native agent's hub message, so a watching
        # page reloads it now rather than when the running turn ends. The flush
        # hands the batch to the model without writing it again.
        row_id = f"observations-{uuid.uuid4().hex}"
        try:
            self._record_user(
                row_id,
                format_agent_hud([entry]),
                "observation",
                None,
                track_completion=True,
            )
        except Exception as error:
            logger.warning(
                "ACP observation history write failed (%s)", type(error).__name__
            )
            pending = self._unwritten_observations
            if len(pending) == pending.maxlen:
                logger.warning(
                    "ACP observation retry queue is full; the oldest message "
                    "waiting for a history row is dropped"
                )
            pending.append(entry)
        else:
            self._observation_rows.append(row_id)
        if not self.is_processing:
            self._flush_observations()

    def _flush_observations(self) -> None:
        if not self._observations or self._closed or self._runner is None:
            return
        discarded = self._observation_count - len(self._observations)
        entries = list(self._observations)
        if discarded:
            entries.insert(0, AgentHudEntry("status", "observations", f"{discarded} older observations omitted."))
        text = format_agent_hud(entries)
        # A message whose arrival row failed gets one more try here. After a
        # second failure it reaches history only through the batch row, which
        # is written when no message has a row of its own.
        unwritten = list(self._unwritten_observations)
        self._unwritten_observations.clear()
        for entry in unwritten:
            row_id = f"observations-{uuid.uuid4().hex}"
            try:
                self._record_user(
                    row_id,
                    format_agent_hud([entry]),
                    "observation",
                    None,
                    track_completion=True,
                )
            except Exception as error:
                logger.warning(
                    "ACP observation history write failed again (%s)",
                    type(error).__name__,
                )
            else:
                self._observation_rows.append(row_id)
        rows, self._observation_rows = self._observation_rows, []
        # The turn takes the newest row's id, so its reply threads under that
        # row; the older rows' bookkeeping goes now.
        request_id = rows[-1] if rows else f"observations-{uuid.uuid4().hex}"
        try:
            self.admit(
                text, request_id=request_id, source="observation", record=not rows
            )
        except Exception:
            self._observation_rows = rows + self._observation_rows
            return
        for stale in rows[:-1]:
            self._parents.pop(stale, None)
            self._user_history_tasks.pop(stale, None)
        self._observations.clear()
        self._observation_count = 0

    def _conversation_id(self) -> str:
        llm = self.event_bus.get_service("llm_service")
        manager = getattr(llm, "conversation_manager", None)
        return str(getattr(manager, "current_session_id", "") or self.identity)

    @property
    def binding_conversation_id(self) -> str | None:
        return self._binding_conversation_id

    def save_binding_for(self, conversation_id: str, binding: Any) -> None:
        """Put back a binding a switch overwrote, so a rollback can resume it."""
        self._bindings.save(conversation_id, binding)

    def _announce_restore_notices(self) -> None:
        """Show each requested value the harness did not take, in the chat."""
        for notice in self.restore_notices:
            self._show_notice(notice)

    def _show_notice(self, notice: str) -> None:
        llm = self.event_bus.get_service("llm_service") if self.event_bus else None
        display = getattr(llm, "message_display_service", None)
        if display is not None:
            display.display_system_message(f"ACP setting not applied: {notice}")

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
            history.append(ConversationMessage(role="user", content=text, metadata=metadata))
        # A row that landed outside the turn it belongs to (a peer's admitted
        # message, steering, a flushed observation) announces itself: the
        # engine warms its mirror and watching windows reload before
        # turn_complete. The turn's own opening row stays quiet, like the
        # native user_message path -- its sender shows it locally and
        # turn_complete reloads everyone.
        if source in {"agent", "observation", "steering"}:
            publish_semantic(
                self.event_bus, "hub_message", message_id=request_id, input_source=source
            )
        display = getattr(llm, "message_display_service", None)
        # Hub already drew incoming messages. The batched model context is
        # persisted as a HUD for web/history replay, never echoed as user input.
        if display is not None and source not in {"agent", "observation"}:
            from kollabor_voice.voice_context import strip_voice_preamble

            # A mic turn shows what was said, never the voice rules sent with it.
            display.display_user_message(strip_voice_preamble(text))

    async def steer(
        self, text: str, *, origin: dict[str, Any], thread_id: str | None = None,
        images: tuple[dict[str, str], ...] = (),
    ) -> bool:
        kwargs = {"images": images} if images else {}
        if not self._runner or not await self._runner.steer(text, origin=origin, thread_id=thread_id, **kwargs):
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
            observed = self._observed_turns.get((admission.request_id, admission.turn_id))
            if (result is None and observed is not None and not observed.finished.is_set()
                    and not isinstance(exc, asyncio.CancelledError)):
                # Cancel rejects the admission before the SDK prompt settles.
                # Retain updates through that prompt's own finish, without
                # reading a replacement turn's shared runner buffer.
                timeout = getattr(self._runner, "cancel_timeout", 30.0) + 15.0
                try:
                    await asyncio.wait_for(observed.finished.wait(), timeout=timeout)
                except asyncio.TimeoutError:
                    logger.warning("ACP stopped-turn history captured before prompt teardown settled")
            session_id = (result.session_id if result is not None else
                          observed.session_id if observed is not None else self._terminal_session_id)
            if session_id is None and self._runner is not None:
                session_id = self._runner.session_id
            session_id = session_id or ""
            generation = (result.generation if result is not None else
                          observed.generation if observed is not None else admission.generation)
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
            error_code = getattr(exc, "code", None)
            if error_code is not None:
                end_frame["code"] = error_code
            if result is None and observed is not None and any(
                update.get("kind") in {"text", "agent_thought_chunk", "tool_call", "tool_call_update"}
                for update in observed.updates
            ):
                # An interrupted turn still owns its observed transcript. A
                # failed write of a normal result is never retried here: that
                # result may already have reached one history sink.
                partial = ACPResult(
                    generation, session_id, "", stop_reason,
                    tuple(deepcopy(observed.updates)), dict(end_frame),
                    admission.turn_id, admission.request_id, admission.source, admission.thread_id,
                )
                try:
                    user_history = self._user_history_tasks.get(admission.request_id)
                    if user_history is not None:
                        await user_history
                    await self._record_result(partial)
                except BaseException as history_exc:
                    logger.warning("ACP interrupted transcript write failed (%s)", type(history_exc).__name__)
            if stop_reason == "error":
                llm = self.event_bus.get_service("llm_service")
                display = getattr(llm, "message_display_service", None)
                if display is not None:
                    display.display_error_message(end_frame["error"])
                publish_semantic(
                    self.event_bus, "error", message=end_frame["error"], code=error_code,
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
                code=error_code,
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
            self._observed_turns.pop((admission.request_id, admission.turn_id), None)
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
                    "acp_updates": [update for update in result.updates
                                    if update.get("kind") not in {"plan", "usage_update"}],
                    "stop_reason": result.stop_reason, "thinking": thoughts}
        tools: dict[str, dict[str, Any]] = {}
        for update in result.updates:
            if update.get("kind") not in {"tool_call", "tool_call_update"}:
                continue
            payload = update.get("payload") or {}
            tool_id = str(payload.get("toolCallId") or "")
            previous = tools.get(tool_id, {}).get("metadata", {}).get("acp", {})
            payload = {**previous, **{key: value for key, value in payload.items() if value is not None}}
            tools[tool_id] = {"id": tool_id, "name": payload.get("title") or payload.get("kind") or "Harness Tool",
                "input": payload.get("rawInput") if payload.get("rawInput") is not None else {},
                "status": payload.get("status"),
                "result": payload.get("rawOutput") if payload.get("rawOutput") is not None else payload.get("content"),
                "is_error": payload.get("status") == "failed",
                "metadata": {"acp": dict(payload)}}
        metadata["tool_calls"] = list(tools.values())
        if manager is not None:
            manager.add_message("assistant", text or result.text,
                                parent_uuid=self._parents.get(result.request_id), metadata=metadata)
            manager.save_conversation()
        history = getattr(llm, "conversation_history", None)
        if isinstance(history, list) and history is not getattr(manager, "messages", None):
            history.append(ConversationMessage(role="assistant", content=text or result.text,
                                               metadata=metadata, thinking="".join(thoughts)))
        logger_service = getattr(llm, "conversation_logger", None)
        if logger_service is not None:
            await logger_service.log_assistant_message(text or result.text,
                parent_uuid=self._parents.get(result.request_id, ""), model=self._profile_name,
                thinking_content=thoughts, tool_calls=list(tools.values()), display_metadata=metadata)
        self._parents.pop(result.request_id, None)

    async def submit(self, text: str, *, source: str = "rpc", thread_id: str | None = None) -> dict[str, Any]:
        request_id = f"request-{uuid.uuid4().hex}"
        admission = self.admit(text, request_id=request_id, source=source, thread_id=thread_id)
        result: ACPResult = await admission.future
        return {"text": result.text, "session_id": result.session_id,
                "generation": result.generation, "turn_id": result.turn_id,
                "request_id": result.request_id, "stop_reason": result.stop_reason,
                "end_frame": result.end_frame}

    def _on_runner_stderr(self, chunk: bytes) -> None:
        logger.info("ACP diagnostic [%s]: %s", self._profile_name, chunk.decode(errors="replace").rstrip())
        llm = self.event_bus.get_service("llm_service")
        display = getattr(llm, "message_display_service", None)
        renderer = getattr(display, "renderer", None)
        terminal_state = getattr(renderer, "terminal_state", None)
        # Raw input owns an active TUI. Keep its diagnostics in the existing
        # file log; detached/standalone and pipe paths retain original stderr.
        if (not getattr(renderer, "pipe_mode", False)
                and getattr(terminal_state, "current_mode", None) == TerminalMode.RAW):
            return
        sys.stderr.buffer.write(b"[acp] " + chunk)
        sys.stderr.buffer.flush()

    def _speak_reply(self, voice: dict[str, Any], text: str) -> None:
        """Hand a mic turn's reply to voice, as the native pipeline does."""
        from kollabor_ai.response_channels import split_response_channels

        display_text, spoken_text = split_response_channels(text)
        if not display_text.strip() and not spoken_text:
            return
        task = asyncio.create_task(self.event_bus.emit_with_hooks(
            "voice_response_ready",
            {"voice": voice, "display_text": display_text, "spoken_text": spoken_text},
            "llm_service",
        ), name="acp-voice-reply")
        self._delivery_tasks.add(task)
        task.add_done_callback(self._delivery_tasks.discard)

    def _on_runner_update(self, update: dict[str, Any]) -> None:
        kind = "token" if update.get("kind") == "text" else str(update.get("kind", "update"))
        key = (str(update.get("request_id") or ""), int(update.get("turn_id") or 0))
        observed = self._observed_turns.get(key)
        if kind == "turn_start":
            observed = self._observed_turns[key] = _ObservedTurn(
                str(getattr(self._runner, "session_id", None) or ""), int(update["generation"]),
            )
        elif observed is not None:
            if kind == "turn_finished":
                observed.finished.set()
            elif kind not in {"permission_request", "permission_resolved"}:
                observed.updates.append(deepcopy(update))
                if kind == "token":
                    observed.text += str(update.get("text") or "")
        payload = update.get("payload") or {}
        llm = self.event_bus.get_service("llm_service")
        display = getattr(llm, "message_display_service", None)
        if display is not None:
            if kind == "turn_start":
                self._display_turn = key
                display.start_streaming_response()
                display.show_loading("Harness is working")
            elif kind == "token" and key == self._display_turn and observed is not None:
                # Match native streaming: the active area previews buffered text;
                # only the coordinator writes the final transcript above it.
                if not getattr(display.renderer, "pipe_mode", False) and observed.text.strip():
                    display.show_loading("\n".join(observed.text.replace("\r\n", "\n").splitlines()[-5:]))
            elif kind == "turn_finished" and key == self._display_turn:
                self._display_turn = None
                display.hide_loading()
                display.end_streaming_response()
                if observed is not None:
                    from kollabor_ai.response_channels import display_response_text

                    display.display_complete_response(
                        thinking_duration=0, response=display_response_text(observed.text)
                    )
        if kind == "turn_finished" and observed is not None:
            voice = self.voice_turns.pop(key[0], None)
            if voice is not None:
                self._speak_reply(voice, observed.text)
            elif kind == "agent_thought_chunk" and key == self._display_turn:
                display.show_loading(str((payload.get("content") or {}).get("text") or "Harness is thinking")[:120])
        if kind in {"tool_call", "tool_call_update"}:
            tool_id = str(payload.get("toolCallId") or "")
            if kind == "tool_call":
                publish_semantic(self.event_bus, "tool_start", tool_id=tool_id,
                    tool_name=payload.get("title") or payload.get("kind") or "Harness Tool",
                    tool_type=payload.get("kind") or "acp", input=payload.get("rawInput") or {},
                    request_id=update.get("request_id"), turn_id=update.get("turn_id"), acp_passthrough=True)
            if payload.get("status") in {"completed", "failed"}:
                output = payload.get("rawOutput") or payload.get("content") or ""
                if not isinstance(output, str):
                    output = json.dumps(output, ensure_ascii=False)
                publish_semantic(self.event_bus, "tool_result", tool_id=tool_id,
                    tool_name=payload.get("title") or "Harness Tool", success=payload.get("status") == "completed",
                    output=output,
                    metadata={"acp": payload}, request_id=update.get("request_id"),
                    turn_id=update.get("turn_id"), acp_passthrough=True)
                if display is not None:
                    display.display_tool_row(
                        str(payload.get("title") or "Harness Tool"),
                        success=payload.get("status") == "completed",
                    )
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
