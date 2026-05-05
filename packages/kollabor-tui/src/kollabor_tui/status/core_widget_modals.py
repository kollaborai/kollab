"""Interactive modal definitions for core status widgets.

Each function is an on_activate handler: receives (widget_id, context) and
returns a modal config dict consumed by WidgetInteractionHandler.activate_modal().

Modal config format:
    {
        "title": str,
        "options": [
            {
                "label": str,           # display text
                "action": str,          # action identifier for routing
                "description": str,     # optional subtitle
                "confirm": bool,        # optional: show confirmation prompt
                "message": str,         # optional: confirmation message
                "input": str,           # optional: input field name
                "input_label": str,     # optional: input field label
            }
        ],
        "footer": str,                  # optional
    }
"""

import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Git Branch Modal
# ---------------------------------------------------------------------------


async def git_branch_modal(widget_id: str, context: Any) -> Dict[str, Any]:
    """Modal for the git-branch status widget."""
    return {
        "title": "Git Branch",
        "options": [
            {
                "label": "View Status",
                "action": "git_status",
                "description": "Show full git status output",
            },
            {
                "label": "Switch Branch",
                "action": "git_checkout",
                "description": "Checkout an existing branch",
                "input": "branch_name",
                "input_label": "Branch name:",
            },
            {
                "label": "Create Branch",
                "action": "git_branch_new",
                "description": "Create a new branch from current HEAD",
                "input": "new_branch_name",
                "input_label": "New branch name:",
            },
            {
                "label": "Pull Latest",
                "action": "git_pull",
                "description": "Pull from remote",
                "confirm": True,
                "message": "Pull from remote?",
            },
            {
                "label": "Push Changes",
                "action": "git_push",
                "description": "Push commits to remote",
                "confirm": True,
                "message": "Push to remote?",
            },
        ],
        "footer": "enter to select  •  esc to close",
    }


# ---------------------------------------------------------------------------
# Profile Switcher Modal
# ---------------------------------------------------------------------------


async def profile_switcher_modal(widget_id: str, context: Any) -> Dict[str, Any]:
    """Modal for the profile status widget.

    Dynamically builds option list from the profile manager when available.
    """
    profile_manager = getattr(context, "profile_manager", None) if context else None
    options: List[Dict[str, Any]] = []

    if profile_manager:
        try:
            profiles = profile_manager.list_profiles()
            current = profile_manager.get_active_profile()
            current_name = current.name if current else ""

            for name in profiles:
                label = name if name != current_name else f"{name} (active)"
                options.append(
                    {
                        "label": label,
                        "action": "set_profile",
                        "description": f"Switch to {name}",
                        "profile_name": name,
                    }
                )
        except Exception as e:
            logger.warning("profile_switcher_modal: could not list profiles: %s", e)
            options.append(
                {"label": "Error loading profiles", "action": "none", "description": str(e)}
            )
    else:
        options.append(
            {"label": "No profile manager available", "action": "none", "description": ""}
        )

    options.append(
        {
            "label": "Create New Profile",
            "action": "create_profile",
            "description": "Add a new LLM API profile",
            "input": "new_profile_name",
            "input_label": "Profile name:",
        }
    )

    return {
        "title": "LLM Profile",
        "options": options,
        "footer": "enter to select  •  esc to close",
    }


# ---------------------------------------------------------------------------
# Tmux Session Modal
# ---------------------------------------------------------------------------


async def tmux_session_modal(widget_id: str, context: Any) -> Dict[str, Any]:
    """Modal for the tmux status widget.

    Dynamically builds option list from live tmux sessions.
    """
    options: List[Dict[str, Any]] = []

    try:
        import subprocess

        result = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}:#{session_attached}"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0 and result.stdout.strip():
            for line in result.stdout.strip().splitlines():
                if ":" not in line:
                    continue
                name, attached = line.split(":", 1)
                label = f"{name} (attached)" if attached == "1" else name
                options.append(
                    {
                        "label": label,
                        "action": "tmux_view",
                        "description": "Open in terminal view",
                        "session_name": name,
                    }
                )
                options.append(
                    {
                        "label": f"Kill {name}",
                        "action": "tmux_kill",
                        "description": f"Terminate session '{name}'",
                        "session_name": name,
                        "confirm": True,
                        "message": f"Kill session '{name}'?",
                    }
                )
        else:
            options.append(
                {"label": "No active sessions", "action": "none", "description": ""}
            )
    except FileNotFoundError:
        options.append({"label": "tmux not installed", "action": "none", "description": ""})
    except Exception as e:
        logger.warning("tmux_session_modal: %s", e)
        options.append({"label": "Error listing sessions", "action": "none", "description": str(e)})

    options.append(
        {
            "label": "New Session",
            "action": "tmux_new",
            "description": "Create a new tmux session",
            "input": "session_name",
            "input_label": "Session name:",
        }
    )

    return {
        "title": "Terminal Sessions",
        "options": options,
        "footer": "enter to select  •  esc to close",
    }


# ---------------------------------------------------------------------------
# CWD Directory Modal
# ---------------------------------------------------------------------------


async def cwd_directory_modal(widget_id: str, context: Any) -> Dict[str, Any]:
    """Modal for the cwd status widget."""
    from pathlib import Path

    try:
        cwd = Path.cwd()
        home = Path.home()
        if cwd == home:
            display = "~ (home)"
        elif cwd.is_relative_to(home):
            display = f"~/{cwd.relative_to(home)}"
        else:
            display = str(cwd)
        can_go_up = cwd != cwd.parent
    except Exception:
        display = "unknown"
        can_go_up = False

    options: List[Dict[str, Any]] = [
        {
            "label": f"Current: {display}",
            "action": "cwd_show",
            "description": "Show full path",
        },
    ]
    if can_go_up:
        options.append(
            {
                "label": "Go to Parent",
                "action": "cwd_parent",
                "description": "Navigate up one level",
            }
        )
    options += [
        {
            "label": "Go to Home",
            "action": "cwd_home",
            "description": "Navigate to ~",
        },
        {
            "label": "Enter Path",
            "action": "cwd_custom",
            "description": "Navigate to an arbitrary path",
            "input": "custom_path",
            "input_label": "Path:",
        },
    ]

    return {
        "title": "Directory",
        "options": options,
        "footer": "enter to select  •  esc to close",
    }


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

CORE_WIDGET_MODALS: Dict[str, Callable] = {
    "git-branch": git_branch_modal,
    "profile": profile_switcher_modal,
    "tmux": tmux_session_modal,
    "cwd": cwd_directory_modal,
}


def get_modal_for_widget(widget_id: str) -> Optional[Callable]:
    """Return the on_activate handler for a widget ID, or None."""
    return CORE_WIDGET_MODALS.get(widget_id)
