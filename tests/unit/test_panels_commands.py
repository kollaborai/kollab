"""list_commands() marks the slash commands that open a panel."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from kollabor.panels import COMMAND_PANELS, get_panel
from kollabor.state.local import LocalStateService


@pytest.mark.asyncio
async def test_only_panel_commands_carry_a_panel_key():
    names = [*COMMAND_PANELS, "version", "mcp"]
    registry = SimpleNamespace(
        get_all_commands=lambda: [
            SimpleNamespace(name=name, description=name, aliases=[]) for name in names
        ]
    )
    bus = SimpleNamespace(
        get_service=lambda name: registry if name == "command_registry" else None
    )
    service = LocalStateService(
        llm_service=None, profile_manager=MagicMock(), event_bus=bus
    )

    catalog = {entry["name"]: entry for entry in await service.list_commands()}

    assert {name: catalog[name]["panel"] for name in COMMAND_PANELS} == COMMAND_PANELS
    assert "panel" not in catalog["version"] and "panel" not in catalog["mcp"]
    assert all(get_panel(panel) for panel in COMMAND_PANELS.values())
