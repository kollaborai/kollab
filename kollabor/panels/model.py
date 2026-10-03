"""Model panel (``/model``): change the model on the active provider profile.

``select`` calls ``ModelCommandHandler._set_active_profile_model`` and
``effort`` calls ``ModelCommandHandler._handle_effort``, the functions the
terminal picker calls. Not ``_switch_to_model`` (it partial-matches profile
names and fails for catalog models off OpenRouter) and not
``LoadoutManager.activate`` (it also applies a loadout's temperature, effort
and max_tokens).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from .base import PanelError, error_result, make_field, ok_result, unknown_action
from .llm import provider_display

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

    provider = (profile.get_provider() or "").lower()
    if provider not in EFFORT_SUPPORTED_PROVIDERS:
        return make_field(
            "effort", "dropdown", "Reasoning Effort",
            value="default", options=["default"], editable=False,
            help=f"{provider_display(provider)} has no reasoning-effort parameter.",
        )
    levels = await handler._model_effort_levels(profile)
    return make_field(
        "effort", "dropdown", "Reasoning Effort",
        value=profile.get_effort() or "default", options=["default", *levels],
        help="Saved to the active provider profile. Default lets the model decide.",
    )


def _one_line(text: str) -> str:
    return " ".join(str(text or "").split())


class ModelPanel:
    """``/model``: pick the active provider's model; set reasoning effort."""

    name = "model"
    kind = "picker"

    async def describe(self, ctx: Any, params: dict) -> dict:
        pm = getattr(ctx, "_profile_manager", None)
        active = pm.get_active_profile() if pm is not None else None
        base = {
            "panel": self.name,
            "kind": self.kind,
            "title": "Models",
            "scope_note": SCOPE_NOTE,
            "rows": [],
            "row_actions": [{"id": "select", "label": "Use"}],
            "toolbar_actions": [{"id": "refresh", "label": "Refresh Catalog"}],
            "controls": [],
            "empty_groups": [],
        }
        if active is None:
            base["notice"] = "No active provider profile. Run Setup first."
            base["row_actions"] = base["toolbar_actions"] = []
            return base
        provider = active.get_provider() or ""
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
                raise PanelError("an effort level is required", errors={"effort": "Choose a level."})
            outcome = await model_handler(ctx)._handle_effort(level)
            if not outcome.success:
                return error_result(_one_line(outcome.message))
            return ok_result(
                f"Reasoning effort set to {level.strip().lower()}.",
                panel=await self.describe(ctx, {}),
            )
        raise unknown_action(self.name, action)


PANELS = {ModelPanel.name: ModelPanel()}
