"""Model panel (``/model``): change the model on the active provider profile.

``select`` calls ``ModelCommandHandler._set_active_profile_model`` and
``effort`` calls ``ModelCommandHandler._handle_effort``, the functions the
terminal picker calls. Not ``_switch_to_model`` (it partial-matches profile
names and fails for catalog models off OpenRouter) and not
``LoadoutManager.activate`` (it also applies a loadout's temperature, effort
and max_tokens).

A payload's ``profile`` names the profile a change is for: an action that
finds another one active is refused, so a pick from one window never lands on
a profile another window switched to meanwhile (#553). Changes run one at a
time, and ``pick`` sets an ACP harness's model and level as one of them,
putting the model back when the level is refused, so a pick never
half-applies or mixes with one made in another window.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import nullcontext
from typing import Any, Dict, List, Optional

from kollabor_ai import acp_options

from .base import PanelError, error_result, make_field, ok_result, unknown_action
from .llm import effort_provider, provider_display

logger = logging.getLogger(__name__)

SCOPE_NOTE = "Saved to the active provider profile; applies to every session using it."
_CATALOG_TIMEOUT = 8.0
_MAX_MODEL_LENGTH = 200


# -- shared by the terminal picker and the panel ---------------------------


def dedup_models(models: List[Dict[str, Any]], current_model: str) -> List[Dict[str, Any]]:
    """Dedup by id, keep first occurrence, mark the current model.

    Order is preserved so callers control priority (current model first, then
    saved profiles, then the bundled registry, then the live catalog).
    """
    seen: set = set()
    out: List[Dict[str, Any]] = []
    for m in models:
        mid = str(m.get("id") or "").strip()
        if not mid or mid in seen:
            continue
        seen.add(mid)
        out.append(
            {"id": mid, "note": m.get("note") or "", "current": mid == current_model}
        )
    return out


async def fetch_catalog_models(
    profile: Any, timeout: Optional[float] = None
) -> List[Dict[str, Any]]:
    """The provider's live catalog as ``[{"id", "note"}]``; ``[]`` on any failure."""
    try:
        from kollabor_ai.model_catalog import list_provider_models

        call = list_provider_models(profile)
        return list(await (asyncio.wait_for(call, timeout) if timeout else call) or [])
    except Exception as exc:  # noqa: BLE001 - the bundled list still shows
        logger.warning("model catalog fetch failed: %s", exc)
        return []


# -- the panel -------------------------------------------------------------


def model_handler(ctx: Any) -> Any:
    """The live ``ModelCommandHandler`` behind ``/model``.

    Found through the command registry the daemon registered it in. The handler
    holds no state beyond its service references, so a fresh one built from
    ``ctx`` is the same thing when the registry cannot say.
    """
    from kollabor.commands.system_commands.handlers.model import ModelCommandHandler

    bus = getattr(ctx, "_event_bus", None)
    registry = bus.get_service("command_registry") if bus is not None else None
    command = registry.get_command("model") if registry is not None else None
    owner = getattr(getattr(command, "handler", None), "__self__", None)
    if isinstance(owner, ModelCommandHandler):
        return owner
    return ModelCommandHandler(
        registry, bus, ctx._profile_manager, getattr(ctx, "_llm_service", None)
    )


async def _effort_control(handler: Any, profile: Any) -> dict:
    from kollabor_ai.providers.tuning import EFFORT_SUPPORTED_PROVIDERS

    provider = (effort_provider(profile) or "").lower()
    if provider not in EFFORT_SUPPORTED_PROVIDERS:
        return make_field(
            "level", "dropdown", "Reasoning Effort", action="effort",
            value="default", options=["default"], editable=False,
            help=f"{provider_display(provider)} has no reasoning-effort parameter.",
        )
    levels = await handler._model_effort_levels(profile)
    return make_field(
        "level", "dropdown", "Reasoning Effort", action="effort",
        value=profile.get_effort() or "default", options=["default", *levels],
        help="Saved to the active provider profile. Default lets the model decide.",
    )


def _one_line(text: str) -> str:
    return " ".join(str(text or "").split())


_acp_option_values = acp_options.option_values
_acp_option = acp_options.find_option


def _level_category(handler: Any) -> str:
    """The option an ACP harness takes its level in: ``thought_level``, else ``effort``."""
    return "thought_level" if handler._acp_option(handler._acp_options(), "thought_level") is not None else "effort"


def _acp_current(handler: Any, category: str) -> str:
    """The value an ACP harness reports for an option, "" when it reports none."""
    option = handler._acp_option(handler._acp_options(), category)
    return str((option or {}).get("currentValue") or "")


class ModelPanel:
    """``/model``: pick the active provider's model; set reasoning effort."""

    name = "model"
    kind = "picker"

    def __init__(self) -> None:
        self._changes = asyncio.Lock()  # one change at a time (#553)

    async def describe(self, ctx: Any, params: dict) -> dict:
        pm = getattr(ctx, "_profile_manager", None)
        active = pm.get_active_profile() if pm is not None else None
        base = {
            "panel": self.name,
            "kind": self.kind,
            "title": "Models",
            "scope_note": SCOPE_NOTE,
            "rows": [],
            "row_actions": [{"id": "select", "label": "Use", "payload_key": "model"}],
            "toolbar_actions": [{"id": "refresh", "label": "Refresh Catalog"}],
            "controls": [],
            "empty_groups": [],
        }
        if active is None:
            base["notice"] = "No active provider profile. Run Setup first."
            base["row_actions"] = base["toolbar_actions"] = []
            return base
        provider = active.get_provider() or ""
        if provider == "acp":
            service = getattr(ctx, "_event_bus", None)
            service = service.get_service("acp_orchestrator") if service is not None else None
            getter = getattr(service, "advertised_config_options", None)
            if getattr(service, "config_outcome_unknown", False) is True:
                base["notice"] = "ACP configuration is unknown. Restart the harness session."
                return base
            options = getter() if callable(getter) else []
            model = _acp_option(options, "model")
            effort = _acp_option(options, "thought_level") or _acp_option(options, "effort")
            base["title"] = "Harness Models"
            base["scope_note"] = "Applies to this harness session; saved for this project and profile when possible."
            base["notice"] = "Choose values advertised by the active ACP harness."
            if model is not None:
                values = _acp_option_values(model)
                base["rows"] = [
                    {
                        "id": value,
                        "label": value,
                        "detail": "current" if value == model.get("currentValue") else "",
                        "group": model.get("name", "Model"),
                        "current": value == model.get("currentValue"),
                        "badges": [],
                    }
                    for value in values
                ]
            if effort is not None:
                base["controls"] = [make_field(
                    "level", "dropdown", effort.get("name", "Reasoning Effort"),
                    action="effort", value=effort.get("currentValue", ""),
                    options=_acp_option_values(effort),
                )]
            if not base["rows"] and not base["controls"]:
                base["notice"] = "The active ACP harness advertises no model or effort options."
            return base
        current = active.get_model() or ""
        handler = model_handler(ctx)
        known = handler._build_known_models(provider, current)
        catalog = await fetch_catalog_models(active, _CATALOG_TIMEOUT)
        label = provider_display(provider)
        base["title"] = f"Models — {label}"
        base["rows"] = [
            {
                "id": m["id"],
                "label": m["id"],
                "detail": m["note"],
                "group": label,
                "current": m["current"],
                "badges": [],
            }
            for m in dedup_models(known + catalog, current)
        ]
        base["controls"] = [await _effort_control(handler, active)]
        return base

    async def act(self, ctx: Any, action: str, payload: dict) -> dict:
        async with self._changes:
            active = ctx._profile_manager.get_active_profile() if ctx._profile_manager else None
            if active is not None and active.get_provider() == "acp":
                from kollabor.llm.acp_bridge import ACPError
                from kollabor.llm.acp_change_lock import change_lock

                bus = getattr(ctx, "_event_bus", None)
                service = bus.get_service("acp_orchestrator") if bus else None
                if service is not None:
                    try:
                        async with change_lock(service):
                            transition = getattr(service, "transition", None)
                            with transition() if callable(transition) else nullcontext():
                                return await self._act(ctx, action, payload)
                    except ACPError as exc:
                        logger.warning("ACP panel transition failed (%s)", type(exc).__name__)
                        return error_result("ACP session is changing; retry when ready.")
            return await self._act(ctx, action, payload)

    async def _act(self, ctx: Any, action: str, payload: dict) -> dict:
        pm = getattr(ctx, "_profile_manager", None)
        active = pm.get_active_profile() if pm is not None else None
        expected = payload.get("profile")
        running = getattr(active, "name", None)
        if expected and running != expected:
            return error_result(f"The session now runs {running or 'no profile'}, not {expected}.")
        if active is not None and active.get_provider() == "acp":
            handler = model_handler(ctx)
            if action == "refresh":
                return ok_result("ACP options refreshed.", panel=await self.describe(ctx, {}))
            if action == "select":
                model = str(payload.get("model") or payload.get("id") or "").strip()
                if not model:
                    raise PanelError("a model value is required", errors={"model": "Choose a model."})
                outcome = await handler._set_acp_option("model", model)
                if not outcome.success:
                    return error_result(_one_line(outcome.message))
                return ok_result(f"ACP model set to {model}.", panel=await self.describe(ctx, {}))
            if action == "effort":
                level = payload.get("level", payload.get("value", payload.get("effort")))
                if not isinstance(level, str) or not level.strip():
                    raise PanelError("an effort level is required", errors={"level": "Choose a level."})
                outcome = await handler._set_acp_option(_level_category(handler), level.strip())
                if not outcome.success:
                    return error_result(_one_line(outcome.message))
                return ok_result(f"ACP reasoning effort set to {level.strip()}.", panel=await self.describe(ctx, {}))
            if action == "pick":
                return await self._pick(ctx, handler, payload)
            return unknown_action(self.name, action)
        if action == "refresh":
            return ok_result("Catalog refreshed.", panel=await self.describe(ctx, {}))
        if action == "select":
            model = str(payload.get("model") or payload.get("id") or "").strip()
            if (
                not model
                or len(model) > _MAX_MODEL_LENGTH
                or any(c.isspace() or not c.isprintable() for c in model)
            ):
                raise PanelError("a model id is required", errors={"model": "Enter a valid model id."})
            outcome = await model_handler(ctx)._set_active_profile_model(model)
            if not outcome.success:
                return error_result(_one_line(outcome.message))
            return ok_result(f"Now using {model}.", panel=await self.describe(ctx, {}))
        if action == "effort":
            level = payload.get("level", payload.get("value", payload.get("effort")))
            if not isinstance(level, str) or not level.strip():
                raise PanelError("an effort level is required", errors={"level": "Choose a level."})
            outcome = await model_handler(ctx)._handle_effort(level)
            if not outcome.success:
                return error_result(_one_line(outcome.message))
            return ok_result(
                f"Reasoning effort set to {level.strip().lower()}.",
                panel=await self.describe(ctx, {}),
            )
        raise unknown_action(self.name, action)

    async def _pick(self, ctx: Any, handler: Any, payload: dict) -> dict:
        """An ACP harness's model and level as one change: a level it refuses puts
        back the model it replaced and that model's level (#553)."""
        model = str(payload.get("model") or "").strip()
        level = str(payload.get("level") or "").strip()
        if not model and not level:
            raise PanelError("a model or level is required", errors={"model": "Choose a model."})
        category = _level_category(handler)
        before = (_acp_current(handler, "model"), _acp_current(handler, category))
        if model:
            outcome = await handler._set_acp_option("model", model, persist=False)
            if not outcome.success:
                return error_result(_one_line(outcome.message))
        if level:
            outcome = await handler._set_acp_option(_level_category(handler), level, persist=False)
            if not outcome.success:
                if outcome.data.get("outcome_unknown"):
                    return error_result(_one_line(outcome.message))
                # A model switch can move the level too, so both go back; the engine reads back what runs.
                if model and before[0]:
                    restored = await handler._set_acp_option("model", before[0], persist=False)
                    if restored.data.get("outcome_unknown"):
                        return error_result(_one_line(restored.message))
                    if restored.success and before[1]:
                        restored_level = await handler._set_acp_option(category, before[1], persist=False)
                        if restored_level.data.get("outcome_unknown"):
                            return error_result(_one_line(restored_level.message))
                return error_result(_one_line(outcome.message))
        return ok_result(f"ACP set to {' · '.join(filter(None, (model, level)))}.", panel=await self.describe(ctx, {}))


PANELS = {ModelPanel.name: ModelPanel()}
