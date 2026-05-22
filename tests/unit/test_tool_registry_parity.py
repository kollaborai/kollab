"""Tests for registry-to-hardcoded parity.

Verifies that the unified tool registry produces output consistent
with the original hardcoded definitions, and that all tools have
required fields.

Updated after phases B-D expanded from 16 to 58+ tools.
"""

import json

from kollabor_agent.tool_registry import ToolRegistry, get_registry
from kollabor_agent.tool_generators.native_json import generate_openai_tools


class TestParity:
    """Verify registry output matches expected structure."""

    @classmethod
    def setup_class(cls):
        ToolRegistry.reset()
        cls.registry = get_registry()

    def test_tool_count_at_least_phase_a(self):
        """Registry has at least the original 16 tools (file_ops + terminal + git)."""
        # Phase A shipped 16; phases B-D added hub, context, scratchpad,
        # task, wait — now 58+. Just verify we have enough.
        assert len(self.registry.list()) >= 16

    def test_file_ops_native_names_present(self):
        """All original hardcoded file ops are in the registry."""
        file_ops_names = [
            "file_read", "file_create", "file_create_overwrite",
            "file_edit", "file_append", "file_insert_after",
            "file_insert_before", "file_delete", "file_move",
            "file_copy", "file_copy_overwrite", "file_mkdir",
            "file_rmdir", "file_grep", "terminal", "git",
        ]
        reg_native = {t.native_name for t in self.registry.list()}
        for name in file_ops_names:
            assert name in reg_native, f"Missing native tool: {name}"

    def test_file_read_schema_matches(self):
        """file-read schema matches the hardcoded definition."""
        tool = self.registry.get("file-read")
        schema = tool.to_json_schema()

        assert schema["name"] == "file_read"
        props = schema["parameters"]["properties"]
        assert "file" in props
        assert props["file"]["type"] == "string"
        assert "offset" in props
        assert props["offset"]["type"] == "integer"
        assert "limit" in props
        assert props["limit"]["type"] == "integer"
        assert schema["parameters"]["required"] == ["file"]

    def test_file_edit_schema_matches(self):
        """file-edit schema matches."""
        tool = self.registry.get("file-edit")
        schema = tool.to_json_schema()

        assert schema["name"] == "file_edit"
        props = schema["parameters"]["properties"]
        assert set(props.keys()) == {"file", "find", "replace"}
        assert schema["parameters"]["required"] == ["file", "find", "replace"]

    def test_terminal_schema_matches(self):
        """terminal schema matches."""
        tool = self.registry.get("terminal")
        schema = tool.to_json_schema()

        assert schema["name"] == "terminal"
        props = schema["parameters"]["properties"]
        assert "command" in props
        assert schema["parameters"]["required"] == ["command"]

    def test_all_tools_have_required_fields(self):
        """Every tool has name, description, xml_tag, and category."""
        for tool in self.registry.list():
            assert tool.name, f"Tool missing name"
            assert tool.description, f"Tool {tool.name} missing description"
            assert tool.xml_tag_name, f"Tool {tool.name} missing xml_tag"
            assert tool.category, f"Tool {tool.name} missing category"

    def test_openai_output_is_valid(self):
        """Generated OpenAI tools are well-formed."""
        tools = generate_openai_tools(self.registry.names(), registry=self.registry)
        assert len(tools) >= 16
        for t in tools:
            assert t["type"] == "function"
            func = t["function"]
            assert "name" in func
            assert "description" in func
            assert "parameters" in func
            params = func["parameters"]
            assert params["type"] == "object"
            assert "properties" in params

    def test_all_categories_represented(self):
        """Registry includes tools from all expected categories."""
        categories = self.registry.all_categories()
        expected = {"file_ops", "terminal", "hub", "context", "scratchpad", "task"}
        for cat in expected:
            assert cat in categories, f"Missing category: {cat}"
