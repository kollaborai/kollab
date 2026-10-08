"""Every AltView under plugins/altview/ registers without an error at launch.

`AltViewCommandIntegrator` builds each discovered class with no arguments to
read its metadata. The old knock review screen had three required arguments,
so every launch logged `Error registering AltView commands for ...: missing 3
required positional arguments`; the knock screen keeps that fixed.
"""

from __future__ import annotations

import importlib.util
import inspect
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from kollabor.altview.command_integration import AltViewCommandIntegrator
from kollabor_tui.altview.base import AltView
from kollabor_tui.key_parser import KeyPress, KeyType
from plugins.altview.knocks_altview import KnockScreenAltView

PLUGINS = Path(__file__).resolve().parents[2] / "plugins"
FILES = sorted(
    f for f in (PLUGINS / "altview").glob("*.py") if not f.name.startswith("__")
)


def _classes(plugin_file: Path) -> list[type]:
    spec = importlib.util.spec_from_file_location(
        f"probe_altview_{plugin_file.stem}", plugin_file
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return [
        cls
        for _, cls in inspect.getmembers(module, inspect.isclass)
        if issubclass(cls, AltView)
        and cls is not AltView
        and cls.__module__ == module.__name__
    ]


ALL_CLASSES = [
    pytest.param(cls, id=f"{path.stem}.{cls.__name__}")
    for path in FILES
    for cls in _classes(path)
]


def test_the_scan_found_the_altview_classes():
    # Guard the parametrization: an empty scan would pass everything below.
    assert len(FILES) >= 10 and len(ALL_CLASSES) >= len(FILES)


@pytest.mark.parametrize("cls", ALL_CLASSES)
def test_every_altview_class_builds_with_no_arguments(cls):
    view = cls()

    assert view.metadata is not None and view.metadata.plugin_type


@pytest.fixture
def restore_altview_modules():
    """Discovery re-executes each plugin file under its real module name."""
    saved = {k: v for k, v in sys.modules.items() if k.startswith("plugins.altview.")}
    yield
    for key in [k for k in sys.modules if k.startswith("plugins.altview.")]:
        del sys.modules[key]
    sys.modules.update(saved)


def test_discovery_logs_no_error_for_any_altview(caplog, restore_altview_modules):
    registry = MagicMock()
    registry.get_command.return_value = None  # nothing pre-registered
    integrator = AltViewCommandIntegrator(registry, MagicMock(), MagicMock())

    with caplog.at_level(logging.DEBUG, logger="kollabor.altview.command_integration"):
        registered = integrator.discover_and_register_plugins(PLUGINS)

    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors == []
    assert registered >= len(FILES) - 1  # every file with a class registers
    assert "knocks" in integrator.get_registered_plugins()


# The auto-registered instance is only a metadata probe, but if it is ever
# opened it must be an empty, inert inbox rather than an exception.


class _Renderer:
    def get_terminal_size(self):
        return (100, 30)

    def clear_screen(self):
        pass

    def write_at(self, *args, **kwargs):
        pass


@pytest.mark.asyncio
async def test_the_knock_screen_without_callbacks_is_empty_and_inert():
    view = KnockScreenAltView()

    await view.on_enter(_Renderer())
    assert await view.render_frame(0)

    assert view._snapshot["ringing"] == [] and view._snapshot["missed"] == []
    for char in ("a", "r", "b", "w", "c"):
        key = KeyPress(name=char, code=ord(char), char=char, type=KeyType.PRINTABLE)
        assert await view.handle_input(key) is False
    assert view._message in ("", "knocks are not connected here")
