"""Help system for interactive status widgets.

This module provides help modal functionality for first-run discovery and
keyboard shortcut reference. It integrates with the modal system and uses
the design system for consistent styling.

Functions:
    show_first_run_help: Show help modal on first run (once per user)
    show_help_overlay: Show keyboard shortcuts (F1 or ? key)
"""

import logging
from typing import Any, Dict

from kollabor_events.models import UIConfig
from kollabor_tui.design_system import Box, S, T, TagBox, solid, solid_fg

logger = logging.getLogger(__name__)


async def show_first_run_help(
    show_modal_callback: Any,
    config: Any,
) -> Dict[str, Any]:
    """Show help modal on first run (once per user).

    Checks config.status.navigation.help_shown and displays a welcome
    modal explaining the new interactive status widgets feature if
    not shown before. Marks as shown after display.

    Args:
        show_modal_callback: Async callback to show modal (from ModalController).
        config: ConfigService for persisting help_shown state.

    Returns:
        Result dict with success status and any error info.
    """
    try:
        # Check if help was already shown
        help_shown = config.get("status.navigation.help_shown", False)
        if help_shown:
            logger.info("First-run help already shown, skipping")
            return {"success": True, "shown": False, "reason": "already_shown"}

        # Build first-run help modal content
        modal_config = {
            "sections": [
                {
                    "title": "NEW: Interactive Status Widgets",
                    "commands": [
                        {
                            "name": "Tab",
                            "description": "Enter Navigation Mode - move between status widgets",
                            "selectable": False,
                        },
                        {
                            "name": "Arrow Keys",
                            "description": "← → to move between widgets, ↑ ↓ between rows",
                            "selectable": False,
                        },
                        {
                            "name": "Enter",
                            "description": "Activate selected widget (e.g., open dropdown menu)",
                            "selectable": False,
                        },
                        {
                            "name": "Esc",
                            "description": "Return to normal input mode",
                            "selectable": False,
                        },
                        {
                            "name": "F1 or ?",
                            "description": "Show this help overlay anytime",
                            "selectable": False,
                        },
                    ],
                },
            ],
            "footer": "[Enter] Got it!  Press Enter to close",
            "actions": [
                {"key": "Enter", "action": "submit"},
            ],
        }

        ui_config = UIConfig(
            type="modal",
            title="Welcome to Interactive Status Widgets",
            modal_config=modal_config,
        )

        # Show the modal
        logger.info("About to call show_modal_callback...")
        await show_modal_callback(ui_config)
        logger.info("show_modal_callback completed successfully")

        # Mark as shown and persist to disk
        config.save_key("status.navigation.help_shown", True)

        logger.info("First-run help displayed and marked as shown")
        return {"success": True, "shown": True}

    except Exception as e:
        logger.error(f"Error showing first-run help: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


async def show_help_overlay(
    show_modal_callback: Any,
    current_mode: str = "INPUT",
) -> Dict[str, Any]:
    """Show keyboard shortcuts help overlay (triggered by F1 or ?).

    Displays a comprehensive keyboard shortcut reference for interactive
    status widgets and general application navigation.

    Args:
        show_modal_callback: Async callback to show modal (from ModalController).
        current_mode: Current navigation mode (INPUT, NAVIGATION, INTERACTION).

    Returns:
        Result dict with success status and any error info.
    """
    try:
        # Build help overlay content
        sections = []

        # Navigation section
        nav_commands = [
            {
                "name": "Tab",
                "description": "Enter Navigation Mode",
                "selectable": False,
            },
            {
                "name": "← →",
                "description": "Move between widgets",
                "selectable": False,
            },
            {
                "name": "↑ ↓",
                "description": "Move between rows",
                "selectable": False,
            },
            {
                "name": "Enter",
                "description": "Activate selected widget",
                "selectable": False,
            },
            {
                "name": "Esc",
                "description": "Return to Input Mode",
                "selectable": False,
            },
        ]
        sections.append(
            {
                "title": "Navigation",
                "commands": nav_commands,
            }
        )

        # Help and shortcuts section
        help_commands = [
            {
                "name": "F1 or ?",
                "description": "Show this help",
                "selectable": False,
            },
            {
                "name": "Ctrl+Z",
                "description": "Undo last action",
                "selectable": False,
            },
            {
                "name": "1-9",
                "description": "Quick jump to widget",
                "selectable": False,
            },
            {
                "name": "Space",
                "description": "Quick toggle (toggle widgets)",
                "selectable": False,
            },
        ]
        sections.append(
            {
                "title": "Shortcuts",
                "commands": help_commands,
            }
        )

        # Current mode indicator
        mode_commands = [
            {
                "name": "Current Mode",
                "description": current_mode,
                "selectable": False,
            },
        ]
        sections.append(
            {
                "title": "Status",
                "commands": mode_commands,
            }
        )

        modal_config = {
            "sections": sections,
            "footer": "Press Esc or Enter to close",
            "actions": [
                {"key": "Escape", "action": "cancel"},
                {"key": "Enter", "action": "cancel"},
            ],
        }

        ui_config = UIConfig(
            type="modal",
            title="Interactive Status Widgets - Help",
            modal_config=modal_config,
        )

        # Show the modal
        await show_modal_callback(ui_config)

        logger.info(f"Help overlay displayed (mode: {current_mode})")
        return {"success": True}

    except Exception as e:
        logger.error(f"Error showing help overlay: {e}")
        return {"success": False, "error": str(e)}


def generate_help_text(
    current_mode: str = "INPUT",
    width: int = 50,
) -> str:
    """Generate help text as plain string (for non-modal display).

    Useful for displaying help in status area or other simple contexts.

    Args:
        current_mode: Current navigation mode.
        width: Width for text formatting.

    Returns:
        Formatted help text as string.
    """
    lines = []

    # Title bar
    title = "Interactive Status Widgets - Help"
    title_bar = Box.render(
        [f"  {S.BOLD}{title}{S.RESET_BOLD}"],
        T().primary,
        T().text,
        width,
    )
    lines.extend(title_bar.split("\n"))

    # Content sections
    sections = [
        (
            "Navigation",
            [
                ("Tab", "Enter Navigation Mode"),
                ("← →", "Move between widgets"),
                ("↑ ↓", "Move between rows"),
                ("Enter", "Activate selected widget"),
                ("Esc", "Return to Input Mode"),
            ],
        ),
        (
            "Shortcuts",
            [
                ("F1 or ?", "Show this help"),
                ("Ctrl+Z", "Undo last action"),
                ("1-9", "Quick jump to widget"),
                ("Space", "Quick toggle"),
            ],
        ),
    ]

    for section_title, items in sections:
        # Section header
        header = TagBox.render(
            lines=[f" {S.BOLD}{section_title}{S.RESET_BOLD}"],
            tag_bg=T().primary[0],
            tag_fg=None,
            tag_width=3,
            content_colors=T().dark[0],
            content_fg=T().text,
            content_width=width - 7,
            tag_chars=[" ■ "],
            use_gradient=False,
        )
        lines.extend(["  " + line for line in header.split("\n")])

        # Section items
        for key, desc in items:
            content = f" {key:<12} {desc}"
            item = TagBox.render(
                lines=[content],
                tag_bg=T().dark[0],
                tag_fg=T().text_dim,
                tag_width=3,
                content_colors=T().dark[0],
                content_fg=T().text_dim,
                content_width=width - 7,
                tag_chars=["   "],
                use_gradient=False,
            )
            lines.extend(["  " + line for line in item.split("\n")])

        # Blank line after section
        lines.append(f"  {' ' * (width - 4)}")

    # Current mode footer
    mode_text = f"  Current Mode: {current_mode}"
    footer = solid(mode_text.ljust(width - 4), T().dark[0], T().text_dim, width - 4)
    lines.append(footer)

    # Bottom border
    bottom = solid_fg("▀" * width, T().primary[0])
    lines.append(bottom)

    return "\n".join(lines)
