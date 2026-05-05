"""Unit tests for tool generators."""

import re

from kollabor_agent.tool_definition import ToolDefinition, ToolParameter
from kollabor_agent.tool_generators.native_json import (
    generate_anthropic_tools,
    generate_openai_tools,
)
from kollabor_agent.tool_generators.xml_regex import build_regex_for_tool
from kollabor_agent.tool_generators.markdown import (
    render_tool_markdown,
    render_for_bundle,
)


class TestNativeJson:
    def test_openai_wrapping(self):
        from kollabor_agent.tool_registry import ToolRegistry
        registry = ToolRegistry()
        tool = ToolDefinition(
            name="test-tool",
            description="Test tool",
            parameters=[
                ToolParameter(name="arg1", type="string", description="First arg", required=True),
            ],
        )
        registry.register(tool)
        schemas = generate_openai_tools(["test-tool"], registry=registry)
        assert len(schemas) == 1
        assert schemas[0]["type"] == "function"
        func = schemas[0]["function"]
        assert func["name"] == "test_tool"
        assert func["description"] == "Test tool"
        assert "arg1" in func["parameters"]["properties"]
        assert func["parameters"]["required"] == ["arg1"]

    def test_anthropic_format(self):
        from kollabor_agent.tool_registry import ToolRegistry
        registry = ToolRegistry()
        tool = ToolDefinition(
            name="test-tool",
            description="Test tool",
            parameters=[
                ToolParameter(name="arg1", type="string", description="First arg", required=True),
            ],
        )
        registry.register(tool)
        schemas = generate_anthropic_tools(["test-tool"], registry=registry)
        assert len(schemas) == 1
        assert schemas[0]["name"] == "test_tool"
        assert "input_schema" in schemas[0]
        assert "arg1" in schemas[0]["input_schema"]["properties"]

    def test_unknown_tool_skipped(self):
        from kollabor_agent.tool_registry import ToolRegistry
        registry = ToolRegistry()
        schemas = generate_openai_tools(["nonexistent"], registry=registry)
        assert schemas == []


class TestXmlRegex:
    def test_body_form(self):
        tool = ToolDefinition(
            name="terminal",
            description="",
            xml_tag="terminal",
            xml_form="body",
            xml_body_param="command",
        )
        pattern = build_regex_for_tool(tool)
        compiled = re.compile(pattern, re.DOTALL | re.IGNORECASE)
        m = compiled.search("<terminal>git status</terminal>")
        assert m is not None
        assert m.group(1) == "git status"

    def test_nested_form(self):
        tool = ToolDefinition(
            name="file-read",
            description="",
            xml_tag="read",
            xml_form="nested",
        )
        pattern = build_regex_for_tool(tool)
        compiled = re.compile(pattern, re.DOTALL | re.IGNORECASE)
        m = compiled.search("<read><file>test.py</file></read>")
        assert m is not None
        assert "<file>test.py</file>" in m.group(1)

    def test_mixed_form(self):
        tool = ToolDefinition(
            name="hub-msg",
            description="",
            xml_tag="hub_msg",
            xml_form="mixed",
            xml_attributes=["to"],
        )
        pattern = build_regex_for_tool(tool)
        compiled = re.compile(pattern, re.DOTALL | re.IGNORECASE)
        m = compiled.search('<hub_msg to="lapis">hello</hub_msg>')
        assert m is not None

    def test_unknown_form_raises(self):
        tool = ToolDefinition(
            name="bad",
            description="",
            xml_form="invalid",
        )
        try:
            build_regex_for_tool(tool)
            assert False, "Should have raised ValueError"
        except ValueError:
            pass


class TestMarkdown:
    def test_render_tool(self):
        tool = ToolDefinition(
            name="test-tool",
            description="A test tool",
            xml_tag="test",
            parameters=[
                ToolParameter(name="arg", type="string", description="The arg", required=True),
            ],
            examples=["<test><arg>value</arg></test>"],
            result_format="Returns the arg",
        )
        md = render_tool_markdown(tool)
        assert "<test>" in md
        assert "A test tool" in md
        assert "arg" in md
        assert "<test><arg>value</arg></test>" in md

    def test_render_for_bundle(self):
        from kollabor_agent.tool_registry import ToolRegistry
        registry = ToolRegistry()
        registry.register(ToolDefinition(
            name="file-read", description="Read", category="file_ops", xml_tag="read",
        ))
        registry.register(ToolDefinition(
            name="terminal", description="Exec", category="terminal",
        ))
        md = render_for_bundle(["file-read", "terminal"], registry=registry)
        assert "Tool Reference" in md
        assert "File Operations" in md
        assert "Terminal" in md

    def test_render_filters_unknown(self):
        from kollabor_agent.tool_registry import ToolRegistry
        registry = ToolRegistry()
        registry.register(ToolDefinition(
            name="file-read", description="Read", category="file_ops", xml_tag="read",
        ))
        md = render_for_bundle(["file-read", "nonexistent"], registry=registry)
        assert "file-read" in md or "read" in md
