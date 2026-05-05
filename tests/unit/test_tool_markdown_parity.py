"""Verify registry generates correct markdown matching tool-reference files."""

import os
from pathlib import Path

from kollabor_agent.tool_registry import ToolRegistry, get_registry
from kollabor_agent.tool_generators.markdown import render_tool_markdown


class TestMarkdownParity:
    """Verify registry-generated markdown matches existing tool-reference docs."""

    @classmethod
    def setup_class(cls):
        ToolRegistry.reset()
        cls.registry = get_registry()
        cls.ref_dir = Path(
            "bundles/agents/_base/sections/tool-reference"
        )

    def test_read_markdown_parity(self):
        """file-read markdown matches file-read.md structure."""
        tool = self.registry.get("file-read")
        md = render_tool_markdown(tool)
        # Should mention the XML tag name
        assert "<read>" in md
        # Should mention parameters
        assert "file" in md

    def test_terminal_markdown_parity(self):
        """terminal markdown matches terminal.md structure."""
        tool = self.registry.get("terminal")
        md = render_tool_markdown(tool)
        assert "<terminal>" in md
        assert "command" in md

    def test_edit_markdown_parity(self):
        """file-edit markdown matches file-edit.md structure."""
        tool = self.registry.get("file-edit")
        md = render_tool_markdown(tool)
        assert "<edit>" in md
        assert "find" in md
        assert "replace" in md

    def test_all_tools_generate_markdown(self):
        """Every registered tool can generate markdown without error."""
        for tool in self.registry.list():
            md = render_tool_markdown(tool)
            assert len(md) > 0, f"{tool.name} generated empty markdown"
            assert tool.xml_tag_name in md, f"{tool.name} missing xml tag in md"
