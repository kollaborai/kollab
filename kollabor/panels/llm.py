"""Loadout panel (``/llm``) and the loadout helpers the terminal list view shares.

The helpers down to ``build_active_profile_base`` moved here from
``plugins/altview/loadout_altview.py``; the view imports them back.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, List, Optional, Tuple

from .base import PanelError, error_result, make_field, ok_result, unknown_action

logger = logging.getLogger(__name__)


PROVIDER_LABELS = {
    "openai_responses": "OpenAI (ChatGPT)",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "openrouter": "OpenRouter",
    "gemini": "Gemini",
    "azure": "Azure OpenAI",
    "custom": "Custom",
}

def provider_display(provider: str) -> str:
    """Title-case display name for a provider type string."""
    if not provider:
        return "Provider"
    return PROVIDER_LABELS.get(provider.lower(), provider.replace("_", " ").title())


def resolve_provider_type(profile_manager: Any, provider_profile: str) -> str:
    """Best-effort provider type (e.g. ``anthropic``) for a profile name."""
    if not profile_manager or not provider_profile:
        return provider_profile or ""
    try:
        profile = profile_manager.get_profile(provider_profile)
    except Exception:
        return provider_profile
    if profile is not None and hasattr(profile, "get_provider"):
        try:
            return profile.get_provider() or provider_profile
        except Exception:
            return provider_profile
    return provider_profile


def is_chatgpt_codex_profile(profile_manager: Any, provider_profile: str) -> bool:
    """Whether a profile targets the ChatGPT OAuth/Codex transport."""
    if not profile_manager or not provider_profile:
        return False
    try:
        profile = profile_manager.get_profile(provider_profile)
        endpoint = profile.get_endpoint() if profile is not None else ""
    except Exception:
        return False
    return "chatgpt.com" in str(endpoint or "").lower()


def resolve_profile_entry(profile_manager: Any, entry: Any) -> Tuple[str, str]:
    """(profile_name, provider_type) from a ``provider_profiles()`` item.

    The item may be a profile name string or an ``LLMProfile``-like object --
    the manager's exact return shape isn't known here (it lands separately),
    so both are handled defensively.
    """
    if isinstance(entry, str):
        return entry, resolve_provider_type(profile_manager, entry)
    name = getattr(entry, "name", "") or str(entry)
    provider = ""
    if hasattr(entry, "get_provider"):
        try:
            provider = entry.get_provider() or ""
        except Exception:
            provider = ""
    elif hasattr(entry, "provider"):
        provider = getattr(entry, "provider") or ""
    return name, provider or resolve_provider_type(profile_manager, name)


def model_registry_info(model: str) -> dict:
    """Registry dict for an exact model id, or ``{}`` when unknown."""
    if not model:
        return {}
    try:
        from kollabor_ai.model_registry import get_model_registry

        info = get_model_registry().get("models", {}).get(model)
        return info if isinstance(info, dict) else {}
    except Exception as exc:  # noqa: BLE001 - registry is best-effort
        logger.debug("loadout: model registry unavailable: %s", exc)
        return {}


def model_note(model: str, provider: str = "") -> str:
    """``1M ctx • $5/$30`` style descriptor, matching ModelCommandHandler._registry_note."""
    parts: List[str] = []
    try:
        from kollabor_ai.model_registry import resolve_context_window

        window = resolve_context_window(model, provider or None)
        if isinstance(window, int) and window > 0:
            parts.append(
                f"{window / 1_000_000:.2f}M ctx".replace(".00M", "M")
                if window >= 1_000_000
                else f"{window // 1000}K ctx"
            )
    except Exception as exc:  # noqa: BLE001 - never block rendering
        logger.debug("loadout: context window lookup failed for %s: %s", model, exc)

    info = model_registry_info(model)
    price_in, price_out = info.get("pricing_in"), info.get("pricing_out")
    if isinstance(price_in, (int, float)) and isinstance(price_out, (int, float)):
        parts.append(f"${price_in:g}/${price_out:g}")
    return " • ".join(parts)


def loadout_note(loadout: Any) -> str:
    """Right-aligned dim row note: ``model • 1M ctx • $5/$30 [• overrides]``."""
    parts = [loadout.model]
    note = model_note(loadout.model)
    if note:
        parts.append(note)
    if not loadout.implicit:
        overrides = []
        if loadout.temperature is not None:
            overrides.append(f"temp {loadout.temperature:g}")
        if loadout.effort:
            overrides.append(f"effort {loadout.effort}")
        if loadout.max_tokens:
            overrides.append(f"max {loadout.max_tokens:,}")
        if overrides:
            parts.append(" • ".join(overrides))
    return " • ".join(p for p in parts if p)


def default_max_tokens(model: str) -> int:
    info = model_registry_info(model)
    value = info.get("default_output") or info.get("max_output")
    if isinstance(value, int) and value > 0:
        # The registry value is a model hint/ceiling. Kollab's provider config
        # intentionally reserves 16K by default so a large output allowance
        # does not consume the context budget on every turn. Users can still
        # enter the registry ceiling explicitly for long generation.
        return min(value, 16384)
    return 16384


def suggest_name(model: str, existing: set, style: str) -> str:
    """Suggested loadout name: bare model id, or ``-custom`` when branching."""
    base = model or "loadout"
    candidate = f"{base}-custom" if style == "custom" else base
    if candidate not in existing:
        return candidate
    i = 2
    while f"{candidate}-{i}" in existing:
        i += 1
    return f"{candidate}-{i}"


def short_error(msg: str) -> str:
    msg = " ".join(msg.split())
    if len(msg) > 120:
        msg = msg[:117] + "..."
    return msg or "unknown error"


def build_active_profile_base(profile_manager: Any) -> Optional[Any]:
    """Synthesize an implicit ``Loadout`` base from the active profile, or None.

    Shared by the list view's "N with nothing selected" fallback and the
    `/loadout new` handler path.
    """
    if profile_manager is None:
        return None
    try:
        active = profile_manager.get_active_profile()
    except Exception:
        return None
    if not active:
        return None
    try:
        model = active.get_model() or ""
    except Exception:
        model = ""
    if not model:
        return None
    try:
        from kollabor_ai.loadout_manager import Loadout
    except Exception as exc:  # pragma: no cover - import guard
        logger.debug("loadout: Loadout dataclass unavailable: %s", exc)
        return None
    return Loadout(
        name="",
        provider_profile=getattr(active, "name", "") or "",
        model=model,
        implicit=True,
    )



# -- shared by the terminal list view and the panel ---------------------------


def build_sections(manager: Any, profile_manager: Any) -> List[Tuple[str, List[Any]]]:
    """(section_title, loadouts) pairs: explicit first, then one per provider.

    A configured provider always gets a section, even with zero rows: skipping
    it made a provider serving no models look identical to one that was never
    configured (the failure that hid OpenRouter's empty registry for a release).
    """
    if manager is None:
        return [("Loadouts", [])]
    try:
        loadouts = list(manager.list_loadouts())
    except Exception as exc:
        logger.error("loadout: list_loadouts() failed: %s", exc)
        loadouts = []
    implicit = [entry for entry in loadouts if entry.implicit]
    sections: List[Tuple[str, List[Any]]] = [
        ("Loadouts", [entry for entry in loadouts if not entry.implicit])
    ]
    try:
        profiles = list(manager.provider_profiles() or [])
    except Exception as exc:
        logger.warning("loadout: provider_profiles() failed: %s", exc)
        profiles = []

    claimed: set = set()
    for entry in profiles:
        name, provider = resolve_profile_entry(profile_manager, entry)
        if not name or name in claimed:
            continue
        claimed.add(name)
        rows = [m for m in implicit if m.provider_profile == name]
        sections.append((f"Models — {provider_display(provider or name)}", rows))

    # Keep any implicit loadout visible even if provider_profiles() missed its
    # profile, so a mismatch between the two manager methods never drops rows.
    by_profile: dict = {}
    for entry in implicit:
        if entry.provider_profile not in claimed:
            by_profile.setdefault(entry.provider_profile, []).append(entry)
    for profile_name, rows in by_profile.items():
        provider = resolve_provider_type(profile_manager, profile_name)
        sections.append((f"Models — {provider_display(provider or profile_name)}", rows))
    return sections


def empty_note(
    title: str, loading: bool = False, retry: str = "/llm again to retry"
) -> str:
    """Placeholder line for a section with no rows."""
    if not title.startswith("Models"):
        return "No saved loadouts — press N to create one from any model."
    if loading:
        return "fetching catalog…"
    return f"no models — catalog unavailable, {retry}"


# -- the panel ----------------------------------------------------------------

EFFORT_OPTIONS = ("default", "low", "medium", "high", "xhigh", "max", "ultra")
SCOPE_NOTE = "Saved to the provider profile; applies to every session using it."
_ACTIVATE_TIMEOUT = 20.0
_CATALOG_TIMEOUT = 8.0
_ROW_ACTIONS = [
    {"id": "activate", "label": "Use", "payload_key": "name"},
    {"id": "edit", "label": "Edit", "payload_key": "name"},
    {"id": "set_default", "label": "Set As Default", "payload_key": "name"},
    {"id": "delete", "label": "Delete", "confirm": True, "payload_key": "name"},
]
_TOOLBAR_ACTIONS = [
    {"id": "new", "label": "New Loadout"},
    {"id": "refresh", "label": "Refresh Catalog"},
]


def _manager(ctx: Any) -> Any:
    pm = getattr(ctx, "_profile_manager", None)
    if pm is None:
        raise PanelError("loadouts are unavailable: no profile manager", status=503)
    from kollabor_ai.loadout_manager import LoadoutManager

    return LoadoutManager(pm, config=getattr(ctx._llm_service, "config", None))


def _find(manager: Any, name: str) -> Any:
    if not name:
        raise PanelError("name is required", errors={"name": "Name is required."})
    loadout = manager.get(name) or manager.resolve(name)[0]
    if loadout is None:
        raise PanelError(f"unknown loadout '{name}'", status=404)
    return loadout


async def _warm_catalogs(manager: Any) -> None:
    try:
        await asyncio.wait_for(manager.refresh_catalogs(), _CATALOG_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 - bundled models still list
        logger.warning("loadout: catalog refresh failed: %s", exc)


async def _activate(manager: Any, ctx: Any, loadout: Any) -> Any:
    return await asyncio.wait_for(
        manager.activate(loadout, getattr(ctx, "_event_bus", None)),
        _ACTIVATE_TIMEOUT,
    )


def _form(ctx: Any, manager: Any, base: Any, mode: str, style: str) -> dict:
    pm = ctx._profile_manager
    profile = pm.get_profile(base.provider_profile)
    existing = {entry.name for entry in manager.list_loadouts()}
    codex = is_chatgpt_codex_profile(pm, base.provider_profile)
    temperature = base.temperature
    if temperature is None:
        temperature = getattr(profile, "temperature", None) or 0.7
    fields = [
        make_field(
            "name",
            "text_input",
            "Name",
            value=base.name if mode == "edit" else suggest_name(base.model, existing, style),
            editable=mode == "create",
        ),
        make_field("model", "label", "Model", value=f"{base.provider_profile} · {base.model}"),
        make_field(
            "temperature", "slider", "Temperature",
            value=temperature, min_value=0, max_value=2, step=0.05,
        ),
        make_field(
            "effort", "dropdown", "Reasoning Effort",
            value=base.effort or "default", options=list(EFFORT_OPTIONS),
        ),
        make_field(
            "max_tokens", "text_input", "Max Output Tokens",
            value=str(base.max_tokens) if base.max_tokens else "",
            editable=not codex,
            help="ChatGPT sign-in uses the backend default."
            if codex
            else f"Empty uses the provider default ({default_max_tokens(base.model):,}).",
        ),
        make_field("description", "text_input", "Description", value=base.description or ""),
    ]
    return {
        "panel": "llm",
        "kind": "form",
        "title": "Edit Loadout" if mode == "edit" else "New Loadout",
        "sections": [{"id": "s0", "title": "Loadout", "fields": fields}],
        "actions": [{"id": "save", "label": "Save and Use"}],
        "context": {
            "mode": mode,
            "base": base.name,
            "provider_profile": base.provider_profile,
            "model": base.model,
        },
    }


def _loadout_values(payload: dict, codex: bool, model: str) -> tuple[dict, dict]:
    """(values, errors) from a save payload; the same rules as the terminal form."""
    values: dict = {}
    errors: dict = {}
    try:
        values["temperature"] = float(payload.get("temperature"))
        if not 0 <= values["temperature"] <= 2:
            raise ValueError
    except (TypeError, ValueError):
        errors["temperature"] = "Temperature must be a number from 0 to 2."
    effort = str(payload.get("effort") or "default")
    if effort not in EFFORT_OPTIONS:
        errors["effort"] = "Unknown effort level."
    values["effort"] = "" if effort == "default" else effort
    values["max_tokens"] = None
    raw = str(payload.get("max_tokens") or "").strip()
    if raw and not codex:
        try:
            tokens = int(raw)
            ceiling = model_registry_info(model).get("max_output")
            if tokens < 1:
                errors["max_tokens"] = "Max Output Tokens must be at least 1."
            elif isinstance(ceiling, int) and 0 < ceiling < tokens:
                errors["max_tokens"] = (
                    f"Max Output Tokens {tokens:,} exceeds the model limit ({ceiling:,})."
                )
            else:
                values["max_tokens"] = tokens
        except ValueError:
            errors["max_tokens"] = "Max Output Tokens must be a whole number."
    description = str(payload.get("description") or "").strip()
    if len(description) > 500:
        errors["description"] = "Keep the description under 500 characters."
    values["description"] = description
    return values, errors


class LlmPanel:
    """``/llm``: choose a loadout (provider profile + model + params)."""

    name = "llm"
    kind = "picker"

    async def describe(self, ctx: Any, params: dict) -> dict:
        manager = _manager(ctx)
        pm = ctx._profile_manager
        if params.get("refresh"):
            await _warm_catalogs(manager)
        active = pm.get_active_profile()
        current = (getattr(active, "name", ""), active.get_model() if active else "")
        default = manager.get_default()
        rows: list = []
        empty: list = []
        for title, items in build_sections(manager, pm):
            if not items and title.startswith("Models"):
                empty.append(
                    {"group": title, "reason": empty_note(title, retry="use Refresh Catalog to retry")}
                )
            for entry in items:
                rows.append(
                    {
                        "id": entry.name,
                        "label": entry.name,
                        "detail": loadout_note(entry),
                        "group": title,
                        "current": (entry.provider_profile, entry.model) == current,
                        "badges": ["default"] if entry.name == default else [],
                    }
                )
        return {
            "panel": self.name,
            "kind": self.kind,
            "title": "Loadouts",
            "scope_note": SCOPE_NOTE,
            "rows": rows,
            "row_actions": _ROW_ACTIONS,
            "toolbar_actions": _TOOLBAR_ACTIONS,
            "controls": [],
            "empty_groups": empty,
        }

    async def act(self, ctx: Any, action: str, payload: dict) -> dict:
        manager = _manager(ctx)
        name = str(payload.get("name") or payload.get("id") or "").strip()
        if action == "activate":
            loadout = _find(manager, name)
            try:
                activated = await _activate(manager, ctx, loadout)
            except asyncio.TimeoutError:
                return error_result(f"Activation timed out after {_ACTIVATE_TIMEOUT:.0f}s.")
            except Exception as exc:  # noqa: BLE001 - surfaced as the action's message
                logger.error("loadout: activate('%s') failed: %s", loadout.name, exc)
                return error_result(short_error(str(exc)))
            if not activated:
                return error_result(f"Could not activate '{loadout.name}'.")
            return ok_result(f"Now using {loadout.name}.", panel=await self.describe(ctx, {}))
        if action in ("new", "edit"):
            base = _find(manager, name) if name else build_active_profile_base(ctx._profile_manager)
            if base is None:
                return error_result("No model to start from. Configure a provider in Setup.")
            if action == "edit" and not base.implicit:
                return ok_result(open=_form(ctx, manager, base, "edit", "plain"))
            return ok_result(
                open=_form(ctx, manager, base, "create", "custom" if action == "edit" else "plain")
            )
        if action == "save":
            return await self._save(ctx, manager, payload)
        if action == "delete":
            loadout = _find(manager, name)
            if loadout.implicit:
                return error_result("Catalog models can't be deleted; save a customized copy instead.")
            if not manager.delete(loadout.name):
                return error_result(f"Could not delete '{loadout.name}'.")
            return ok_result(f"Deleted '{loadout.name}'.", panel=await self.describe(ctx, {}))
        if action == "set_default":
            loadout = _find(manager, name)
            level = str(payload.get("level") or "global")
            if level not in ("global", "project"):
                raise PanelError("level must be global or project", errors={"level": "Use global or project."})
            if not manager.set_default(loadout.name, level):
                return error_result(f"Could not set '{loadout.name}' as the startup default.")
            return ok_result(
                f"Startup default is now '{loadout.name}'.", panel=await self.describe(ctx, {})
            )
        if action == "refresh":
            await _warm_catalogs(manager)
            return ok_result("Catalog refreshed.", panel=await self.describe(ctx, {}))
        raise unknown_action(self.name, action)

    async def _save(self, ctx: Any, manager: Any, payload: dict) -> dict:
        form = payload.get("context") if isinstance(payload.get("context"), dict) else {}
        # a form action sends {"changes": {path: value}, "context": {...}}
        changes = payload.get("changes") if isinstance(payload.get("changes"), dict) else payload
        base_name = str(payload.get("base") or form.get("base") or "").strip()
        if base_name:
            base = _find(manager, base_name)
        else:  # a form started from the active profile's own model: no loadout name yet
            from kollabor_ai.loadout_manager import Loadout

            profile_name = str(form.get("provider_profile") or "")
            model = str(form.get("model") or "")
            if (
                not model
                or len(model) > 200
                or not model.isprintable()
                or ctx._profile_manager.get_profile(profile_name) is None
            ):
                raise PanelError("unknown base model", errors={"name": "Start again from a model."})
            base = Loadout(name="", provider_profile=profile_name, model=model, implicit=True)
        mode = str(payload.get("mode") or form.get("mode") or "create")
        if mode not in ("create", "edit"):
            raise PanelError("mode must be create or edit", errors={"mode": "Unknown mode."})
        name = base.name if mode == "edit" else str(changes.get("name") or "").strip()
        codex = is_chatgpt_codex_profile(ctx._profile_manager, base.provider_profile)
        values, errors = _loadout_values(changes, codex, base.model)
        if not name:
            errors["name"] = "Name is required."
        elif mode == "create" and name in {entry.name for entry in manager.list_loadouts()}:
            errors["name"] = f"Name '{name}' is already in use."
        if errors:
            raise PanelError("Check the highlighted fields.", errors=errors)
        if mode == "edit":
            saved = manager.update(name, **values)
        else:
            saved = manager.create(
                name, provider_profile=base.provider_profile, model=base.model, **values
            )
        if not saved:
            return error_result(f"Could not save loadout '{name}'.")
        activated = None
        try:
            activated = await _activate(manager, ctx, manager.get(name))
        except Exception as exc:  # noqa: BLE001 - the save itself succeeded
            logger.warning("loadout form: activate failed for %s: %s", name, exc)
        note = "and switched to it" if activated else "but could not switch to it"
        return ok_result(f"Saved '{name}' {note}.", panel=await self.describe(ctx, {}))


PANELS = {LlmPanel.name: LlmPanel()}
