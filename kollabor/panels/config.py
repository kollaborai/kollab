"""The /config panel: every setting as data, and the one way a setting is saved.

``build_config_sections`` and ``apply_config_changes`` are what ``ConfigAltView``
used to do inline; the terminal view and the web panel both call them.
"""

from __future__ import annotations

import json
import logging
import math
from typing import Any, Mapping, Optional

from kollabor_config.config_utils import (
    get_global_config_path,
    get_local_config_path,
)
from kollabor_config.managed_config import (
    ManagedConfig,
    managed_by,
    read_managed_config,
)
from kollabor_config.secrets import is_secret_path

from .base import (
    FIELD_TYPES,
    PanelError,
    error_result,
    make_field,
    ok_result,
    unknown_action,
)

logger = logging.getLogger(__name__)

TITLE = "System Configuration"
TARGETS = ("local", "global")
MAX_TEXT_BYTES = 4096
MAX_CHANGES = 500
_LOADOUT_SECTION = "LLM Settings"


# -- what the network's primary manages ----------------------------------------


def read_managed_state() -> tuple[Optional[ManagedConfig], dict]:
    """The managed config (None: nothing is managed) and the global settings
    file the primary wrote, read fresh from disk."""
    managed = read_managed_config()
    if not managed:
        return managed, {}
    try:
        data = json.loads(get_global_config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return managed, {}
    return managed, data if isinstance(data, dict) else {}


def managed_value(
    managed: Optional[ManagedConfig], global_settings: dict, config_path: str
) -> Any:
    """A synced key's value as the primary last wrote it, straight from disk.

    In an attached window this process's config service can lag a sync by a
    moment; the file is the truth, so a synced row never shows a stale value.
    """
    for segments in managed.keys if managed else ():
        if ".".join(segments) == config_path:
            node: Any = global_settings
            for part in segments:
                node = node.get(part) if isinstance(node, dict) else None
            return node
    return None


# -- sections -----------------------------------------------------------------


def _effective(config_service: Any, managed, global_settings, config_path: str):
    if managed_by(config_path, managed):
        return managed_value(managed, global_settings, config_path)
    return config_service.get(config_path) if config_service else None


def _loadout_rows(config_service: Any, managed, global_settings) -> list[dict]:
    """The active loadout at the top of LLM Settings, read-only.

    Change it with /llm; on a secondary it also says who manages it.
    """
    active = (
        _effective(
            config_service, managed, global_settings, "kollabor.llm.active_profile"
        )
        or "default"
    )
    rows = [
        {
            "type": "label",
            "label": "Loadout",
            "config_path": "kollabor.llm.active_profile",
            "value": str(active),
            "help": "The active LLM loadout; change it with /llm",
        }
    ]
    model_path = f"kollabor.llm.profiles.{active}.model"
    if managed_by(model_path, managed):
        model = managed_value(managed, global_settings, model_path)
    else:
        profiles = (
            config_service.get("kollabor.llm.profiles") if config_service else None
        )
        profile = profiles.get(active) if isinstance(profiles, dict) else None
        model = profile.get("model") if isinstance(profile, dict) else None
    if model:
        rows.append(
            {
                "type": "label",
                "label": "Model",
                "config_path": model_path,
                "value": str(model),
                "help": "The model of the active loadout; change it with /llm",
            }
        )
    return rows


def build_config_sections(
    config_service: Any,
    managed: Optional[ManagedConfig] = None,
    global_settings: Optional[dict] = None,
) -> list[dict]:
    """The /config sections: ``[{"title": str, "widgets": [widget_def, ...]}]``.

    Widget definitions come from ``ConfigWidgetDefinitions``; the two loadout
    rows lead LLM Settings. Each definition is copied and annotated with
    ``managed_by`` (the primary's name, or None). Pass the result of
    ``read_managed_state()`` as ``managed`` / ``global_settings``.
    """
    from kollabor_tui.config_widgets import ConfigWidgetDefinitions

    settings = global_settings or {}
    sections = ConfigWidgetDefinitions.get_config_modal_definition().get("sections", [])
    built = []
    for section in sections:
        widgets = list(section.get("widgets", []))
        if section.get("title") == _LOADOUT_SECTION:
            widgets = _loadout_rows(config_service, managed, settings) + widgets
        built.append(
            {
                **section,
                "widgets": [
                    {**w, "managed_by": managed_by(w.get("config_path", ""), managed)}
                    for w in widgets
                ],
            }
        )
    return built


# -- saving ---------------------------------------------------------------------


def apply_config_changes(
    config_service: Any, changes: Mapping[str, Any], target: str
) -> bool:
    """Save ``{config_path: value}`` to ``target`` ("local" or "global").

    Every config save, from the terminal or the browser, goes through here: set
    in memory, write each key to its file, then tell the running process so the
    LLM and plugins that cache config keys refresh. Other processes pick the
    change up from the file's mtime. Returns False if any write failed.
    """
    if not changes:
        return True
    for path, value in changes.items():
        config_service.set(path, value)
    ok = True
    for path, value in changes.items():
        if not config_service.save_key(path, value, save_target=target):
            ok = False
    if not ok:
        logger.error("config save to %s failed for at least one key", target)
        return False
    logger.info("saved %d config change(s) to %s", len(changes), target)
    notify = getattr(config_service, "_notify_reload_callbacks", None)
    if callable(notify):
        try:
            notify()
        except Exception as exc:
            logger.warning("reload notify after config save failed: %s", exc)
    return True


def _number_error(field: dict, value: Any) -> Optional[str]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "must be a number"
    if not math.isfinite(value):
        return "must be a finite number"
    low, high, step = field.get("min_value"), field.get("max_value"), field.get("step")
    if low is not None and value < low:
        return f"must be at least {low}"
    if high is not None and value > high:
        return f"must be at most {high}"
    if step:
        position = (value - (low or 0)) / step
        if abs(position - round(position)) > 1e-6:
            return f"must be a multiple of {step}"
    return None


def validate_config_changes(
    fields: Mapping[str, dict], changes: Mapping[str, Any]
) -> dict[str, str]:
    """Why each change is refused, ``{path: reason}`` (empty: all fine).

    ``fields`` is ``{path: field}`` from ``describe()``: only editable fields
    are accepted, so labels, injected loadout rows, managed paths and unknown
    paths are all refused, and values are checked against their widget.
    """
    errors: dict[str, str] = {}
    for path, value in changes.items():
        field = fields.get(path)
        if field is None:
            errors[path] = "unknown setting"
        elif field.get("managed_by"):
            errors[path] = f"managed by {field['managed_by']}"
        elif not field.get("editable"):
            errors[path] = "read-only"
        elif field["type"] == "checkbox":
            if not isinstance(value, bool):
                errors[path] = "must be true or false"
        elif field["type"] in ("slider", "spinbox"):
            reason = _number_error(field, value)
            if reason:
                errors[path] = reason
        elif field["type"] == "dropdown":
            if value not in (field.get("options") or []):
                errors[path] = "not one of the options"
        elif field["type"] == "text_input":
            if not isinstance(value, str):
                errors[path] = "must be text"
            elif len(value.encode("utf-8")) > MAX_TEXT_BYTES:
                errors[path] = f"longer than {MAX_TEXT_BYTES} bytes"
        else:
            errors[path] = "read-only"
    return errors


def _whole_numbers(fields: Mapping[str, dict], changes: dict[str, Any]) -> dict:
    """20.0 for an integer-stepped setting is saved as 20, like the terminal does."""
    out = dict(changes)
    for path, value in changes.items():
        field = fields.get(path, {})
        steps = [field.get("min_value"), field.get("step")]
        if (
            field.get("type") in ("slider", "spinbox")
            and isinstance(value, float)
            and value.is_integer()
            and all(isinstance(s, int) for s in steps if s is not None)
        ):
            out[path] = int(value)
    return out


# -- the panel --------------------------------------------------------------------


def _config_service(ctx: Any) -> Any:
    service = getattr(getattr(ctx, "_llm_service", None), "config", None)
    if service is None:
        raise PanelError("configuration is not available", status=503)
    return service


def _field(row: dict, config_service: Any, managed, global_settings) -> dict:
    path = row.get("config_path", "")
    wtype = row.get("type", "label")
    wtype = wtype if wtype in FIELD_TYPES else "label"
    owner = row.get("managed_by")
    if owner:
        value = managed_value(managed, global_settings, path)
    elif wtype == "label":
        value = row.get("value")
    else:
        value = config_service.get(path)
        if value is None:
            value = row.get("default")
    return make_field(
        path,
        wtype,
        row.get("label", path),
        help=row.get("help", ""),
        value=value,
        min_value=row.get("min_value"),
        max_value=row.get("max_value"),
        step=row.get("step"),
        options=row.get("options"),
        placeholder=row.get("placeholder"),
        managed_by=owner,
        secret=is_secret_path(path, row),
    )


class ConfigPanel:
    name = "config"
    kind = "form"

    async def describe(self, ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
        service = _config_service(ctx)
        managed, global_settings = read_managed_state()
        sections = build_config_sections(service, managed, global_settings)
        return {
            "panel": self.name,
            "kind": self.kind,
            "title": TITLE,
            "sections": [
                {
                    "id": f"s{index}",
                    "title": section.get("title", "Section"),
                    "fields": [
                        _field(row, service, managed, global_settings)
                        for row in section["widgets"]
                    ],
                }
                for index, section in enumerate(sections)
            ],
            "actions": [{"id": "save", "label": "Save", "targets": list(TARGETS)}],
            "save_targets": {
                "local": str(get_local_config_path()),
                "global": str(get_global_config_path()),
            },
        }

    async def act(
        self, ctx: Any, action: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        if action != "save":
            raise unknown_action(self.name, action)
        changes, target = payload.get("changes"), payload.get("target")
        if target not in TARGETS:
            raise PanelError("target must be 'local' or 'global'")
        if not isinstance(changes, dict) or len(changes) > MAX_CHANGES:
            raise PanelError(f"changes must be an object of at most {MAX_CHANGES}")
        if not all(isinstance(path, str) for path in changes):
            raise PanelError("changes must be keyed by setting path")

        current = await self.describe(ctx, {})
        fields = {f["path"]: f for s in current["sections"] for f in s["fields"]}
        errors = validate_config_changes(fields, changes)
        if errors:
            return error_result("Some settings were not saved", errors)

        changes = _whole_numbers(fields, changes)
        if not apply_config_changes(_config_service(ctx), changes, target):
            return error_result("Could not write the settings file")
        where = current["save_targets"][target]
        noun = "setting" if len(changes) == 1 else "settings"
        return ok_result(
            f"Saved {len(changes)} {noun} to {where}",
            panel=await self.describe(ctx, {}),
        )


PANELS = {"config": ConfigPanel()}
