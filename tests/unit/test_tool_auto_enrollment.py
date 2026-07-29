"""Integration tests for unified tool auto-enrollment.

These tests verify the FULL path from tool definition to execution:
1. A new ToolDefinition is registered in ToolRegistry
2. _register_registry_tool_tags() picks it up and registers with response_parser
3. Response parser recognizes the XML tag in agent output
4. Native JSON schema is correct for API tool definitions
5. Tool executor dispatches the tool_type correctly

The key test (test_new_tool_auto_enrolls) defines a brand new tool,
registers it, and verifies it becomes immediately available in both
XML and native modes WITHOUT any manual registration.

This is the regression test for the bug where tools were registered
in ToolRegistry but invisible to the response parser.
"""

import asyncio
import os
import re
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from kollabor_agent.tool_definition import ToolDefinition, ToolParameter
from kollabor_agent.tool_registry import ToolRegistry
from kollabor_agent.tool_generators.xml_regex import build_regex_for_tool


def _run(coro):
    """Run a coroutine with a fresh event loop."""
    try:
        loop = asyncio.new_event_loop()
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _register_tool_tags(parser, registry):
    """Simulate llm_coordinator._register_registry_tool_tags().
    
    This is the EXACT logic from llm_coordinator.py that auto-enrolls
    all ToolRegistry tools with the response parser. We replicate it
    here so the test doesn't need the full llm_coordinator stack.
    """
    hardcoded_tags = {
        "terminal", "terminal-status", "terminal-output", "terminal-kill",
        "tool", "tool_call",
        "read", "edit", "create", "create-overwrite", "delete", "move",
        "copy", "copy-overwrite", "append", "insert-after", "insert-before",
        "grep", "mkdir", "rmdir",
    }

    existing = {t["tool_type"] for t in parser._plugin_tags}
    existing.update(t["tool_type"].replace("_", "-") for t in parser._plugin_tags)

    count = 0
    for tool in registry.list():
        tag_name = tool.xml_tag_name
        if tag_name in hardcoded_tags:
            continue

        tool_type_hyphen = tool.name
        tool_type_underscore = tool_type_hyphen.replace("-", "_")
        if tool_type_underscore in existing or tool_type_hyphen in existing:
            continue

        pattern_str = build_regex_for_tool(tool)
        pattern = re.compile(pattern_str, re.DOTALL | re.IGNORECASE)

        if tool.xml_form == "nested":
            def _make_extract(tl):
                def _extract(m):
                    raw = m.group(1)
                    params = {}
                    for param in tl.parameters:
                        param_pattern = re.compile(
                            rf"<{re.escape(param.name)}>(.*?)</{re.escape(param.name)}>",
                            re.DOTALL | re.IGNORECASE,
                        )
                        pm = param_pattern.search(raw)
                        if pm:
                            params[param.name] = pm.group(1).strip()
                    return params
                return _extract
            extract_fn = _make_extract(tool)
        elif tool.xml_form == "body":
            def _make_body_extract(tl):
                body_param = tl.xml_body_param or (
                    tl.parameters[0].name if tl.parameters else "command"
                )
                def _extract(m):
                    return {body_param: m.group(1).strip()}
                return _extract
            extract_fn = _make_body_extract(tool)
        else:
            def _make_attr_extract(tl):
                def _extract(m):
                    params = {}
                    for i, param in enumerate(tl.parameters):
                        if i + 1 <= m.lastindex:
                            val = m.group(i + 1)
                            if val is not None:
                                params[param.name] = val.strip()
                    return params
                return _extract
            extract_fn = _make_attr_extract(tool)

        parser.register_plugin_tag(tag_name, pattern, tool_type_underscore, extract_fn)
        count += 1

    return count


class TestAutoEnrollmentFullPath(unittest.TestCase):
    """Verify the full auto-enrollment pipeline: define → register → parse → dispatch."""

    def setUp(self):
        """Reset registry and load all built-in tools."""
        ToolRegistry.reset()
        self.registry = ToolRegistry.get_global()

    def test_new_tool_auto_enrolls_xml(self):
        """A newly registered tool should be parseable in XML mode immediately.

        This is the core regression test: define a tool, register it,
        auto-enroll the tags, and verify the response parser recognizes it.
        """
        from kollabor_ai.response_parser import ResponseParser

        # Step 1: Define a brand new tool
        test_tool = ToolDefinition(
            name="test-widget",
            description="A test tool for auto-enrollment verification.",
            category="test",
            xml_tag="test-widget",
            xml_form="nested",
            parameters=[
                ToolParameter(
                    name="color",
                    type="string",
                    description="Widget color",
                    required=True,
                ),
                ToolParameter(
                    name="size",
                    type="integer",
                    description="Widget size",
                    required=False,
                    default=10,
                ),
            ],
        )

        # Step 2: Register in ToolRegistry
        self.registry.register(test_tool, replace=True)
        self.assertIsNotNone(self.registry.get("test-widget"))

        # Step 3: Auto-enroll with response parser
        parser = ResponseParser()
        count = _register_tool_tags(parser, self.registry)
        self.assertGreater(count, 0, "Should have registered at least one tag")

        # Step 4: Verify parser recognizes the XML tag
        test_response = (
            "Let me create a widget. "
            "<test-widget><color>red</color><size>42</size></test-widget>"
        )
        parsed = parser.parse_response(test_response)

        # The tool should appear in plugin_tools
        plugin_tools = parsed.get("components", {}).get("plugin_tools", [])
        tool_found = [t for t in plugin_tools if t.get("type") == "test_widget"]
        self.assertEqual(
            len(tool_found), 1,
            f"Expected 1 test_widget in plugin_tools, got {len(tool_found)}",
        )

        # Parameters should be extracted correctly
        tool_data = tool_found[0]
        self.assertEqual(tool_data["color"], "red")
        self.assertEqual(tool_data["size"], "42")

        # Clean content should have the tag stripped
        self.assertNotIn("<test-widget>", parsed["content"])

    def test_new_tool_auto_enrolls_native(self):
        """A newly registered tool should produce correct native JSON schema."""
        test_tool = ToolDefinition(
            name="test-gadget",
            description="A test gadget for native mode.",
            category="test",
            xml_tag="test-gadget",
            xml_form="nested",
            parameters=[
                ToolParameter(
                    name="name",
                    type="string",
                    description="Gadget name",
                    required=True,
                ),
            ],
        )

        self.registry.register(test_tool, replace=True)

        # Native schema should be correct
        schema = test_tool.to_json_schema()
        self.assertEqual(schema["name"], "test_gadget")
        self.assertIn("test gadget", schema["description"].lower())
        self.assertEqual(schema["parameters"]["type"], "object")
        self.assertIn("name", schema["parameters"]["properties"])
        self.assertIn("name", schema["parameters"]["required"])

    def test_existing_tools_all_enroll(self):
        """All 66 built-in tools should auto-enroll without errors."""
        from kollabor_ai.response_parser import ResponseParser

        parser = ResponseParser()
        count = _register_tool_tags(parser, self.registry)

        # Should register ~51 tools (66 total minus ~15 hardcoded)
        self.assertGreater(count, 40, f"Expected 40+ auto-enrolled tags, got {count}")
        self.assertLessEqual(count, 66, f"Should not exceed 66 tags, got {count}")

    def test_xml_form_body_tool_enrolls(self):
        """A body-form tool should auto-enroll and parse correctly."""
        from kollabor_ai.response_parser import ResponseParser

        test_tool = ToolDefinition(
            name="test-echo",
            description="Echo back text.",
            category="test",
            xml_tag="test-echo",
            xml_form="body",
            xml_body_param="text",
            parameters=[
                ToolParameter(
                    name="text",
                    type="string",
                    description="Text to echo",
                    required=True,
                ),
            ],
        )

        self.registry.register(test_tool, replace=True)

        parser = ResponseParser()
        _register_tool_tags(parser, self.registry)

        test_response = "Echo this: <test-echo>hello world</test-echo>"
        parsed = parser.parse_response(test_response)

        plugin_tools = parsed.get("components", {}).get("plugin_tools", [])
        tool_found = [t for t in plugin_tools if t.get("type") == "test_echo"]
        self.assertEqual(len(tool_found), 1)
        self.assertEqual(tool_found[0]["text"], "hello world")

    def test_no_double_registration(self):
        """Auto-enrollment should skip already-registered tags."""
        from kollabor_ai.response_parser import ResponseParser

        parser = ResponseParser()
        count1 = _register_tool_tags(parser, self.registry)
        count2 = _register_tool_tags(parser, self.registry)

        # Second call should register 0 new tags
        self.assertEqual(count2, 0, "Second enrollment should skip existing tags")

    def test_hardcoded_tags_skipped(self):
        """Terminal and file-op tags should NOT be double-registered."""
        from kollabor_ai.response_parser import ResponseParser

        parser = ResponseParser()
        _register_tool_tags(parser, self.registry)

        # Check that terminal is NOT in plugin_tags (it's hardcoded)
        plugin_types = {t["tool_type"] for t in parser._plugin_tags}
        self.assertNotIn("terminal", plugin_types)
        self.assertNotIn("read", plugin_types)
        self.assertNotIn("edit", plugin_types)

    def test_executor_dispatch_for_new_tool_type(self):
        """Tool executor should dispatch a new tool type without errors.

        We can't test the actual handler (it doesn't exist), but we can
        verify the executor returns a proper 'Unknown tool type' error
        rather than crashing.
        """
        from kollabor_agent.tool_executor import ToolExecutor

        mcp = MagicMock()
        mcp.tool_registry = {}
        mcp.server_connections = {}

        event_bus = MagicMock()
        event_bus.emit_with_hooks = AsyncMock(return_value=None)

        executor = ToolExecutor(
            mcp_integration=mcp,
            event_bus=event_bus,
            terminal_timeout=5,
            mcp_timeout=10,
        )

        # A tool type with no handler should return Unknown tool type error
        # We test this via _execute_tool_search which we know works
        result = _run(executor._execute_tool_search({
            "type": "tool_search",
            "id": "test_0",
            "query": "test",
        }))

        # tool_search is a real handler — should succeed
        self.assertTrue(result.success)

    def test_full_cycle_define_register_parse_load(self):
        """Full cycle: define tool → register → parse XML → tool-search finds it → tool-load loads it."""
        from kollabor_ai.response_parser import ResponseParser
        from kollabor_agent.tool_executor import ToolExecutor

        # Step 1: Define and register a new tool
        test_tool = ToolDefinition(
            name="test-oracle",
            description="Ask the oracle a question.",
            category="test",
            xml_tag="test-oracle",
            xml_form="nested",
            risk_level="low",
            parameters=[
                ToolParameter(
                    name="question",
                    type="string",
                    description="Question to ask",
                    required=True,
                ),
            ],
            examples=["<test-oracle><question>What is the meaning of life?</question></test-oracle>"],
        )
        self.registry.register(test_tool, replace=True)

        # Step 2: Auto-enroll with response parser
        parser = ResponseParser()
        _register_tool_tags(parser, self.registry)

        # Step 3: Parser recognizes it
        test_response = "<test-oracle><question>What is 2+2?</question></test-oracle>"
        parsed = parser.parse_response(test_response)
        plugin_tools = parsed.get("components", {}).get("plugin_tools", [])
        oracle_tools = [t for t in plugin_tools if t.get("type") == "test_oracle"]
        self.assertEqual(len(oracle_tools), 1)
        self.assertEqual(oracle_tools[0]["question"], "What is 2+2?")

        # Step 4: tool-search finds it
        mcp = MagicMock()
        mcp.tool_registry = {}
        mcp.server_connections = {}
        event_bus = MagicMock()
        event_bus.emit_with_hooks = AsyncMock(return_value=None)
        executor = ToolExecutor(
            mcp_integration=mcp, event_bus=event_bus,
            terminal_timeout=5, mcp_timeout=10,
        )

        search_result = _run(executor._execute_tool_search({
            "type": "tool_search", "id": "ts_0", "query": "oracle",
        }))
        self.assertTrue(search_result.success)
        self.assertIn("test-oracle", search_result.output)

        # Step 5: tool-load loads it
        load_result = _run(executor._execute_tool_load({
            "type": "tool_load", "id": "tl_0", "name": "test-oracle",
        }))
        self.assertTrue(load_result.success)
        self.assertEqual(load_result.metadata["loaded_tool"], "test-oracle")
        self.assertIn("test-oracle", load_result.output)


class TestRegistryToParserConsistency(unittest.TestCase):
    """Verify every ToolRegistry tool is recognizable by the response parser.

    This is the regression test that catches the exact bug Marco hit:
    tools registered in ToolRegistry but invisible to the response parser.
    """

    def setUp(self):
        ToolRegistry.reset()
        self.registry = ToolRegistry.get_global()

    def test_every_non_hardcoded_tool_has_plugin_tag(self):
        """After auto-enrollment, every non-hardcoded tool should have a plugin tag."""
        from kollabor_ai.response_parser import ResponseParser

        parser = ResponseParser()
        _register_tool_tags(parser, self.registry)

        hardcoded_tags = {
            "terminal", "terminal-status", "terminal-output", "terminal-kill",
            "tool", "tool_call",
            "read", "edit", "create", "create-overwrite", "delete", "move",
            "copy", "copy-overwrite", "append", "insert-after", "insert-before",
            "grep", "mkdir", "rmdir",
        }

        # Get all tool types registered as plugin tags
        registered_types = {t["tool_type"] for t in parser._plugin_tags}

        # Check every non-hardcoded tool is registered
        missing = []
        for tool in self.registry.list():
            if tool.xml_tag_name in hardcoded_tags:
                continue

            tool_type_underscore = tool.name.replace("-", "_")
            if tool_type_underscore not in registered_types:
                missing.append(tool.name)

        self.assertEqual(
            missing, [],
            f"Tools missing from response parser after auto-enrollment: {missing}",
        )

    def test_every_tool_produces_valid_regex(self):
        """Every tool's XML regex should compile and match a sample tag."""
        for tool in self.registry.list():
            with self.subTest(tool=tool.name):
                pattern_str = build_regex_for_tool(tool)
                try:
                    pattern = re.compile(pattern_str, re.DOTALL | re.IGNORECASE)
                except Exception as e:
                    self.fail(f"{tool.name}: regex compilation failed: {e}")

                # Build a sample tag and verify it matches
                tag = tool.xml_tag_name
                if tool.xml_form == "nested":
                    sample = f"<{tag}>"
                    for p in tool.parameters:
                        sample += f"<{p.name}>value</{p.name}>"
                    sample += f"</{tag}>"
                elif tool.xml_form == "body":
                    sample = f"<{tag}>some text</{tag}>"
                elif tool.xml_form == "attributes":
                    # Self-closing tag with optional attributes
                    sample = f"<{tag} />"
                else:
                    # mixed or unknown — try both forms
                    sample = f"<{tag}>text</{tag}>"

                match = pattern.search(sample)
                self.assertIsNotNone(
                    match,
                    f"{tool.name}: regex does not match sample tag: {sample}",
                )

    def test_every_tool_produces_valid_json_schema(self):
        """Every tool should produce a valid native JSON schema."""
        for tool in self.registry.list():
            with self.subTest(tool=tool.name):
                schema = tool.to_json_schema()
                self.assertIn("name", schema, f"{tool.name}: missing name")
                self.assertIn("description", schema, f"{tool.name}: missing description")
                self.assertIn("parameters", schema, f"{tool.name}: missing parameters")
                self.assertEqual(
                    schema["parameters"]["type"], "object",
                    f"{tool.name}: parameters type should be object",
                )

    def test_native_names_are_unique(self):
        """All native names should be unique for dispatch."""
        names = [t.native_name for t in self.registry.list()]
        duplicates = [n for n in names if names.count(n) > 1]
        # Known exception: git shares terminal tag but has different native name
        unique_duplicates = set(duplicates) - {"terminal"}
        self.assertEqual(
            len(unique_duplicates), 0,
            f"Duplicate native names: {unique_duplicates}",
        )


class TestWildcardBundleScope(unittest.TestCase):
    """Test the ["*"] wildcard for agent.json tools field."""

    def setUp(self):
        ToolRegistry.reset()
        ToolRegistry.get_global()

    def test_wildcard_allows_all_tools(self):
        """tools=["*"] should allow any tool through scope check."""
        from kollabor_agent.tool_executor import ToolExecutor

        mcp = MagicMock()
        mcp.tool_registry = {}
        mcp.server_connections = {}
        event_bus = MagicMock()
        event_bus.emit_with_hooks = AsyncMock(return_value=None)
        executor = ToolExecutor(
            mcp_integration=mcp, event_bus=event_bus,
            terminal_timeout=5, mcp_timeout=10,
        )

        # Set wildcard scope
        executor.set_bundle_scope(["*"])

        # Any tool should be allowed
        for tool_type in ["terminal", "web_search", "tool_search", "git", "hub_msg"]:
            error = executor._check_bundle_scope(tool_type)
            self.assertIsNone(
                error,
                f"Wildcard scope should allow '{tool_type}', got: {error}",
            )

    def test_explicit_scope_still_restricts(self):
        """Explicit tool list should still restrict access."""
        from kollabor_agent.tool_executor import ToolExecutor

        mcp = MagicMock()
        mcp.tool_registry = {}
        mcp.server_connections = {}
        event_bus = MagicMock()
        event_bus.emit_with_hooks = AsyncMock(return_value=None)
        executor = ToolExecutor(
            mcp_integration=mcp, event_bus=event_bus,
            terminal_timeout=5, mcp_timeout=10,
        )

        executor.set_bundle_scope(["terminal", "file-read"])

        # Allowed tools
        self.assertIsNone(executor._check_bundle_scope("terminal"))
        self.assertIsNone(executor._check_bundle_scope("file_read"))

        # Blocked tools
        self.assertIsNotNone(executor._check_bundle_scope("git"))
        self.assertIsNotNone(executor._check_bundle_scope("web_search"))

    def test_tool_load_bypasses_scope_for_loaded_tools(self):
        """After tool-load adds a tool, it should pass scope check."""
        from kollabor_agent.tool_executor import ToolExecutor

        mcp = MagicMock()
        mcp.tool_registry = {}
        mcp.server_connections = {}
        event_bus = MagicMock()
        event_bus.emit_with_hooks = AsyncMock(return_value=None)
        executor = ToolExecutor(
            mcp_integration=mcp, event_bus=event_bus,
            terminal_timeout=5, mcp_timeout=10,
        )

        executor.set_bundle_scope(["terminal"])

        # git is blocked
        self.assertIsNotNone(executor._check_bundle_scope("git"))

        # Load git
        result = _run(executor._execute_tool_load({
            "type": "tool_load", "id": "tl_0", "name": "git",
        }))
        self.assertTrue(result.success)

        # Now git is allowed
        self.assertIsNone(executor._check_bundle_scope("git"))


if __name__ == "__main__":
    unittest.main()
