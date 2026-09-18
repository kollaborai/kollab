"""Tests for the cross-platform clipboard readers."""

import base64
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from kollabor_tui import clipboard

PNG_BYTES = b"\x89PNG\r\n\x1a\nnot-a-real-but-signature-valid-png"
REAL_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1Pe"
    "AAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
)


@pytest.mark.skipif(sys.platform != "darwin", reason="Requires macOS AppKit")
@pytest.mark.parametrize("representation", ["png", "tiff", "both", "text"])
def test_macos_image_reader_with_real_isolated_pasteboard(representation):
    # Exercise osascript and its actual stdout contract without changing the
    # user's general clipboard. The named pasteboard is released afterwards.
    setup = f"""
const isolatedPasteboard = $.NSPasteboard.pasteboardWithUniqueName;
const png = $.NSData.alloc.initWithBase64EncodedStringOptions("{REAL_PNG_BASE64}", 0);
"""
    if representation in ("png", "both"):
        setup += "isolatedPasteboard.setDataForType(png, $.NSPasteboardTypePNG);\n"
    if representation in ("tiff", "both"):
        setup += """
const tiff = $.NSBitmapImageRep.imageRepWithData(png).TIFFRepresentation;
isolatedPasteboard.setDataForType(tiff, $.NSPasteboardTypeTIFF);
"""
    if representation == "text":
        setup += 'isolatedPasteboard.setStringForType("text only", $.NSPasteboardTypeString);\n'

    # Keep the production reader and subprocess untouched; only substitute its
    # clipboard selection with this private fixture and guaranteed cleanup.
    invocation = "readClipboardImage($.NSPasteboard.generalPasteboard);"
    assert invocation in clipboard._MACOS_READ_IMAGE_SCRIPT
    fixture_invocation = f"""
(function () {{
  {setup}
  try {{ return readClipboardImage(isolatedPasteboard); }}
  finally {{ isolatedPasteboard.releaseGlobally; }}
}})();
"""
    script = clipboard._MACOS_READ_IMAGE_SCRIPT.replace(invocation, fixture_invocation)
    with patch.object(clipboard, "_MACOS_READ_IMAGE_SCRIPT", script):
        result = clipboard.read_image_from_clipboard()

    if representation == "text":
        assert result is None
    else:
        assert result is not None
        media_type, payload = result
        assert media_type == "image/png"
        assert payload.startswith(b"\x89PNG\r\n\x1a\n")
        if representation in ("png", "both"):
            assert payload == base64.b64decode(REAL_PNG_BASE64)


def test_read_image_from_clipboard_decodes_bounded_base64_output():
    encoded = base64.b64encode(PNG_BYTES)
    command = (["test-clipboard-reader"], "base64", "test")

    with (
        patch.object(clipboard, "_clipboard_image_commands", return_value=[command]),
        patch.object(
            clipboard.subprocess,
            "run",
            return_value=SimpleNamespace(returncode=0, stdout=encoded),
        ),
    ):
        assert clipboard.read_image_from_clipboard() == ("image/png", PNG_BYTES)


def test_read_image_from_clipboard_rejects_payload_without_supported_signature():
    command = (["test-clipboard-reader"], "base64", "test")
    encoded = base64.b64encode(b"not an image")

    with (
        patch.object(clipboard, "_clipboard_image_commands", return_value=[command]),
        patch.object(
            clipboard.subprocess,
            "run",
            return_value=SimpleNamespace(returncode=0, stdout=encoded),
        ),
    ):
        assert clipboard.read_image_from_clipboard() is None


def test_read_text_from_clipboard_uses_text_fallback():
    with patch.object(
        clipboard.subprocess,
        "run",
        return_value=SimpleNamespace(returncode=0, stdout=b"clipboard text"),
    ) as run:
        assert clipboard.read_text_from_clipboard() == "clipboard text"

    run.assert_called_once()
