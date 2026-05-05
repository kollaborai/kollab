"""Integration tests for unified tool loading.

Verifies the full pipeline from registry through generators to
system prompt and native tool schemas, in both coexistence modes.
"""

import json
from unittest.mock import MagicMock

import pytest

from kollabor_agent.tool_registry import ToolRegistry, get_registry
from kollabor_agent.tool_generators.native_json import generate_openai_tools
from kollabor_agent.tool_generators.xml_regex import generate_all_regexes
from kollabor_agent.tool_generators.markdown import render_for_bundle


@pytest.fixture(autouse=True)
def fresh_registry():
    """Ensure fresh registry for each test."""
    ToolRegistry.reset()
    get_registry()


class TestRegistryIntegration:
    """End-to-end registry integration tests."""

    def test_full_pipeline_native_json(self):
        """Registry -> native JSON -> valid OpenAI schemas."""
        r = get_registry()
        tools = generate_openai_tools(r.names(), registry=r)

        assert len(tools) >= 16  # Was 16 in phase A; now 58+ after phases B-D
        for t in tools:
            func = t["function"]
            assert func["name"]
            assert func["description"]
            assert "parameters" in func
            params = func["parameters"]
            assert params["type"] == "object"
            assert isinstance(params["properties"], dict)

    def test_full_pipeline_regex(self):
        """Registry -> XML regex -> matches sample XML."""
        r = get_registry()
        regexes = generate_all_regexes()

        # Test a representative sample
        samples = {
            "file-read": "<read><file>test.py</file></read>",
            "file-edit": "<edit><file>a.py</file><find>x</find><replace>y</replace></edit>",
            "file-create": "<create><file>a.py</file><content>hi</content></create>",
            "file-delete": "<delete><file>a.py</file></delete>",
            "file-move": "<move><from>a</from><to>b</to></move>",
            "file-copy": "<copy><from>a</from><to>b</to></copy>",
            "file-append": "<append><file>a.py</file><content>more</content></append>",
            "file-grep": "<grep><file>a.py</file><pattern>def</pattern></grep>",
            "terminal": "<terminal>ls</terminal>",
            "directory": "<mkdir><path>src/pkg</path></mkdir>",
            "directory-remove": "<rmdir><path>src/old</path></rmdir>",
        }

        for name, sample in samples.items():
            pat = regexes.get(name)
            assert pat is not None, f"No regex for {name}"
            m = pat.search(sample)
            assert m is not None, f"Regex for {name} didn't match: {sample}"

    def test_full_pipeline_markdown(self):
        """Registry -> markdown -> valid docs for a bundle."""
        r = get_registry()
        # Simulate coder bundle tools
        bundle_tools = ["terminal", "file-read", "file-edit", "file-append", "directory"]
        md = render_for_bundle(bundle_tools, registry=r)

        assert "Tool Reference" in md
        assert "File Operations" in md
        assert "Terminal" in md
        # Each tool's XML tag should appear
        assert "<read>" in md
        assert "<edit>" in md
        assert "<terminal>" in md

    def test_mcp_integration_registry_path_flag_on(self):
        """mcp_integration._get_registry_tools returns tools when flag on."""
        from kollabor_agent.mcp_integration import MCPIntegration

        config = MagicMock()
        config.get = lambda key, default=None: (
            True if key == "kollabor.tool_registry.use_registry" else default
        )

        mcp = MCPIntegration.__new__(MCPIntegration)
        mcp.config = config

        result = mcp._get_registry_tools()
        assert result is not None
        assert len(result) >= 16  # 16 in phase A; 58+ after phases B-D
        names = {t["name"] for t in result}
        assert "file_read" in names
        assert "terminal" in names
        assert "file_mkdir" in names  # directory -> file_mkdir

    def test_mcp_integration_registry_path_flag_off(self):
        """mcp_integration._get_registry_tools returns None when flag off."""
        from kollabor_agent.mcp_integration import MCPIntegration

        config = MagicMock()
        config.get = lambda key, default=None: (
            False
            if key == "kollabor.tool_registry.use_registry"
            else default
        )

        mcp = MCPIntegration.__new__(MCPIntegration)
        mcp.config = config

        result = mcp._get_registry_tools()
        assert result is None

    def test_system_prompt_builder_injects_registry(self):
        """system_prompt_builder adds registry docs when flag on."""
        from kollabor_ai.system_prompt_builder import SystemPromptBuilder

        config = MagicMock()
        config.get = lambda key, default=None: (
            True
            if key == "kollabor.tool_registry.use_registry"
            else (
                False
                if key == "terminal.interactive_shell"
                else (
                    True
                    if key == "kollabor.llm.system_prompt.include_project_structure"
                    else default
                )
            )
        )

        agent_mgr = MagicMock()
        agent_mgr.get_system_prompt.return_value = None
        agent_mgr.get_active_agent.return_value = None

        builder = SystemPromptBuilder(config, agent_mgr)
        builder.set_plugin_instances({})

        prompt = builder.build()
        assert "Tool Reference" in prompt
        assert "The tools below are available" in prompt

    def test_system_prompt_builder_skips_registry_when_off(self):
        """system_prompt_builder skips registry docs when flag off."""
        from kollabor_ai.system_prompt_builder import SystemPromptBuilder

        config = MagicMock()
        config.get = lambda key, default=None: (
            False
            if key == "kollabor.tool_registry.use_registry"
            else (
                False
                if key == "terminal.interactive_shell"
                else (
                    True
                    if key == "kollabor.llm.system_prompt.include_project_structure"
                    else default
                )
            )
        )

        agent_mgr = MagicMock()
        agent_mgr.get_system_prompt.return_value = None
        agent_mgr.get_active_agent.return_value = None

        builder = SystemPromptBuilder(config, agent_mgr)
        builder.set_plugin_instances({})

        prompt = builder.build()
        # Should NOT have registry-generated tool reference
        assert not (
            "Tool Reference" in prompt
            and "The tools below are available" in prompt
        )
