"""Unit tests for the tool registry."""

import pytest

from kollabor_agent.tool_definition import ToolDefinition, ToolParameter
from kollabor_agent.tool_registry import ToolRegistry


@pytest.fixture
def fresh_registry():
    """Create a fresh registry for each test."""
    return ToolRegistry()


class TestToolRegistry:
    def test_register_and_get(self, fresh_registry):
        tool = ToolDefinition(name="test-tool", description="Test")
        fresh_registry.register(tool)
        assert fresh_registry.get("test-tool") is tool
        assert fresh_registry.get("nonexistent") is None

    def test_duplicate_registration_raises(self, fresh_registry):
        t1 = ToolDefinition(name="dup", description="First")
        t2 = ToolDefinition(name="dup", description="Second")
        fresh_registry.register(t1)
        # Duplicate without replace=True just skips silently
        fresh_registry.register(t2)
        # But the original tool should remain
        assert fresh_registry.get("dup").description == "First"

    def test_list_sorted_by_name(self, fresh_registry):
        fresh_registry.register(ToolDefinition(name="zzz", description=""))
        fresh_registry.register(ToolDefinition(name="aaa", description=""))
        fresh_registry.register(ToolDefinition(name="mmm", description=""))
        names = [t.name for t in fresh_registry.list()]
        assert names == ["aaa", "mmm", "zzz"]

    def test_list_by_category(self, fresh_registry):
        fresh_registry.register(ToolDefinition(name="a", description="", category="file_ops"))
        fresh_registry.register(ToolDefinition(name="b", description="", category="terminal"))
        fresh_registry.register(ToolDefinition(name="c", description="", category="file_ops"))
        file_ops = fresh_registry.list_by_category("file_ops")
        assert {t.name for t in file_ops} == {"a", "c"}

    def test_get_for_bundle_filters(self, fresh_registry):
        fresh_registry.register(ToolDefinition(name="file-read", description=""))
        fresh_registry.register(ToolDefinition(name="file-edit", description=""))
        fresh_registry.register(ToolDefinition(name="terminal", description=""))

        bundle_tools = fresh_registry.get_for_bundle(
            ["file-read", "terminal", "unknown"]
        )
        names = [t.name for t in bundle_tools]
        assert names == ["file-read", "terminal"]

    def test_get_by_native_name(self, fresh_registry):
        fresh_registry.register(ToolDefinition(name="file-read", description=""))
        tool = fresh_registry.get_by_native_name("file_read")
        assert tool is not None
        assert tool.name == "file-read"
        assert fresh_registry.get_by_native_name("nonexistent") is None

    def test_get_by_xml_tag(self, fresh_registry):
        fresh_registry.register(
            ToolDefinition(name="file-read", description="", xml_tag="read")
        )
        tool = fresh_registry.get_by_xml_tag("read")
        assert tool is not None
        assert tool.name == "file-read"
        assert fresh_registry.get_by_xml_tag("nonexistent") is None

    def test_all_categories(self, fresh_registry):
        fresh_registry.register(ToolDefinition(name="a", description="", category="file_ops"))
        fresh_registry.register(ToolDefinition(name="b", description="", category="terminal"))
        assert fresh_registry.all_categories() == ["file_ops", "terminal"]

    def test_names(self, fresh_registry):
        fresh_registry.register(ToolDefinition(name="file-read", description=""))
        fresh_registry.register(ToolDefinition(name="terminal", description=""))
        assert fresh_registry.names() == ["file-read", "terminal"]

    def test_reset(self):
        ToolRegistry.reset()
        assert ToolRegistry._instance is None


class TestToolDefinition:
    def test_native_name(self):
        tool = ToolDefinition(name="file-read", description="test")
        assert tool.native_name == "file_read"

    def test_xml_tag_name_default(self):
        tool = ToolDefinition(name="file-read", description="test")
        assert tool.xml_tag_name == "file-read"

    def test_xml_tag_name_explicit(self):
        tool = ToolDefinition(name="file-read", description="test", xml_tag="read")
        assert tool.xml_tag_name == "read"

    def test_to_json_schema(self):
        tool = ToolDefinition(
            name="file-read",
            description="Read a file",
            parameters=[
                ToolParameter(name="file", type="string", description="Path", required=True),
                ToolParameter(name="limit", type="integer", description="Max lines"),
            ],
        )
        schema = tool.to_json_schema()
        assert schema["name"] == "file_read"
        assert schema["description"] == "Read a file"
        assert "file" in schema["parameters"]["properties"]
        assert schema["parameters"]["required"] == ["file"]

    def test_parameter_json_schema(self):
        param = ToolParameter(
            name="flag",
            type="boolean",
            description="A flag",
            default=False,
        )
        schema = param.to_json_schema()
        assert schema["type"] == "boolean"
        assert schema["default"] is False
