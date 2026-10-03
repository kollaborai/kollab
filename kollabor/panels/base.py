"""Panel contract: one daemon-owned screen that the terminal and the web share.

A panel is keyed by its slash command name. ``describe()`` returns the screen
as plain JSON-able data; ``act()`` runs one action and returns the shape built
by ``ok_result`` / ``error_result``. ``ctx`` is the daemon's
``LocalStateService``. Panels keep no state between calls.

Wire shapes live in ``docs/specs/webui-unified-config.md`` ("wire format"):

* ``kind == "form"``    -> ``{panel, kind, title, sections, actions, save_targets}``
* ``kind == "picker"``  -> ``{panel, kind, title, rows, row_actions, toolbar_actions, ...}``
* ``kind == "wizard"``  -> ``{panel, kind, title, steps, actions}``

Every field in any of them is built with ``make_field``.

Registry convention
-------------------
Each module ``kollabor.panels.<module>`` listed in ``PANEL_MODULES`` exports
``PANELS: dict[str, Panel]`` keyed by panel name. The first dash-separated
segment of a panel name is its module, so ``connect``, ``connect-join`` and
``connect-knocks`` all live in ``connect.py``. ``get_panel`` imports only that
module, on first use. A missing module or name means "unknown panel", never an
import crash.

Errors
------
Raise ``PanelError(message, errors={path: text})`` for a request the panel
cannot serve (400); routes show ``errors`` per field. ``get_panel`` /
``require_panel`` signal an unknown panel (404). Returning
``error_result(...)`` with non-empty ``errors`` is the same 400; ``ok: False``
with no ``errors`` is an outcome (a failed connection test), not a bad request.
"""

from __future__ import annotations

import importlib
import logging
from typing import Any, Mapping, Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

# Modules that may export PANELS, in the order list_panels() loads them.
PANEL_MODULES: tuple[str, ...] = ("config", "llm", "model", "setup", "connect")

# Slash command (canonical name, no "/") -> the panel it opens in the web UI.
COMMAND_PANELS: dict[str, str] = {
    "config": "config",
    "llm": "llm",
    "model": "model",
    "setup": "setup",
    "connect": "connect",
}

# Where the web UI shows each panel. Used in the reply a command gives when it
# runs where no terminal exists ("/config opens in Settings → Configuration").
PANEL_LOCATIONS: dict[str, str] = {
    "config": "Settings → Configuration",
    "llm": "Settings → Loadouts",
    "model": "Settings → Model",
    "setup": "Settings → Setup",
    "connect": "Settings → Network",
}

FIELD_TYPES: tuple[str, ...] = (
    "checkbox",
    "slider",
    "spinbox",
    "dropdown",
    "text_input",
    "label",
)


@runtime_checkable
class Panel(Protocol):
    """One screen. ``kind`` is ``"form"``, ``"picker"`` or ``"wizard"``."""

    name: str
    kind: str

    async def describe(self, ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
        """The screen as data. ``params`` carries only non-sensitive filters."""
        ...

    async def act(
        self, ctx: Any, action: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Run one action; return ``ok_result(...)`` or ``error_result(...)``."""
        ...


class PanelError(Exception):
    """A request a panel cannot serve. ``status`` is the HTTP code routes use."""

    status = 400

    def __init__(
        self,
        message: str,
        errors: Optional[Mapping[str, str]] = None,
        status: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.errors: dict[str, str] = dict(errors or {})
        if status is not None:
            self.status = status


class UnknownPanelError(PanelError):
    """No panel with that name (404)."""

    status = 404


def unknown_action(panel: str, action: str) -> PanelError:
    """The error ``act()`` raises for an action it does not have (404)."""
    return PanelError(f"unknown action '{action}' for panel '{panel}'", status=404)


def ok_result(message: str = "", **extra: Any) -> dict[str, Any]:
    """An ``act()`` result with ``ok: True``. See ``result`` for ``extra``."""
    return result(True, message, **extra)


def error_result(
    message: str, errors: Optional[Mapping[str, str]] = None, **extra: Any
) -> dict[str, Any]:
    """An ``act()`` result with ``ok: False`` and per-field ``errors``."""
    return result(False, message, errors=errors, **extra)


def result(
    ok: bool,
    message: str = "",
    *,
    errors: Optional[Mapping[str, str]] = None,
    panel: Optional[dict[str, Any]] = None,
    open: Optional[dict[str, Any]] = None,  # noqa: A002 - wire key
    reveal: Optional[dict[str, Any]] = None,
    poll: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """The ``act()`` shape ``{ok, message, errors, panel, open}``.

    ``panel`` is a fresh ``describe()`` of the same panel; ``open`` is another
    panel's ``describe()`` for the browser to switch to. ``reveal`` and
    ``poll`` (see ``make_reveal`` / ``make_poll``) appear only when set.
    """
    out: dict[str, Any] = {
        "ok": bool(ok),
        "message": message,
        "errors": dict(errors or {}),
        "panel": panel,
        "open": open,
    }
    if reveal is not None:
        out["reveal"] = reveal
    if poll is not None:
        out["poll"] = poll
    return out


def make_reveal(
    label: str,
    value: str,
    expires_at: Optional[float] = None,
    status: str = "",
) -> dict[str, Any]:
    """A one-time secret in an action response, shown with a countdown.

    ``expires_at`` is epoch seconds. Never put a reveal in ``describe()``.
    """
    return {
        "label": label,
        "value": value,
        "expires_at": expires_at,
        "status": status,
    }


def make_poll(
    action: str, payload: Optional[dict[str, Any]] = None, every_s: float = 2.0
) -> dict[str, Any]:
    """Ask the browser to re-send ``action`` every ``every_s`` seconds.

    It stops when the panel's response carries no ``poll``.
    """
    return {"action": action, "payload": dict(payload or {}), "every_s": every_s}


def make_field(
    path: str,
    type: str,  # noqa: A002 - wire key
    label: str,
    *,
    help: str = "",  # noqa: A002 - wire key
    value: Any = None,
    min_value: Any = None,
    max_value: Any = None,
    step: Any = None,
    options: Optional[list[str]] = None,
    placeholder: Optional[str] = None,
    editable: bool = True,
    managed_by: Optional[str] = None,
    secret: bool = False,
    action: Optional[str] = None,
) -> dict[str, Any]:
    """One field in the wire format.

    A secret never carries its value: ``value`` is None and ``is_set`` says
    whether one exists. ``editable`` is forced false for ``label`` rows and for
    managed paths. ``options`` is a list of strings (dropdown). A picker control
    sets ``action``: the browser posts ``{path: value}`` to that action.
    """
    field = {
        "path": path,
        "type": type,
        "label": label,
        "help": help,
        "value": None if secret else value,
        "min_value": min_value,
        "max_value": max_value,
        "step": step,
        "options": options,
        "placeholder": placeholder,
        "editable": bool(editable) and managed_by is None and type != "label",
        "managed_by": managed_by,
        "secret": bool(secret),
        "is_set": value is not None and value != "",
    }
    if action:
        field["action"] = action
    return field


def panel_for_command(command: str) -> Optional[str]:
    """The panel a slash command opens, or None. Accepts ``"/config"``."""
    return COMMAND_PANELS.get(command.lstrip("/").strip().lower())


def _module_panels(module: str) -> dict[str, Panel]:
    path = f"{__package__}.{module}"
    try:
        found = importlib.import_module(path).PANELS
    except ModuleNotFoundError as exc:
        if exc.name != path:  # the panel module exists but its own import broke
            logger.warning("panel module %s failed to import", path, exc_info=True)
        return {}
    except Exception:
        logger.warning("panel module %s failed to load", path, exc_info=True)
        return {}
    return found if isinstance(found, dict) else {}


def get_panel(name: str) -> Optional[Panel]:
    """The panel called ``name``, or None. Imports at most one module."""
    module = name.split("-", 1)[0]
    if module not in PANEL_MODULES:
        return None
    return _module_panels(module).get(name)


def require_panel(name: str) -> Panel:
    """Like ``get_panel`` but raises ``UnknownPanelError`` (404)."""
    panel = get_panel(name)
    if panel is None:
        raise UnknownPanelError(f"unknown panel '{name}'")
    return panel


def list_panels() -> dict[str, Panel]:
    """Every panel that loads, keyed by name. Imports every panel module."""
    panels: dict[str, Panel] = {}
    for module in PANEL_MODULES:
        panels.update(_module_panels(module))
    return panels
