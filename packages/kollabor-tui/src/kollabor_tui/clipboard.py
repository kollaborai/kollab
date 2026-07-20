"""System clipboard helper.

Cross-platform best-effort clipboard copy used by TUI surfaces (login
device-code view, /save clipboard, etc.). Tries the common platform
utilities in order and returns whether any of them succeeded.

Never raises -- callers get a bool so they can show feedback without a
try/except at every call site.
"""

import logging
import subprocess

logger = logging.getLogger(__name__)

# (command, description) tried in order. First one present wins.
_CLIPBOARD_COMMANDS: list[tuple[list[str], str]] = [
    (["pbcopy"], "pbcopy"),  # macOS
    (["xclip", "-selection", "clipboard"], "xclip"),  # X11
    (["xsel", "--clipboard", "--input"], "xsel"),  # X11 alt
    (["wl-copy"], "wl-copy"),  # Wayland
]


def copy_to_clipboard(content: str) -> bool:
    """Copy ``content`` to the system clipboard.

    Args:
        content: Text to place on the clipboard.

    Returns:
        True if a clipboard utility accepted the content, False if none
        was found or an error occurred.
    """
    payload = content.encode("utf-8")

    for command, name in _CLIPBOARD_COMMANDS:
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError:
            continue
        except Exception as e:  # noqa: BLE001 - never let clipboard crash caller
            logger.error("Error copying to clipboard via %s: %s", name, e)
            continue

        try:
            process.communicate(input=payload)
        except Exception as e:  # noqa: BLE001
            logger.error("Error writing to clipboard via %s: %s", name, e)
            continue

        if process.returncode == 0:
            logger.info("Copied to clipboard using %s", name)
            return True

        logger.warning("Clipboard utility %s exited with %s", name, process.returncode)

    logger.warning("No clipboard utility found (pbcopy, xclip, xsel, wl-copy)")
    return False
