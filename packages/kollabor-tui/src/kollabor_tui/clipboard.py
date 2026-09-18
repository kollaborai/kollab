"""System clipboard helper.

Cross-platform best-effort clipboard copy used by TUI surfaces (login
device-code view, /save clipboard, etc.). Tries the common platform
utilities in order and returns whether any of them succeeded.

Never raises -- callers get a bool so they can show feedback without a
try/except at every call site.
"""

import base64
import binascii
import logging
import subprocess
import sys
from typing import Optional

logger = logging.getLogger(__name__)

MAX_CLIPBOARD_IMAGE_BYTES = 10 * 1024 * 1024
CLIPBOARD_READ_TIMEOUT_SECONDS = 2.0

# JXA is present on macOS even when PyObjC is not installed. It is also much
# faster than starting a Swift compiler for every Ctrl+V. The pasteboard may
# expose PNG and TIFF representations; PNG is preferred because it is accepted
# by every image-capable provider in this repository.
# Return the result of the final expression: JXA console.log writes to stderr,
# while the reader below consumes stdout. Convert TIFF-only clipboards to PNG.
_MACOS_READ_IMAGE_SCRIPT = """
ObjC.import("AppKit");
ObjC.import("Foundation");
function readClipboardImage(pasteboard) {
  let data = pasteboard.dataForType($.NSPasteboardTypePNG);
  if (!data.isNil()) return data.base64EncodedStringWithOptions(0).js;

  const tiff = pasteboard.dataForType($.NSPasteboardTypeTIFF);
  if (tiff.isNil()) return "";
  const bitmap = $.NSBitmapImageRep.imageRepWithData(tiff);
  if (bitmap.isNil()) return "";
  data = bitmap.representationUsingTypeProperties($.NSBitmapImageFileTypePNG, $({}));
  if (data.isNil()) return "";
  return data.base64EncodedStringWithOptions(0).js;
}
readClipboardImage($.NSPasteboard.generalPasteboard);
""".strip()

_WINDOWS_READ_IMAGE_SCRIPT = """
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
if ([System.Windows.Forms.Clipboard]::ContainsImage()) {
  $image = [System.Windows.Forms.Clipboard]::GetImage()
  $stream = New-Object System.IO.MemoryStream
  $image.Save($stream, [System.Drawing.Imaging.ImageFormat]::Png)
  [Convert]::ToBase64String($stream.ToArray())
}
""".strip()

# (command, description) tried in order. First one present wins.
_CLIPBOARD_COMMANDS: list[tuple[list[str], str]] = [
    (["pbcopy"], "pbcopy"),  # macOS
    (["xclip", "-selection", "clipboard"], "xclip"),  # X11
    (["xsel", "--clipboard", "--input"], "xsel"),  # X11 alt
    (["wl-copy"], "wl-copy"),  # Wayland
]


def _detect_image_mime(payload: bytes) -> Optional[str]:
    """Return a supported image MIME type from a small magic-byte probe."""
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if payload.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(payload) >= 12 and payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return "image/webp"
    return None


def _clipboard_image_commands() -> list[tuple[list[str], str, str]]:
    """Return platform-specific image readers.

    The mode is either ``base64`` for helpers that cannot safely write binary
    stdout through their scripting bridge, or ``raw:<mime>`` for native
    clipboard utilities.
    """
    if sys.platform == "darwin":
        return [
            (
                [
                    "osascript",
                    "-l",
                    "JavaScript",
                    "-e",
                    _MACOS_READ_IMAGE_SCRIPT,
                ],
                "base64",
                "osascript",
            )
        ]
    if sys.platform == "win32":
        return [
            (
                [
                    "powershell",
                    "-NoProfile",
                    "-NonInteractive",
                    "-STA",
                    "-Command",
                    _WINDOWS_READ_IMAGE_SCRIPT,
                ],
                "base64",
                "powershell",
            )
        ]

    commands: list[tuple[list[str], str, str]] = []
    for mime in ("image/png", "image/jpeg", "image/webp"):
        commands.extend(
            [
                (
                    ["xclip", "-selection", "clipboard", "-target", mime, "-o"],
                    f"raw:{mime}",
                    f"xclip/{mime}",
                ),
                (
                    ["wl-paste", "--no-newline", "--type", mime],
                    f"raw:{mime}",
                    f"wl-paste/{mime}",
                ),
            ]
        )
    return commands


def read_image_from_clipboard(
    max_bytes: int = MAX_CLIPBOARD_IMAGE_BYTES,
) -> Optional[tuple[str, bytes]]:
    """Read one supported image from the system clipboard.

    Returns ``(media_type, bytes)`` or ``None`` when the clipboard contains no
    image or the platform has no supported reader. Commands are fixed, do not
    invoke a shell, and are bounded by a timeout and byte limit.
    """
    max_encoded_bytes = (max_bytes * 4) // 3 + 1024
    for command, mode, name in _clipboard_image_commands():
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                check=False,
                timeout=CLIPBOARD_READ_TIMEOUT_SECONDS,
            )
        except FileNotFoundError:
            continue
        except (OSError, subprocess.SubprocessError) as exc:
            logger.debug("Clipboard image reader %s failed: %s", name, exc)
            continue

        if result.returncode != 0 or not result.stdout:
            continue
        if len(result.stdout) > max_encoded_bytes and mode == "base64":
            logger.warning("Clipboard image from %s exceeds the byte limit", name)
            continue

        if mode == "base64":
            try:
                encoded = b"".join(result.stdout.split())
                payload = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError):
                logger.debug("Clipboard image reader %s returned invalid base64", name)
                continue
        else:
            payload = result.stdout

        if not payload or len(payload) > max_bytes:
            logger.warning("Clipboard image from %s exceeds the byte limit", name)
            continue

        media_type = _detect_image_mime(payload)
        if media_type:
            logger.info("Read %s clipboard image via %s", media_type, name)
            return media_type, payload

    return None


def read_text_from_clipboard(max_chars: int = 100_000) -> Optional[str]:
    """Read text from the system clipboard for the Ctrl+V fallback."""
    commands = [
        (["pbpaste"], "pbpaste"),
        (["xclip", "-selection", "clipboard", "-o"], "xclip"),
        (["xsel", "--clipboard", "--output"], "xsel"),
        (["wl-paste", "--no-newline"], "wl-paste"),
    ]
    for command, name in commands:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                check=False,
                timeout=CLIPBOARD_READ_TIMEOUT_SECONDS,
            )
        except FileNotFoundError:
            continue
        except (OSError, subprocess.SubprocessError) as exc:
            logger.debug("Clipboard text reader %s failed: %s", name, exc)
            continue
        if result.returncode != 0 or not result.stdout:
            continue
        try:
            text = result.stdout.decode("utf-8")
        except UnicodeDecodeError:
            continue
        return text[:max_chars]
    return None


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
