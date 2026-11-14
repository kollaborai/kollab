"""Application-owned ACP runner orchestration.

This module adapts configured ACP profiles to the existing event/history seam;
ACP transport and permission protocol details remain in ``acp_bridge``.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import secrets
import sys
import uuid
from collections import deque
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from kollabor.user_input_source import HUMAN_USER_INPUT_SOURCES
from kollabor_ai.profile_manager import EFFORT_LEVELS, LLMProfile
from kollabor_events import EventType
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

# How long a close may hold the session transition. The runner's own teardown
# is bounded well under this; a close still running after it is set aside.
RUNNER_CLOSE_TIMEOUT = 30.0


@dataclass
class _ObservedTurn:
    session_id: str
    generation: int
    text: str = ""
    updates: list[dict[str, Any]] = field(default_factory=list)
    finished: asyncio.Event = field(default_factory=asyncio.Event)


# Saved picks are keyed by what the option means, so a harness that renames its option still restores it.
_CANONICAL_PICK_KEYS = {"model": "model", "thought_level": "effort", "effort": "effort"}


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
        self._starting = False
        self._pending_ready_runner: ACPRunner | None = None
        self._transition_owner: asyncio.Task[Any] | None = None
        self._transition_depth = 0
        self._conversation_transition = False
        self._completion_tasks: set[asyncio.Task[Any]] = set()
        self._delivery_tasks: set[asyncio.Task[Any]] = set()
        self._cancel_tasks: set[asyncio.Task[Any]] = set()
        # Closes that outlived RUNNER_CLOSE_TIMEOUT: their runner is no longer
        # this orchestrator's, but the close keeps running to completion.
        self._quarantined_closes: set[asyncio.Task[Any]] = set()
        self._terminal_session_id: str | None = None
        self._selected_profile: LLMProfile | None = None
        # Explicit loadouts and Session-tab overrides belong to this daemon,
        # separate from ordinary profile defaults and shared saved picks.
        self._loadout_override: tuple[str, str, str] | None = None
        self._session_options: dict[str, dict[str, str | bool]] = {}
        self.startup_error: str | None = None
        # Set while the model fallback switches away from this runner: new
        # prompts are refused with it, since the switch closes the runner.
        self.closing_reason = ""
        # Set while a fallback switch for this runner's failure waits: peer
        # messages are kept for the harness the switch starts (#548).
        self.observations_held = False
        # Each observation turn's messages and their count, by request id, until
        # it ends: one that fails while they are held gets them back.
        self._sent_observations: dict[str, tuple[list[AgentHudEntry], int]] = {}
        self._parents: dict[str, str] = {}
        self._history_tasks: set[asyncio.Task] = set()
        self._user_history_tasks: dict[str, asyncio.Task] = {}
        # An admission owns its history parent and user-row write under its own
        # (generation, turn_id), so a caller-supplied request id reused by a
        # later admission cannot overwrite them (#597). `_parents` and
        # `_user_history_tasks` stay keyed by row id for observation and
        # steering rows, which are uuid-unique.
        self._turn_parents: dict[tuple[int, int], str | None] = {}
        self._turn_user_tasks: dict[tuple[int, int], asyncio.Task] = {}
        # The conversation an admission was made under (#596): resume swaps the
        # live manager's session id in place, and the reply must not follow it.
        self._turn_conversations: dict[tuple[int, int], str] = {}
        self._observed_turns: dict[tuple[str, int], _ObservedTurn] = {}
        self._display_turn: tuple[str, int] | None = None
        # Voice data of a mic turn, by request id, until its reply is spoken.
        # Keyed by (request_id, turn_id): admissions may reuse a caller
        # request ID (#597), and a bare request_id key would let the second
        # admission overwrite the first one's mic-turn metadata.
        self.voice_turns: dict[tuple[str, int], dict[str, Any]] = {}
        # Voice epochs whose rules already went to the harness.
        self.voice_rules_epochs: set[Any] = set()
        from pathlib import Path

        from kollabor_config.config_utils import get_project_data_dir
        self._bindings = ACPBindingStore(Path(get_project_data_dir()) / "acp" / "sessions")
        # The model/effort picked for each harness profile, re-applied at start.
        self._options_path = Path(get_project_data_dir()) / "acp" / "options.json"
        # The Hub child binding's credential file: owner-only, one per rotation.
        self._hub_token_dir = Path(get_project_data_dir()) / "acp" / "hub-bindings"
        self._hub_token_file: Path | None = None
        self._binding_conversation_id: str | None = None
        self.restore_notices: list[str] = []
        self._observations: deque[AgentHudEntry] = deque(maxlen=20)
        self._observation_rows: list[str] = []
        self._unwritten_observations: deque[tuple[str, AgentHudEntry]] = deque(maxlen=20)
        self._observation_count = 0
        # Runner turn IDs are consecutive within this orchestrator lifecycle.
        # A frontier plus completed ranges preserves replay protection without
        # retaining one key per turn. Ranges represent only out-of-order work.
        self._usage_settled_through = 0
        self._usage_settled_ranges: list[tuple[int, int]] = []
        # Only the current display's validated payload is needed on a replay;
        # retaining every turn's full usage would grow for the whole session.
        self._display_usage_payload: tuple[tuple[str, int], dict[str, Any]] | None = None
        # A later prompt may start while the previous turn is still writing
        # history. Its delayed settlement must not populate the new display.
        self._usage_display_turn: tuple[str, int] | None = None

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
        return (self._runner is not None and not self._closed and not getattr(self, "_starting", False)
                and (not self.transitioning or self.transition_owned_by_current_task))

    @property
    def transitioning(self) -> bool:
        return getattr(self, "_transition_owner", None) is not None

    @property
    def conversation_transitioning(self) -> bool:
        return getattr(self, "_conversation_transition", False)

    @property
    def transition_owned_by_current_task(self) -> bool:
        try:
            owner = getattr(self, "_transition_owner", None)
            return owner is not None and owner is asyncio.current_task()
        except RuntimeError:
            return False

    @contextmanager
    def transition(self):
        """Keep startup, profile activation and rollback under one task owner."""
        task = asyncio.current_task()
        if self._transition_owner is not None and self._transition_owner is not task:
            raise ACPError("ACP startup or profile change is in progress; retry when ready")
        if task is None:
            raise ACPError("ACP transition requires an asyncio task")
        self._transition_owner = task
        self._transition_depth += 1
        try:
            yield
        finally:
            self._transition_depth -= 1
            if self._transition_depth == 0:
                self._transition_owner = None
                pending, self._pending_ready_runner = self._pending_ready_runner, None
                if pending is not None and pending is self._runner and not self._closed and not self._starting:
                    try:
                        self._publish_runner_ready(pending)
                    except Exception as exc:
                        logger.warning("ACP ready notice failed (%s)", type(exc).__name__)

    @property
    def runner(self) -> ACPRunner | None:
        return self._runner

    @property
    def supports_image_input(self) -> bool:
        return bool(self._runner and getattr(self._runner, "supports_image_input", False))

    def advertised_config_options(self) -> list[dict[str, Any]]:
        """Return config options advertised by the active ACP session."""
        runner = self._runner
        if runner is None or self._closed or runner is getattr(self, "_unknown_config_runner", None):
            return []
        return runner.advertised_config_options()

    @property
    def config_outcome_unknown(self) -> bool:
        runner = self._runner
        return runner is not None and runner is getattr(self, "_unknown_config_runner", None)

    def quarantine_config_options(self) -> None:
        """A lost response leaves the current runner's configuration unknown.

        Late notifications cannot clear this guard. A replacement runner from an
        explicit session restart is the next source of authoritative options.
        """
        self._unknown_config_runner = self._runner

    async def set_config_option(
        self, config_id: str, value: str | bool, *, persist: bool = True,
    ) -> list[dict[str, Any]]:
        """Set a confirmed ACP option, optionally for this daemon only."""
        runner = self._runner
        if runner is not None and runner is getattr(self, "_unknown_config_runner", None):
            raise ACPError("ACP configuration is unknown; restart the harness session")
        if (runner is None or self._closed or getattr(self, "_starting", False)
                or (self.transitioning and not self.transition_owned_by_current_task)):
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
            key = _CANONICAL_PICK_KEYS.get(category, config_id)
            session_options = getattr(self, "_session_options", None)
            if session_options is None:
                session_options = self._session_options = {}
            if persist:
                try:
                    self._save_confirmed_options({key: value})
                except OSError as exc:
                    # The live pick still belongs to this daemon even if the
                    # shared store could not safely be updated.
                    session_options.setdefault(self._profile_name, {})[key] = value
                    logger.warning("ACP option save failed: %s", exc)
            else:
                session_options.setdefault(self._profile_name, {})[key] = value
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

    def _save_confirmed_options(self, picks: dict[str, str | bool]) -> None:
        """Atomically save already-confirmed picks without losing sibling profiles."""
        if not self._profile_name:
            raise OSError("ACP profile is not active")
        from kollabor_config.atomic_write import write_text_atomic
        from kollabor_config.file_lock import locked_file

        self._options_path.parent.mkdir(parents=True, exist_ok=True)
        with locked_file(str(self._options_path) + ".lock", private=True):
            if self._options_path.exists():
                try:
                    saved = json.loads(self._options_path.read_text())
                except (OSError, ValueError) as exc:
                    raise OSError("ACP options are unreadable; saved picks were not overwritten") from exc
                if not isinstance(saved, dict) or not isinstance(saved.get(self._profile_name, {}), dict):
                    raise OSError("ACP options have an invalid shape; saved picks were not overwritten")
            else:
                saved = {}
            saved.setdefault(self._profile_name, {}).update(picks)
            write_text_atomic(self._options_path, json.dumps(saved, indent=2))
        for key in picks:
            getattr(self, "_session_options", {}).get(self._profile_name, {}).pop(key, None)

    def _effort_option(self) -> dict[str, Any] | None:
        """The advertised reasoning-effort option, found by category. The ID "effort" is only the
        fallback for a harness that advertises it with no category."""
        options = self.advertised_config_options()
        return (next((o for o in options if str(o.get("category") or "").lower() in {"thought_level", "effort"}), None)
                or next((o for o in options if o.get("id") == "effort" and not o.get("category")), None))

    async def _restore_options(self, profile: LLMProfile) -> list[str]:
        """Restore daemon, loadout, saved, then profile picks in precedence order.

        Apply model before effort because changing the model changes the
        advertised effort values. Returns a notice for each rejected pick.
        """
        notices: list[str] = []
        runner = self._runner
        if runner is None:
            return notices
        saved = dict(self._saved_options().get(profile.name) or {})
        picked = getattr(self, "_user_picks", {})  # confirmed this session: a replay must not undo them
        override = getattr(self, "_loadout_override", None)
        loadout_model, loadout_effort = override[1:] if override and override[0] == profile.name else ("", "")
        session = getattr(self, "_session_options", {}).get(profile.name, {})
        profile_model = str(getattr(profile, "model", "") or "")
        profile_effort = str(getattr(profile, "effort", "") or "")
        if not (saved or session or loadout_model or loadout_effort or profile_model or profile_effort):
            return notices  # nothing to re-apply: stay lazy, as before
        model = next((o for o in self.advertised_config_options()
                      if o.get("category") == "model" or o.get("id") == "model"), None)
        saved_model = saved.pop("model", None)
        if saved_model is None and model is not None and str(model["id"]) not in picked:
            saved_model = saved.pop(str(model["id"]), None)  # an older save under the raw ID
        model_value = session.get("model") or loadout_model or saved_model or profile_model
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
            effort_value = session.get("effort") or loadout_effort or saved_effort or profile_effort
            if effort_value:
                notices.append(
                    f"effort {effort_value} was not applied because the model is not active"
                )
        elif effort is not None:
            effort_value = session.get("effort") or loadout_effort or saved_effort or profile_effort
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
        elif session.get("effort") or loadout_effort or saved_effort or profile_effort:
            effort_value = session.get("effort") or loadout_effort or saved_effort or profile_effort
            notices.append(
                f"this harness advertises no effort option, so effort {effort_value} was not applied"
            )
        # An option the loadout or a canonical saved pick already set is not re-sent under its
        # raw ID (an older save): the loadout wins, and a raw copy must not undo it.
        handled = set()
        if model is not None and model_value:
            handled.add(str(model["id"]))
        if effort_id is not None and (session.get("effort") or loadout_effort or saved_effort or profile_effort):
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

    async def start(
        self, profile: LLMProfile, *, resume_conversation: str | None = None,
        loadout: tuple[str, str] | None = None,
    ) -> None:
        with self.transition():
            await self._start_owned(profile, resume_conversation=resume_conversation, loadout=loadout)

    async def _start_owned(
        self, profile: LLMProfile, *, resume_conversation: str | None = None,
        loadout: tuple[str, str] | None = None,
    ) -> None:
        if profile.provider != "acp":
            raise ValueError(f"profile {profile.name!r} is not an ACP profile")
        if not profile.command:
            raise ValueError(f"ACP profile {profile.name!r} has no command")
        binding = self._bindings.load(resume_conversation) if resume_conversation else None
        if self._runner is not None:
            # A restart is transactional: retire the old child/session before
            # acquiring a replacement, without creating a second owner.
            await self.close()
        previous_override = self._loadout_override
        previous_session = dict(self._session_options.get(profile.name, {}))
        if loadout is not None:
            self._loadout_override = (profile.name, loadout[0], loadout[1])
            self._session_options.pop(profile.name, None)
        elif self._loadout_override and self._loadout_override[0] != profile.name:
            self._loadout_override = None
        self._closed = False
        self._starting = True
        self._pending_ready_runner = None
        self._forget_session_picks()
        self.startup_error = None
        self._terminal_session_id = None
        self._usage_settled_through = 0
        self._usage_settled_ranges.clear()
        self._display_usage_payload = None
        self._usage_display_turn = None
        if resume_conversation:
            # Another conversation: the messages this one heard are not its.
            self._observations.clear()
            self._observation_count = 0
            self._observation_rows.clear()
            self._unwritten_observations.clear()
        # Otherwise a peer message the old harness never heard stays for the
        # new one: a fallback switch restarts it while the failed turn ends (#548).
        self._selected_profile = profile
        self.hub_plugin = self.event_bus.get_service("hub_plugin") or self.hub_plugin
        if not getattr(self.hub_plugin, "enabled", True):
            self.hub_plugin = None  # hub off (pipe mode, KOLLAB_NO_HUB): a harness with no mesh tools

        owned_runner = None
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
            owned_runner = ACPRunner(
                acp_profile,
                permission_bridge=permission_bridge,
                # A runner set aside by a stalled close may still answer; its
                # late updates must not reach the runner that replaced it.
                update_callback=lambda update: (
                    self._on_runner_update(update) if owned_runner is self._runner else None
                ),
                lifecycle_callback=self._runner_lifecycle,
            )
            self._runner = owned_runner
            self._runner.stderr_callback = self._on_runner_stderr
            self._profile_name = profile.name
            llm = self.event_bus.get_service("llm_service")
            stats = getattr(llm, "session_stats", None)
            if isinstance(stats, dict):
                stats.pop("acp_usage", None)
                stats.pop("acp_turn_usage", None)
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
            self._resume_requested = binding is not None
            await self._runner.start(binding=binding)
            self.restore_notices = await self._restore_options(profile)
            if loadout is not None:
                options = self.advertised_config_options()
                model = next((o for o in options if str(o.get("category") or "").lower() == "model"
                              or o.get("id") == "model"), None)
                effort = self._effort_option()
                for label, value, option in (("model", loadout[0], model),
                                             ("effort", loadout[1], effort)):
                    unadvertised = (option is not None and option.get("type") == "select"
                                    and value not in option_values(option))
                    if value and (option is None or option.get("currentValue") != value or unadvertised):
                        reported = option.get("currentValue") if option else None
                        raise ValueError(
                            f"ACP loadout {label} {value!r} was not applied "
                            f"(harness reports {reported!r})"
                        )
            self._announce_restore_notices()
            self.event_bus.register_service("acp_orchestrator", self)
            self._starting = False
            if self._pending_ready_runner is not None and self._transition_depth == 1:
                pending, self._pending_ready_runner = self._pending_ready_runner, None
                await self._runner_lifecycle("ready", pending)
            else:
                self._save_binding()
            if not self.is_processing:
                self._flush_observations()  # what the harness it replaced never heard
        except BaseException as exc:
            if self._runner is not owned_runner:
                # A stale attempt may retire only its own child, never a
                # successor installed by another lifecycle owner.
                if owned_runner is not None:
                    try:
                        await owned_runner.close()
                    except BaseException as cleanup_exc:
                        logger.warning("Stale ACP runner cleanup failed: %s", cleanup_exc)
                raise
            self.startup_error = str(exc) or type(exc).__name__
            try:
                await self.close()
            except BaseException as cleanup_exc:
                logger.warning("ACP runner restart cleanup failed: %s", cleanup_exc)
            # close retains a failed teardown owner for an explicit retry.
            self._binding = None
            self._profile_name = None
            self._closed = True
            if loadout is not None:
                self._loadout_override = previous_override
                if previous_session:
                    self._session_options[profile.name] = previous_session
                else:
                    self._session_options.pop(profile.name, None)
            raise
        finally:
            if self._runner is None or self._runner is owned_runner:
                self._starting = False
                if self._transition_depth == 1:
                    self._pending_ready_runner = None

    async def _runner_lifecycle(self, state: str, runner: ACPRunner) -> None:
        if runner is not self._runner:
            return
        if state == "starting" and self.hub_plugin is not None:
            token, generation = self.hub_plugin.rotate_acp_hub_binding()
            self._binding = (token, generation)
            socket_path = getattr(getattr(self.hub_plugin, "_socket_server", None), "socket_path", "")
            if not socket_path:
                raise RuntimeError("ACP hub socket is not ready before session/new")
            from kollabor.mcp_hub import kollab_hub_argv
            argv = kollab_hub_argv()
            # Adapters put the MCP config, env included, on the child's command
            # line (--mcp-config), so it names a credential file, never the token.
            token_file = self._write_hub_token_file(token)
            runner.profile = replace(runner.profile, mcp_servers=({
                "name": "kollab-hub", "command": argv[0], "args": argv[1:],
                "env": {"KOLLAB_ACP_HUB_TOKEN_FILE": str(token_file),
                        "KOLLAB_ACP_GENERATION": str(generation),
                        "KOLLAB_ACP_HUB_SOCKET": str(socket_path)},
            },))
        if state == "ready":
            if self._starting or self._transition_depth > 1:
                self._pending_ready_runner = runner
                return
            self._publish_runner_ready(runner)

    def _write_hub_token_file(self, token: str) -> Path:
        """Replace the binding's owner-only credential file and return its path."""
        from kollabor_config.private_fs import ensure_private_dir, open_nofollow

        self._drop_hub_token_file()
        ensure_private_dir(self._hub_token_dir)
        path = self._hub_token_dir / f"{secrets.token_hex(16)}.token"
        fd = open_nofollow(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self._hub_token_file = path
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(token)
        return path

    def _drop_hub_token_file(self) -> None:
        path, self._hub_token_file = self._hub_token_file, None
        if path is not None:
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    def _publish_runner_ready(self, runner: ACPRunner) -> None:
        self._save_binding()
        profile = self._selected_profile
        publish_semantic(self.event_bus, "acp_session",
                         harness=profile.description or profile.name if profile else self._profile_name,
                         profile=self._profile_name, state=runner.state, context=runner.resume_mode,
                         supports_vision=getattr(runner, "supports_image_input", False) is True)
        llm = self.event_bus.get_service("llm_service")
        display = getattr(llm, "message_display_service", None)
        if display is not None:
            harness = profile.description or profile.name if profile else self._profile_name
            if runner.resume_mode in {"resumed", "loaded"}:
                line = f"{self.identity} resumed its session"
            elif getattr(self, "_resume_requested", False):
                line = (f"{harness} could not resume its previous session, so its earlier context is lost; "
                        f"{self.identity} starts with fresh context")
            else:
                line = f"{self.identity} starts with fresh context"
            display.display_system_message(line)

    def admit(
        self, text: str, *, request_id: str, source: str, thread_id: str | None = None,
        origin: dict[str, Any] | None = None,
        record: bool = True,
        images: tuple[dict[str, str], ...] = (),
        operator_inputs: tuple[dict[str, str], ...] = (),
    ) -> ACPAdmission:
        if self.closing_reason:
            raise ACPError(self.closing_reason)
        if (self._runner is None or self._closed or getattr(self, "_starting", False)
                or (self.transitioning and not self.transition_owned_by_current_task)):
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
        turn_key = (admission.generation, admission.turn_id)
        self._turn_conversations[turn_key] = self._conversation_id()
        if record:
            try:
                self._record_user(
                    request_id,
                    text,
                    source,
                    thread_id,
                    track_completion=True,
                    operator_inputs=operator_inputs,
                )
            except Exception as exc:
                # Admission is already owned. Remove this admission (by identity,
                # not request id) before the worker can run, and let its terminal
                # event report the persistence error.
                self._parents.pop(request_id, None)
                self._user_history_tasks.pop(request_id, None)
                if not admission.future.done():
                    admission.future.set_exception(exc)
                self._runner.cancel_queued(request_id, admission)
                return admission
        self._take_turn_history(turn_key, request_id)
        return admission

    def _take_turn_history(self, turn_key: tuple[int, int], request_id: str) -> None:
        """Move this row's parent and user-row write under the admission's own key."""
        self._turn_parents[turn_key] = self._parents.pop(request_id, None)
        task = self._user_history_tasks.pop(request_id, None)
        if task is not None:
            self._turn_user_tasks[turn_key] = task

    def hold_observations(self) -> None:
        """Keep peer messages for the harness a pending fallback switch starts:
        the failing one would take them and fail them too (#548)."""
        self.observations_held = True

    def release_observations(self, flush: bool = True) -> None:
        """The switch is over: the harness that runs now gets what was kept, or,
        without ``flush``, the next prompt it runs does."""
        self.observations_held = False
        if flush and not self.is_processing:
            self._flush_observations()

    def _requeue_observations(self, request_id: str) -> None:
        """An observation turn failed while a switch waits on that failure: the
        harness the switch starts gets its messages, under their written row."""
        sent = self._sent_observations.pop(request_id, None)
        if sent is None or not self.observations_held:
            return
        entries, count = sent
        self._observations = deque([*entries, *self._observations], maxlen=self._observations.maxlen)
        self._observation_count += count
        self._observation_rows.insert(0, request_id)

    def observe_notification(self, message: Any) -> bool:
        """Admit a peer observation. False means refused: nothing was admitted.

        A message whose history row fails waits in the retry queue until a row
        lands. The queue is bounded: when it is full, the new message is refused
        instead of evicting an accepted one, and the hub must not acknowledge it
        (#581).
        """
        pending = self._unwritten_observations
        if len(pending) == pending.maxlen and not self.is_processing:
            # Storage may have recovered since the queue filled: drain it first.
            self._flush_observations()
        if len(pending) == pending.maxlen:
            # Decided before any row is written: add_message appends its row
            # before an autosave can raise, so a refused message that was
            # written first would leave one more row on every offer (#581).
            logger.warning(
                "ACP observation retry queue is full; the message was refused "
                "and not acknowledged"
            )
            return False
        thread = f" [thread:{message.thread_id}]" if message.thread_id else ""
        reply = f" [reply-to:{message.reply_to}]" if message.reply_to else ""
        entry = AgentHudEntry(
            section="hub", label=f"{message.from_identity}->{message.to}",
            content=f"[hub channel: {message.from_identity} -> {message.to}{thread}{reply}]\n{message.content}",
        )
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
            pending.append((row_id, entry))
        else:
            self._observation_rows.append(row_id)
        self._observations.append(entry)
        self._observation_count += 1
        if not self.is_processing:
            self._flush_observations()
        return True

    def _flush_observations(self) -> None:
        if self._closed or self._runner is None or self.observations_held:
            return
        if not self._observations and not self._unwritten_observations:
            return
        # A message whose history row is still missing keeps its place in the
        # retry queue until a row lands. A failed retry is kept, never dropped
        # (#581).
        pending = list(self._unwritten_observations)
        self._unwritten_observations.clear()
        in_batch = {id(entry) for entry in self._observations}
        earlier_rows: list[str] = []
        for row_id, entry in pending:
            try:
                # The retry keeps its first attempt's row id (#581).
                self._record_user(
                    row_id,
                    format_agent_hud([entry]),
                    "observation",
                    None,
                    track_completion=True,
                    retry=True,
                )
            except Exception as error:
                logger.warning(
                    "ACP observation history write failed again (%s)",
                    type(error).__name__,
                )
                self._unwritten_observations.append((row_id, entry))
            else:
                # A retried row of a message an earlier batch already carried is
                # only a record; the reply to this batch must not thread under it.
                if id(entry) in in_batch:
                    self._observation_rows.append(row_id)
                else:
                    earlier_rows.append(row_id)
        for row in earlier_rows:
            self._parents.pop(row, None)
            self._user_history_tasks.pop(row, None)
        rows, self._observation_rows = self._observation_rows, []
        if not self._observations:
            # Only retries ran. Their messages already reached the model in an
            # earlier batch, so their own rows are the record.
            for row in rows:
                self._parents.pop(row, None)
                self._user_history_tasks.pop(row, None)
            return
        discarded = self._observation_count - len(self._observations)
        entries = list(self._observations)
        if discarded:
            entries.insert(0, AgentHudEntry("status", "observations", f"{discarded} older observations omitted."))
        text = format_agent_hud(entries)
        # The turn takes the newest row's id, so its reply threads under that
        # row. The batch gets no row of its own: each message is persisted by
        # its own row, so a batch row would only duplicate them (#581).
        request_id = rows[-1] if rows else f"observations-{uuid.uuid4().hex}"
        try:
            self.admit(text, request_id=request_id, source="observation", record=False)
        except Exception:
            self._observation_rows = rows + self._observation_rows
            return
        for stale in rows[:-1]:
            self._parents.pop(stale, None)
            self._user_history_tasks.pop(stale, None)
        self._sent_observations[request_id] = (list(self._observations), self._observation_count)
        self._observations.clear()
        self._observation_count = 0

    async def hand_over_to_native(self) -> bool:
        """Give the peer messages still buffered for the harness to the native profile.

        Call it once a native profile is active after this harness closed. Each
        message is already in the conversation; this drops the buffer so no
        harness is offered them again. A message that still has no history row
        is kept, and no turn starts. Each history write is awaited first. True
        means the native continuation can safely read every kept message, and
        the one turn that answers them has been started here.
        """
        self.observations_held = False
        if not self._observation_count and not self._unwritten_observations:
            return False
        # A message whose arrival row failed has no history row yet; the native
        # model reads history, so it gets one more try here.
        still_unwritten: list[tuple[str, AgentHudEntry]] = []
        writes: list[asyncio.Task | None] = []
        for row_id, entry in list(self._unwritten_observations):
            if not self._write_history_row(row_id, format_agent_hud([entry])):
                still_unwritten.append((row_id, entry))
            self._parents.pop(row_id, None)
            writes.append(self._user_history_tasks.pop(row_id, None))
        for row_id in self._observation_rows:
            self._parents.pop(row_id, None)
            writes.append(self._user_history_tasks.pop(row_id, None))
        self._observation_rows.clear()
        self._unwritten_observations.clear()
        self._observations.clear()
        self._observation_count = 0
        if still_unwritten:
            # The handover writes such messages as one batch row. No native turn may
            # start while one of them is still missing from history.
            batch_id = f"observations-{uuid.uuid4().hex}"
            batch_text = format_agent_hud([entry for _, entry in still_unwritten])
            if not self._write_history_row(batch_id, batch_text):
                self._unwritten_observations.extend(still_unwritten)
                return False
            self._parents.pop(batch_id, None)
            writes.append(self._user_history_tasks.pop(batch_id, None))
        # The native turn reads history, so it starts once these writes have settled.
        # A write that failed was already shown by _observation_task_done.
        settled = [task for task in writes if task is not None]
        if settled:
            await asyncio.gather(*settled, return_exceptions=True)
        # Every switch into a native profile ends here (coordinator, state RPC,
        # loadout activation, the fallback plugin); the turn that answers the
        # buffered messages starts with this event (#560).
        await self.event_bus.emit_with_hooks(
            EventType.TRIGGER_LLM_CONTINUE,
            {"source": "acp_handover", "content": ""},
            "acp_orchestrator",
        )
        return True

    def _write_history_row(self, row_id: str, text: str) -> bool:
        """Write one observation row. True when it is in history, even if the write raised.

        A write that raised after its row landed is retried once under the same id, so the
        rest of the write (log, mirror) runs and the row is never appended twice (#581).
        A write that raised before its row landed returns False, and the caller keeps the
        message queued.
        """
        for _ in range(2):
            try:
                self._record_user(row_id, text, "observation", None, retry=True, track_completion=True)
                return True
            except Exception as error:
                logger.warning(
                    "ACP observation history write failed again (%s)",
                    type(error).__name__,
                )
                if self._landed_row_uuid(row_id) is None:
                    return False
        return True

    def _landed_row_uuid(self, request_id: str) -> str | None:
        """The uuid of the conversation row a failed write already appended, if any."""
        llm = self.event_bus.get_service("llm_service")
        manager = getattr(llm, "conversation_manager", None)
        for message in reversed(getattr(manager, "messages", None) or []):
            if (message.get("metadata") or {}).get("request_id") == request_id:
                return message.get("uuid")
        return None

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

    def _observation_task_done(self, task: asyncio.Task) -> None:
        """A failed observation history write is shown in the chat, not only logged."""
        self._history_task_done(task)
        if not task.cancelled() and task.exception() is not None:
            llm = self.event_bus.get_service("llm_service") if self.event_bus else None
            display = getattr(llm, "message_display_service", None)
            if display is not None:
                display.display_system_message("Conversation history not saved for this message.")

    def _record_user(
        self, request_id: str, text: str, source: str, thread_id: str | None,
        *,
        track_completion: bool = False,
        retry: bool = False,
        operator_inputs: tuple[dict[str, str], ...] = (),
    ) -> None:
        llm = self.event_bus.get_service("llm_service")
        manager = getattr(llm, "conversation_manager", None)
        metadata = {
            "provider": "acp",
            "request_id": request_id,
            "source": source,
            "thread_id": thread_id,
        }
        if source in HUMAN_USER_INPUT_SOURCES:
            trusted_operator_inputs = [
                {
                    "source": record["source"],
                    "timestamp": record["timestamp"],
                    "text": record["text"],
                }
                for record in operator_inputs
                if isinstance(record, dict)
                and record.get("source") == source
                and record.get("source") in HUMAN_USER_INPUT_SOURCES
                and isinstance(record.get("timestamp"), str)
                and bool(record.get("timestamp"))
                and isinstance(record.get("text"), str)
                and bool(record.get("text"))
            ]
            if trusted_operator_inputs:
                metadata["operator_inputs"] = trusted_operator_inputs
        if manager is not None:
            # A retry whose first save raised after the append reuses that row,
            # so the conversation holds it once. The log and mirror still run (#581).
            landed = self._landed_row_uuid(request_id) if retry else None
            if landed is None:
                landed = manager.add_message("user", text, metadata=metadata)
            self._parents[request_id] = landed
        logger_service = getattr(llm, "conversation_logger", None)
        if logger_service is not None:
            task = asyncio.create_task(logger_service.log_user_message(text))
            self._history_tasks.add(task)
            if track_completion:
                self._user_history_tasks[request_id] = task
            task.add_done_callback(
                self._observation_task_done if source == "observation" else self._history_task_done
            )
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

    def _mark_usage_settled(self, turn_id: int) -> bool:
        """Mark one unique runner turn; compress already settled neighbors."""
        if turn_id <= self._usage_settled_through:
            return False
        ranges = self._usage_settled_ranges
        index = 0
        while index < len(ranges) and ranges[index][1] < turn_id - 1:
            index += 1
        if index < len(ranges) and ranges[index][0] <= turn_id <= ranges[index][1]:
            return False
        start = end = turn_id
        if index < len(ranges) and ranges[index][1] == turn_id - 1:
            start = ranges[index][0]
            ranges.pop(index)
        if index < len(ranges) and ranges[index][0] == turn_id + 1:
            end = ranges[index][1]
            ranges.pop(index)
        ranges.insert(index, (start, end))
        if ranges[0][0] == self._usage_settled_through + 1:
            self._usage_settled_through = ranges.pop(0)[1]
        return True

    def _settle_turn_usage(self, result: ACPResult) -> dict[str, Any] | None:
        """Fold one prompt response's reconciled token totals into session stats.

        The bundled Codex and Claude adapters report cache counters separately
        from input, with reasoning included in output. Other adapters may use
        different breakdowns; require these components to reconcile with their
        reported total instead of guessing. The reported scope belongs to the
        adapter: Claude's prompt usage excludes subagent/sidechain costs.
        Each (request, turn) settles once. Cost is never invented (#495).
        """
        key = (result.request_id, result.turn_id)
        if not self._mark_usage_settled(result.turn_id):
            return None
        usage = getattr(result, "usage", None) or (result.end_frame or {}).get("acp_turn_usage")
        current_display = self._usage_display_turn in (None, key)
        llm = self.event_bus.get_service("llm_service")
        stats = getattr(llm, "session_stats", None)
        if not isinstance(usage, dict) or not usage:
            if current_display:
                self._display_usage_payload = None
                if isinstance(stats, dict):
                    stats.pop("acp_turn_usage", None)
            return None
        try:
            raw_input = int(usage.get("input_tokens", 0) or 0)
            cache_read = int(usage.get("cached_read_tokens", 0) or 0)
            cache_write = int(usage.get("cached_write_tokens", 0) or 0)
            output_total = int(usage.get("output_tokens", 0) or 0)
        except (TypeError, ValueError, OverflowError):
            raw_input = cache_read = cache_write = output_total = -1
        input_total = raw_input + cache_read + cache_write
        reported_total = usage.get("total_tokens")
        if (any(usage.get(name) is None for name in ("total_tokens", "input_tokens", "output_tokens"))
                or not isinstance(reported_total, int) or isinstance(reported_total, bool)
                or reported_total < 0 or min(raw_input, cache_read, cache_write, output_total) < 0
                or input_total + output_total != reported_total):
            logger.warning("ACP prompt usage breakdown does not match its total; not settling")
            if current_display:
                self._display_usage_payload = None
                if isinstance(stats, dict):
                    stats.pop("acp_turn_usage", None)
            return None
        if current_display:
            self._display_usage_payload = (key, dict(usage))
        if isinstance(stats, dict):
            stats["total_input_tokens"] = int(stats.get("total_input_tokens", 0)) + input_total
            stats["total_output_tokens"] = int(stats.get("total_output_tokens", 0)) + output_total
            if cache_read:
                stats["total_cache_read_tokens"] = (
                    int(stats.get("total_cache_read_tokens", 0)) + cache_read
                )
            if cache_write:
                stats["total_cache_creation_tokens"] = (
                    int(stats.get("total_cache_creation_tokens", 0)) + cache_write
                )
            if current_display:
                stats["acp_turn_usage"] = dict(usage)
        return {"usage": dict(usage), "input_total": input_total, "output_total": output_total}

    async def _await_user_history(self, turn_key: tuple[int, int]) -> None:
        """Wait for the turn's user-row write; a failed write keeps the reply and is shown.

        The adapter already ran and the reply is in hand, so the turn goes on with
        it. A user row that did not reach the file is logged as an error and shown
        in the chat, as a failed reply row is. Cancellation still propagates.
        """
        user_history = self._turn_user_tasks.get(turn_key)
        if user_history is None:
            return
        try:
            await user_history
        except Exception as exc:
            # Reported once: the failed task leaves the map, so a later caller
            # for this turn finds nothing to report again.
            self._turn_user_tasks.pop(turn_key, None)
            logger.error("ACP user message not written to history: %s", type(exc).__name__)
            llm = self.event_bus.get_service("llm_service")
            display = getattr(llm, "message_display_service", None)
            if display is not None:
                display.display_system_message("Conversation history not saved for this message.")

    async def _complete(self, admission: ACPAdmission) -> dict[str, Any]:
        turn_key = (admission.generation, admission.turn_id)
        result: ACPResult | None = None
        settled_usage: dict[str, Any] | None = None
        try:
            result = await admission.future
            # Once the adapter returned, its usage was incurred even if any
            # later binding or history operation fails or is cancelled.
            settled_usage = self._settle_turn_usage(result)
            await self._await_user_history(turn_key)
            self._save_binding()
            await self._record_result(result, turn_key)
        except BaseException as exc:
            # Admission is immediately acknowledged, so every admitted request
            # must still receive one correlated terminal event on close/failure.
            if result is None and admission.future.done() and not admission.future.cancelled():
                # Cancellation can win the scheduling race after the future
                # already received its result but before this task resumed.
                try:
                    received = admission.future.result()
                except BaseException:
                    pass
                else:
                    if isinstance(received, ACPResult):
                        result = received
                        settled_usage = self._settle_turn_usage(result)
            if result is None:
                # No result can arrive through this completed/cancelled future.
                # Close its ID as well so later turns do not leave a gap.
                self._mark_usage_settled(admission.turn_id)
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
            event_usage = ((settled_usage or {}).get("usage") if result is not None else None)
            if (event_usage is None and result is not None and self._display_usage_payload is not None
                    and self._display_usage_payload[0] == (result.request_id, result.turn_id)):
                event_usage = self._display_usage_payload[1]
            if event_usage is not None:
                end_frame["acp_turn_usage"] = dict(event_usage)
            error_code = getattr(exc, "code", None)
            if error_code is not None:
                end_frame["code"] = error_code
            error_class = getattr(exc, "error_class", None)
            if error_class is not None:
                end_frame["errorClass"] = error_class
            error_cause = getattr(exc, "cause", None)
            if error_cause is not None:
                end_frame["cause"] = error_cause
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
                    await self._await_user_history(turn_key)
                    await self._record_result(partial, turn_key)
                except BaseException as history_exc:
                    logger.warning("ACP interrupted transcript write failed (%s)", type(history_exc).__name__)
            if stop_reason == "error":
                llm = self.event_bus.get_service("llm_service")
                display = getattr(llm, "message_display_service", None)
                if display is not None:
                    display.display_error_message(end_frame["error"])
                publish_semantic(
                    self.event_bus, "error", message=end_frame["error"], code=error_code,
                    error_class=error_class, cause=error_cause,
                    request_id=admission.request_id, turn_id=admission.turn_id,
                    session_id=session_id, generation=generation,
                    input_source=admission.source, thread_id=admission.thread_id,
                )
            usage_fields = ({
                "input_tokens": (settled_usage or {}).get("input_total", 0),
                "output_tokens": (settled_usage or {}).get("output_total", 0),
                "turn_usage": dict(event_usage),
            } if event_usage is not None else {})
            publish_semantic(
                self.event_bus, "turn_complete", request_id=admission.request_id,
                session_id=session_id, generation=generation,
                turn_id=admission.turn_id, stop_reason=stop_reason,
                input_source=admission.source, thread_id=admission.thread_id,
                error=str(exc) or type(exc).__name__,
                code=error_code,
                **usage_fields,
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
            self._requeue_observations(admission.request_id)
            self._flush_observations()
            return {"text": "", "session_id": session_id,
                    "generation": generation, "turn_id": admission.turn_id,
                    "request_id": admission.request_id,
                    "stop_reason": stop_reason, "end_frame": end_frame}
        finally:
            self._turn_parents.pop(turn_key, None)
            self._turn_user_tasks.pop(turn_key, None)
            self._turn_conversations.pop(turn_key, None)
            self._observed_turns.pop((admission.request_id, admission.turn_id), None)
            self._sent_observations.pop(admission.request_id, None)
        event_usage = (settled_usage or {}).get("usage")
        if (event_usage is None and self._display_usage_payload is not None
                and self._display_usage_payload[0] == (result.request_id, result.turn_id)):
            event_usage = self._display_usage_payload[1]
        publish_semantic(
            self.event_bus, "turn_complete", request_id=result.request_id,
            session_id=result.session_id, generation=result.generation,
            turn_id=result.turn_id, stop_reason=result.stop_reason,
            input_source=result.source, thread_id=result.thread_id,
            input_tokens=(settled_usage or {}).get("input_total", 0),
            output_tokens=(settled_usage or {}).get("output_total", 0),
            turn_usage=dict(event_usage) if event_usage is not None else None,
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

    async def _record_result(self, result: ACPResult, turn_key: tuple[int, int]) -> None:
        llm = self.event_bus.get_service("llm_service")
        parent = self._turn_parents.get(turn_key)
        manager = getattr(llm, "conversation_manager", None)
        # A turn that outlived its conversation (resume, restart, replacement)
        # goes to that conversation's own log, never into the live one.
        bound = self._turn_conversations.get(turn_key)
        elsewhere = manager is not None and bound is not None and bound != self._conversation_id()
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
        if manager is not None and not elsewhere:
            manager.add_message("assistant", text or result.text,
                                parent_uuid=parent, metadata=metadata)
            manager.save_conversation()
        history = getattr(llm, "conversation_history", None)
        if (isinstance(history, list) and history is not getattr(manager, "messages", None)
                and not elsewhere):
            history.append(ConversationMessage(role="assistant", content=text or result.text,
                                               metadata=metadata, thinking="".join(thoughts)))
        logger_service = getattr(llm, "conversation_logger", None)
        if logger_service is not None:
            # The row is already in memory and the adapter finished, so a write
            # that did not reach the file is logged and shown, as on the native
            # path, and the turn keeps its outcome. Never retried: that could
            # duplicate the row. Cancellation still propagates.
            owner = {"session_id": bound} if elsewhere else {}
            try:
                await logger_service.log_assistant_message(text or result.text,
                    parent_uuid=parent or "", model=self._profile_name,
                    thinking_content=thoughts, tool_calls=list(tools.values()), display_metadata=metadata,
                    **owner)
            except Exception as exc:
                logger.error("ACP reply not written to history: %s", exc)
                display = getattr(llm, "message_display_service", None)
                if display is not None:
                    display.display_system_message("Conversation history not saved for this reply.")

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
            self._usage_display_turn = key
            self._display_usage_payload = None
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
        if kind == "turn_start":
            stats = getattr(llm, "session_stats", None)
            if isinstance(stats, dict):
                stats.pop("acp_turn_usage", None)
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
            voice = self.voice_turns.pop(key, None)
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

    async def shutdown(self) -> None:
        """Cancel an in-flight transition before shutting down its runner."""
        owner = self._transition_owner
        if owner is not None and owner is not asyncio.current_task():
            owner.cancel()
            await asyncio.gather(owner, return_exceptions=True)
        await self.close()

    async def close(self) -> None:
        with self.transition():
            await self._close_owned()

    async def _close_runner_bounded(self, runner: ACPRunner) -> bool:
        """Close `runner`, but never hold the transition past RUNNER_CLOSE_TIMEOUT.

        Returns True when the close finished. A close still running at the
        bound is set aside: this orchestrator stops owning the runner (so a
        successor can be installed), and the close task finishes on its own
        without touching any orchestrator state. Nothing is signalled here;
        the runner's close alone decides which processes it owns.
        """
        task = asyncio.ensure_future(runner.close())
        try:
            await asyncio.wait({task}, timeout=RUNNER_CLOSE_TIMEOUT)
        except BaseException:
            # Cancelled while closing: pass the cancellation to the close, as
            # an inline await would, and give it the same bound to settle.
            task.cancel()
            await asyncio.wait({task}, timeout=RUNNER_CLOSE_TIMEOUT)
            if not task.done():
                self._set_aside_close(runner, task)
            elif not task.cancelled():
                task.exception()
            raise
        if not task.done():
            self._set_aside_close(runner, task)
            return False
        task.result()
        return True

    def _set_aside_close(self, runner: ACPRunner, task: asyncio.Task[Any]) -> None:
        logger.warning("ACP runner close still running after %ss; set aside", RUNNER_CLOSE_TIMEOUT)
        self._quarantined_closes.add(task)

        def settled(done: asyncio.Task[Any]) -> None:
            self._quarantined_closes.discard(done)
            if not done.cancelled() and done.exception() is not None:
                logger.warning("Set-aside ACP runner close failed (%s)", type(done.exception()).__name__)

        task.add_done_callback(settled)
        if self._runner is runner:
            self._runner = None

    async def _close_owned(self) -> None:
        if self._closed and self._runner is None:
            return
        self._closed = True
        close_error: BaseException | None = None
        stalled = False
        runner = self._runner
        if self._pending_ready_runner is runner:
            self._pending_ready_runner = None
        try:
            if not self._starting:
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
                if not await self._close_runner_bounded(runner):
                    stalled = True
            except BaseException as exc:
                stalled = stalled or self._runner is not runner
                # Cancellation must not skip process/binding cleanup.  Keep the
                # original error for the caller after all owned resources settle.
                if close_error is None:
                    close_error = exc
                else:
                    logger.warning("ACP runner teardown also failed (%s)", type(exc).__name__)
            else:
                if self._runner is runner:
                    self._runner = None
        if stalled:
            # Turns admitted on the set-aside runner will never get their
            # result: end them rather than wait on a close that may not return.
            for task in tuple(self._completion_tasks):
                task.cancel()
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
            stats.pop("acp_turn_usage", None)
        self._usage_display_turn = None
        self._display_usage_payload = None
        if self.hub_plugin is not None and self.hub_plugin._identity is not None:
            self.hub_plugin._identity.tags.pop("harness", None)
            self.hub_plugin._identity.tags.pop("provider", None)
            self.hub_plugin._identity.tags.pop("acp_waiting", None)
            if self.hub_plugin._presence:
                self.hub_plugin._presence.publish()
        if self.hub_plugin is not None and self._binding is not None:
            self.hub_plugin.rotate_acp_hub_binding()
            self._binding = None
        self._drop_hub_token_file()
        publish_semantic(self.event_bus, "acp_session", state="closed", harness="", context="")
        if close_error is not None:
            raise close_error
