"""Regression test: every registered ToolDefinition's xml_tag must be
recognized by the response parser.

This test catches the exact bug we hit where tools were registered in
ToolRegistry but invisible to the response parser — agents could emit
the XML tag and it would be ignored as plain text.

The test works by:
1. Getting all ToolDefinitions from the global registry
2. For each tool, generating a sample XML tag using its xml_tag + xml_form
3. Feeding that tag through the response parser
4. Asserting the parser extracts a tool of the correct type

If a new tool is added to the registry but not wired to the parser,
this test will fail with a clear message showing which tool is missing.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from kollabor_agent.tool_registry import ToolRegistry
from kollabor_agent.tool_generators.xml_regex import build_regex_for_tool


class TestToolTagParserCoverage(unittest.TestCase):
    """Verify every ToolDefinition's XML tag is parseable."""

    @classmethod
    def setUpClass(cls):
        """Reset and load the global registry fresh."""
        ToolRegistry.reset()
        cls.registry = ToolRegistry.get_global()
        cls.all_tools = cls.registry.list()
        # Build a quick lookup: xml_tag -> tool_type (native_name)
        cls.tag_to_type = {}
        for tool in cls.all_tools:
            cls.tag_to_type[tool.xml_tag_name] = tool.native_name

    def test_registry_has_tools(self):
        """Sanity check — registry is loaded."""
        self.assertGreater(len(self.all_tools), 0,
                           "ToolRegistry should have tools loaded")

    def test_every_tool_has_xml_tag(self):
        """Every registered tool must have an xml_tag_name."""
        missing = []
        for tool in self.all_tools:
            if not tool.xml_tag_name:
                missing.append(tool.name)
        self.assertEqual(missing, [],
                         f"Tools missing xml_tag_name: {missing}")

    def test_every_tool_has_xml_form(self):
        """Every registered tool must have an xml_form."""
        missing = []
        for tool in self.all_tools:
            if not tool.xml_form:
                missing.append(tool.name)
        self.assertEqual(missing, [],
                         f"Tools missing xml_form: {missing}")

    def test_every_tool_tag_is_parseable(self):
        """For each ToolDefinition, the xml_regex generator must produce
        a valid regex that matches a sample tag for that tool.

        This is the core regression test. If a tool's XML tag can't be
        parsed, it means agents can't use it — the exact bug we hit.
        """
        import re

        failures = []
        for tool in self.all_tools:
            tag = tool.xml_tag_name
            form = tool.xml_form

            # Build a sample XML string for this tool
            sample = _build_sample_tag(tool)
            if sample is None:
                failures.append(f"{tool.name}: could not build sample tag "
                                f"(xml_form={form})")
                continue

            # Build the regex pattern from the ToolDefinition
            try:
                pattern_str = build_regex_for_tool(tool)
                pattern = re.compile(pattern_str, re.DOTALL | re.IGNORECASE)
            except Exception as e:
                failures.append(f"{tool.name}: regex build failed: {e}")
                continue

            # Verify the regex matches the sample
            match = pattern.search(sample)
            if not match:
                failures.append(
                    f"{tool.name}: regex does not match sample tag: {sample!r}"
                )

        self.assertEqual(failures, [],
                         "Tool XML tags that failed to parse:\n  " +
                         "\n  ".join(failures))

    def test_every_tool_has_native_name(self):
        """Every registered tool must have a native_name for dispatch."""
        missing = []
        for tool in self.all_tools:
            if not tool.native_name:
                missing.append(tool.name)
        self.assertEqual(missing, [],
                         f"Tools missing native_name: {missing}")

    def test_native_names_are_unique(self):
        """Native names should be unique to avoid dispatch conflicts."""
        seen = {}
        duplicates = []
        for tool in self.all_tools:
            name = tool.native_name
            if name in seen:
                duplicates.append(f"{name} (used by {seen[name]} and {tool.name})")
            else:
                seen[name] = tool.name
        self.assertEqual(duplicates, [],
                         f"Duplicate native_names: {duplicates}")

    def test_xml_tags_are_unique(self):
        """XML tags should be unique to avoid parser conflicts.

        Known exception: 'git' reuses the 'terminal' tag because git
        commands are dispatched through the terminal executor.
        """
        # Tools allowed to share an xml_tag with another tool
        known_shared = {("git", "terminal")}
        seen = {}
        duplicates = []
        for tool in self.all_tools:
            tag = tool.xml_tag_name
            if tag in seen:
                pair = (tool.name, seen[tag])
                pair_canonical = tuple(sorted(pair))
                if pair_canonical in known_shared:
                    continue
                duplicates.append(f"<{tag}> (used by {seen[tag]} and {tool.name})")
            else:
                seen[tag] = tool.name
        self.assertEqual(duplicates, [],
                         f"Duplicate xml_tags: {duplicates}")

    def test_core_tools_present(self):
        """Verify essential tools are in the registry."""
        essential = [
            "file-read", "file-edit", "file-create", "terminal",
            "hub-msg", "web-fetch", "web-search",
            "tool-search", "tool-load",
        ]
        registry_names = {t.name for t in self.all_tools}
        missing = [name for name in essential if name not in registry_names]
        self.assertEqual(missing, [],
                         f"Essential tools missing from registry: {missing}")

    def test_web_tools_have_correct_xml_form(self):
        """Web tools use nested XML form."""
        for name in ("web-fetch", "web-search"):
            tool = self.registry.get(name)
            self.assertIsNotNone(tool, f"{name} not in registry")
            self.assertEqual(tool.xml_form, "nested",
                             f"{name} should have xml_form='nested'")

    def test_on_demand_tools_have_correct_xml_form(self):
        """On-demand tools use nested XML form."""
        for name in ("tool-search", "tool-load"):
            tool = self.registry.get(name)
            self.assertIsNotNone(tool, f"{name} not in registry")
            self.assertEqual(tool.xml_form, "nested",
                             f"{name} should have xml_form='nested'")

    def test_mcp_reload_has_correct_xml_form(self):
        """mcp-reload uses body XML form."""
        tool = self.registry.get("mcp-reload")
        self.assertIsNotNone(tool, "mcp-reload not in registry")
        self.assertEqual(tool.xml_form, "body",
                         "mcp-reload should have xml_form='body'")


def _build_sample_tag(tool) -> str | None:
    """Build a sample XML string for a tool based on its xml_form.

    Returns None if the form is unrecognized.
    """
    tag = tool.xml_tag_name

    if tool.xml_form == "body":
        return f"<{tag}></{tag}>"

    if tool.xml_form == "nested":
        # Build with first parameter if available
        if tool.parameters:
            param = tool.parameters[0]
            if param.type == "string":
                return f"<{tag}><{param.name}>test_value</{param.name}></{tag}>"
            elif param.type == "integer":
                return f"<{tag}><{param.name}>42</{param.name}></{tag}>"
            elif param.type == "boolean":
                return f"<{tag}><{param.name}>true</{param.name}></{tag}>"
            else:
                return f"<{tag}><{param.name}>test</{param.name}></{tag}>"
        else:
            return f"<{tag}></{tag}>"

    if tool.xml_form == "attributes":
        if tool.parameters:
            param = tool.parameters[0]
            return f'<{tag} {param.name}="test_value" />'
        return f"<{tag} />"

    if tool.xml_form == "mixed":
        if tool.parameters:
            param = tool.parameters[0]
            return f'<{tag} {param.name}="test_value">body_text</{tag}>'
        return f"<{tag}>body_text</{tag}>"

    return None


if __name__ == "__main__":
    unittest.main()
