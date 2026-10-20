"""Render the real /config Dream Model widget in an isolated terminal.

This is component-render evidence only. It does not launch Kollab, read the
user's config, start the engine, or call any model/provider.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--theme", choices=("dark", "ocean", "mono"), default="dark")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[3]
    temp_home = tempfile.TemporaryDirectory(prefix="kollab-issue260-render-")
    home = Path(temp_home.name)
    os.environ["HOME"] = str(home)
    os.environ["KOLLAB_HOME"] = str(home)
    os.environ["XDG_CONFIG_HOME"] = str(home / ".config")
    for key in (
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "KOLLAB_ENGINE_URL",
    ):
        os.environ.pop(key, None)
    os.chdir(home)
    sys.path.insert(0, str(repo_root))
    logging.disable(logging.CRITICAL)

    from kollabor_tui.design_system.theme import set_theme
    from kollabor_tui.fullscreen.renderer import FullScreenRenderer
    from kollabor_tui.key_parser import KeyPress, KeyType
    from plugins.altview import config_altview
    from plugins.altview.config_altview import ConfigAltView

    class DummyConfig:
        """In-memory settings only; saving is deliberately unavailable."""

        def get(self, path: str, default: Any = None) -> Any:
            if path == "plugins.hub.dreaming_model":
                return "gpt-5-mini"
            if path == "kollabor.ui.theme":
                return args.theme
            return default

        def set(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("isolated render does not persist settings")

    # Avoid reading the real managed-settings file; this view has no managed
    # settings in the temporary render environment.
    config_altview.read_managed_state = lambda: (None, {})

    set_theme(args.theme)
    view = ConfigAltView()
    view.config_service = DummyConfig()
    renderer = FullScreenRenderer()

    async def render() -> None:
        await view.on_enter(renderer)
        section_index = next(
            index
            for index, title in enumerate(view._sections)
            if title == "Hub (Agent Mesh)"
        )
        widget_index = next(
            index
            for index, widget in enumerate(view._section_widgets[section_index])
            if widget.get_label() == "Dream Model"
        )

        tab = KeyPress(name="Tab", code=9, type=KeyType.SPECIAL)
        down = KeyPress(name="ArrowDown", code="[B", type=KeyType.SPECIAL)
        for _ in range(section_index):
            await view.handle_input(tab)
        for _ in range(widget_index):
            await view.handle_input(down)

        focused = view._current_widget()
        assert focused is not None and focused.get_label() == "Dream Model"
        await view.render_frame(0.0)
        sys.stdout.flush()
        await asyncio.sleep(1.5)

    try:
        asyncio.run(render())
    finally:
        os.chdir(repo_root)
        temp_home.cleanup()


if __name__ == "__main__":
    main()
