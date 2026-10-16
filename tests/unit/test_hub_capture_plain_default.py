"""Hub capture defaults to plain (ANSI-stripped) output; raw is opt-in.

Regression tests for the capture format default: _handle_capture_command
used to call _capture_peer_output(peer, lines) with no format, so both the
CLI (/hub capture) and the hub_capture agent tool returned raw terminal
rendering whose ANSI escape codes inflated agent token usage. Plain —
escape-free text from plugins.hub.capture — is now the default; raw
terminal output requires an explicit request (--raw / format="terminal").
"""

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.append(str(Path(__file__).parent.parent.parent))

from plugins.hub.crystal_store import CrystalStore
from plugins.hub.messenger import AgentMessenger
from plugins.hub.plugin import HubPlugin


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_plugin(identity_name="sapphire"):
    plugin = object.__new__(HubPlugin)
    mock_identity = MagicMock()
    mock_identity.identity = identity_name
    mock_identity.agent_id = f"{identity_name}-agent-001"
    plugin._identity = mock_identity
    plugin._presence = None
    plugin._dns_registry = None
    plugin._task_ledger = None
    plugin._change_feed = None
    plugin._vault = MagicMock()
    plugin._vault._vault_dir = Path(tempfile.mkdtemp())
    plugin._vault.global_vault_dir = Path(tempfile.mkdtemp())
    plugin._vault.append_stream = MagicMock()
    plugin._session_state_mgr = MagicMock()
    plugin._crystal_store = CrystalStore(plugin._vault._vault_dir)
    plugin._global_crystal_store = CrystalStore(plugin._vault.global_vault_dir)
    plugin._get_orchestrator = MagicMock(return_value=MagicMock())
    return plugin


def _peer(identity="lapis"):
    return SimpleNamespace(identity=identity, socket_path="/tmp/capture-test.sock")


def _patch_transport(plain_payload=None, raw_lines=None):
    """Patch both capture transports; records which one the code path used."""
    plain = AsyncMock(return_value=(plain_payload, None))
    raw = AsyncMock(return_value=(raw_lines, None))
    ctx = SimpleNamespace(plain=plain, raw=raw)
    return (
        patch.object(AgentMessenger, "request_capture", plain),
        patch.object(AgentMessenger, "request_output_diagnostic", raw),
        ctx,
    )


class TestCaptureCommandPlainDefault(unittest.TestCase):
    def test_capture_command_defaults_to_plain_format(self):
        plugin = _make_plugin()
        plugin._get_peer_by_identity = MagicMock(return_value=_peer())
        plain_patch, raw_patch, ctx = _patch_transport(
            plain_payload={
                "format": "plain",
                "order": "oldest-first",
                "text": "clean output",
                "truncated": False,
                "next_cursor": None,
            },
            raw_lines=["\x1b[31mraw\x1b[0m"],
        )
        with plain_patch, raw_patch:
            result = _run(plugin._handle_capture_command("lapis 50"))

        self.assertEqual(result, "clean output")
        ctx.raw.assert_not_awaited()
        ctx.plain.assert_awaited_once()
        self.assertEqual(ctx.plain.await_args.kwargs.get("capture_format"), "plain")
        self.assertEqual(ctx.plain.await_args.kwargs.get("lines"), 50)

    def test_capture_command_raw_flag_uses_terminal_diagnostics(self):
        plugin = _make_plugin()
        plugin._get_peer_by_identity = MagicMock(return_value=_peer())
        plain_patch, raw_patch, ctx = _patch_transport(
            plain_payload={"text": "should not be used"},
            raw_lines=["\x1b[31mred\x1b[0m line"],
        )
        with plain_patch, raw_patch:
            result = _run(plugin._handle_capture_command("lapis 50 --raw"))

        ctx.plain.assert_not_awaited()
        ctx.raw.assert_awaited_once()
        self.assertEqual(result, "[lapis]\n\x1b[31mred\x1b[0m line")

    def test_raw_flag_is_filtered_from_any_argument_position(self):
        plugin = _make_plugin()
        seen = {}

        async def _fake(peer, lines, *, format="terminal"):
            seen["target"] = peer.identity
            seen["lines"] = lines
            seen["format"] = format
            return "ok"

        plugin._get_peer_by_identity = MagicMock(return_value=_peer())
        plugin._capture_peer_output = _fake
        result = _run(plugin._handle_capture_command("--raw lapis 30"))
        self.assertEqual(result, "ok")
        self.assertEqual(seen, {"target": "lapis", "lines": 30, "format": "terminal"})

    def test_usage_string_documents_the_raw_flag(self):
        plugin = _make_plugin()
        result = _run(plugin._handle_capture_command(""))
        self.assertEqual(
            result, "usage: /hub capture <identity|name|all> [lines] [--raw]"
        )


class TestPlainEnvelopeRendering(unittest.TestCase):
    def test_envelope_text_is_returned_verbatim(self):
        result = HubPlugin._render_plain_capture(
            "lapis",
            {"format": "plain", "text": "stripped text", "truncated": False},
        )
        self.assertEqual(result, "stripped text")

    def test_truncated_envelope_appends_cursor_hint(self):
        result = HubPlugin._render_plain_capture(
            "lapis",
            {"text": "page one", "truncated": True, "next_cursor": "42"},
        )
        self.assertEqual(result, "page one\n... (truncated, next_cursor=42)")

    def test_legacy_bare_lines_are_normalized(self):
        result = HubPlugin._render_plain_capture(
            "lapis", {"lines": ["\x1b[31mred\x1b[0m", "plain"]}
        )
        self.assertEqual(result, "red\nplain")

    def test_empty_envelope_reports_no_output(self):
        result = HubPlugin._render_plain_capture(
            "lapis", {"text": "", "truncated": False, "next_cursor": None}
        )
        self.assertEqual(result, "no recent output from 'lapis'")

    def test_non_dict_payload_is_stringified(self):
        self.assertEqual(HubPlugin._render_plain_capture("lapis", "legacy"), "legacy")
        self.assertEqual(
            HubPlugin._render_plain_capture("lapis", None),
            "no recent output from 'lapis'",
        )


class TestHubCaptureToolPlainDefault(unittest.TestCase):
    def test_tool_defaults_to_plain_transport(self):
        plugin = _make_plugin()
        plugin._get_peer_by_identity = MagicMock(return_value=_peer())
        plain_patch, raw_patch, ctx = _patch_transport(
            plain_payload={"format": "plain", "text": "clean", "truncated": False},
            raw_lines=["\x1b[31mraw\x1b[0m"],
        )
        with plain_patch, raw_patch:
            result = _run(
                plugin._handle_hub_capture_tool(
                    {
                        "id": "capture-native",
                        "type": "hub_capture",
                        "name": "hub_capture",
                        "input": {"name": "lapis", "lines": 80},
                    }
                )
            )
        self.assertTrue(result.success)
        self.assertEqual(result.output, "clean")
        ctx.raw.assert_not_awaited()
        self.assertEqual(ctx.plain.await_args.kwargs.get("capture_format"), "plain")

    def test_tool_format_terminal_requests_raw(self):
        plugin = _make_plugin()
        plugin._get_peer_by_identity = MagicMock(return_value=_peer())
        plain_patch, raw_patch, ctx = _patch_transport(
            plain_payload={"text": "plain"},
            raw_lines=["\x1b[31mraw\x1b[0m"],
        )
        with plain_patch, raw_patch:
            result = _run(
                plugin._handle_hub_capture_tool(
                    {
                        "id": "capture-raw",
                        "input": {"name": "lapis", "format": "terminal"},
                    }
                )
            )
        self.assertTrue(result.success)
        self.assertIn("\x1b[31mraw\x1b[0m", result.output)
        ctx.plain.assert_not_awaited()

    def test_tool_raw_bool_requests_raw(self):
        plugin = _make_plugin()
        seen = {}

        async def _fake_handle_capture_command(arg):
            seen["args"] = arg
            return "raw text"

        plugin._handle_capture_command = _fake_handle_capture_command
        result = _run(
            plugin._handle_hub_capture_tool(
                {"id": "capture-bool", "input": {"name": "lapis", "raw": True}}
            )
        )
        self.assertTrue(result.success)
        self.assertEqual(seen["args"], "lapis 50 --raw")

    def test_tool_legacy_xml_format_attribute_requests_raw(self):
        plugin = _make_plugin()
        seen = {}

        async def _fake_handle_capture_command(arg):
            seen["args"] = arg
            return "raw text"

        plugin._handle_capture_command = _fake_handle_capture_command
        result = _run(
            plugin._handle_hub_capture_tool(
                {
                    "id": "capture-xml",
                    "cap_name": "sapphire",
                    "cap_lines": "25",
                    "cap_format": "terminal",
                }
            )
        )
        self.assertTrue(result.success)
        self.assertEqual(seen["args"], "sapphire 25 --raw")

    def test_tool_plain_format_is_the_quiet_default(self):
        plugin = _make_plugin()
        seen = {}

        async def _fake_handle_capture_command(arg):
            seen["args"] = arg
            return "plain text"

        plugin._handle_capture_command = _fake_handle_capture_command
        result = _run(
            plugin._handle_hub_capture_tool(
                {"id": "capture-plain", "input": {"name": "lapis", "format": "plain"}}
            )
        )
        self.assertTrue(result.success)
        self.assertEqual(seen["args"], "lapis 50")


if __name__ == "__main__":
    unittest.main()
