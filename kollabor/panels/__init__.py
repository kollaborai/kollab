"""Panels: daemon-owned screens shared by the terminal and the web UI.

See ``kollabor.panels.base`` for the contract and
``docs/specs/webui-unified-config.md`` for the design.
"""

from .base import (
    COMMAND_PANELS,
    FIELD_TYPES,
    PANEL_LOCATIONS,
    PANEL_MODULES,
    Panel,
    PanelError,
    UnknownPanelError,
    error_result,
    get_panel,
    list_panels,
    make_field,
    make_poll,
    make_reveal,
    ok_result,
    panel_for_command,
    require_panel,
    result,
    unknown_action,
)

__all__ = [
    "COMMAND_PANELS",
    "FIELD_TYPES",
    "PANEL_LOCATIONS",
    "PANEL_MODULES",
    "Panel",
    "PanelError",
    "UnknownPanelError",
    "error_result",
    "get_panel",
    "list_panels",
    "make_field",
    "make_poll",
    "make_reveal",
    "ok_result",
    "panel_for_command",
    "require_panel",
    "result",
    "unknown_action",
]
