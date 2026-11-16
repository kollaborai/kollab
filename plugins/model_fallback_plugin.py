"""Configured fallback advice without automatic profile or harness activation.

A failed turn can suggest its configured loadout, but only an explicit user
selection changes routing. Failure handling never owns a profile, provider,
harness, resend, or restoration operation.
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
    """Suggests an explicit loadout selection when a model cannot serve."""

    def __init__(self, name: str, event_bus: Any, renderer: Any, config: Any) -> None:
        self.name = name
        self.event_bus = event_bus
        self.renderer = renderer
        self.config = config
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._tap: Any = None
        self._failed_message: Optional[str] = None  # the ACP turn's, until it completes
        self._failed_runner: Any = None  # the harness runner that turn ran on
        self._stopped = False

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
                    "help": "Loadout suggested after a usage limit or expired login. "
                    "Automatic switching is disabled; empty for none.",
                },
            ],
        }

    async def initialize(self, args: Optional[Any] = None, **kwargs) -> None:
        self._stopped = False
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
            retry_attempts=0,  # a switch never runs twice
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
        self._stopped = True
        if self._tap is not None:
            self._tap.remove_listener(self._on_tap_event)
            self._tap = None
        self._failed_message = None
        self._failed_runner = None

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
        self._failed_message = None
        self._failed_runner = None
        tap.add_listener(self._on_tap_event)
        self._tap = tap

    async def _on_startup(self, data: Dict[str, Any], event) -> Dict[str, Any]:
        self._attach()
        return data

    async def _on_request_error(self, data: Dict[str, Any], event) -> Dict[str, Any]:
        """Advise the failed turn without authorizing a retry or a switch."""
        self._attach()
        message = str(data.get("message") or "")
        kind = None if data.get("context_window_exceeded") else failure_kind(
            message, error_type=data.get("error_type"), error_code=data.get("error_code")
        )
        if kind:
            self._advise(message, kind=kind)
        return data

    def _harness_runner(self) -> Any:
        acp = self.event_bus.get_service("acp_orchestrator") if self.event_bus else None
        return getattr(acp, "runner", None)

    def _on_tap_event(self, event: Dict[str, Any]) -> None:
        """An ACP failure gets one notice after its own turn completes."""
        if event.get("type") == "error" and "generation" in event:
            self._failed_message = str(event.get("message") or "")
            self._failed_runner = self._harness_runner()
        elif event.get("type") == "turn_complete":
            message, self._failed_message = self._failed_message, None
            runner, self._failed_runner = self._failed_runner, None
            if (message is not None and runner is self._harness_runner()
                    and self._loop is not None):
                self._loop.call_soon_threadsafe(self._advise, message)

    def _advise(self, message: str, kind: Optional[str] = None) -> None:
        """Read a fallback choice and publish advice; never acquire routing state."""
        if self._stopped:
            return
        kind = kind or failure_kind(message)
        target = self.choice()
        if kind is None or not target:
            return
        profile_manager = self.event_bus.get_service("profile_manager")
        if profile_manager is None:
            return
        loadout = self.resolve(target)
        if loadout is None:
            self._say(f"Fallback loadout '{target}' not found; staying on this model.")
            return
        active = profile_manager.get_active_profile()
        failing = active.get_model() if active is not None else "This model"
        target_profile = profile_manager.get_profile(loadout.provider_profile)
        target_model = loadout.model or (
            target_profile.get_model() if target_profile is not None else ""
        )
        if target_model and failing == target_model:
            self._say(
                f"{failing} {REASONS[kind]}, and fallback {loadout.name} uses the same model. Staying on this model.",
                reason=kind, failed_model=failing, loadout=loadout.name,
            )
            return
        self._say(
            f"{failing} {REASONS[kind]}. Automatic switching is disabled. "
            f"Select /llm {loadout.name} explicitly, then send your message again.",
            reason=kind, failed_model=failing, loadout=loadout.name,
        )

    def _say(self, text: str, **fields: Any) -> None:
        logger.info("model fallback: %s", text)
        coordinator = getattr(self.renderer, "message_coordinator", None)
        if coordinator is not None:
            try:
                coordinator.display_message_sequence([("system", text, {"display_type": "info"})])
            except Exception:
                logger.debug("model fallback notice display failed", exc_info=True)
        publish_semantic(self.event_bus, "model_fallback", message=text, **fields)
