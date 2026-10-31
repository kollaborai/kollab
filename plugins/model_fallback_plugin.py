"""Model fallback: a turn the model can't serve moves the agent to its fallback.

When a turn fails because the model itself can't serve (usage limit, quota,
rate limit after retries, expired login: ``kollabor.llm.usage_limits``), the
agent switches to its fallback loadout once, says so in the chat, and runs
the turn again on the new model. One hop: a turn that fails on the fallback
never switches again, and the agent stays on the fallback until someone
switches it back.

Settings: ``plugins.model_fallback.loadout`` (every agent; "" = none) and
``plugins.model_fallback.agents.<agent>`` (one agent; "off" = none), set with
``/llm fallback``. A turn moved to an ACP harness is not run again: the
harness has its own conversation, so the notice asks for the message again.
"""

import asyncio
import logging
from typing import Any, Dict, Optional

from kollabor.llm.usage_limits import REASONS, failure_kind
from kollabor_events.models import EventType, Hook, HookPriority
from kollabor_plugins.base import BasePlugin
from kollabor_tui.display_tap import publish_semantic

logger = logging.getLogger(__name__)

_OFF = {"", "off", "none"}


class ModelFallbackPlugin(BasePlugin):
    """Switches the agent to its fallback loadout when its model can't serve."""

    def __init__(self, name: str, event_bus: Any, renderer: Any, config: Any) -> None:
        self.name = name
        self.event_bus = event_bus
        self.renderer = renderer
        self.config = config
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._tap: Any = None
        self._native_failed = False
        self._failed_message: Optional[str] = None
        self._task: Optional[asyncio.Task] = None

    @staticmethod
    def get_default_config() -> Dict[str, Any]:
        return {"plugins": {"model_fallback": {"loadout": "", "agents": {}}}}

    @staticmethod
    def get_config_widgets() -> Dict[str, Any]:
        return {
            "title": "Model Fallback",
            "widgets": [
                {
                    "type": "text_input",
                    "label": "Fallback Loadout",
                    "config_path": "plugins.model_fallback.loadout",
                    "help": "Loadout an agent switches to when its model hits a usage "
                    "limit; empty for none. /llm fallback sets one agent's.",
                },
            ],
        }

    async def initialize(self, args: Optional[Any] = None, **kwargs) -> None:
        self._loop = asyncio.get_running_loop()
        self.event_bus.register_service("model_fallback", self)
        self._attach()

    async def register_hooks(self) -> None:
        await self.event_bus.register_hook(Hook(
            name="model_fallback_request_error",
            plugin_name=self.name,
            event_type=EventType.LLM_REQUEST_ERROR,
            priority=HookPriority.POSTPROCESSING.value,
            callback=self._on_request_error,
        ))
        # Plugin files start before plugin packages, so the hub creates its
        # display tap after this plugin's initialize: attach once all started.
        await self.event_bus.register_hook(Hook(
            name="model_fallback_startup",
            plugin_name=self.name,
            event_type=EventType.SYSTEM_STARTUP,
            priority=HookPriority.POSTPROCESSING.value,
            callback=self._on_startup,
        ))

    async def shutdown(self) -> None:
        if self._tap is not None:
            self._tap.remove_listener(self._on_tap_event)
            self._tap = None

    # -- the setting ----------------------------------------------------------

    def agent_name(self) -> str:
        hub = self.event_bus.get_service("hub_plugin")
        identity = getattr(hub, "_identity", None)
        return str(getattr(identity, "identity", "") or "")

    def choice(self) -> str:
        """This agent's fallback loadout, or "" for none."""
        name = self.agent_name()
        value = self.config.get(f"plugins.model_fallback.agents.{name}") if name else None
        if value is None:
            value = self.config.get("plugins.model_fallback.loadout", "")
        value = str(value or "").strip()
        return "" if value.lower() in _OFF else value

    def resolve(self, name: str) -> Any:
        """The loadout ``name`` picks, as ``kollab --llm`` reads it.

        A loadout's own name first, then a provider profile's exact name (that
        profile with its own model), then the forgiving loadout match.
        """
        from kollabor_ai.loadout_manager import Loadout, LoadoutManager

        profile_manager = self.event_bus.get_service("profile_manager")
        if profile_manager is None or not name:
            return None
        loadout, _ = LoadoutManager(profile_manager, config=self.config).resolve(name)
        if loadout is not None and loadout.name == name:
            return loadout
        profile = profile_manager.get_profile(name)
        if profile is not None:
            return Loadout(name=name, provider_profile=name, model=profile.get_model() or "")
        return loadout

    def set_choice(self, loadout: str) -> bool:
        """Save this agent's fallback ("off" for none) in the folder's config."""
        name = self.agent_name()
        path = f"plugins.model_fallback.agents.{name}" if name else "plugins.model_fallback.loadout"
        return bool(self.config.save_key(path, loadout, save_target="local"))

    # -- noticing a failed turn -----------------------------------------------

    def _attach(self) -> None:
        # The hub makes a new tap each time it initializes (it can twice), and
        # turns publish to the newest one: follow whichever is registered now.
        tap = self.event_bus.get_service("display_tap")
        if tap is self._tap or tap is None or not hasattr(tap, "add_listener"):
            return
        if self._tap is not None:
            self._tap.remove_listener(self._on_tap_event)
        tap.add_listener(self._on_tap_event)
        self._tap = tap

    async def _on_startup(self, data: Dict[str, Any], event) -> Dict[str, Any]:
        self._attach()
        return data

    async def _on_request_error(self, data: Dict[str, Any], event) -> Dict[str, Any]:
        # A native turn failed; its error event (with the text) follows at once.
        self._attach()
        self._native_failed = True
        return data

    def _on_tap_event(self, event: Dict[str, Any]) -> None:
        """Runs inside DisplayTap.publish: note a turn's failure, act after it ends."""
        kind = event.get("type")
        if kind == "error":
            # Only a failed turn: a native one (flagged just before) or an ACP
            # one (its error carries the harness generation). Slash commands and
            # hub mentions publish errors too.
            if self._native_failed or "generation" in event:
                self._failed_message = str(event.get("message") or "")
            self._native_failed = False
        elif kind == "turn_complete" and self._failed_message is not None:
            message, self._failed_message = self._failed_message, None
            if self._loop is not None and failure_kind(message) and self.choice():
                self._loop.call_soon_threadsafe(self._start, message)

    def _start(self, message: str) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.ensure_future(self.fall_back(message))

    # -- switching ------------------------------------------------------------

    async def fall_back(self, message: str) -> bool:
        """Switch to the fallback for this failure; True if the agent switched."""
        kind = failure_kind(message)
        target = self.choice()
        if kind is None or not target:
            return False
        from kollabor_ai.loadout_manager import LoadoutManager

        profile_manager = self.event_bus.get_service("profile_manager")
        if profile_manager is None:
            return False
        loadout = self.resolve(target)
        if loadout is None:
            self._say(f"Fallback loadout '{target}' not found; staying on this model.")
            return False
        manager = LoadoutManager(profile_manager, config=self.config)
        active = profile_manager.get_active_profile()
        failing = active.get_model() if active is not None else "This model"
        if active is not None and active.name == loadout.provider_profile and (
            not loadout.model or failing == loadout.model
        ):
            logger.info("model fallback: already on %s, not switching again", loadout.name)
            return False
        try:
            activated = await manager.activate(loadout, self.event_bus)
        except Exception as exc:
            logger.warning("model fallback to %s failed: %s", loadout.name, exc)
            activated = None
        if not activated:
            self._say(f"{failing} {REASONS[kind]}, and switching to {loadout.name} failed.")
            return False
        harness = profile_manager.get_profile(loadout.provider_profile)
        to_acp = str(getattr(harness, "get_provider", lambda: "")() or "").lower() == "acp"
        self._say(
            f"{failing} {REASONS[kind]}. Switched to {loadout.name}"
            + (". Send your message again." if to_acp else " and resent your message."),
            reason=kind, failed_model=failing, loadout=loadout.name,
        )
        if not to_acp:
            await self.event_bus.emit_with_hooks(
                EventType.TRIGGER_LLM_CONTINUE, {"source": "model_fallback"}, "model_fallback"
            )
        return True

    def _say(self, text: str, **fields: Any) -> None:
        logger.info("model fallback: %s", text)
        coordinator = getattr(self.renderer, "message_coordinator", None)
        if coordinator is not None:
            try:
                coordinator.display_message_sequence([("system", text, {"display_type": "info"})])
            except Exception:
                logger.debug("model fallback notice display failed", exc_info=True)
        publish_semantic(self.event_bus, "model_fallback", message=text, **fields)
